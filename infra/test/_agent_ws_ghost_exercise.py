"""
_agent_ws_ghost_exercise.py — exercício do `probe_agent_ws_ghost_instance.sh` (AGH-01).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Fala o protocolo do Console no
`/agent/ws` do mcp-server e pergunta ao Redis quem continua sendo instância do pool.

  C1 controle: A entra e sai sozinho → após a janela, A NÃO é mais instância (o desregistro
     funciona; sem este controle, G1 verde poderia ser um pool que nunca registra ninguém)
  G1 A sai e B entra no MESMO pool dentro da janela de graça → A sai do pool, B fica
     (o defeito: o login de B cancelava o logout de A, e A ficava pronto sem socket)
  G2 A sai e o PRÓPRIO A volta dentro da janela (F5) → A continua instância
     (a proteção de recarga que o cancelamento existe para dar não pode ir junto)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import jwt
import redis.asyncio as aioredis
import websockets

POOL   = os.environ["POOL"]
TENANT = os.environ.get("TENANT", "tenant_demo")
MCP    = os.environ.get("MCP_WS", "ws://mcp-server-plughub:3100/agent/ws")
SECRET = os.environ["PLUGHUB_AUTH_JWT_SECRET"]
GRACE  = 2.5


def credencial(user: str) -> str:
    """O mesmo JWT que o Console apresenta (CAP-19): `sub` = usuário, `atender` no pool."""
    n = int(time.time())
    mc = {"agent_assist": {"atender": {"access": "read_write", "scope": [f"pool:{POOL}"]}}}
    return jwt.encode({"sub": user, "tenant_id": TENANT, "module_config": mc, "iat": n, "exp": n + 600},
                      SECRET, algorithm="HS256")


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def url(user: str) -> str:
    return f"{MCP}?pool={POOL}&user_id={user}&user_login={user}@probe.local&max_concurrent=1"


async def conectar(user: str):
    ws = await websockets.connect(url(user), subprotocols=["plughub.bearer", credencial(user)])
    await asyncio.wait_for(ws.recv(), 5)   # connection.accepted
    return ws


async def instancia(r, user: str) -> bool:
    inst = f"human-{user}"
    return bool(await r.exists(f"{TENANT}:instance:{inst}")) and \
        bool(await r.sismember(f"{TENANT}:pool:{POOL}:instances", inst))


async def esperar(r, user: str, quer: bool, secs: float) -> bool:
    fim = time.monotonic() + secs
    while time.monotonic() < fim:
        if await instancia(r, user) == quer:
            return True
        await asyncio.sleep(0.2)
    return await instancia(r, user) == quer


async def limpar(r, user: str) -> None:
    """Remove a instância pelo caminho do PRODUTO: conecta e sai sozinho, sem ninguém na janela."""
    if await instancia(r, user):
        ws = await conectar(user)
        await asyncio.sleep(1.0)
        await ws.close()
        await esperar(r, user, False, GRACE + 4)


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    usados: list[str] = []
    novo = lambda: usados.append("u-agh01-" + uuid.uuid4().hex[:6]) or usados[-1]
    try:
        # C1 — sai sozinho
        a = novo()
        ws = await conectar(a)
        reg = await esperar(r, a, True, 8)
        await ws.close()
        saiu = await esperar(r, a, False, GRACE + 5)
        emit("OK" if reg and saiu else ("INCONCL" if not reg else "FALHA"), "C1",
             f"A sozinho: registrou={reg} saiu_do_pool_apos_janela={saiu}")
        await asyncio.sleep(GRACE + 1)

        # G1 — A sai, B entra dentro da janela
        a, b = novo(), novo()
        wa = await conectar(a)
        reg_a = await esperar(r, a, True, 8)
        await wa.close()
        await asyncio.sleep(0.3)
        wb = await conectar(b)
        reg_b = await esperar(r, b, True, 8)
        await asyncio.sleep(GRACE + 3)
        a_ficou, b_ficou = await instancia(r, a), await instancia(r, b)
        emit("OK" if (reg_a and reg_b and not a_ficou and b_ficou) else ("INCONCL" if not (reg_a and reg_b) else "FALHA"),
             "G1", f"A saiu e B entrou em 0.3s: A_ainda_instancia={a_ficou} (fantasma se True) B_instancia={b_ficou}")
        await wb.close()
        await asyncio.sleep(GRACE + 3)

        # G2 — o próprio A volta dentro da janela (recarga)
        a = novo()
        wa = await conectar(a)
        reg = await esperar(r, a, True, 8)
        await wa.close()
        await asyncio.sleep(0.5)
        wa2 = await conectar(a)
        await asyncio.sleep(GRACE + 3)
        ficou = await instancia(r, a)
        emit("OK" if reg and ficou else ("INCONCL" if not reg else "FALHA"), "G2",
             f"A recarregou em 0.5s: continua_instancia={ficou}")
        await wa2.close()
        await asyncio.sleep(GRACE + 3)

        # limpeza pelo produto, um por vez (sem ninguém na janela do outro)
        for u in usados:
            await limpar(r, u)
        restos = [u for u in usados if await instancia(r, u)]
        emit("OK" if not restos else "FALHA", "LIMPEZA", f"instancias do probe restantes: {restos}")
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
