"""
_webrtc_contact_entry_exercise.py — exercício do `probe_webrtc_contact_entry.sh` (VOZ-04, fatia de entrada).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente é um WebSocket de
verdade falando o protocolo que o widget de demo fala; as respostas são perguntadas aos
LEITORES (estado que routing-engine e bridge gravam no Redis), nunca ao que o gateway diz de si.

  E1 handshake do widget (conn.ready → hello + authenticate) → conn.authenticated
  E2 o ROUTING-ENGINE aceitou o pedido: a sessão está no ZSET da fila do pool
     (`{tenant}:pool:{pool}:queue`). ⚠️ Não a chave `{tenant}:session:pool:{sid}`: a limpeza da
     fila não a apaga, então ela não distingue "na fila" de "já saiu" — foi o primeiro erro deste
     instrumento.
  E3 `ws_alive` existe com TTL acima da cadência do watchdog do bridge
  E4 texto do cliente chega ao bridge como mensagem de texto (lido no log DO BRIDGE: sem agente
     na fila, o veredicto do leitor não deixa estado — ver o probe)
  E5 formato errado (`content.text`) é RECUSADO dito: `conn.error bad_message`
  E6 desligar → o contato é fechado (`session:{sid}:closed`) e SAI do ZSET da fila

  K  O instrumento do E2 discrimina, medido no próprio routing-engine: pedido montado por
     `contact_lifecycle.routing_request` publicado direto no Kafka É enfileirado; o mesmo pedido
     sem `started_at` (o formato de antes) NÃO é. Sem o K, um E2 verde poderia ser chave
     escrita por outro caminho.
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
from aiokafka import AIOKafkaProducer

from plughub_channel_gateway.adapters import contact_lifecycle
from plughub_channel_gateway.models import ContactClosedEvent

POOL     = os.environ["POOL"]
TENANT   = os.environ["PLUGHUB_TENANT_ID"]
SECRET   = os.environ["PLUGHUB_JWT_SECRET"]
BROKERS  = os.environ["PLUGHUB_KAFKA_BROKERS"]
WATCHDOG = int(os.environ.get("WATCHDOG_S", "120"))
GW       = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"


def na_fila(r: aioredis.Redis, sid: str):
    async def _q():
        return await r.zscore(f"{TENANT}:pool:{POOL}:queue", sid) is not None
    return _q


def fora_da_fila(r: aioredis.Redis, sid: str):
    async def _q():
        return (await r.zscore(f"{TENANT}:pool:{POOL}:queue", sid) is None
                and not await r.exists(f"{TENANT}:queue_contact:{sid}"))
    return _q


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


async def wait_for(pred, secs: float, step: float = 0.5):
    fim = time.monotonic() + secs
    while time.monotonic() < fim:
        v = await pred()
        if v:
            return v
        await asyncio.sleep(step)
    return await pred()


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


async def contato(r: aioredis.Redis) -> None:
    sub = "c-voz04-" + uuid.uuid4().hex[:6]
    now = int(time.time())
    token = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": now, "exp": now + 600},
                       SECRET, algorithm="HS256")
    sid = ""
    async with websockets.connect(GW) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if ready.get("type") == "conn.ready":          # o que o widget faz desde a VOZ-04
            await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
            await ws.send(json.dumps({"type": "conn.authenticate", "token": token}))
        auth = await recv_until(ws, "conn.authenticated", 10)
        sid = (auth or {}).get("session_id", "")
        emit("OK" if sid else "FALHA", "E1", f"handshake do widget: primeiro={ready.get('type')} autenticou={bool(sid)}")
        if not sid:
            return
        print(f"SID {sid}", flush=True)

        val = await wait_for(na_fila(r, sid), 15)
        emit("OK" if val else "FALHA", "E2", f"routing-engine enfileirou a sessao em {TENANT}:pool:{POOL}:queue={val}")

        ttl = await r.ttl(f"session:{sid}:ws_alive")
        emit("OK" if ttl > WATCHDOG else "FALHA", "E3",
             f"ws_alive TTL={ttl}s (watchdog do bridge a cada {WATCHDOG}s)")

        await ws.send(json.dumps({"type": "webrtc.message", "text": "voz04 texto do cliente"}))
        await ws.send(json.dumps({"type": "webrtc.message", "content": {"text": "formato antigo"}}))
        err = await recv_until(ws, "conn.error", 8)
        emit("OK" if (err or {}).get("code") == "bad_message" else "FALHA", "E5",
             f"formato antigo `content.text` recusado dito: {err}")
        await asyncio.sleep(4)          # o bridge consome a mensagem (E4 é lido pelo probe)

        await ws.send(json.dumps({"type": "webrtc.hangup"}))
        closed = await wait_for(lambda: r.exists(f"session:{sid}:closed"), 15)
        saiu = await wait_for(fora_da_fila(r, sid), 15)
        emit("OK" if (closed and saiu) else "FALHA", "E6",
             f"desligar: closed={bool(closed)}; saiu da fila={bool(saiu)}")


async def controle_do_instrumento(r: aioredis.Redis) -> None:
    prod = AIOKafkaProducer(bootstrap_servers=BROKERS)
    await prod.start()
    try:
        bom, ruim = "probe_voz04_k_" + uuid.uuid4().hex[:8], "probe_voz04_k_" + uuid.uuid4().hex[:8]
        started = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        pedido = lambda sid: contact_lifecycle.routing_request(
            session_id=sid, tenant_id=TENANT, customer_id="k-" + sid[-6:], channel="webrtc",
            pool_id=POOL, started_at=started, customer_participant_id=str(uuid.uuid4()))
        antigo = {k: v for k, v in pedido(ruim).items() if k not in ("started_at", "elapsed_ms")}
        antigo["type"] = "routing.request"
        await prod.send_and_wait("conversations.inbound", json.dumps(pedido(bom)).encode())
        await prod.send_and_wait("conversations.inbound", json.dumps(antigo).encode())
        v_bom = await wait_for(na_fila(r, bom), 15)
        await asyncio.sleep(3)
        v_ruim = await na_fila(r, ruim)()
        emit("OK" if (v_bom and not v_ruim) else "FALHA", "K",
             f"routing enfileira o formato novo ({v_bom!r}) e recusa o antigo sem started_at ({v_ruim!r})")
        # limpeza: fecha o contato do controle pelo mesmo evento que o gateway publica
        await prod.send_and_wait("conversations.events", json.dumps(ContactClosedEvent(
            contact_id="k-" + bom[-6:], session_id=bom, tenant_id=TENANT, channel="webrtc",
            reason="client_disconnect", started_at=started, pool_id=POOL,
            customer_id="k-" + bom[-6:], close_reason="customer_disconnect").model_dump()).encode())
        saiu = await wait_for(fora_da_fila(r, bom), 15)
        emit("OK" if saiu else "FALHA", "LIMPEZA", f"contato do controle {bom} fechado e fora da fila={bool(saiu)}")
    finally:
        await prod.stop()


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    try:
        await contato(r)
        await controle_do_instrumento(r)
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
