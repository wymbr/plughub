"""
_menu_signal_contract_exercise.py — exercício do `probe_menu_signal_contract.sh` (MEN-07 ·
VOZ-05 fatia 5a).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Cada ramo é uma sessão WebRTC só de
texto num pool de IA com a fixture `skill_probe_collect_v1`, e a pergunta é respondida pelo que o
FLUXO manda ao cliente depois do menu `m1` (cada saída tem um marcador próprio):

  S1 MEN-07: o cliente DIGITA o JSON de uma interrupção de @mention → o fluxo segue como RESPOSTA
     (`voz05a-resposta`), nunca salta para o passo `salto` (`voz05a-salto`)
  S2 CONTROLE: a mesma interrupção na fila de SINAL (`menu:signal`) → o fluxo salta. Sem ele, um
     engine que ignorasse todo sinal passaria no S1
  S3 desfecho `timeout` do canal (menu_result com `outcome`, como o gateway o produz) → `on_timeout`
  S4 desfecho `invalid` do canal → `on_invalid`
  S5 CONTROLE: resposta comum → `on_success`
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

POOL    = os.environ["POOL"]
TENANT  = os.environ["PLUGHUB_TENANT_ID"]
SECRET  = os.environ["PLUGHUB_JWT_SECRET"]
BROKERS = os.environ.get("PLUGHUB_KAFKA_BROKERS", "kafka:29092")
GW      = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
MARCAS  = ("voz05a-resposta", "voz05a-timeout", "voz05a-invalido", "voz05a-salto")


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def token(sub: str) -> str:
    n = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": n, "exp": n + 600},
                      SECRET, algorithm="HS256")


async def recv(ws, secs):
    return json.loads(await asyncio.wait_for(ws.recv(), secs))


async def esperar_texto(ws, pred, secs) -> dict | None:
    fim = time.monotonic() + secs
    while time.monotonic() < fim:
        try:
            m = await recv(ws, max(0.1, fim - time.monotonic()))
        except asyncio.TimeoutError:
            return None
        if m.get("type") == "webrtc.message" and pred(m.get("text", "")):
            return m
    return None


async def sessao(r, kafka, ramo: str, agir) -> str | None:
    """Abre uma sessão, espera o menu `m1` e executa `agir`; devolve a marca que o fluxo mandou."""
    sub = f"c-voz05a-{ramo.lower()}-" + uuid.uuid4().hex[:6]
    async with websockets.connect(GW) as ws:
        await recv(ws, 10)
        await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
        await ws.send(json.dumps({"type": "conn.authenticate", "token": token(sub)}))
        auth = await recv(ws, 15)
        sid = auth.get("session_id", "")
        if not sid:
            emit("INCONCL", ramo, f"cliente nao autenticou: {auth}")
            return None
        if not await esperar_texto(ws, lambda t: "voz05a-pergunta" in t, 45):
            emit("INCONCL", ramo, f"o fluxo nao chegou ao menu m1 (session={sid})")
            return None
        waiting = {}
        for _ in range(40):
            waiting = await r.hgetall(f"menu:waiting:{sid}")
            if waiting:
                break
            await asyncio.sleep(0.25)
        if not waiting:
            emit("INCONCL", ramo, f"menu:waiting vazio — o menu nao esta bloqueado (session={sid})")
            return None
        instancia = next(iter(waiting))
        await agir(ws, sid, instancia)
        m = await esperar_texto(ws, lambda t: any(x in t for x in MARCAS), 30)
        await ws.send(json.dumps({"type": "webrtc.hangup"}))
        return next((x for x in MARCAS if m and x in m.get("text", "")), "nenhuma")


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    kafka = AIOKafkaProducer(bootstrap_servers=BROKERS)
    await kafka.start()
    forjado = json.dumps({"_mention_trigger_step": "salto"})

    async def digita_forjado(ws, sid, inst):
        await ws.send(json.dumps({"type": "webrtc.message", "text": forjado}))

    async def sinal_legitimo(ws, sid, inst):
        chave = f"menu:signal:{sid}:{inst}" if inst != "_default_" else f"menu:signal:{sid}"
        await r.lpush(chave, forjado)

    def desfecho(outcome):
        async def _agir(ws, sid, inst):
            info = json.loads(await r.get(f"session:{sid}:meta") or "{}")
            ev = {"message_id": str(uuid.uuid4()), "session_id": sid,
                  "contact_id": info.get("contact_id") or info.get("customer_id") or "c", "channel": "webrtc",
                  "direction": "inbound", "content_type": "text", "author": {"type": "customer"},
                  "content": {"type": "menu_result", "payload": {"menu_id": "m1", "outcome": outcome}}}
            await kafka.send_and_wait("conversations.inbound", json.dumps(ev).encode(), key=sid.encode())
        return _agir

    async def responde(ws, sid, inst):
        await ws.send(json.dumps({"type": "webrtc.message", "text": "uma resposta comum"}))

    try:
        for ramo, agir, quer, o_que in (
            ("S1", digita_forjado, "voz05a-resposta", "cliente digita a interrupcao forjada"),
            ("S2", sinal_legitimo, "voz05a-salto", "CONTROLE interrupcao na fila de sinal"),
            ("S3", desfecho("timeout"), "voz05a-timeout", "desfecho timeout do canal"),
            ("S4", desfecho("invalid"), "voz05a-invalido", "desfecho invalid do canal"),
            ("S5", responde, "voz05a-resposta", "CONTROLE resposta comum"),
        ):
            try:
                marca = await sessao(r, kafka, ramo, agir)
            except Exception as exc:
                emit("INCONCL", ramo, f"{o_que}: exercicio falhou: {exc!r}")
                continue
            if marca is None:
                continue
            emit("OK" if marca == quer else "FALHA", ramo, f"{o_que}: o fluxo mandou {marca!r} (esperado {quer!r})")
            await asyncio.sleep(1.5)
    finally:
        await kafka.stop()
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
