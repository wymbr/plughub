"""
_webrtc_agent_console_exercise.py — exercício do `probe_webrtc_agent_console.sh` (VOZ-04, fatia 2).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Dois lados de verdade:
  · o CLIENTE fala o protocolo do widget com o gateway;
  · o AGENTE HUMANO fala o protocolo do Console com o mcp-server (`/agent/ws`) e pede o token
    pelo MESMO caminho do browser — o nginx do platform-ui (`/webrtc/token/…`).
As respostas são perguntadas a quem as consome: o que chega no socket do agente, o que o
stream carrega, o que o SFU lista.

  G1 o agente recebe `conversation.assigned` com `channel = webrtc` (o Console abre a sala)
  G2 o bridge anunciou o atendente ao plano de mídia: `routing.assigned` humano no stream, com
     a instância do agente, e a sala EXISTE no SFU
  G3 o token do agente, pelo caminho do browser, é 200 e publica o `agent_publish` do pool
  G4 sessão desconhecida pelo mesmo caminho é 404 SEM `room_not_ready` — o Console desiste em
     vez de repetir às cegas (o controle do código que ele usa para repetir)
  G5 agente e cliente entram na MESMA sala e o SFU lista os dois
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
import redis.asyncio as aioredis
import websockets
from livekit import api, rtc

POOL     = os.environ["POOL"]
TENANT   = os.environ["PLUGHUB_TENANT_ID"]
CSEC     = os.environ["PLUGHUB_JWT_SECRET"]
ASEC     = os.environ["PLUGHUB_AUTH_JWT_SECRET"]
LK_URL   = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]
LK_KEY   = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_KEY"]
LK_SEC   = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_SECRET"]
AGENT_PUBLISH = json.loads(os.environ["AGENT_PUBLISH"])
UI       = "http://platform-ui:5174"


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def ctoken(sub: str) -> str:
    n = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": n, "exp": n + 600},
                      CSEC, algorithm="HS256")


def atoken(sub: str) -> str:
    n = int(time.time())
    mc = {"agent_assist": {"atender": {"access": "read_write", "scope": [f"pool:{POOL}"]}}}
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "module_config": mc, "iat": n, "exp": n + 600},
                      ASEC, algorithm="HS256")


async def recv_json(ws, secs: float):
    return json.loads(await asyncio.wait_for(ws.recv(), secs))


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    user = "u-voz04-" + uuid.uuid4().hex[:6]
    instance = f"human-{user}"
    sid = ""
    rooms: list[rtc.Room] = []
    try:
        async with websockets.connect(f"ws://channel-gateway:8010/ws/webrtc/{POOL}") as cws:
            await recv_json(cws, 10)
            await cws.send(json.dumps({"type": "conn.hello", "version": "1"}))
            await cws.send(json.dumps({"type": "conn.authenticate", "token": ctoken("c-" + user)}))
            sid = (await recv_json(cws, 10)).get("session_id", "")
            print(f"SID {sid}", flush=True)

            url = (f"ws://mcp-server-plughub:3100/agent/ws?pool={POOL}&user_id={user}"
                   f"&user_login={user}@probe.local&max_concurrent=1")
            async with websockets.connect(url) as aws:
                assigned = None
                fim = time.monotonic() + 40
                while time.monotonic() < fim and assigned is None:
                    try:
                        m = await recv_json(aws, 5)
                    except asyncio.TimeoutError:
                        await aws.send(json.dumps({"type": "pong"}))
                        continue
                    if m.get("type") == "conversation.assigned" and m.get("session_id") == sid:
                        assigned = m
                emit("OK" if (assigned or {}).get("channel") == "webrtc" else "FALHA", "G1",
                     f"agente recebeu conversation.assigned channel={(assigned or {}).get('channel')!r} "
                     f"(atribuido={assigned is not None})")
                if assigned is None:
                    return

                # O Console pede o token ASSIM que recebe a atribuição; medimos a corrida.
                async with httpx.AsyncClient(timeout=10) as h:
                    first = await h.get(f"{UI}/webrtc/token/{sid}?role=agent",
                                        headers={"Authorization": f"Bearer {atoken(user)}"})
                    print(f"INFO primeira tentativa do token: {first.status_code} {first.text[:90]}", flush=True)

                    entry = None
                    for _ in range(30):
                        for _id, f in await r.xrange(f"session:{sid}:stream"):
                            if f.get("type") == "routing.assigned" and f.get("instance_id") == instance:
                                entry = f
                        if entry:
                            break
                        await asyncio.sleep(0.5)
                    room_name = await r.get(f"channel:webrtc:{sid}:room_name")
                    async with api.LiveKitAPI(LK_URL, LK_KEY, LK_SEC) as lk:
                        lista = await lk.room.list_rooms(api.ListRoomsRequest(names=[room_name or "-"]))
                    emit("OK" if (entry and entry.get("framework") == "human" and room_name and lista.rooms) else "FALHA",
                         "G2", f"routing.assigned humano={bool(entry)} framework={(entry or {}).get('framework')!r} "
                               f"sala={room_name!r} existe_no_SFU={bool(lista.rooms)}")

                    tok = None
                    for _ in range(20):
                        resp = await h.get(f"{UI}/webrtc/token/{sid}?role=agent",
                                           headers={"Authorization": f"Bearer {atoken(user)}"})
                        if resp.status_code == 200:
                            tok = resp.json()
                            break
                        await asyncio.sleep(0.5)
                    emit("OK" if (tok and tok.get("publish") == AGENT_PUBLISH) else "FALHA", "G3",
                         f"token do agente pelo nginx do platform-ui: "
                         f"{'200' if tok else resp.status_code} publish={(tok or {}).get('publish')} "
                         f"(esperado {AGENT_PUBLISH})")

                    # VOZ-15 — dívida registrada, NÃO veredicto: a rota confere a capacidade no
                    # pool, não que o chamador é quem atende. Medido a cada rodada para a ficha
                    # não envelhecer; quando fechar, isto vira ramo com 403 esperado.
                    outro = await h.get(f"{UI}/webrtc/token/{sid}?role=agent",
                                        headers={"Authorization": f"Bearer {atoken('u-outro-' + uuid.uuid4().hex[:6])}"})
                    print(f"INFO VOZ-15 outro usuario com o mesmo grant, que nao atende: {outro.status_code}", flush=True)

                    fake = await h.get(f"{UI}/webrtc/token/{uuid.uuid4()}?role=agent",
                                       headers={"Authorization": f"Bearer {atoken(user)}"})
                    code = ((fake.json() if fake.headers.get("content-type", "").startswith("application/json") else {})
                            .get("detail") or {})
                    code = code.get("code") if isinstance(code, dict) else None
                    emit("OK" if (fake.status_code == 404 and code != "room_not_ready") else "FALHA", "G4",
                         f"sessao desconhecida: {fake.status_code} code={code!r} (o Console so repete em room_not_ready)")

                # G5 — os dois na mesma sala
                ready = None
                fim = time.monotonic() + 10
                while time.monotonic() < fim and ready is None:
                    try:
                        m = await recv_json(cws, 2)
                    except asyncio.TimeoutError:
                        continue
                    if m.get("type") in ("webrtc.ready", "webrtc.media") and m.get("token"):
                        ready = m
                if tok and ready:
                    for t in (tok["token"], ready["token"]):
                        room = rtc.Room()
                        await asyncio.wait_for(room.connect(LK_URL, t), 25)
                        rooms.append(room)
                    await asyncio.sleep(1.5)
                    async with api.LiveKitAPI(LK_URL, LK_KEY, LK_SEC) as lk:
                        ps = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
                    ids = sorted(p.identity for p in ps.participants)
                    ok = f"agent-{user}" in ids and any(i.startswith("customer-") for i in ids)
                    emit("OK" if ok else "FALHA", "G5", f"SFU lista na sala {room_name}: {ids}")
                else:
                    emit("FALHA", "G5", f"sem token de agente ({bool(tok)}) ou do cliente ({bool(ready)})")

                for room in rooms:
                    try:
                        await asyncio.wait_for(room.disconnect(), 10)
                    except Exception:
                        pass
            await cws.send(json.dumps({"type": "webrtc.hangup"}))
        closed = False
        for _ in range(30):
            closed = bool(await r.exists(f"session:{sid}:closed"))
            if closed:
                break
            await asyncio.sleep(0.5)
        # A instância humana sai quando o socket do agente fecha (depois da carência do
        # mcp-server). Se ficasse, o próximo contato do pool seria ATRIBUÍDO a um agente que
        # não existe — e o probe de entrada, que espera fila, reprovaria por resíduo.
        gone = False
        for _ in range(30):
            gone = not await r.exists(f"{TENANT}:instance:{instance}") and \
                not await r.sismember(f"{TENANT}:pool:{POOL}:instances", instance)
            if gone:
                break
            await asyncio.sleep(0.5)
        emit("OK" if (closed and gone) else "FALHA", "LIMPEZA",
             f"sessao {sid} fechada={closed}; instancia {instance} removida={gone}")
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
