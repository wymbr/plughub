"""
_webrtc_voice_collect_exercise.py — exercício do `probe_webrtc_voice_collect.sh` (VOZ-05 fatia 5b).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Cada ramo é uma CHAMADA: um WebSocket
com o protocolo do widget e um participante LiveKit de verdade (`customer-…`) que publica microfone,
tecla DTMF pelo SFU (`publish_dtmf`, o mesmo evento da futura perna SIP) e grava o que ouve. A
fixture `skill_probe_voice_collect_v1` manda um marcador por saída, com o valor coletado no texto.

  H  o prompt do menu é FALADO com as teclas ("tecle dois")
  A1 tecla de OUTRO participante (`agent-…`) não responde o menu — o SFU entrega DTMF a todos
  K1 CONTROLE do A1: a tecla do cliente responde → `voz05b-valor=correio`
  K2 código de vários dígitos por teclado, com terminador → `voz05b-codigo=427`
  R1 no campo SÓ de teclado, a fala do cliente não responde (fica como registro)
  R2 CONTROLE do R1: o teclado responde o mesmo campo depois
  V1 no menu com fala, "opção dois" dita responde → `voz05b-valor=correio`
  I1 duas teclas fora das opções → `voz05b-invalido`, com a mensagem de inválido antes
  T1 nada → `voz05b-timeout`, e só depois do prazo (prompt + 12 s)
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
from livekit import api, rtc

POOL   = os.environ["POOL"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
LK_URL = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]
LK_KEY = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_KEY"]
LK_SEC = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_SECRET"]
SPEACHES  = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
STT_MODEL = os.environ.get("PLUGHUB_WEBRTC_STT_MODEL", "")
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
GW = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
SR = 16000
LIMIAR = 300
DTMF_CODE = {str(i): i for i in range(10)} | {"*": 10, "#": 11}


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def norm(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if c.isalnum() or c.isspace())
    return t.split()


def falando(pcm: bytes) -> bool:
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    return bool(a.size) and float(np.sqrt((a ** 2).mean())) > LIMIAR


class Chamada:
    """Um cliente: WebSocket do widget + participante na sala, com microfone e gravação."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.ws = None
        self.room = rtc.Room()
        self.pend: list[tuple[float, dict]] = []
        self.quadros: list[tuple[float, bytes]] = []
        self.fala: asyncio.Queue[bytes] = asyncio.Queue()
        self.tarefas: list[asyncio.Task] = []
        self.sid = ""
        self.room_name = ""

    async def abre(self, ramo: str) -> bool:
        sub = f"c-voz05b-{ramo.lower()}-" + uuid.uuid4().hex[:6]
        n = int(time.time())
        tok = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": n, "exp": n + 900},
                         SECRET, algorithm="HS256")
        self.ws = await websockets.connect(GW)
        await self.espera(lambda m: m.get("type") == "conn.ready", 10)
        await self.manda({"type": "conn.hello", "version": "1"})
        await self.manda({"type": "conn.authenticate", "token": tok})
        _, auth = await self.espera(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            emit("INCONCL", ramo, "cliente nao autenticou")
            return False
        self.sid = auth["session_id"]
        _, ready = await self.espera(lambda m: m.get("type") == "webrtc.ready", 60)
        if not (ready and ready.get("token")):
            emit("INCONCL", ramo, f"nenhum agente de IA atendeu com midia (session={self.sid})")
            return False
        self.room_name = ready.get("room_name") or ""

        @self.room.on("track_subscribed")
        def _t(track, pub, part):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return

            async def consome():
                async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
                    self.quadros.append((time.monotonic(), bytes(ev.frame.data)))
            self.tarefas.append(asyncio.ensure_future(consome()))

        await asyncio.wait_for(self.room.connect(LK_URL, ready["token"]), 20)
        src = rtc.AudioSource(SR, 1)
        mic = rtc.LocalAudioTrack.create_audio_track("mic-cliente", src)
        await self.room.local_participant.publish_track(
            mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

        async def microfone():
            t0, i, pend = time.monotonic(), 0, b""
            while True:
                if not pend and not self.fala.empty():
                    pend = self.fala.get_nowait()
                chunk, pend = (pend[:320], pend[320:]) if pend else (b"\x00" * 320, b"")
                chunk = chunk.ljust(320, b"\x00")
                await src.capture_frame(rtc.AudioFrame(data=chunk, sample_rate=SR, num_channels=1,
                                                       samples_per_channel=160))
                i += 1
                atraso = t0 + i * 0.01 - time.monotonic()
                if atraso > 0:
                    await asyncio.sleep(atraso)
        self.tarefas.append(asyncio.ensure_future(microfone()))
        return True

    async def manda(self, m: dict) -> None:
        await self.ws.send(json.dumps(m))

    async def espera(self, pred, prazo_s: float):
        for item in self.pend:
            if pred(item[1]):
                self.pend.remove(item)
                return item
        fim = time.monotonic() + prazo_s
        while time.monotonic() < fim:
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), max(0.1, fim - time.monotonic())))
            except asyncio.TimeoutError:
                break
            except websockets.ConnectionClosed:
                # a sessão fechou (o fluxo terminou): o que chegou antes já foi lido; não é exceção do ramo
                break
            item = (time.monotonic(), m)
            if pred(m):
                return item
            self.pend.append(item)
        return None, None

    async def texto(self, trecho: str, prazo_s: float):
        return await self.espera(lambda m: m.get("type") == "webrtc.message" and trecho in (m.get("text") or ""), prazo_s)

    async def marca(self, prazo_s: float):
        """O marcador de SAÍDA do fluxo — `voz05b-inicio` é a entrada, não conta."""
        return await self.espera(lambda m: m.get("type") == "webrtc.message"
                                 and (m.get("text") or "").startswith("voz05b-")
                                 and m.get("text") != "voz05b-inicio", prazo_s)

    async def menu(self, menu_prompt: str, prazo_s: float = 45):
        return await self.espera(lambda m: m.get("type") == "webrtc.interaction" and menu_prompt in (m.get("prompt") or ""), prazo_s)

    async def tecla(self, digitos: str, pausa: float = 0.4) -> None:
        for d in digitos:
            await self.room.local_participant.publish_dtmf(code=DTMF_CODE[d], digit=d)
            await asyncio.sleep(pausa)

    async def diz(self, frase: str) -> None:
        r = await self.http.post(f"{SPEACHES}/v1/audio/speech", json={
            "model": TTS_MODEL, "input": frase, "voice": TTS_VOICE, "response_format": "pcm", "sample_rate": SR})
        r.raise_for_status()
        self.fala.put_nowait(r.content)

    async def fim_da_fala(self, desde: float, prazo_s: float = 30) -> float:
        """Espera 1,5 s sem voz do agente depois de ter havido voz desde `desde`."""
        fim = time.monotonic() + prazo_s
        while time.monotonic() < fim:
            ultimo = max((t for t, d in self.quadros if t >= desde and falando(d)), default=None)
            if ultimo and time.monotonic() - ultimo > 1.5:
                return ultimo
            await asyncio.sleep(0.2)
        return time.monotonic()

    async def transcreve(self, t0: float, t1: float) -> str:
        pcm = b"".join(d for t, d in self.quadros if t0 <= t < t1)
        if not pcm:
            return ""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
        r = await self.http.post(f"{SPEACHES}/v1/audio/transcriptions",
                                 files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
                                 data={"model": STT_MODEL, "language": "pt", "response_format": "json"})
        return r.json().get("text", "") if r.status_code == 200 else f"(http {r.status_code})"

    async def fecha(self) -> None:
        for t in self.tarefas:
            t.cancel()
        try:
            await asyncio.wait_for(self.room.disconnect(), 10)
        except Exception:
            pass
        try:
            await self.manda({"type": "webrtc.hangup"})
            await asyncio.sleep(1)
            await self.ws.close()
        except Exception:
            pass


