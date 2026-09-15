"""
_webrtc_tts_spoken_exercise.py — exercício do `probe_webrtc_tts_spoken.sh` (VOZ-05, fatia 3).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente é um WebSocket que fala o
protocolo do widget, num pool de IA só-WebRTC com a fixture `skill_probe_tts_v1`, e um participante
LiveKit de verdade que publica microfone (silêncio, e uma fala na hora do barge-in) e GRAVA o que
ouve na sala. O que ele ouve é transcrito pelo `speaches` — o mesmo serviço, mas do lado do cliente.

  T1 o cliente recebe uma trilha de áudio do agente e o aviso N1 é ouvido (palavras da frase)
  T2 o prompt do menu M1 também é falado — não só `notify`
  T3 na ordem em que o fluxo mandou (N1 antes de M1), sem sobreposição que embaralhe as palavras
  C  CONTROLE do barge-in: N1+M1, sem interrupção, são ouvidos quase inteiros (≥ 70% da voz
     sintetizada) — sem ele, "a fala durou pouco" no T4 poderia ser só perda de áudio
  T4 barge-in: o cliente fala 2 s depois de N2 começar → a voz do agente para em ≤ 1,5 s, N2
     chega a < 60% da duração, e o fim de N2 não é ouvido
  T5 depois da interrupção a fala volta: N3 é ouvido (a fala do barge-in responde o menu de TEXTO
     livre M2 — o menu de botão tomaria a frase como escolha, e isso é da NIV-13)
  LAT texto do N1 no WebSocket → primeiro áudio do agente

Imprime `SID <id>` para o .sh.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import unicodedata
import uuid
import wave

import httpx
import jwt
import numpy as np
import websockets
from livekit import rtc

POOL   = os.environ["POOL"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
LK_URL = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]
SPEACHES  = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
STT_MODEL = os.environ.get("PLUGHUB_WEBRTC_STT_MODEL", "")
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
GW = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"

FLOW = json.load(open("/fixture.json", encoding="utf-8"))["flow"]["steps"]
TXT = {s["id"]: s.get("message") or s.get("prompt") for s in FLOW}
N1, M1, N2, N3 = TXT["n1"], TXT["m1"], TXT["n2"], TXT["n3"]
# as frases em que o probe se apoia TÊM de estar na fixture — trocar a fixture sem trocar
# aqui deixaria as palavras procuradas sem fonte
assert "central de energia" in N1 and "fatura" in M1 and "cancelamento" in N2 and "aguardar" in N3
assert N2.count(".") == 1, "N2 tem de ser UMA frase: o barge-in precisa cair no MEIO dela"
P_N1 = {"central", "energia", "confirmar"}
# Palavras NATIVAS da frase. "email" e "correio" saíram depois de medir: na voz `pf_dora`, pelo
# Opus da sala, o STT do probe ouviu "Emaio" e "coqueio" — o prompt chegava inteiro e na ordem,
# e o ramo reprovava pela transcrição de duas palavras (o que é achado de pronúncia, não de fala).
P_M1 = {"prefere", "receber", "fatura"}
P_N2_FIM = {"cancelamento", "custo", "adicional", "juros"}
P_N3 = {"obrigado", "aguardar"}
FRASE_BARGE = "Espera um pouco, por favor."
LIMIAR = 300          # RMS em quadro de 20 ms a 16 kHz
SR = 16000


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def norm(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if c.isalnum() or c.isspace())
    return t.split()


def voz_s(pcm: bytes) -> float:
    a = np.frombuffer(pcm[: len(pcm) // 640 * 640], dtype=np.int16).astype(np.float32)
    if a.size == 0:
        return 0.0
    q = a.reshape(-1, 320)
    return float((np.sqrt((q ** 2).mean(axis=1)) > LIMIAR).sum()) * 0.02


class Gravador:
    """O que o cliente OUVE: quadros de 10 ms do agente, com a hora de chegada."""

    def __init__(self) -> None:
        self.quadros: list[tuple[float, bytes]] = []
        self.origem = ""

    def trecho(self, t0: float, t1: float) -> bytes:
        return b"".join(d for t, d in self.quadros if t0 <= t < t1)

    def voz_inicio(self, desde: float, ate: float) -> float | None:
        for t, d in self.quadros:
            if desde <= t < ate and voz_s(d * 2) > 0:
                return t
        return None

    def silencio_desde(self, desde: float, janela: float = 1.0) -> float | None:
        """Primeiro instante ≥ desde a partir do qual há `janela` s sem voz. 1 s, não menos: a pausa
        de vírgula da voz sintetizada passa de 0,4 s, e com 0,4 s a sala que NÃO cortava a fala passava."""
        quadros = [(t, d) for t, d in self.quadros if t >= desde]
        ini = None
        for t, d in quadros:
            a = np.frombuffer(d, dtype=np.int16).astype(np.float32)
            falando = a.size and float(np.sqrt((a ** 2).mean())) > LIMIAR
            if falando:
                ini = None
            elif ini is None:
                ini = t
            elif t - ini >= janela:
                return ini
        return None


async def transcrever(http: httpx.AsyncClient, pcm: bytes) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    r = await http.post(f"{SPEACHES}/v1/audio/transcriptions",
                        files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
                        data={"model": STT_MODEL, "language": "pt", "response_format": "json"})
    return r.json().get("text", "") if r.status_code == 200 else f"(http {r.status_code})"


async def sintetizar(http: httpx.AsyncClient, texto: str, sr: int) -> bytes:
    r = await http.post(f"{SPEACHES}/v1/audio/speech", json={
        "model": TTS_MODEL, "input": texto, "voice": TTS_VOICE, "response_format": "pcm", "sample_rate": sr})
    r.raise_for_status()
    return r.content


class Cliente:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.pendentes: list[tuple[float, dict]] = []

    async def espera(self, pred, prazo_s: float) -> tuple[float, dict] | tuple[None, None]:
        for item in self.pendentes:
            if pred(item[1]):
                self.pendentes.remove(item)
                return item
        fim = time.monotonic() + prazo_s
        while time.monotonic() < fim:
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), max(0.1, fim - time.monotonic())))
            except asyncio.TimeoutError:
                break
            item = (time.monotonic(), m)
            if pred(m):
                return item
            self.pendentes.append(item)
        return None, None

    async def manda(self, m: dict) -> None:
        await self.ws.send(json.dumps(m))


def _texto(trecho):
    return lambda m: m.get("type") == "webrtc.message" and trecho in (m.get("text") or "")


def _menu(trecho):
    return lambda m: m.get("type") == "webrtc.interaction" and trecho in (m.get("prompt") or "")


async def main() -> None:
    sub = "c-voz05t-" + uuid.uuid4().hex[:6]
    now = int(time.time())
    token = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": now, "exp": now + 900},
                       SECRET, algorithm="HS256")
    grav = Gravador()
    room = rtc.Room()
    fala_fila: asyncio.Queue[bytes] = asyncio.Queue()
    tarefas: list[asyncio.Task] = []

    @room.on("track_subscribed")
    def _t(track, pub, part):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        grav.origem = part.identity

        async def consome():
            async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
                grav.quadros.append((time.monotonic(), bytes(ev.frame.data)))
        tarefas.append(asyncio.ensure_future(consome()))

    async with httpx.AsyncClient(timeout=120) as http, websockets.connect(GW) as ws:
        c = Cliente(ws)
        _, pronto = await c.espera(lambda m: m.get("type") == "conn.ready", 10)
        await c.manda({"type": "conn.hello", "version": "1"})
        await c.manda({"type": "conn.authenticate", "token": token})
        _, auth = await c.espera(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            emit("FALHA", "T1", "cliente nao autenticou")
            return
        sid = auth["session_id"]
        print(f"SID {sid}", flush=True)
        try:
            _, ready = await c.espera(lambda m: m.get("type") == "webrtc.ready", 60)
            if not (ready and ready.get("token")):
                emit("FALHA", "T1", "nenhum agente de IA atendeu com midia (webrtc.ready sem token)")
                return
            await asyncio.wait_for(room.connect(LK_URL, ready["token"]), 20)
            src = rtc.AudioSource(SR, 1)
            mic = rtc.LocalAudioTrack.create_audio_track("mic-cliente", src)
            await room.local_participant.publish_track(mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

            async def microfone():
                silencio = b"\x00" * 320
                t0 = time.monotonic(); i = 0
                pend = b""
                while True:
                    if not pend and not fala_fila.empty():
                        pend = fala_fila.get_nowait()
                    chunk, pend = (pend[:320], pend[320:]) if pend else (silencio, b"")
                    if len(chunk) < 320:
                        chunk = chunk + b"\x00" * (320 - len(chunk))
                    await src.capture_frame(rtc.AudioFrame(data=chunk, sample_rate=SR, num_channels=1, samples_per_channel=160))
                    i += 1
                    atraso = t0 + i * 0.01 - time.monotonic()
                    if atraso > 0:
                        await asyncio.sleep(atraso)
            tarefas.append(asyncio.ensure_future(microfone()))

            # ── T1/T2/T3 + controle ──────────────────────────────────────────
            t_n1, n1 = await c.espera(_texto("central de energia"), 60)
            t_m1, m1 = await c.espera(_menu("fatura"), 30)
            if not (n1 and m1):
                emit("FALHA", "T1", f"o fluxo nao mandou N1/M1 (n1={bool(n1)} m1={bool(m1)})")
                return
            # espera o agente terminar de falar: 1,5 s sem voz depois de ter havido voz (máx 30 s)
            fim = time.monotonic() + 30
            while time.monotonic() < fim:
                ultimo = max((t for t, d in grav.quadros if voz_s(d * 2) > 0), default=None)
                if ultimo and time.monotonic() - ultimo > 1.5 and ultimo > t_m1:
                    break
                await asyncio.sleep(0.2)
            t_fim1 = time.monotonic()
            aud1 = grav.trecho(t_n1 - 0.5, t_fim1)
            txt1 = await transcrever(http, aud1) if aud1 else ""
            pal1 = norm(txt1)
            primeiro = grav.voz_inicio(t_n1 - 0.5, t_fim1)
            emit("OK" if (grav.origem and P_N1 <= set(pal1)) else "FALHA", "T1",
                 f"o cliente ouve o agente (trilha de {grav.origem or 'NINGUEM'}): {txt1!r}")
            emit("OK" if P_M1 <= set(pal1) else "FALHA", "T2", f"prompt do menu falado (palavras {sorted(P_M1)})")
            ordem = (P_N1 <= set(pal1) and P_M1 <= set(pal1)
                     and pal1.index("central") < pal1.index("fatura"))
            emit("OK" if ordem else "FALHA", "T3", f"N1 antes de M1, sem embaralhar (palavras={pal1})")
            ref1 = voz_s(await sintetizar(http, N1, SR)) + voz_s(await sintetizar(http, M1, SR))
            ouvido1 = voz_s(aud1)
            emit("OK" if (ref1 and ouvido1 >= 0.7 * ref1) else "FALHA", "C",
                 f"CONTROLE sem interrupcao: ouvido {ouvido1:.1f} s de {ref1:.1f} s de voz sintetizada")
            if primeiro:
                emit("INFO", "LAT", f"texto do N1 no WebSocket → primeiro audio do agente: {primeiro - t_n1:.2f} s")

            # ── T4 barge-in ──────────────────────────────────────────────────
            await c.manda({"type": "webrtc.menu_submit", "menu_id": m1["menu_id"], "interaction": "button", "result": "email"})
            t_n2, n2 = await c.espera(_texto("condições do seu contrato"), 30)
            if not n2:
                emit("FALHA", "T4", "o fluxo nao mandou N2")
                return
            ini2 = None
            fim = time.monotonic() + 20
            while time.monotonic() < fim and ini2 is None:
                ini2 = grav.voz_inicio(t_n2 - 0.5, time.monotonic())
                await asyncio.sleep(0.05)
            if ini2 is None:
                emit("FALHA", "T4", "N2 nunca comecou a ser falado")
                return
            await asyncio.sleep(max(0.0, ini2 + 2.0 - time.monotonic()))
            barge = await sintetizar(http, FRASE_BARGE, SR)
            t_barge = time.monotonic()
            fala_fila.put_nowait(barge)
            # a fala do cliente responde o menu de texto livre M2, e o fluxo manda N3: a janela do
            # N2 fecha quando N3 chega (senão a voz de N3 contaria como resto de N2)
            t_n3, n3 = await c.espera(_texto("obrigado por aguardar"), 25)
            fim_n2 = min(t_n3 or (t_barge + 6), t_barge + 6)
            await asyncio.sleep(max(0.0, fim_n2 - time.monotonic()))
            corte = grav.silencio_desde(t_barge)
            aud2 = grav.trecho(ini2 - 0.1, fim_n2)
            ref2 = voz_s(await sintetizar(http, N2, SR))
            ouvido2 = voz_s(aud2)
            txt2 = await transcrever(http, aud2) if aud2 else ""
            fim_ouvido = P_N2_FIM & set(norm(txt2))
            atraso = (corte - t_barge) if (corte and corte < fim_n2) else None
            emit("OK" if (atraso is not None and atraso <= 1.5) else "FALHA", "T4",
                 f"barge-in: a voz do agente parou {atraso if atraso is None else round(atraso, 2)} s depois de o cliente falar")
            emit("OK" if (ref2 and ouvido2 < 0.6 * ref2) else "FALHA", "T4",
                 f"N2 interrompido: ouvido {ouvido2:.1f} s de {ref2:.1f} s de voz")
            emit("OK" if not fim_ouvido else "FALHA", "T4",
                 f"o fim de N2 nao foi ouvido (ouvidas={sorted(fim_ouvido)}): {txt2!r}")
            depois = voz_s(grav.trecho(t_barge + 1.5, fim_n2))
            emit("OK" if depois <= 0.3 else "FALHA", "T4",
                 f"silencio depois do corte: {depois:.1f} s de voz do agente entre +1,5 s e a chegada de N3")

            # ── T5 a fala volta ─────────────────────────────────────────────
            if not n3:
                emit("FALHA", "T5", "o fluxo nao mandou N3 (a fala do cliente nao respondeu o menu de texto M2)")
                return
            _, m3 = await c.espera(_menu("encerrar"), 30)
            await asyncio.sleep(max(0.0, t_n3 + 8 - time.monotonic()))
            txt3 = await transcrever(http, grav.trecho(t_n3 - 0.2, time.monotonic()))
            emit("OK" if P_N3 <= set(norm(txt3)) else "FALHA", "T5",
                 f"depois do barge-in a fala volta: {txt3!r}")
            if m3:
                await c.manda({"type": "webrtc.menu_submit", "menu_id": m3["menu_id"], "interaction": "button", "result": "sim"})
                await asyncio.sleep(2)
        finally:
            for t in tarefas:
                t.cancel()
            try:
                await asyncio.wait_for(room.disconnect(), 10)
            except Exception:
                pass
            try:
                await c.manda({"type": "webrtc.hangup"})
                await asyncio.sleep(1)
            except Exception:
                pass


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
