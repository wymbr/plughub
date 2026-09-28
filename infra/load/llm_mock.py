"""
llm_mock.py — provedor de LLM simulado para as rodadas grandes do teste de carga (PRD-02).

Por quê: 3.000 sessões com IA custam dinheiro e estouram a cota do provedor antes de
medir a plataforma. O ai-gateway usa o SDK da Anthropic (`messages.create`), que lê
`ANTHROPIC_BASE_URL` do ambiente — apontada para cá, nenhum outro ajuste é preciso.
Uma rodada MENOR com o provedor real continua obrigatória: é ela que valida a cota
(`producao-v1-dimensionamento.md` § 4).

Responde `POST /v1/messages` como a Anthropic:
  · com `tool_choice: {type: tool, name}` (é o `submit_result` do step `reason`,
    `ai-gateway/reason.py`), devolve um bloco `tool_use` cujo `input` é montado a partir
    do `input_schema` da ferramenta — enum pega o primeiro valor, número o mínimo,
    objeto preenche os campos obrigatórios;
  · sem ferramenta forçada, devolve texto curto.
Latência log-normal em torno de `--latency-ms`, e `usage` com tokens estimados.

`--overrides` aceita JSON [{"match": "<trecho do system ou da última mensagem>",
"input": {...}}] para fixar a saída de um step cujo fluxo depende do valor.

Rotas do harness: GET /_harness/stats.

Uso: python3 llm_mock.py --port 9200 --latency-ms 900 [--overrides overrides.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
import uuid
from collections import Counter

from aiohttp import web

CFG = {"latency_ms": 900.0, "sigma": 0.35, "overrides": []}
STATS: Counter = Counter()
LAT: list[float] = []


def fill(schema: dict, depth: int = 0):
    """Valor mínimo válido para um JSON Schema — suficiente para o fluxo andar."""
    if depth > 6 or not isinstance(schema, dict):
        return None
    for key in ("anyOf", "oneOf", "allOf"):
        if schema.get(key):
            return fill(schema[key][0], depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required") or list(props)
        return {k: fill(props[k], depth + 1) for k in required if k in props}
    if t == "array":
        item = fill(schema.get("items", {"type": "string"}), depth + 1)
        return [item] * max(1, int(schema.get("minItems", 1)))
    if t in ("integer", "number"):
        # 90% do intervalo, não o mínimo: `confidence` com minimum 0 sairia 0.0 e
        # jogaria todo fluxo no ramo de baixa confiança — mediria só o fallback.
        lo = float(schema.get("minimum", 0))
        hi = float(schema.get("maximum", lo + 1))
        val = lo + (hi - lo) * 0.9
        return int(round(val)) if t == "integer" else round(val, 3)
    if t == "boolean":
        return False
    return "ok"


def _text_of(body: dict) -> str:
    parts = [body.get("system") if isinstance(body.get("system"), str) else json.dumps(body.get("system", ""))]
    msgs = body.get("messages") or []
    if msgs:
        parts.append(json.dumps(msgs[-1].get("content", ""), ensure_ascii=False))
    return " ".join(p for p in parts if p)


async def messages(request: web.Request) -> web.Response:
    body = await request.json()
    delay = random.lognormvariate(math.log(CFG["latency_ms"]), CFG["sigma"])
    await asyncio.sleep(delay / 1000)
    LAT.append(delay)
    STATS["requests"] += 1
    in_tokens = max(1, len(json.dumps(body)) // 4)
    choice = body.get("tool_choice") or {}
    tools = {t.get("name"): t for t in body.get("tools") or []}
    if choice.get("type") == "tool" and choice.get("name") in tools:
        name = choice["name"]
        text = _text_of(body)
        forced = next((o["input"] for o in CFG["overrides"] if o.get("match", "") in text), None)
        tool_input = forced if forced is not None else fill(tools[name].get("input_schema", {}))
        STATS["tool_use"] += 1
        content = [{"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:20]}",
                    "name": name, "input": tool_input}]
        stop = "tool_use"
    else:
        STATS["text"] += 1
        content = [{"type": "text", "text": "Certo, posso ajudar com isso."}]
        stop = "end_turn"
    return web.json_response({
        "id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant",
        "model": body.get("model", "mock"), "content": content, "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": in_tokens, "output_tokens": 40},
    })


async def stats(_request: web.Request) -> web.Response:
    s = sorted(LAT)
    pct = (lambda p: round(s[int((len(s) - 1) * p)], 1) if s else None)
    return web.json_response({**STATS, "latency_ms": {"n": len(s), "p50": pct(.5), "p95": pct(.95)},
                              "config": {k: v for k, v in CFG.items() if k != "overrides"},
                              "overrides": len(CFG["overrides"]), "uptime_s": round(time.time() - START, 1)})


START = time.time()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9200)
    ap.add_argument("--latency-ms", type=float, default=900.0)
    ap.add_argument("--sigma", type=float, default=0.35, help="dispersão log-normal da latência")
    ap.add_argument("--overrides")
    args = ap.parse_args()
    CFG["latency_ms"], CFG["sigma"] = args.latency_ms, args.sigma
    if args.overrides:
        with open(args.overrides, encoding="utf-8") as fh:
            CFG["overrides"] = json.load(fh)
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.router.add_post("/v1/messages", messages)
    app.router.add_get("/_harness/stats", stats)
    web.run_app(app, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
