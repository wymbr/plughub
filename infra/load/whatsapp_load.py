"""
whatsapp_load.py — carga de WHATSAPP pelo webhook de produção (PRD-02).

Cada worker é um número de telefone: manda a mensagem do cliente como a Meta manda
(`POST /webhooks/whatsapp`, corpo `entry[].changes[].value.messages[]`, assinado com
`X-Hub-Signature-256`) e espera a resposta do agente no `fake_meta.py`, que recebeu o
envio do gateway. Responde botão e lista pelo ID da opção, como o cliente faria.

Pré-requisitos no gateway sob teste:
  PLUGHUB_WHATSAPP_GRAPH_API_URL = http://<fake_meta>:9100/v19.0
  PLUGHUB_WHATSAPP_APP_SECRET    = o mesmo `--app-secret` daqui
  PLUGHUB_WHATSAPP_PHONE_NUMBER_ID = o POOL que atende (hoje é assim — ver PID-22)
  PLUGHUB_WHATSAPP_ACCESS_TOKEN  = qualquer valor (o simulador não confere)

⚠️ Cada mensagem é mandada com um `wamid` único. Para medir a deduplicação (WHA-01),
`--duplicate-rate` reenvia a MESMA entrega — como a Meta faz — e o relatório conta
quantas respostas vieram a mais.

Métricas (ms): webhook_post, first_response, turn_response. Contadores: sessions_started,
webhook_status:<código>, no_first_response, turns, duplicates_sent.

Uso:
  python3 whatsapp_load.py --gateway http://GATEWAY:8010 --fake-meta http://FAKE:9100 \\
      --app-secret "$SECRET" --concurrency 2000 --ramp-s 300 --duration 1800 --report wa.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid

import aiohttp

from harness_common import Metrics, meta_signature, ramp_delay, write_report


def _now_ms() -> float:
    return time.perf_counter() * 1000


def _inbound(phone: str, message: dict) -> dict:
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "load", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "contacts": [{"wa_id": phone, "profile": {"name": "Carga"}}],
                "messages": [{"from": phone, "id": f"wamid.load{uuid.uuid4().hex}",
                              "timestamp": str(int(time.time())), **message}]}}]}]}


async def post_webhook(http: aiohttp.ClientSession, args, m: Metrics, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json",
               "X-Hub-Signature-256": meta_signature(raw, args.app_secret)}
    copies = 2 if random.random() < args.duplicate_rate else 1
    for _ in range(copies):
        t0 = _now_ms()
        try:
            async with http.post(f"{args.gateway}/webhooks/whatsapp", data=raw, headers=headers) as r:
                m.count(f"webhook_status:{r.status}")
        except Exception as exc:  # noqa: BLE001
            m.count(f"error:webhook:{type(exc).__name__}")
            return
        m.timing("webhook_post", _now_ms() - t0)
    if copies == 2:
        m.count("duplicates_sent")


def _reply_for(body: dict, default_text: str) -> dict:
    """Resposta do cliente ao que o agente mandou: id de botão/lista, ou texto."""
    if body.get("type") == "interactive":
        inter = body.get("interactive", {})
        action = inter.get("action", {})
        if inter.get("type") == "button":
            ids = [b.get("reply", {}).get("id") for b in action.get("buttons", [])]
            ids = [i for i in ids if i]
            if ids:
                return {"type": "interactive", "interactive": {
                    "type": "button_reply", "button_reply": {"id": ids[0], "title": ids[0]}}}
        if inter.get("type") == "list":
            ids = [row.get("id") for sec in action.get("sections", []) for row in sec.get("rows", [])]
            ids = [i for i in ids if i]
            if ids:
                return {"type": "interactive", "interactive": {
                    "type": "list_reply", "list_reply": {"id": ids[0], "title": ids[0]}}}
    return {"type": "text", "text": {"body": default_text}}


async def one_session(http, args, m: Metrics, phone: str) -> None:
    m.count("sessions_started")
    m.enter()
    started = time.monotonic()
    index = 0
    try:
        await post_webhook(http, args, m, _inbound(phone, {"type": "text", "text": {"body": args.opening}}))
        pending = _now_ms()
        first = True
        turns = 0
        last: dict | None = None
        while turns < args.max_turns and time.monotonic() - started < args.max_session_s:
            wait = args.idle_reply_s if last else args.response_timeout_s
            async with http.get(f"{args.fake_meta}/_harness/next",
                                params={"to": phone, "after": str(index), "timeout": str(wait)}) as r:
                data = await r.json()
            msg = data.get("message")
            if msg is None:
                if last is None:
                    m.count("no_first_response" if first else "error:turn_no_response")
                    return
                # Agente parou de falar: responder à última mensagem dele.
                await asyncio.sleep(random.uniform(0, args.think_s))
                await post_webhook(http, args, m, _inbound(phone, _reply_for(last["body"], args.default_text)))
                pending, last = _now_ms(), None
                turns += 1
                m.count("turns")
                continue
            index = data["index"]
            if pending is not None:
                m.timing("first_response" if first else "turn_response", _now_ms() - pending)
                first, pending = False, None
            last = msg
        m.count("session_ended:driver_limit")
    except Exception as exc:  # noqa: BLE001
        m.count(f"error:{type(exc).__name__}")
    finally:
        m.leave()
        m.timing("session_duration", (time.monotonic() - started) * 1000)


async def worker(idx: int, args, http, m: Metrics, deadline: float) -> None:
    await asyncio.sleep(ramp_delay(idx, args.concurrency, args.ramp_s))
    seq = 0
    while time.monotonic() < deadline:
        # Único por sessão: número repetido cairia na sessão ANTERIOR do mesmo telefone
        # (`channel:whatsapp:{from}:session`) e mediria outra coisa.
        phone = f"55{args.phone_prefix}{args.run_tag}{idx:05d}{seq:05d}"
        await one_session(http, args, m, phone)
        seq += 1
        if args.once:
            return
        await asyncio.sleep(args.pause_between_s)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--fake-meta", required=True)
    ap.add_argument("--app-secret", required=True)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--ramp-s", type=float, default=30)
    ap.add_argument("--duration", type=float, default=300)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--opening", default="oi")
    ap.add_argument("--default-text", default="sim")
    ap.add_argument("--phone-prefix", default="119", help="DDD + início do número sintético")
    ap.add_argument("--think-s", type=float, default=3.0)
    ap.add_argument("--idle-reply-s", type=float, default=4.0)
    ap.add_argument("--response-timeout-s", type=float, default=60.0)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--max-session-s", type=float, default=600)
    ap.add_argument("--pause-between-s", type=float, default=1.0)
    ap.add_argument("--duplicate-rate", type=float, default=0.0,
                    help="fração de entregas reenviadas, como a Meta faz (mede a WHA-01)")
    ap.add_argument("--report")
    args = ap.parse_args()
    args.run_tag = f"{random.randint(0, 999):03d}"   # rodadas seguidas não reusam telefone

    m = Metrics()
    deadline = time.monotonic() + args.ramp_s + args.duration
    conn = aiohttp.TCPConnector(limit=0)       # sem teto: o gerador não pode ser o gargalo
    timeout = aiohttp.ClientTimeout(total=args.response_timeout_s + 30)
    async with aiohttp.ClientSession(connector=conn, timeout=timeout) as http:
        await asyncio.gather(*(worker(i, args, http, m, deadline) for i in range(args.concurrency)))
        try:
            async with http.get(f"{args.fake_meta}/_harness/stats") as r:
                meta_stats = await r.json()
        except Exception as exc:  # noqa: BLE001
            meta_stats = {"error": str(exc)}
    write_report(args.report, {"driver": "whatsapp", "args": vars(args), "fake_meta": meta_stats,
                               **m.summary()})


if __name__ == "__main__":
    asyncio.run(main())
