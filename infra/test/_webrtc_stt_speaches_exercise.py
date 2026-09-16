"""
_webrtc_stt_speaches_exercise.py — exercício do `probe_webrtc_stt_speaches.sh` (VOZ-05, fatia 2).

Roda DENTRO da imagem do channel-gateway, na rede do compose, com o adapter e os provedores COMO
A IMAGEM OS CONSTRÓI, contra Redis, SFU e o serviço `speaches` reais. O cliente é um participante
LiveKit de verdade que FALA — a fala é sintetizada pelo próprio serviço (Piper) a partir de uma
frase conhecida, e a pergunta é se o texto que o gateway publica para o agente de IA a contém.

  S1 CONTROLE do instrumento: o `speaches`, sem o gateway, transcreve a fixture e devolve a frase.
     Falhou ⇒ INCONCLUSIVO (o que se mediria depois seria o serviço, não o gateway).
  S2 agente de IA com pool de áudio atende → o cliente ganha áudio e o bot entra na sala (SFU).
  S3 o cliente fala → o gateway publica, para o bridge, a fala do CLIENTE como texto
     (`content_type=audio_transcript`, autor `customer`) contendo as palavras da frase.
  S4 outro participante fala AO MESMO TEMPO outra frase → as palavras dele NÃO aparecem na
     transcrição do cliente (o bot assina a trilha do cliente, não "a primeira trilha de áudio").
  S5 (fatia 4) o outro participante é `agent-probe-outro` — um atendente humano — e a fala dele
     sai com o autor DELE (`agent_human`, `human-probe-outro`), sem as palavras do cliente.
  INFO latência: fim da fala → texto publicado.
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
from unittest.mock import AsyncMock, MagicMock

import httpx
import redis.asyncio as aioredis
from livekit import api, rtc

from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from plughub_channel_gateway.adapters.webrtc_provider import TokenGrants
from plughub_channel_gateway.config import Settings

SPEACHES = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
# Os modelos que o GATEWAY pede (env do container) — a fixture usa os mesmos que o produto usa.
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "speaches-ai/piper-pt_BR-faber-medium")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "faber")
STT_MODEL = os.environ.get("PLUGHUB_WEBRTC_STT_MODEL", "Systran/faster-whisper-small")
FRASE_CLIENTE = "Eu quero falar sobre a minha fatura de energia."
FRASE_OUTRO = "Por favor cancele o pedido de pizza de calabresa."
PALAVRAS_CLIENTE = {"fatura", "energia"}
# Todas as palavras da frase do outro que NÃO estão na do cliente. A 1ª versão tinha só três
# ("pizza", "calabresa", "cancele") e a mutação sem filtro de identidade passou no S4: a mistura
# das duas vozes saiu "Por favor, falar seriamente a dívida…" — vazou "favor", que não estava na lista.
PALAVRAS_OUTRO = {"favor", "cancele", "pedido", "pizza", "calabresa"}
_POLICY = {"customer_publish": ["audio"], "agent_publish": ["audio"]}


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def _norm(t: str) -> set[str]:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if c.isalnum() or c.isspace())
    return set(t.split())


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, m: dict) -> None:
        self.sent.append(m)

    async def send_text(self, t: str) -> None:
        self.sent.append(json.loads(t))


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict, float]] = []

    async def send(self, topic, value=None, **_):
        raw = value if value is not None else b"{}"
        self.sent.append((topic, json.loads(raw) if isinstance(raw, (bytes, str)) else raw, time.monotonic()))

    async def send_and_wait(self, topic, value=None, **kw):
        await self.send(topic, value, **kw)


async def _tts(http: httpx.AsyncClient, frase: str, sr: int) -> bytes:
    r = await http.post(f"{SPEACHES}/v1/audio/speech", json={
        "model": TTS_MODEL, "input": frase, "voice": TTS_VOICE, "response_format": "pcm", "sample_rate": sr})
    r.raise_for_status()
    return r.content


async def _falar(source: rtc.AudioSource, pcm48: bytes, silencio_s: float) -> None:
    passo = 480 * 2                      # 10 ms a 48 kHz, 16 bits mono
    pcm48 = pcm48 + b"\x00" * int(48000 * 2 * silencio_s)
    t0 = time.monotonic()
    for i, off in enumerate(range(0, len(pcm48) - passo + 1, passo)):
        frame = rtc.AudioFrame(data=pcm48[off:off + passo], sample_rate=48000, num_channels=1, samples_per_channel=480)
        await source.capture_frame(frame)
        atraso = t0 + (i + 1) * 0.01 - time.monotonic()
        if atraso > 0:
            await asyncio.sleep(atraso)


async def _participante(url: str, token: str, nome: str) -> tuple[rtc.Room, rtc.AudioSource]:
    room = rtc.Room()
    await asyncio.wait_for(room.connect(url, token), 20)
    source = rtc.AudioSource(48000, 1)
    track = rtc.LocalAudioTrack.create_audio_track(nome, source)
    await asyncio.wait_for(room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)), 20)
    return room, source


async def main() -> None:
    s = Settings()
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    rooms: list[rtc.Room] = []
    a = None
    sid = "probe_voz05s_" + uuid.uuid4().hex[:8]
    room_name = f"plughub-{sid}"
    try:
        async with httpx.AsyncClient(timeout=120) as http:
            # ── S1 ────────────────────────────────────────────────────────────
            try:
                pcm16 = await _tts(http, FRASE_CLIENTE, 16000)
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm16)
                rr = await http.post(f"{SPEACHES}/v1/audio/transcriptions",
                                     files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
                                     data={"model": STT_MODEL, "language": "pt", "response_format": "json"})
                ctrl = rr.json().get("text", "") if rr.status_code == 200 else f"http {rr.status_code}"
            except Exception as exc:
                ctrl = f"{type(exc).__name__}: {exc}"
            if not PALAVRAS_CLIENTE <= _norm(ctrl):
                emit("INCONCL", "S1", f"o proprio speaches nao transcreveu a fixture: {ctrl!r}")
                return
            emit("OK", "S1", f"CONTROLE speaches transcreve a fixture: {ctrl!r}")
            voz_cliente = await _tts(http, FRASE_CLIENTE, 48000)
            voz_outro = await _tts(http, FRASE_OUTRO, 48000)

        prod = FakeProducer()
        registry = MagicMock(); registry.append_message = AsyncMock()
        ctx = MagicMock(); ctx.get_snapshot = AsyncMock(return_value={})
        a = WebRTCAdapter(producer=prod, redis=r, settings=s, registry=registry, context_reader=ctx)
        contact = "c" + uuid.uuid4().hex[:6]
        await r.setex(f"session:{sid}:contact_id", 300, contact)
        a._sessions[sid] = {"contact_id": contact, "pool_id": "probe_voz05", "started_at": "2026-09-15T00:00:00+00:00"}
        ws = FakeWS()
        pool = {"pool_id": "probe_voz05", "media_policy_source": "registry", "media_policy": _POLICY}
        await a._on_routing_assigned(ws, sid, {"type": "routing.assigned", "framework": "native",
                                               "instance_id": "ia1", "pool": json.dumps(pool), "segment_id": ""}, s)
        ready = next((m for m in ws.sent if m.get("type") == "webrtc.ready"), {})
        await asyncio.sleep(4)
        async with api.LiveKitAPI(s.webrtc_livekit_url, s.webrtc_livekit_api_key, s.webrtc_livekit_api_secret) as lk:
            try:
                await lk.room.get_participant(api.RoomParticipantIdentity(room=room_name, identity=f"bot-{sid[:8]}"))
                bot = True
            except Exception:
                bot = False
        emit("OK" if (ready.get("publish") == ["audio"] and bot) else "FALHA", "S2",
             f"IA com audio atende: cliente publish={ready.get('publish')}; bot na sala (SFU)={bot}; "
             f"bot_leg={(json.loads(await r.get(f'channel:webrtc:{sid}:media') or '{}').get('customer') or {}).get('bot_leg')}")
        if not ready.get("token"):
            emit("FALHA", "S3", "sem token do cliente — nada a falar")
            return

        cli_room, cli_src = await _participante(s.webrtc_livekit_url, ready["token"], "mic-cliente")
        rooms.append(cli_room)
        outro_tok = a._provider.generate_token(TokenGrants(
            room_name=room_name, identity="agent-probe-outro", display_name="outro",
            can_publish=True, can_subscribe=True, can_publish_data=False, hidden=False, ttl_seconds=300))
        out_room, out_src = await _participante(s.webrtc_livekit_url, outro_tok, "mic-outro")
        rooms.append(out_room)
        await asyncio.sleep(2)

        await asyncio.gather(_falar(cli_src, voz_cliente, 1.5), _falar(out_src, voz_outro, 1.5))
        fim_fala = time.monotonic() - 1.5

        transcritos: list[tuple[dict, float]] = []
        prazo = time.monotonic() + 25
        while time.monotonic() < prazo:
            transcritos = [(ev, t) for (topic, ev, t) in prod.sent
                           if ev.get("content_type") == "audio_transcript"]
            if transcritos and PALAVRAS_CLIENTE <= _norm(" ".join(e["content"]["text"] for e, _ in transcritos)):
                break
            await asyncio.sleep(0.5)
        # Desde a fatia 4 o ouvinte transcreve também o atendente humano (`agent-…`), e o outro
        # participante deste exercício tem essa identidade: a fala dele sai com o autor DELE. O
        # S3/S4 medem a transcrição DO CLIENTE; juntar todos os autores confundia as duas.
        do_cliente = [(e, t) for e, t in transcritos if (e.get("author") or {}).get("type") == "customer"]
        do_outro = [e for e, _ in transcritos if (e.get("author") or {}).get("type") == "agent_human"]
        texto = " ".join(e["content"]["text"] for e, _ in do_cliente)
        autores = {e.get("author", {}).get("type") for e, _ in transcritos}
        emit("OK" if (PALAVRAS_CLIENTE <= _norm(texto) and "customer" in autores) else "FALHA", "S3",
             f"fala do cliente publicada ao bridge: {texto!r} autores={sorted(a for a in autores if a)} "
             f"eventos={len(transcritos)}")
        if do_cliente:
            emit("INFO", "LAT", f"fim da fala → primeiro texto publicado: {do_cliente[0][1] - fim_fala:.2f} s")
        vazou = PALAVRAS_OUTRO & _norm(texto)
        emit("OK" if (do_cliente and not vazou) else "FALHA", "S4",
             f"fala do OUTRO participante fora da transcricao do cliente (vazou={sorted(vazou)})")
        texto_outro = " ".join(e["content"]["text"] for e in do_outro)
        ids_outro = sorted({(e.get("author") or {}).get("id") for e in do_outro})
        emit("OK" if (PALAVRAS_OUTRO <= _norm(texto_outro) and ids_outro == ["human-probe-outro"]
                      and not (PALAVRAS_CLIENTE & _norm(texto_outro))) else "FALHA", "S5",
             f"fala do outro participante (agent-probe-outro) com o autor DELE: {texto_outro!r} ids={ids_outro}")
    finally:
        for room in rooms:
            try:
                await asyncio.wait_for(room.disconnect(), 10)
            except Exception:
                pass
        if a is not None:
            await a._stop_bot_leg(sid) if hasattr(a, "_stop_bot_leg") else None
            try:
                await a._provider.delete_room(room_name)
            except Exception:
                pass
        await r.delete(f"session:{sid}:contact_id", f"channel:webrtc:{sid}:room_name", f"channel:webrtc:{sid}:media")
        await r.aclose()
        emit("OK", "LIMPEZA", f"sala {room_name} e chaves da fixture apagadas por nome")


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