class Intruso:
    """Outro participante da sala, com as permissões de um ATENDENTE do Console (publica e assina).
    Medido 2026-09-16: DTMF de quem entrou há ≤ 3 s não chega ao ouvinte; com 4 s, chega — e o
    atendente real está na sala desde a atribuição. Por isso ele ENTRA quando o menu chega e só
    tecla depois do prompt."""

    def __init__(self) -> None:
        self.room = rtc.Room()
        self.entrou = 0.0

    async def entra(self, room_name: str) -> None:
        tok = (api.AccessToken(LK_KEY, LK_SEC).with_identity("agent-probe-voz05b")
               .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True,
                                            can_subscribe=True, can_publish_data=True)).to_jwt())
        await asyncio.wait_for(self.room.connect(LK_URL, tok), 20)
        self.entrou = time.monotonic()

    async def tecla(self, digito: str) -> None:
        await asyncio.sleep(max(0.0, self.entrou + 4.0 - time.monotonic()))
        await self.room.local_participant.publish_dtmf(code=DTMF_CODE[digito], digit=digito)
        await asyncio.sleep(1.0)

    async def sai(self) -> None:
        try:
            await self.room.disconnect()
        except Exception:
            pass


async def ramo_teclado(http) -> None:
    c = Chamada(http)
    try:
        if not await c.abre("K"):
            return
        print(f"SIDK {c.sid}", flush=True)       # o .sh confere que a tecla do intruso CHEGOU ao ouvinte
        t_m1, m1 = await c.menu("fatura")
        if not m1:
            emit("INCONCL", "K1", f"o fluxo nao mandou o menu m1 (session={c.sid})")
            return
        intr = Intruso()
        await intr.entra(c.room_name)
        fim = await c.fim_da_fala(t_m1 - 0.5)
        ouvido = await c.transcreve(t_m1 - 0.5, fim + 0.3)
        pal = set(norm(ouvido))
        emit("OK" if {"tecle", "dois"} <= pal or {"tecle", "2"} <= pal else "FALHA", "H",
             f"prompt falado com as teclas: {ouvido!r}")
        await intr.tecla("2")
        await intr.sai()
        _, cedo = await c.marca(2)
        emit("OK" if not cedo else "FALHA", "A1",
             f"tecla de outro participante nao respondeu o menu (o fluxo mandou {cedo and cedo.get('text')!r})")
        await c.tecla("2")
        _, val = await c.marca(20)
        emit("OK" if val and val["text"] == "voz05b-valor=correio" else "FALHA", "K1",
             f"CONTROLE tecla do cliente: o fluxo mandou {val and val.get('text')!r}")
        if not val:
            return
        _, m2 = await c.menu("código")
        if not m2:
            emit("FALHA", "K2", "o fluxo nao mandou o campo de codigo")
            return
        await c.tecla("427#")
        _, cod = await c.marca(20)
        emit("OK" if cod and cod["text"] == "voz05b-codigo=427" else "FALHA", "K2",
             f"codigo por teclado com terminador: o fluxo mandou {cod and cod.get('text')!r}")
    finally:
        await c.fecha()


