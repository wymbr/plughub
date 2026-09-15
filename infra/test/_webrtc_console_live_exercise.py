"""
_webrtc_console_live_exercise.py — exercício do `gate_webrtc_console_live.sh` (VOZ-04, item 5).

Roda DENTRO da imagem do channel-gateway, na rede do compose, ENQUANTO um humano faz a chamada
do roteiro `docs/guias/roteiro-validacao-webrtc-console.md`. Não cria nada: só pergunta a quem
sabe — o stream da sessão e o SFU.

  L1 existe contato WebRTC ABERTO no pool, atribuído a humano (`routing.assigned framework=human`)
  L2 a sala existe no SFU e tem o agente (`agent-*`) e o cliente (`customer-*`)
  L3 cada um publica o que o teto do pool permite — trilhas de áudio e vídeo PUBLICADAS e não
     mudas, lidas no SFU (é a prova de mídia; a tela sozinha não distingue vídeo local de remoto)
  L4 a identidade na sala casa com a instância atribuída (`agent-{sub}` ↔ `human-{sub}`)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import redis.asyncio as aioredis
from livekit import api

POOL   = os.environ.get("POOL", "webrtc_atendimento")
SID    = os.environ.get("SID", "")
WANT   = json.loads(os.environ.get("WANT_KINDS", '["audio","video"]'))
LK_URL = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]
LK_KEY = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_KEY"]
LK_SEC = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_SECRET"]


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


async def _l5_audio(room_name: str, identidades: list[str], secs: float = 6.0) -> None:
    import datetime, math
    import numpy as np
    from livekit import rtc

    tok = (api.AccessToken(LK_KEY, LK_SEC).with_identity("gate-ouvinte")
           .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=False,
                                        can_subscribe=True, hidden=True))
           .with_ttl(datetime.timedelta(minutes=2)).to_jwt())
    medidas: dict[str, tuple[float, int]] = {}
    tarefas = []

    async def medir(track, quem):
        stream = rtc.AudioStream(track)
        soma, n, pico = 0.0, 0, 0
        fim = asyncio.get_running_loop().time() + secs
        async for ev in stream:
            a = np.frombuffer(bytes(ev.frame.data), dtype=np.int16).astype(np.int64)
            soma += float((a * a).sum()); n += a.size; pico = max(pico, int(np.abs(a).max(initial=0)))
            if asyncio.get_running_loop().time() > fim:
                break
        await stream.aclose()
        medidas[quem] = (math.sqrt(soma / n) if n else 0.0, pico)

    room = rtc.Room()

    @room.on("track_subscribed")
    def _sub(track, _pub, part):
        if track.kind == rtc.TrackKind.KIND_AUDIO and part.identity in identidades:
            tarefas.append(asyncio.ensure_future(medir(track, part.identity)))

    try:
        await asyncio.wait_for(room.connect(LK_URL, tok), 20)
        await asyncio.sleep(secs + 3)
        await asyncio.wait_for(asyncio.gather(*tarefas, return_exceptions=True), 10)
    except Exception as exc:
        emit("INCONCL", "L5", f"ouvinte nao mediu: {type(exc).__name__}: {str(exc)[:80]}")
        return
    finally:
        await room.disconnect()
    for quem in identidades:
        rms, pico = medidas.get(quem, (0.0, 0))
        # Silêncio digital é pico ~0; ruído de ambiente de um microfone real já passa de 50.
        emit("OK" if pico > 50 else "FALHA", "L5",
             f"{quem}: audio com sinal rms={rms:.0f} pico={pico} (silencio digital reprova)")


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    try:
        candidatas = [SID] if SID else []
        if not candidatas:
            async for k in r.scan_iter("session:*:meta"):
                try:
                    meta = json.loads(await r.get(k) or "{}")
                except Exception:
                    continue
                if meta.get("channel") == "webrtc" and meta.get("pool_id") == POOL:
                    candidatas.append(k.split(":")[1])
        abertas = [s for s in candidatas if not await r.exists(f"session:{s}:closed")]
        if not abertas:
            emit("INCONCL", "L1", f"nenhum contato WebRTC aberto no pool {POOL} — faca a chamada do roteiro e rode DURANTE ela")
            return
        if len(abertas) > 1 and not SID:
            emit("INCONCL", "L1", f"{len(abertas)} contatos abertos no pool ({abertas}) — rode com SID=<id>")
            return
        sid = abertas[0]
        print(f"SID {sid}", flush=True)

        assigned = None
        for _id, f in await r.xrange(f"session:{sid}:stream"):
            if f.get("type") == "routing.assigned":
                assigned = f
        fw = (assigned or {}).get("framework")
        inst = (assigned or {}).get("instance_id", "")
        emit("OK" if fw == "human" and inst.startswith("human-") else "FALHA", "L1",
             f"contato {sid} atribuido: framework={fw!r} instancia={inst!r}")

        room_name = await r.get(f"channel:webrtc:{sid}:room_name")
        async with api.LiveKitAPI(LK_URL, LK_KEY, LK_SEC) as lk:
            ps = (await lk.room.list_participants(api.ListParticipantsRequest(room=room_name or "-"))).participants
        ids = sorted(p.identity for p in ps)
        agentes = [p for p in ps if p.identity.startswith("agent-")]
        clientes = [p for p in ps if p.identity.startswith("customer-")]
        emit("OK" if agentes and clientes else "FALHA", "L2", f"sala {room_name!r} no SFU: {ids}")

        def kinds(p) -> list[str]:
            out = []
            for t in p.tracks:
                k = {0: "audio", 1: "video"}.get(int(t.type))   # livekit.proto TrackType: AUDIO=0, VIDEO=1
                if k and not t.muted:
                    out.append(k)
            return sorted(set(out))
        for rot, grupo in (("agente", agentes), ("cliente", clientes)):
            if not grupo:
                emit("FALHA", "L3", f"{rot}: ausente da sala")
                continue
            got = kinds(grupo[0])
            emit("OK" if all(k in got for k in WANT) else "FALHA", "L3",
                 f"{rot} {grupo[0].identity} publica {got} (esperado {WANT})")

        # L5 — o áudio PUBLICADO é som. Trilha publicada e não muda pode carregar silêncio digital
        # (microfone errado, dispositivo tomado por outra aba); L3 não distingue. Um ouvinte oculto
        # assina as trilhas e mede o sinal. Não prova que o browser TOCA — isso é V4 do roteiro.
        await _l5_audio(room_name or "-", [p.identity for p in agentes + clientes])

        sub = inst.removeprefix("human-")
        emit("OK" if any(p.identity == f"agent-{sub}" for p in agentes) else "FALHA", "L4",
             f"identidade na sala casa com a instancia atribuida ({inst!r} -> 'agent-{sub}')")
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
