"""
_webrtc_speech_tuning_exercise.py — exercício do `probe_webrtc_speech_tuning.sh` (VOZ-18).

Roda DENTRO da imagem do channel-gateway, na rede do compose. UMA chamada: WebSocket com o protocolo
do widget e um participante LiveKit de verdade (`customer-…`) que publica microfone. A fixture
`skill_probe_speech_tuning_v1` faz quatro menus só por fala, cada um com uma tentativa, e manda um
marcador por saída. A fala do cliente é sintetizada pelo próprio speaches.

  C1 min_confidence 0.99: "Atendente." (reconhecida certo pela chamada, confiança ~0,7) → inválido
  C2 CONTROLE min_confidence 0.3: a MESMA fala → valor atendente
  S1 end_silence_ms 2500: "Atendente." + 1,2 s + "Cancelar." — o desfecho só é INFORMADO: que foi UMA
     fala o .sh julga pela janela da fala no stream, porque o Whisper pode descartar uma das palavras
  S2 CONTROLE sem ajuste (700 ms), logo depois do S1: "Cancelar." + 1,2 s + "Atendente." são DUAS
     falas, e a primeira responde sozinha → valor cancelar. Prova também que o ajuste DESLIGOU.

Palavras escolhidas por medição (2026-09-17): pela chamada (Opus, 48→16 kHz) "Fatura." virou
"Batura!" com 0,45 — o controle reprovava por reconhecimento, não por confiança.

Imprime `SID <session_id>` para o .sh conferir no log e no stream o que o desfecho não mostra.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

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
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
GW = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
SR = 16000
LIMIAR = 300
PAUSA_S = 1.2          # entre 700 ms (default) e 2500 ms (ajuste do m3)


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def falando(pcm: bytes) -> bool:
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    return bool(a.size) and float(np.sqrt((a ** 2).mean())) > LIMIAR


class Chamada:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.ws = None
        self.room = rtc.Room()
        self.pend: list[tuple[float, dict]] = []
        self.quadros: list[tuple[float, bytes]] = []
        self.fala: asyncio.Queue[bytes] = asyncio.Queue()
        self.tarefas: list[asyncio.Task] = []
        self.sid = ""

    async def abre(self) -> bool:
        sub = "c-voz18-" + uuid.uuid4().hex[:6]
        n = int(time.time())
        tok = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": n, "exp": n + 900},
                         SECRET, algorithm="HS256")
        self.ws = await websockets.connect(GW)
        await self.espera(lambda m: m.get("type") == "conn.ready", 10)
        await self.manda({"type": "conn.hello", "version": "1"})
        await self.manda({"type": "conn.authenticate", "token": tok})
        _, auth = await self.espera(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            emit("INCONCL", "C1", "cliente nao autenticou")
            return False
        self.sid = auth["session_id"]
        print(f"SID {self.sid}", flush=True)
        _, ready = await self.espera(lambda m: m.get("type") == "webrtc.ready", 60)
        if not (ready and ready.get("token")):
            emit("INCONCL", "C1", f"nenhum agente de IA atendeu com midia (session={self.sid})")
            return False

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
                break
            item = (time.monotonic(), m)
            if pred(m):
                return item
            self.pend.append(item)
        return None, None

    async def marca(self, menu: str, prazo_s: float):
        return await self.espera(lambda m: m.get("type") == "webrtc.message"
                                 and (m.get("text") or "").startswith(f"voz18-{menu}"), prazo_s)

    async def menu(self, trecho: str, prazo_s: float = 45):
        return await self.espera(lambda m: m.get("type") == "webrtc.interaction" and trecho in (m.get("prompt") or ""), prazo_s)

    async def fim_da_fala(self, desde: float, prazo_s: float = 30) -> None:
        """1,5 s sem voz do agente depois de ter havido voz desde `desde` — o prompt terminou."""
        fim = time.monotonic() + prazo_s
        while time.monotonic() < fim:
            ultimo = max((t for t, d in self.quadros if t >= desde and falando(d)), default=None)
            if ultimo and time.monotonic() - ultimo > 1.5:
                return
            await asyncio.sleep(0.2)

    async def sintetiza(self, frase: str) -> bytes:
        r = await self.http.post(f"{SPEACHES}/v1/audio/speech", json={
            "model": TTS_MODEL, "input": frase, "voice": TTS_VOICE, "response_format": "pcm", "sample_rate": SR})
        r.raise_for_status()
        return r.content[: len(r.content) // 2 * 2]

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


async def responde(c: Chamada, menu: str, trecho: str, pcm: bytes, ramo: str, esperado: str | None, rotulo: str) -> bool:
    t_m, m = await c.menu(trecho)
    if not m:
        emit("INCONCL", ramo, f"o fluxo nao mandou o menu {menu} (session={c.sid})")
        return False
    await c.fim_da_fala(t_m - 0.5)
    c.fala.put_nowait(pcm)
    _, val = await c.marca(menu, 30)
    if esperado is None:
        print(f"INFO {ramo} {rotulo}: o fluxo mandou {val and val.get('text')!r}", flush=True)
        return bool(val)
    emit("OK" if val and val["text"] == esperado else "FALHA", ramo,
         f"{rotulo}: o fluxo mandou {val and val.get('text')!r} (esperado {esperado!r})")
    return bool(val)


async def main() -> None:
    async with httpx.AsyncClient(timeout=120) as http:
        c = Chamada(http)
        try:
            if not await c.abre():
                return
            cancelar = await c.sintetiza("Cancelar.")
            atendente = await c.sintetiza("Atendente.")
            pausa = b"\x00\x00" * int(SR * PAUSA_S)
            _ = (await c.espera(lambda m: m.get("type") == "webrtc.message"
                                and m.get("text") == "voz18-inicio", 30))
            if not await responde(c, "m1", "Pergunta um", atendente, "C1", "voz18-m1-invalido",
                                  "'Atendente.' com min_confidence 0.99"):
                return
            if not await responde(c, "m2", "Pergunta dois", atendente, "C2", "voz18-m2=atendente",
                                  "CONTROLE a mesma fala com min_confidence 0.3"):
                return
            if not await responde(c, "m3", "Pergunta tr", atendente + pausa + cancelar, "S1", None,
                                  f"'Atendente.' + {PAUSA_S} s + 'Cancelar.' com end_silence_ms 2500"):
                return
            await responde(c, "m4", "Pergunta quatro", cancelar + pausa + atendente, "S2", "voz18-m4=cancelar",
                           f"CONTROLE 'Cancelar.' + {PAUSA_S} s + 'Atendente.' sem ajuste, depois do m3 (a primeira responde sozinha)")
        finally:
            await c.fecha()


try:
    asyncio.run(main())
except Exception as exc:
    emit("INCONCL", "C1", f"exercicio falhou: {exc!r}")
finally:
    sys.stdout.flush()
    os._exit(0)
