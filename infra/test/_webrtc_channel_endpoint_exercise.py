"""
_webrtc_channel_endpoint_exercise.py — exercício do `probe_webrtc_channel_endpoint.sh` (VOZ-26).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Abre UM contato WebRTC pelo
endereço `IDENT` (`/ws/webrtc/{IDENT}`), com o handshake do widget, e pergunta ao LEITOR — o
`session:{sid}:meta` que o bridge e o routing-engine leem — em que pool a sessão caiu. Desliga
em seguida. Imprime:

  POOL <ident> <pool_id>       o pool gravado na sessão
  FALHA <motivo>               não autenticou ou não achou a meta
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import jwt
import redis.asyncio as aioredis
import websockets

IDENT  = os.environ["IDENT"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
REDIS  = os.environ.get("PLUGHUB_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://redis:6379"
GW     = f"ws://channel-gateway:8010/ws/webrtc/{IDENT}"


async def recv_until(ws, tipo: str, secs: float) -> dict | None:
    fim = time.monotonic() + secs
    while time.monotonic() < fim:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, fim - time.monotonic())))
        except Exception:
            return None
        if m.get("type") == tipo:
            return m
    return None


async def main() -> None:
    r = aioredis.from_url(REDIS, decode_responses=True)
    now = int(time.time())
    token = jwt.encode({"sub": "c-voz26-" + uuid.uuid4().hex[:6], "tenant_id": TENANT, "channel": "webrtc",
                        "iat": now, "exp": now + 600}, SECRET, algorithm="HS256")
    async with websockets.connect(GW) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if ready.get("type") == "conn.ready":
            await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
            await ws.send(json.dumps({"type": "conn.authenticate", "token": token}))
        auth = await recv_until(ws, "conn.authenticated", 10)
        sid = (auth or {}).get("session_id", "")
        if not sid:
            print(f"FALHA {IDENT} nao autenticou (primeiro={ready.get('type')})", flush=True)
            return
        raw = await r.get(f"session:{sid}:meta")
        pool = (json.loads(raw) if raw else {}).get("pool_id")
        if pool:
            print(f"POOL {IDENT} {pool}", flush=True)
        else:
            print(f"FALHA {IDENT} sessao {sid} sem pool_id na meta", flush=True)
        await ws.send(json.dumps({"type": "webrtc.hangup"}))
        await asyncio.sleep(1)
    await r.aclose()


asyncio.run(main())
