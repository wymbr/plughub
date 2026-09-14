"""
_webrtc_media_plane_exercise.py — exercício do `probe_webrtc_media_plane.sh` (VOZ-01).

Roda DENTRO da imagem do channel-gateway (`docker run --rm <imagem>`), na rede do
compose, com o código do provider que a imagem carrega — nunca uma cópia do teste.
Imprime uma linha por verificação, `OK|FALHA|INCONCL <ramo> <texto>`, e o shell conta.

Ramos:
  D  SFU REAL — o provider da imagem cria sala; um participante entra SÓ POR RELAY
     (TURN) e publica trilha; o SFU lista esse participante. Duas contraprovas de que o
     SFU VERIFICA: token com segredo errado → recusado; sala não criada → recusada
     (`auto_create: false`).
  F  ROTA `/webrtc/token/{sid}` ao vivo — 401/401/403/403/404/422 e dois controles
     POSITIVOS (agente e supervisor) cujo token o SFU ACEITA. Sem o positivo, uma rota
     que recusasse tudo passaria nos seis negativos.

MODE=wrong_secret: só o `create_room` com o segredo trocado, que TEM de falhar — é a
mutação embutida que impede o D de ficar verde por não medir nada.

Fixtures: uma sessão própria (`probe_voz01_<hex>`) com meta e sala no Redis; tudo é
apagado por NOME no fim, inclusive a sala no SFU.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid

LK_URL      = os.environ["LK_URL"]
LK_KEY      = os.environ["LK_KEY"]
LK_SECRET   = os.environ["LK_SECRET"]
MODE        = os.environ.get("MODE", "full")

out: list[str] = []


def emit(status: str, ramo: str, texto: str) -> None:
    line = f"{status} {ramo} {texto}"
    out.append(line)
    print(line, flush=True)


def _jwt_payload(tok: str) -> dict:
    part = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


async def _join(url: str, token: str, relay_only: bool, publish: bool,
                check=None) -> tuple[bool, str]:
    """Entra, publica (opcional) e roda `check()` ENQUANTO conectado."""
    from livekit import rtc

    room = rtc.Room()
    opts = rtc.RoomOptions()
    if relay_only:
        opts = rtc.RoomOptions(rtc_config=rtc.RtcConfiguration(
            ice_transport_type=rtc.IceTransportType.TRANSPORT_RELAY,
        ))
    try:
        await asyncio.wait_for(room.connect(url, token, opts), 25)
        if publish:
            src = rtc.AudioSource(48000, 1)
            track = rtc.LocalAudioTrack.create_audio_track("probe", src)
            await asyncio.wait_for(room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            ), 25)
        if check is not None:
            return await check()
        return True, "conectou"
    except Exception as exc:  # o texto do SFU é a evidência
        return False, f"{type(exc).__name__}: {str(exc)[:90]}"
    finally:
        try:
            # Sem teto de tempo, uma publicação recusada deixava o exercício pendurado aqui
            # para sempre (medido na VOZ-09 e de novo na VOZ-10).
            await asyncio.wait_for(room.disconnect(), 10)
        except Exception:
            pass


async def wrong_secret() -> None:
    from plughub_channel_gateway.adapters.webrtc_provider import LiveKitProvider

    p = LiveKitProvider(LK_URL, LK_KEY, "segredo_errado_" + "x" * 32)
    try:
        info = await p.create_room("probe-voz01-mut-" + uuid.uuid4().hex[:6])
        emit("FALHA", "M1", f"create_room com segredo ERRADO devolveu sala {info.room_sid}")
    except Exception as exc:
        emit("OK", "M1", f"segredo errado recusado pelo SFU ({type(exc).__name__})")


async def full() -> None:
    import httpx
    import jwt as pyjwt
    import redis.asyncio as aioredis
    from livekit import api
    from plughub_channel_gateway.adapters.webrtc_provider import (
        LiveKitProvider, TokenGrants, build_room_name,
    )

    gw_url   = os.environ["GW_URL"]
    auth_sec = os.environ["AUTH_SECRET"]
    tenant   = os.environ["TENANT"]
    r = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)

    sid  = "probe_voz01_" + uuid.uuid4().hex[:10]
    pool = "probe_voz01_pool"
    room = build_room_name(sid)
    p = LiveKitProvider(LK_URL, LK_KEY, LK_SECRET)

    try:
        # ── D · SFU REAL ─────────────────────────────────────────────────────
        info = await p.create_room(room, empty_timeout_s=60)
        got = await p.get_room(room)
        if got and got.room_sid == info.room_sid and info.room_sid.startswith("RM_") \
                and "dev" not in info.room_sid and "mock" not in info.room_sid:
            emit("OK", "D1", f"sala criada e LIDA de volta no SFU ({info.room_sid})")
        else:
            emit("FALHA", "D1", f"sala criada={info.room_sid} lida={got}")

        def listed(identity: str):
            async def _check() -> tuple[bool, str]:
                ids = [x.identity for x in await p.list_participants(room)]
                return identity in ids, f"SFU lista {ids}"
            return _check

        ident = "agent-probe-" + uuid.uuid4().hex[:6]
        tok = p.generate_token(TokenGrants(room_name=room, identity=ident))
        ok, why = await _join(LK_URL, tok, relay_only=True, publish=True, check=listed(ident))
        emit("OK" if ok else "FALHA", "D2",
             f"join SÓ POR RELAY (TURN) + publish de trilha, participante visto pelo SFU: {why}")

        bad = api.AccessToken(LK_KEY, "x" * 40).with_identity("intruso") \
            .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt()
        ok, why = await _join(LK_URL, bad, relay_only=False, publish=False)
        emit("FALHA" if ok else "OK", "D3", f"token com segredo errado: {why}")

        ghost = p.generate_token(TokenGrants(room_name=room + "-nao-criada", identity="g"))
        ok, why = await _join(LK_URL, ghost, relay_only=False, publish=False)
        emit("FALHA" if ok else "OK", "D4", f"sala nao criada (auto_create:false): {why}")

        # ── F · ROTA ─────────────────────────────────────────────────────────
        await r.setex(f"session:{sid}:meta", 300, json.dumps({"tenant_id": tenant, "pool_id": pool}))
        await r.setex(f"channel:webrtc:{sid}:room_name", 300, room)
        # O agente pede token numa sala onde já foi ATRIBUÍDO: desde a VOZ-10 o teto dele é o
        # `agent_publish` do pool que o pôs lá. Sem atendente registrado o teto é vazio, o SFU
        # recusa a publicação do F8 — e o cliente Python do LiveKit pendura no `disconnect`.
        await r.setex(f"channel:webrtc:{sid}:media", 300, json.dumps({
            "attendants": {"h-probe": {"framework": "human", "pool_id": pool,
                                       "customer_publish": ["audio"], "agent_publish": ["audio"],
                                       "policy_source": f"pool:{pool}"}},
            "customer": {"publish": ["audio"]},
        }))

        def mint(sub: str, mc: dict, ten: str = tenant, secret: str = auth_sec) -> str:
            now = int(time.time())
            return pyjwt.encode({"sub": sub, "tenant_id": ten, "module_config": mc,
                                 "iat": now, "exp": now + 300}, secret, algorithm="HS256")

        atender = {"agent_assist": {"atender": {"access": "read_write", "scope": [f"pool:{pool}"]}}}
        monitor = {"contacts": {"monitorar": {"access": "read_only", "scope": [pool]}}}
        outro   = {"agent_assist": {"atender": {"access": "read_write", "scope": ["pool:outro_pool"]}}}

        async with httpx.AsyncClient(base_url=gw_url, timeout=10) as http:
            async def get(role: str, tok: str | None, extra: str = "") -> httpx.Response:
                h = {"Authorization": f"Bearer {tok}"} if tok else {}
                return await http.get(f"/webrtc/token/{sid}?role={role}{extra}", headers=h)

            casos = [
                ("F1", "sem Bearer",                     await get("agent", None),                                       401),
                ("F2", "assinatura invalida",            await get("agent", mint("u1", atender, secret="y" * 40)),         401),
                ("F3", "sem capacidade nenhuma",         await get("agent", mint("u1", {})),                               403),
                ("F4", "atender escopado a OUTRO pool",  await get("agent", mint("u1", outro)),                            403),
                ("F5", "supervisor so com atender",      await get("supervisor", mint("u1", atender)),                     403),
                ("F6", "tenant de outro",                await get("agent", mint("u1", atender, ten="tenant_outro")),      404),
                ("F7", "role fora da tabela",            await get("root", mint("u1", atender)),                           422),
            ]
            for rid, nome, resp, esperado in casos:
                emit("OK" if resp.status_code == esperado else "FALHA", rid,
                     f"{nome}: http={resp.status_code} esperado={esperado}")

            # Controles POSITIVOS — e o token precisa entrar na sala de verdade.
            sub = "user-probe-" + uuid.uuid4().hex[:6]
            resp = await get("agent", mint(sub, atender), extra="&identity=forjado")
            if resp.status_code != 200:
                emit("FALHA", "F8", f"agente com atender no pool: http={resp.status_code} {resp.text[:80]}")
            else:
                body = resp.json()
                pl = _jwt_payload(body["token"])
                ident_ok = pl.get("sub") == f"agent-{sub}"
                ok, why = await _join(LK_URL, body["token"], relay_only=False, publish=True,
                                      check=listed(f"agent-{sub}"))
                emit("OK" if (ident_ok and ok) else "FALHA", "F8",
                     f"agente 200; identidade do JWT (nao da query)={ident_ok}; SFU aceitou e publicou: {why}")
                emit("OK" if body.get("livekit_url") and "livekit:" not in body["livekit_url"] else "FALHA",
                     "F9", f"URL entregue ao cliente e a PUBLICA: {body.get('livekit_url')}")

            resp = await get("supervisor", mint(sub, monitor))
            if resp.status_code != 200:
                emit("FALHA", "F10", f"supervisor com monitorar no pool: http={resp.status_code} {resp.text[:80]}")
            else:
                v = _jwt_payload(resp.json()["token"]).get("video", {})
                oculto = v.get("hidden") is True and not v.get("canPublish")
                ok, why = await _join(LK_URL, resp.json()["token"], relay_only=False, publish=False)
                emit("OK" if (oculto and ok) else "FALHA", "F10",
                     f"supervisor 200; oculto e sem publish={oculto}; SFU aceitou: {why}")
    finally:
        await r.delete(f"session:{sid}:meta", f"channel:webrtc:{sid}:room_name",
                       f"channel:webrtc:{sid}:media")
        await r.aclose()
        await p.delete_room(room)
        still = await p.get_room(room)
        emit("OK" if still is None else "FALHA", "LIMPEZA", f"sala da fixture apagada no SFU ({still})")


asyncio.run(wrong_secret() if MODE == "wrong_secret" else full())
