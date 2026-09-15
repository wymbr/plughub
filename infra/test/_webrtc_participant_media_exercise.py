"""
_webrtc_participant_media_exercise.py — exercício do `probe_webrtc_participant_media.sh` (VOZ-09).

Roda DENTRO da imagem do channel-gateway, na rede do compose, com o adapter e o provider
DA IMAGEM contra Redis e SFU reais. O WebSocket do cliente é o único dublê (grava o que o
servidor manda); o cliente na sala é um participante LiveKit de verdade, e as perguntas
sobre permissão são feitas AO SFU (`get_participant`), nunca ao estado que o adapter grava.

Cenário (a mesma sessão, em ordem):
  C1 humano atende          → `webrtc.ready` com teto [audio, video]; o cliente entra e
                              PUBLICA microfone (controle positivo).
  C2 especialista IA entra  → nada muda: sem mensagem, e o SFU segue permitindo microfone
                              e câmera. É a negação exata do defeito medido.
  C3 humano sai             → `webrtc.media` com teto []; o SFU revoga a permissão e
                              RETIRA a trilha que o cliente tinha publicado.
  C4 outro humano atende    → `webrtc.media` com [audio, video]; o SFU reconcede e o cliente
                              volta a publicar SEM reconectar.
  C5 cliente fora da sala   → a troca devolve "não está na sala", e o token novo entregue
                              traz exatamente o teto novo.
  C6 supervisor             → token oculto e sem fonte; o SFU aplica `can_publish=False` e
                              `hidden` a ele (perguntado ao SFU, não tentado: ver o bloco).

MODE=mut_never_falls : `customer_ceiling` que nunca cai  → C3 TEM de reprovar.
MODE=mut_replace     : último atendente SUBSTITUI (a semântica velha) → C2 TEM de reprovar.
As duas mutações provam que C2 e C3 não estão verdes por não medirem nada.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from unittest.mock import MagicMock

import redis.asyncio as aioredis
from livekit import api, rtc

from plughub_channel_gateway.adapters import media_policy
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from plughub_channel_gateway.config import Settings

MODE = os.environ.get("MODE", "full")


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, m: dict) -> None:
        self.sent.append(m)

    async def send_text(self, t: str) -> None:
        self.sent.append(json.loads(t))


def _video(tok: str) -> dict:
    part = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("video", {})


# Desde a VOZ-10 a política vem do POOL no evento; aqui ela é fixa e ampla de propósito —
# a proposição deste exercício é a UNIÃO por participante e o SFU obedecer, não a política
# (essa é do `probe_webrtc_pool_media_policy.sh`).
_POLICY = {"customer_publish": ["audio", "video"], "agent_publish": ["audio", "video"]}


def _assigned(framework: str, iid: str) -> dict:
    pool = {"pool_id": "probe_voz09", "media_policy_source": "registry", "media_policy": _POLICY}
    return {"type": "routing.assigned", "framework": framework, "instance_id": iid,
            "pool": json.dumps(pool), "segment_id": ""}


async def _publish(room: rtc.Room, kind: str, timeout: float = 20) -> tuple[bool, str]:
    if kind == "audio":
        track = rtc.LocalAudioTrack.create_audio_track("mic", rtc.AudioSource(48000, 1))
        src = rtc.TrackSource.SOURCE_MICROPHONE
    else:
        track = rtc.LocalVideoTrack.create_video_track("cam", rtc.VideoSource(320, 240))
        src = rtc.TrackSource.SOURCE_CAMERA
    try:
        await asyncio.wait_for(room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=src)), timeout)
        return True, "publicou"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:70]}"


async def _sfu_perm(lk: api.LiveKitAPI, room_name: str, identity: str) -> tuple[bool, list[int], int]:
    p = await lk.room.get_participant(api.RoomParticipantIdentity(room=room_name, identity=identity))
    return p.permission.can_publish, list(p.permission.can_publish_sources), len(p.tracks)


async def main() -> None:
    s = Settings()
    s.webrtc_stt_enabled = False
    # `**kw`: desde a VOZ-05 o teto recebe `bot_leg_audio` — sem ele a mutação levantava
    # TypeError, o exercício morria antes do C2 e o probe acusava "mutação não reprovou".
    if MODE == "mut_never_falls":
        media_policy.customer_ceiling = lambda attendants, **kw: frozenset({"audio", "video"})
    elif MODE == "mut_replace":
        _orig = media_policy.customer_ceiling

        def _replace(attendants, **kw):
            last = dict(list(attendants.items())[-1:])
            return _orig(last, **kw)
        media_policy.customer_ceiling = _replace

    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    a = WebRTCAdapter(producer=MagicMock(), redis=r, settings=s,
                      registry=MagicMock(), context_reader=MagicMock())
    sid = "probe_voz09_" + uuid.uuid4().hex[:8]
    room_name = f"plughub-{sid}"
    contact = "c" + uuid.uuid4().hex[:6]
    await r.setex(f"session:{sid}:contact_id", 300, contact)
    ident = f"customer-{contact}"
    ws = FakeWS()
    MIC, CAM = 2, 1   # TrackSource.MICROPHONE / CAMERA
    room = rtc.Room()

    async with api.LiveKitAPI(s.webrtc_livekit_url, s.webrtc_livekit_api_key, s.webrtc_livekit_api_secret) as lk:
        try:
            # ── C1 ────────────────────────────────────────────────────────────
            await a._on_routing_assigned(ws, sid, _assigned("human", "h1"), s)
            ready = ws.sent[-1]
            ok = ready.get("type") == "webrtc.ready" and ready.get("publish") == ["audio", "video"] \
                and "negotiated_medium" not in ready
            await room.connect(s.webrtc_livekit_url, ready["token"])
            pub_ok, why = await _publish(room, "audio")
            await asyncio.sleep(1)
            can, srcs, ntracks = await _sfu_perm(lk, room_name, ident)
            emit("OK" if (ok and pub_ok and can and set(srcs) == {MIC, CAM} and ntracks == 1) else "FALHA", "C1",
                 f"humano atende: ready.publish={ready.get('publish')}; cliente publicou={why}; "
                 f"SFU can_publish={can} fontes={srcs} trilhas={ntracks}")

            # ── C2 ────────────────────────────────────────────────────────────
            n_before = len(ws.sent)
            await a._on_routing_renegotiate(ws, sid, _assigned("native", "ia1"), s)
            await asyncio.sleep(1)
            can, srcs, ntracks = await _sfu_perm(lk, room_name, ident)
            novas = [m.get("type") for m in ws.sent[n_before:]]
            emit("OK" if (not novas and can and set(srcs) == {MIC, CAM} and ntracks == 1) else "FALHA", "C2",
                 f"especialista IA entra: mensagens novas={novas}; SFU can_publish={can} fontes={srcs} trilhas={ntracks}")

            # ── C3 ────────────────────────────────────────────────────────────
            await a._on_attendant_left(ws, sid, {"type": "participant_left", "author_id": "h1"})
            await asyncio.sleep(2)
            last = ws.sent[-1]
            can, srcs, ntracks = await _sfu_perm(lk, room_name, ident)
            emit("OK" if (last.get("type") == "webrtc.media" and last.get("publish") == []
                          and not can and ntracks == 0) else "FALHA", "C3",
                 f"humano sai: msg={last.get('type')} publish={last.get('publish')}; "
                 f"SFU can_publish={can} trilhas={ntracks} (a trilha do cliente tem de SAIR)")

            # ── C4 ────────────────────────────────────────────────────────────
            await a._on_routing_renegotiate(ws, sid, _assigned("human", "h2"), s)
            await asyncio.sleep(2)
            last = ws.sent[-1]
            can, srcs, _ = await _sfu_perm(lk, room_name, ident)
            pub_ok, why = await _publish(room, "audio")
            emit("OK" if (last.get("type") == "webrtc.media" and last.get("publish") == ["audio", "video"]
                          and can and set(srcs) == {MIC, CAM} and pub_ok) else "FALHA", "C4",
                 f"outro humano atende: publish={last.get('publish')}; SFU can_publish={can} fontes={srcs}; "
                 f"republicou sem reconectar={why}")

            await asyncio.wait_for(room.disconnect(), 10)
            await asyncio.sleep(1)

            # ── C5 ────────────────────────────────────────────────────────────
            await a._on_attendant_left(ws, sid, {"type": "participant_left", "author_id": "h2"})
            last = ws.sent[-1]
            v = _video(last.get("token", "x.e30.x"))
            emit("OK" if (last.get("publish") == [] and v.get("canPublish") is False) else "FALHA", "C5",
                 f"cliente FORA da sala: publish={last.get('publish')}; token novo canPublish={v.get('canPublish')}")

            # ── C6 ────────────────────────────────────────────────────────────
            # ⚠️ Não se TENTA publicar com o supervisor: medido em 2026-09-14, depois que o
            # SFU recusa uma publicação o cliente Python do LiveKit pendura no `disconnect`
            # e o processo não termina. A recusa já foi provada no C3 (o SFU retira a trilha
            # quando `can_publish` cai); aqui a pergunta é a permissão que o SFU aplicou.
            tok = await a.get_token(sid, "supervisor", "sup-probe")
            v = _video(tok["token"])
            await a._provider.create_room(room_name)
            sup = rtc.Room()
            await asyncio.wait_for(sup.connect(s.webrtc_livekit_url, tok["token"]), 25)
            p = await lk.room.get_participant(api.RoomParticipantIdentity(room=room_name, identity="supervisor-sup-probe"))
            await asyncio.wait_for(sup.disconnect(), 10)
            emit("OK" if (v.get("hidden") is True and tok["publish"] == [] and not p.permission.can_publish
                          and p.permission.hidden) else "FALHA", "C6",
                 f"supervisor: token hidden={v.get('hidden')} publish={tok['publish']}; "
                 f"SFU can_publish={p.permission.can_publish} hidden={p.permission.hidden}")
        finally:
            try:
                await asyncio.wait_for(room.disconnect(), 10)
            except Exception:
                pass
            await a._provider.delete_room(room_name)
            await r.delete(f"session:{sid}:contact_id", f"channel:webrtc:{sid}:room_name",
                           f"channel:webrtc:{sid}:media")
            await r.aclose()
            emit("OK", "LIMPEZA", f"sala {room_name} e chaves da fixture apagadas por nome")


try:
    asyncio.run(main())
finally:
    # Threads do FFI do LiveKit podem manter o processo vivo depois do `main` — o
    # veredicto já foi impresso, então sai sem esperá-las.
    sys.stdout.flush()
    os._exit(0)
