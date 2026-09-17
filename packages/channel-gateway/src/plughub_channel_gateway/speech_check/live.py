"""
live.py — as dependências REAIS da verificação (VOZ-23): config-api, agent-registry, speaches, Kafka e a
chamada (WebSocket do widget + participante LiveKit). A orquestração e o julgamento moram em `runner.py`
e `reference.py`, testados sem rede.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import struct
import time
import uuid

import httpx
import jwt

from ..config import Settings
from . import reference
from .runner import CheckFailed

logger = logging.getLogger("plughub.speech-check.live")

SR = reference.SAMPLE_RATE
_QUADRO = 320                     # 10 ms de PCM16 mono a 16 kHz
_VOZ_RMS = 300.0


def _rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return 0.0
    a = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(x * x for x in a) / n)


class LiveDeps:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.s, self.http = settings, http

    async def get_profiles(self, tenant_id: str) -> dict:
        r = await self.http.get(f"{self.s.config_api_url.rstrip('/')}/config/speech_profiles",
                                params={"tenant_id": tenant_id})
        r.raise_for_status()
        return r.json().get("entries") or {}

    def _reg_headers(self, tenant_id: str) -> dict:
        return {"x-tenant-id": tenant_id, "x-service-token": self.s.agent_registry_service_token,
                "x-user-id": "service:speech-check"}

    async def create_endpoint(self, tenant_id: str, identifier: str, pool_id: str, settings: dict) -> str:
        r = await self.http.post(f"{self.s.agent_registry_url.rstrip('/')}/v1/channel-endpoints",
                                 headers=self._reg_headers(tenant_id),
                                 json={"channel": "webrtc", "identifier": identifier, "pool_id": pool_id,
                                       "display_name": "verificacao de fala (temporario, VOZ-23)",
                                       "settings": settings, "origin": "internal"})
        if r.status_code != 201:
            raise RuntimeError(f"http {r.status_code}: {r.text[:200]}")
        return r.json()["id"]

    async def delete_endpoint(self, tenant_id: str, endpoint_id: str) -> None:
        r = await self.http.delete(f"{self.s.agent_registry_url.rstrip('/')}/v1/channel-endpoints/{endpoint_id}",
                                   headers=self._reg_headers(tenant_id))
        if r.status_code not in (204, 404):
            raise RuntimeError(f"http {r.status_code}: {r.text[:200]}")

    async def synthesize(self, text: str) -> bytes:
        r = await self.http.post(f"{self.s.webrtc_speaches_url.rstrip('/')}/v1/audio/speech", json={
            "model": self.s.webrtc_tts_model, "input": text, "voice": self.s.webrtc_tts_voice,
            "response_format": "pcm", "sample_rate": SR})
        r.raise_for_status()
        if not r.content:
            raise RuntimeError("audio vazio")
        return r.content[: len(r.content) // 2 * 2]

    def listener(self) -> "KafkaListener":
        return KafkaListener(self.s)

    def call(self) -> "LiveCall":
        return LiveCall(self.s)


class KafkaListener:
    """Transcrições do cliente (`conversations.inbound`) e o resumo da fala (`speech.metrics`), a partir do
    FIM de cada partição no instante do `start` — nada de antes da chamada entra."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._consumer = None
        self._task: asyncio.Task | None = None
        self._falas: list[tuple[float, str, str, float | None]] = []    # (chegada, sessão, texto, confiança)
        self._resumos: dict[str, dict] = {}

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer, TopicPartition
        c = AIOKafkaConsumer(bootstrap_servers=self.s.kafka_brokers, enable_auto_commit=False,
                             group_id=None, auto_offset_reset="latest")
        await c.start()
        tps = []
        for topico in (self.s.kafka_topic_inbound, "speech.metrics"):
            partes = c.partitions_for_topic(topico)
            if partes is None:
                await c._client.force_metadata_update()          # noqa: SLF001 — metadado do tópico
                partes = c.partitions_for_topic(topico)
            if not partes:
                await c.stop()
                raise RuntimeError(f"topico {topico} sem particoes")
            tps += [TopicPartition(topico, p) for p in partes]
        c.assign(tps)
        await c.seek_to_end(*tps)
        for tp in tps:
            await c.position(tp)                                  # materializa o fim ANTES da chamada
        self._consumer = c
        self._task = asyncio.ensure_future(self._consome())

    async def _consome(self) -> None:
        while True:
            lotes = await self._consumer.getmany(timeout_ms=500)
            agora = time.monotonic()
            for tp, msgs in lotes.items():
                for m in msgs:
                    try:
                        ev = json.loads(m.value)
                    except (ValueError, TypeError):
                        continue
                    if tp.topic == "speech.metrics":
                        if ev.get("event_type") == "stt_stream_summary" and ev.get("session_id"):
                            self._resumos[ev["session_id"]] = ev
                    elif (ev.get("content_type") == "audio_transcript"
                          and (ev.get("author") or {}).get("type") == "customer"):
                        cont = ev.get("content") or {}
                        conf = (cont.get("payload") or {}).get("confidence")
                        self._falas.append((agora, ev.get("session_id") or "", cont.get("text") or "",
                                            float(conf) if isinstance(conf, (int, float)) else None))

    def transcripts(self, session_id: str, since: float, until: float) -> list[tuple[str, float | None]]:
        return [(t, c) for chegada, sid, t, c in self._falas if sid == session_id and since <= chegada < until]

    async def summary(self, session_id: str, timeout_s: float) -> dict | None:
        fim = time.monotonic() + timeout_s
        while time.monotonic() < fim:
            if session_id in self._resumos:
                return self._resumos[session_id]
            await asyncio.sleep(0.5)
        return self._resumos.get(session_id)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._consumer:
            await self._consumer.stop()


