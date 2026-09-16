"""
_webrtc_bot_leg_gate_exercise.py — exercício do `probe_webrtc_bot_leg_gate.sh` (VOZ-05, fatia 1).

Roda DENTRO da imagem do channel-gateway, na rede do compose, com o adapter DA IMAGEM contra
Redis e SFU reais. A pergunta sobre quem está na sala é feita AO SFU (`get_participant` pela
identidade do bot), nunca ao que o adapter guarda de si.

Modelo (do dono): toda chamada com áudio é TRANSCRITA — cliente e humano — porque a qualidade
avalia sobre a transcrição (pede STT); o agente de IA ouve pelo STT e fala pelo TTS (pede os dois).

  V1 humano atende com política de áudio, num deploy SEM provedor (a fábrica da imagem com a
     config de provedor apagada) → nenhum bot na sala (antes: um mock que não transcrevia nada) e
     o estado NOMEIA que a chamada não é transcrita.
  V2 agente de IA atende com política de áudio, deploy SEM provedor → o cliente NÃO ganha
     áudio, e o estado nomeia o que falta. Ausência nunca vira permissão.
  V3 CONTROLE POSITIVO: IA com STT e TTS PRESENTES → o cliente ganha áudio e o bot ENTRA na sala
     (o SFU o vê).
  V4 CONTROLE humano com STT presente → o OUVINTE entra (fatia 4, 2026-09-16) e o estado não
     acusa falta. Até ali este ramo afirmava o contrário: sem destino para a fala de chamada com
     humano, o bot não entrava e o estado dizia por quê. O destino (mensagem do falante, marcada)
     é medido ponta a ponta no `probe_webrtc_human_transcript`.

MODE=mut_bot_sem_stt       : o bot entra sem STT (o placebo)            → V1 TEM de reprovar.
MODE=mut_available_always  : IA ganha áudio sem provedor                → V2 TEM de reprovar.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from unittest.mock import MagicMock

import redis.asyncio as aioredis
from livekit import api

from plughub_channel_gateway.adapters.voice_provider import MockSTTProvider, MockTTSProvider
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from plughub_channel_gateway.config import Settings

MODE = os.environ.get("MODE", "full")
_POLICY = {"customer_publish": ["audio"], "agent_publish": ["audio"]}


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, m: dict) -> None:
        self.sent.append(m)

    async def send_text(self, t: str) -> None:
        self.sent.append(json.loads(t))


def _assigned(framework: str, iid: str) -> dict:
    pool = {"pool_id": "probe_voz05", "media_policy_source": "registry", "media_policy": _POLICY}
    return {"type": "routing.assigned", "framework": framework, "instance_id": iid,
            "pool": json.dumps(pool), "segment_id": ""}


async def _bot_na_sala(lk: api.LiveKitAPI, room: str, sid: str) -> bool:
    try:
        await lk.room.get_participant(api.RoomParticipantIdentity(room=room, identity=f"bot-{sid[:8]}"))
        return True
    except Exception:
        return False


async def _cenario(r, lk, s, framework: str, providers: bool) -> tuple[dict, dict, bool, WebRTCAdapter, str]:
    kw = {}
    if providers:
        kw = {"stt_provider": MockSTTProvider(), "tts_provider": MockTTSProvider(synthesize_returns_none=False)}
    else:
        # Desde a fatia 2 a imagem TEM provedor (speaches). "Sem STT/TTS" é um deploy sem ele:
        # a fábrica da imagem roda com a configuração de provedor apagada — nada é injetado.
        s = Settings()
        s.webrtc_speech_provider = ""
        s.webrtc_speaches_url = ""
        s.voice_deepgram_api_key = ""
        s.voice_elevenlabs_api_key = ""
    a = WebRTCAdapter(producer=MagicMock(), redis=r, settings=s, registry=MagicMock(),
                      context_reader=MagicMock(), **kw)
    if MODE == "mut_bot_sem_stt" and hasattr(a, "_bot_leg_should_run"):
        a._bot_leg_should_run = lambda state, publish: "audio" in publish
    if MODE == "mut_available_always" and hasattr(a, "_convert_available"):
        a._convert_available = lambda: True
    sid = "probe_voz05_" + uuid.uuid4().hex[:8]
    room = f"plughub-{sid}"
    await r.setex(f"session:{sid}:contact_id", 300, "c" + uuid.uuid4().hex[:6])
    ws = FakeWS()
    await a._on_routing_assigned(ws, sid, _assigned(framework, framework[:2] + "1"), s)
    await asyncio.sleep(5)          # tempo de o bot conectar, se for entrar
    ready = next((m for m in ws.sent if m.get("type") == "webrtc.ready"), {})
    state = json.loads(await r.get(f"channel:webrtc:{sid}:media") or "{}")
    na_sala = await _bot_na_sala(lk, room, sid)
    return ready, state, na_sala, a, sid


async def _limpa(r, a: WebRTCAdapter, sid: str) -> None:
    rc = a._room_clients.pop(sid, None)
    task = a._stt_tasks.pop(sid, None)
    if task:
        task.cancel()
    if rc is not None:
        try:
            await asyncio.wait_for(rc.disconnect(), 10)
        except Exception:
            pass
    try:
        await a._provider.delete_room(f"plughub-{sid}")
    except Exception:
        pass
    await r.delete(f"session:{sid}:contact_id", f"channel:webrtc:{sid}:room_name", f"channel:webrtc:{sid}:media")


async def main() -> None:
    s = Settings()                  # o que a imagem usa: webrtc_stt_enabled vem do env do compose
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    feitos: list[tuple[WebRTCAdapter, str]] = []
    try:
        async with api.LiveKitAPI(s.webrtc_livekit_url, s.webrtc_livekit_api_key, s.webrtc_livekit_api_secret) as lk:
            bl_of = lambda st: (st.get("customer") or {}).get("bot_leg") or {}

            ready, state, bot, a, sid = await _cenario(r, lk, s, "human", providers=False)
            feitos.append((a, sid))
            bl = bl_of(state)
            emit("OK" if (ready.get("publish") == ["audio"] and not bot and bl.get("transcribe") is True
                          and bl.get("available") is False and "transcrita" in (bl.get("reason") or "")) else "FALHA", "V1",
                 f"humano atende sem STT: cliente publish={ready.get('publish')}; bot na sala (SFU)={bot}; bot_leg={bl}")

            ready, state, bot, a, sid = await _cenario(r, lk, s, "native", providers=False)
            feitos.append((a, sid))
            bl = bl_of(state)
            emit("OK" if (ready.get("publish") == [] and not bot and bl.get("convert") is True
                          and bl.get("available") is False and bl.get("reason")) else "FALHA", "V2",
                 f"IA atende sem STT/TTS: cliente publish={ready.get('publish')}; bot na sala={bot}; bot_leg={bl}")

            ready, state, bot, a, sid = await _cenario(r, lk, s, "native", providers=True)
            feitos.append((a, sid))
            bl = bl_of(state)
            emit("OK" if (ready.get("publish") == ["audio"] and bot and bl.get("available") is True) else "FALHA", "V3",
                 f"CONTROLE IA com STT/TTS presentes: cliente publish={ready.get('publish')}; bot na sala={bot}; bot_leg={bl}")

            ready, state, bot, a, sid = await _cenario(r, lk, s, "human", providers=True)
            feitos.append((a, sid))
            bl = bl_of(state)
            emit("OK" if (ready.get("publish") == ["audio"] and bot and bl.get("available") is True
                          and bl.get("convert") is False) else "FALHA", "V4",
                 f"CONTROLE humano com STT presente: cliente publish={ready.get('publish')}; "
                 f"ouvinte na sala={bot}; bot_leg={bl}")
    finally:
        for a, sid in feitos:
            await _limpa(r, a, sid)
        await r.aclose()
        emit("OK", "LIMPEZA", f"{len(feitos)} sala(s) e chaves da fixture apagadas por nome")


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
