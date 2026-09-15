"""
_webrtc_close_farewell_exercise.py — exercício do `probe_webrtc_close_farewell.sh` (VOZ-16).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente é um WebSocket que fala
o protocolo do widget de demo, num pool WebRTC humano SEM agente e com teto de espera curto
declarado no POOL (`queue_config.max_wait_s`), para o teto vencer em segundos e não em 300.

  F1 o cliente autentica e o routing-engine o ENFILEIRA (sem isto, o resto não mede nada)
  F2 CONTROLE: o routing-engine TIROU o contato da fila por teto (ZSET vazio para a sessão) —
     o fechamento que o cliente vê é o do timeout, e não outro
  F3 antes do `webrtc.session_closed`, o cliente recebe `webrtc.message` de autor `system` com o
     texto que o routing mandou — `msg_queue_timeout`, lido VIVO na config-api, nunca constante
  F4 o `webrtc.session_closed` diz o motivo de NEGÓCIO (`max_wait_exceeded`), não um default
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

POOL   = os.environ["POOL"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
WAIT_S = int(os.environ.get("MAX_WAIT_S", "10"))
GW     = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
CFG    = "http://config-api:3600"


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(f"{CFG}/config/routing/msg_queue_timeout", params={"tenant_id": TENANT})
            esperado = (resp.json() or {}).get("value") if resp.status_code == 200 else None
        if not esperado:
            emit("INCONCL", "F3", f"msg_queue_timeout nao lido da config-api (http {resp.status_code})")

        sub = "c-voz16-" + uuid.uuid4().hex[:6]
        now = int(time.time())
        token = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": now, "exp": now + 600},
                           SECRET, algorithm="HS256")
        async with websockets.connect(GW) as ws:
            ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if ready.get("type") == "conn.ready":
                await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
                await ws.send(json.dumps({"type": "conn.authenticate", "token": token}))
            sid, recebidas = "", []
            fim = time.monotonic() + 15
            while time.monotonic() < fim and not sid:
                m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, fim - time.monotonic())))
                if m.get("type") == "conn.authenticated":
                    sid = m.get("session_id", "")
            if not sid:
                emit("FALHA", "F1", "cliente nao autenticou")
                return
            print(f"SID {sid}", flush=True)
            na_fila = False
            for _ in range(30):
                if await r.zscore(f"{TENANT}:pool:{POOL}:queue", sid) is not None:
                    na_fila = True
                    break
                await asyncio.sleep(0.5)
            emit("OK" if na_fila else "FALHA", "F1", f"routing-engine enfileirou a sessao={na_fila}")
            if not na_fila:
                return

            # tudo o que o cliente recebe até o servidor fechar (teto + varredura + folga)
            fechou = None
            fim = time.monotonic() + WAIT_S + 60
            while time.monotonic() < fim:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, fim - time.monotonic())))
                except Exception:
                    break
                recebidas.append(m)
                if m.get("type") == "webrtc.session_closed":
                    fechou = m
                    break

        saiu = await r.zscore(f"{TENANT}:pool:{POOL}:queue", sid) is None
        emit("OK" if (saiu and fechou) else "FALHA", "F2",
             f"routing tirou o contato da fila={saiu}; servidor fechou o cliente={fechou is not None}")
        if not fechou:
            return

        idx_close = recebidas.index(fechou)
        sistema = [m for m in recebidas[:idx_close]
                   if m.get("type") == "webrtc.message" and m.get("author") == "system"]
        textos = [m.get("text") for m in sistema]
        if esperado:
            emit("OK" if esperado in textos else "FALHA", "F3",
                 f"aviso de sistema antes do fechamento = {textos!r} (esperado {esperado!r})")
        emit("OK" if fechou.get("reason") == "max_wait_exceeded" else "FALHA", "F4",
             f"motivo no webrtc.session_closed = {fechou.get('reason')!r} (esperado 'max_wait_exceeded')")
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
