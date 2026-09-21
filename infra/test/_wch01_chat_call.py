"""
_wch01_chat_call.py — corpo do `probe_wch01_chat_call.sh` (WCH-01). Roda DENTRO do container do
channel-gateway (usa o `websockets`, o `redis` e o segredo de webchat que ele já tem).

Sem gente e sem humano logado: o contato de chat cai num pool de IA, que nesta fatia não oferece
mídia — então o que se mede é a PORTA, a espera DITA e a independência chamada × contato. O caminho
com humano e áudio foi validado pelo dono no browser (2026-09-21).
Saída: 0 verde · 1 alguma falha · 2 não mediu.
"""
import asyncio
import sys
import json
import os
import time

import jwt
import redis.asyncio as aioredis
import websockets

GW = "ws://localhost:8010"
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
POOL = os.environ.get("POOL", "demo_ia")


def tok(sub, **extra):
    now = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "iat": now, "exp": now + 600, **extra},
                      SECRET, algorithm="HS256")


async def recv_until(ws, types, timeout=15):
    fim = time.time() + timeout
    vistos = []
    while time.time() < fim:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=fim - time.time()))
        except asyncio.TimeoutError:
            break
        vistos.append(m)
        if m.get("type") in types:
            return m, vistos
    return None, vistos


async def call(token, sid):
    ws = await websockets.connect(f"{GW}/ws/call")
    m, _ = await recv_until(ws, {"conn.ready", "conn.error"})
    if m["type"] == "conn.error":
        return ws, m
    await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
    await ws.send(json.dumps({"type": "conn.authenticate", "token": token, "session_id": sid}))
    m, _ = await recv_until(ws, {"conn.authenticated", "conn.error"})
    return ws, m


async def main():
    r = aioredis.from_url(os.environ.get("PLUGHUB_REDIS_URL", "redis://redis:6379"), decode_responses=True)
    ok = falha = 0

    def veredito(cond, txt):
        nonlocal ok, falha
        print(("  OK     " if cond else "  FALHA  ") + txt)
        ok, falha = ok + bool(cond), falha + (not cond)

    sub = f"wch01-{int(time.time())}"
    token = tok(sub)
    chat = await websockets.connect(f"{GW}/ws/chat/{POOL}")
    await recv_until(chat, {"conn.hello"})
    await chat.send(json.dumps({"type": "conn.authenticate", "token": token}))
    m, _ = await recv_until(chat, {"conn.authenticated"})
    sid = m["session_id"]
    print(f"chat aberto session={sid} pool={POOL}")
    await asyncio.sleep(4)          # roteamento para a IA

    # A — recusas na porta
    _, m = await call(tok("outro-cliente"), sid)
    veredito(m.get("code") == "session_not_found", f"A1 cliente alheio recusado ({m.get('code')})")
    _, m = await call(token, "nao-existe")
    veredito(m.get("code") == "session_not_found", f"A2 sessao inexistente recusada ({m.get('code')})")

    # B — a chamada prende; atendente de IA não oferece mídia nesta fatia → espera DITA, sem sala
    cw, m = await call(token, sid)
    veredito(m.get("type") == "conn.authenticated" and m.get("mode") == "call", f"B1 chamada presa ({m})")
    p, vistos = await recv_until(cw, {"webrtc.call_pending", "webrtc.ready"}, timeout=10)
    veredito(p is not None and p["type"] == "webrtc.call_pending",
             f"B2 so IA atende -> call_pending ({p and p.get('policy_sources')})")
    veredito(not await r.get(f"channel:webrtc:{sid}:room_name"), "B3 nenhuma sala criada")
    _, m = await call(token, sid)
    veredito(m.get("code") == "call_already_active", f"B4 segunda chamada recusada ({m.get('code')})")

    # C — desligar a chamada não encerra o contato
    await cw.send(json.dumps({"type": "webrtc.hangup"}))
    await asyncio.sleep(2)
    await cw.close()
    veredito(not await r.exists(f"session:{sid}:closed_recorded"), "C1 contato NAO fechou com o fim da chamada")
    await chat.send(json.dumps({"type": "msg.text", "id": "m1", "text": "ainda estou aqui"}))
    await asyncio.sleep(2)
    fim = await r.xrevrange(f"session:{sid}:stream", count=30)
    veredito(any("ainda estou aqui" in json.dumps(f) for _, f in fim), "C2 o chat segue: mensagem depois da chamada chegou ao stream")
    tipos = [f.get("type") for _, f in fim]
    veredito("media.call" not in tipos, "C3 sem media.call (a chamada nunca montou)")

    # D — contato fechado: nova chamada recusada
    await chat.close()
    for _ in range(20):
        if await r.exists(f"session:{sid}:closed_recorded"):
            break
        await asyncio.sleep(1)
    _, m = await call(token, sid)
    veredito(m.get("code") in ("contact_closed", "session_not_found"), f"D1 contato encerrado recusa chamada ({m.get('code')})")

    print(f"── {ok} OK, {falha} FALHA")
    return 1 if falha else 0


try:
    sys.exit(asyncio.run(main()))
except (OSError, websockets.exceptions.WebSocketException) as exc:
    print(f"  INCONCL gateway inalcancavel de dentro do container: {exc}")
    sys.exit(2)