class LiveCall:
    """O cliente sintético: protocolo do widget no WebSocket e microfone publicado na sala LiveKit."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.ws = None
        self.room = None
        self._tarefas: list[asyncio.Task] = []
        self._fala: asyncio.Queue[tuple[bytes, asyncio.Event]] = asyncio.Queue()
        self._quadros_bot: list[float] = []          # instantes com voz vinda da sala

    async def _recebe(self, tipo: str, prazo_s: float) -> dict | None:
        fim = time.monotonic() + prazo_s
        while time.monotonic() < fim:
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), max(0.1, fim - time.monotonic())))
            except asyncio.TimeoutError:
                return None
            if m.get("type") == tipo:
                return m
        return None

    async def open(self, identifier: str) -> str:
        import websockets
        from livekit import rtc
        n = int(time.time())
        tok = jwt.encode({"sub": f"speech-check-{uuid.uuid4().hex[:8]}", "tenant_id": self.s.tenant_id,
                          "channel": "webrtc", "iat": n, "exp": n + 900}, self.s.jwt_secret, algorithm="HS256")
        try:
            self.ws = await websockets.connect(f"{self.s.speech_check_gateway_ws_url.rstrip('/')}/ws/webrtc/{identifier}")
            if not await self._recebe("conn.ready", 10):
                raise CheckFailed("call_failed", "gateway nao mandou conn.ready")
            await self.ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
            await self.ws.send(json.dumps({"type": "conn.authenticate", "token": tok}))
            auth = await self._recebe("conn.authenticated", 15)
            if not auth:
                raise CheckFailed("call_failed", "cliente sintetico nao autenticou")
            sid = auth["session_id"]
            ready = await self._recebe("webrtc.ready", 60)
            if not (ready and ready.get("token")):
                raise CheckFailed("call_not_answered", f"nenhum agente atendeu com midia (session={sid})")
        except CheckFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CheckFailed("call_failed", repr(exc)) from exc

        self.room = rtc.Room()

        @self.room.on("track_subscribed")
        def _t(track, pub, part):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return

            async def consome():
                async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
                    if _rms(bytes(ev.frame.data)) > _VOZ_RMS:
                        self._quadros_bot.append(time.monotonic())
            self._tarefas.append(asyncio.ensure_future(consome()))

        await asyncio.wait_for(self.room.connect(self.s.webrtc_livekit_url, ready["token"]), 20)
        src = rtc.AudioSource(SR, 1)
        mic = rtc.LocalAudioTrack.create_audio_track("mic-verificacao", src)
        await self.room.local_participant.publish_track(
            mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

        async def microfone():
            t0, i = time.monotonic(), 0
            pend, feito = b"", None
            while True:
                if not pend and not self._fala.empty():
                    pend, feito = self._fala.get_nowait()
                if pend:
                    chunk, pend = pend[:_QUADRO], pend[_QUADRO:]
                    if not pend and feito is not None:
                        feito.set()
                        feito = None
                else:
                    chunk = b""
                chunk = chunk.ljust(_QUADRO, b"\x00")
                await src.capture_frame(rtc.AudioFrame(data=chunk, sample_rate=SR, num_channels=1,
                                                       samples_per_channel=_QUADRO // 2))
                i += 1
                atraso = t0 + i * 0.01 - time.monotonic()
                if atraso > 0:
                    await asyncio.sleep(atraso)
        self._tarefas.append(asyncio.ensure_future(microfone()))
        return sid

    async def wait_bot_voice(self, timeout_s: float) -> bool:
        """Espera a voz do bot começar e 1,5 s de silêncio depois dela. False = não ouviu voz nenhuma."""
        fim = time.monotonic() + timeout_s
        while time.monotonic() < fim:
            if self._quadros_bot and time.monotonic() - self._quadros_bot[-1] > 1.5:
                return True
            await asyncio.sleep(0.2)
        return bool(self._quadros_bot)

    async def speak(self, pcm: bytes) -> None:
        feito = asyncio.Event()
        self._fala.put_nowait((pcm, feito))
        await asyncio.wait_for(feito.wait(), len(pcm) / 2 / SR + 10)

    async def close(self) -> None:
        for t in self._tarefas:
            t.cancel()
        self._tarefas = []
        if self.room is not None:
            try:
                await asyncio.wait_for(self.room.disconnect(), 10)
            except Exception:  # noqa: BLE001 — fechando: o que importa é desligar o WS abaixo
                pass
            self.room = None
        if self.ws is not None:
            try:
                await self.ws.send(json.dumps({"type": "webrtc.hangup"}))
                await asyncio.sleep(1)
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            self.ws = None