async def ramo_fala_fora_do_modo(http) -> None:
    c = Chamada(http)
    try:
        if not await c.abre("R"):
            return
        print(f"SIDR {c.sid}", flush=True)       # o .sh confere que a fala CHEGOU e foi classificada
        _, m1 = await c.menu("fatura")
        if not m1:
            emit("INCONCL", "R1", f"o fluxo nao mandou o menu m1 (session={c.sid})")
            return
        await c.tecla("1")
        _, val = await c.marca(20)
        t_m2, m2 = await c.menu("código")
        if not (val and m2):
            emit("INCONCL", "R1", f"nao chegou ao campo de codigo (valor={val and val.get('text')!r})")
            return
        await c.fim_da_fala(t_m2 - 0.5)
        await c.diz("quatro dois sete")
        _, cedo = await c.marca(10)
        emit("OK" if not cedo else "FALHA", "R1",
             f"fala no campo so de teclado nao respondeu (o fluxo mandou {cedo and cedo.get('text')!r})")
        await c.tecla("427#")
        _, cod = await c.marca(20)
        emit("OK" if cod and cod["text"] == "voz05b-codigo=427" else "FALHA", "R2",
             f"CONTROLE o teclado responde o mesmo campo: o fluxo mandou {cod and cod.get('text')!r}")
    finally:
        await c.fecha()


async def ramo_fala(http) -> None:
    c = Chamada(http)
    try:
        if not await c.abre("V"):
            return
        t_m1, m1 = await c.menu("fatura")
        if not m1:
            emit("INCONCL", "V1", f"o fluxo nao mandou o menu m1 (session={c.sid})")
            return
        await c.fim_da_fala(t_m1 - 0.5)
        await c.diz("Opção dois.")
        _, val = await c.marca(25)
        emit("OK" if val and val["text"] == "voz05b-valor=correio" else "FALHA", "V1",
             f"'opcao dois' dita no menu com fala: o fluxo mandou {val and val.get('text')!r}")
    finally:
        await c.fecha()


async def ramo_invalido(http) -> None:
    c = Chamada(http)
    try:
        if not await c.abre("I"):
            return
        _, m1 = await c.menu("fatura")
        if not m1:
            emit("INCONCL", "I1", f"o fluxo nao mandou o menu m1 (session={c.sid})")
            return
        await c.tecla("9")
        _, aviso = await c.texto("Opção inválida", 10)
        await c.tecla("9")
        _, fim = await c.marca(20)
        emit("OK" if aviso and fim and fim["text"] == "voz05b-invalido" else "FALHA", "I1",
             f"dois invalidos: aviso={bool(aviso)} e o fluxo mandou {fim and fim.get('text')!r}")
    finally:
        await c.fecha()


async def ramo_prazo(http) -> None:
    c = Chamada(http)
    try:
        if not await c.abre("T"):
            return
        t_m1, m1 = await c.menu("fatura")
        if not m1:
            emit("INCONCL", "T1", f"o fluxo nao mandou o menu m1 (session={c.sid})")
            return
        fim_prompt = await c.fim_da_fala(t_m1 - 0.5)
        t_to, to = await c.marca(60)
        espera = (t_to - fim_prompt) if t_to else None
        emit("OK" if to and to["text"] == "voz05b-timeout" and espera is not None and espera >= 10 else "FALHA", "T1",
             f"sem entrada: o fluxo mandou {to and to.get('text')!r} "
             f"{'%.1f s depois do fim do prompt (prazo 12 s)' % espera if espera is not None else ''}")
    finally:
        await c.fecha()


async def main() -> None:
    async with httpx.AsyncClient(timeout=120) as http:
        for ramo, fn in (("K", ramo_teclado), ("R", ramo_fala_fora_do_modo), ("V", ramo_fala),
                         ("I", ramo_invalido), ("T", ramo_prazo)):
            try:
                await fn(http)
            except Exception as exc:
                emit("INCONCL", ramo, f"exercicio falhou: {exc!r}")
            await asyncio.sleep(8)          # a chamada anterior libera a instância de IA


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
