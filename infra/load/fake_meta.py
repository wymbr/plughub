"""
fake_meta.py — simulador da Graph API do WhatsApp para o teste de carga (PRD-02).

O gateway envia pela `PLUGHUB_WHATSAPP_GRAPH_API_URL` (default
`https://graph.facebook.com/v19.0`, `channel-gateway/config.py`). Apontada para cá,
cada envio é registrado por destinatário, e o driver (`whatsapp_load.py`) espera a
próxima mensagem do agente por long-poll — é assim que o tempo de resposta é medido
sem tocar a Meta.

Também injeta o que a Meta faz sob carga: `429` numa fração configurável dos envios e
latência artificial. É o que a `WHA-01` precisa para testar vazão e backoff.

Rotas da Graph API:
  POST /{phone_number_id}/messages   → {"messages":[{"id":"wamid.fake…"}]} ou 429
Rotas do harness:
  GET  /_harness/next?to=&after=&timeout=   próxima mensagem para `to` depois do índice `after`
  GET  /_harness/stats                      contagens e 429 injetados
  POST /_harness/config {rate_429, delay_ms}

Uso: python3 fake_meta.py --port 9100 [--rate-429 0.02] [--delay-ms 80]
"""
from __future__ import annotations

import argparse
import asyncio
import random
import time
import uuid
from collections import defaultdict

from aiohttp import web

STATE = {
    "rate_429": 0.0,
    "delay_ms": 0.0,
    "sent": 0,
    "injected_429": 0,
    "by_type": defaultdict(int),
}
OUTBOX: dict[str, list[dict]] = defaultdict(list)
WAITERS: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)


async def send_message(request: web.Request) -> web.Response:
    if STATE["delay_ms"]:
        await asyncio.sleep(STATE["delay_ms"] / 1000)
    if STATE["rate_429"] and random.random() < STATE["rate_429"]:
        STATE["injected_429"] += 1
        return web.json_response(
            {"error": {"message": "(#130429) Rate limit hit", "code": 130429}}, status=429)
    body = await request.json()
    to = str(body.get("to", ""))
    kind = body.get("type", "text")
    STATE["sent"] += 1
    STATE["by_type"][kind] += 1
    entry = {"at": time.time(), "type": kind, "body": body}
    cond = WAITERS[to]
    async with cond:
        OUTBOX[to].append(entry)
        cond.notify_all()
    return web.json_response({"messaging_product": "whatsapp",
                              "messages": [{"id": f"wamid.fake{uuid.uuid4().hex}"}]})


async def next_message(request: web.Request) -> web.Response:
    to = request.query.get("to", "")
    after = int(request.query.get("after", "0"))
    timeout = float(request.query.get("timeout", "30"))
    cond = WAITERS[to]
    async with cond:
        try:
            await asyncio.wait_for(cond.wait_for(lambda: len(OUTBOX[to]) > after), timeout)
        except asyncio.TimeoutError:
            return web.json_response({"index": after, "message": None})
        return web.json_response({"index": after + 1, "message": OUTBOX[to][after]})


async def stats(_request: web.Request) -> web.Response:
    return web.json_response({"sent": STATE["sent"], "injected_429": STATE["injected_429"],
                              "by_type": dict(STATE["by_type"]), "recipients": len(OUTBOX),
                              "rate_429": STATE["rate_429"], "delay_ms": STATE["delay_ms"]})


async def config(request: web.Request) -> web.Response:
    body = await request.json()
    for key in ("rate_429", "delay_ms"):
        if key in body:
            STATE[key] = float(body[key])
    return await stats(request)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--rate-429", type=float, default=0.0)
    ap.add_argument("--delay-ms", type=float, default=0.0)
    args = ap.parse_args()
    STATE["rate_429"], STATE["delay_ms"] = args.rate_429, args.delay_ms
    app = web.Application()
    app.router.add_get("/_harness/next", next_message)
    app.router.add_get("/_harness/stats", stats)
    app.router.add_post("/_harness/config", config)
    # A versão vem no path da URL base (…/v19.0); aceita com e sem ela.
    app.router.add_post("/{version}/{phone_id}/messages", send_message)
    app.router.add_post("/{phone_id}/messages", send_message)
    web.run_app(app, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
