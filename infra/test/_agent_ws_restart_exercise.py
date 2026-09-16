"""
_agent_ws_restart_exercise.py — exercício do `probe_agent_ws_restart_ghost.sh` (AGH-02).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Um agente headless com a MESMA
credencial do Console (CAP-19) conecta ao `/agent/ws` e SEGURA a conexão por `HOLD_S` segundos,
respondendo o `ping` de aplicação com `pong` como o Console faz (o ping de protocolo a biblioteca
responde sozinha). Quem reinicia o mcp-server, e quem julga pelo Redis, é o .sh.

Saída: `REGISTERED <user>` quando a instância aparece no pool · `DROPPED <user> <motivo>` se o
socket cair · `HELD <user>` se segurou até o fim.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import jwt
import redis.asyncio as aioredis
import websockets

POOL   = os.environ["POOL"]
USER   = os.environ["AGENT_USER"]
HOLD_S = float(os.environ.get("HOLD_S", "30"))
TENANT = os.environ.get("TENANT", "tenant_demo")
MCP    = os.environ.get("MCP_WS", "ws://mcp-server-plughub:3100/agent/ws")
SECRET = os.environ["PLUGHUB_AUTH_JWT_SECRET"]


def credencial() -> str:
    n = int(time.time())
    mc = {"agent_assist": {"atender": {"access": "read_write", "scope": [f"pool:{POOL}"]}}}
    return jwt.encode({"sub": USER, "tenant_id": TENANT, "module_config": mc, "iat": n, "exp": n + 3600},
                      SECRET, algorithm="HS256")


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    url = f"{MCP}?pool={POOL}&user_id={USER}&user_login={USER}@probe.local&max_concurrent=1"
    try:
        async with websockets.connect(url, subprotocols=["plughub.bearer", credencial()]) as ws:
            inst = f"human-{USER}"
            fim_reg = time.monotonic() + 10
            while time.monotonic() < fim_reg:
                if await r.sismember(f"{TENANT}:pool:{POOL}:instances", inst):
                    print(f"REGISTERED {USER}", flush=True)
                    break
                await asyncio.sleep(0.2)
            fim = time.monotonic() + HOLD_S
            while time.monotonic() < fim:
                try:
                    raw = await asyncio.wait_for(ws.recv(), max(0.1, fim - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
            print(f"HELD {USER}", flush=True)
    except Exception as exc:
        print(f"DROPPED {USER} {type(exc).__name__}: {exc}", flush=True)
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
