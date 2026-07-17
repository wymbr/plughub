"""
http_api.py — Frente 1 F2a: API HTTP mínima do Routing Engine para as operações
de **dispatch pull** (claim/release).

Mantém a invariante "Routing Engine é o único árbitro": o `ZREM`/`claim_instance`/
`mark_busy`/lease/`conversations.routed` acontecem DENTRO do engine (em
`Router.work_task_claim`/`work_task_release`); a Console/mcp-server só **solicita**
via esta API. A LEITURA da fila (`work_queue_list`) é Redis-direta no mcp-server e
NÃO passa aqui.

Auth: `X-Admin-Token` quando `ROUTING_ADMIN_TOKEN` está setado; caso contrário
aberta (rede interna do compose).
"""
from __future__ import annotations

import logging
import os

from aiohttp import web

logger = logging.getLogger("plughub.routing.http_api")

_ADMIN_TOKEN = os.getenv("ROUTING_ADMIN_TOKEN", "")
_REQUIRED = ("tenant_id", "pool_id", "session_id", "instance_id")


def _authorized(request: web.Request) -> bool:
    if not _ADMIN_TOKEN:
        return True
    return request.headers.get("X-Admin-Token") == _ADMIN_TOKEN


async def _read_body(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def build_app(router) -> web.Application:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def claim(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await _read_body(request)
        if body is None:
            return web.json_response({"error": "bad_json"}, status=400)
        missing = [f for f in _REQUIRED if not body.get(f)]
        if missing:
            return web.json_response({"error": "missing_fields", "fields": missing}, status=400)
        result = await router.work_task_claim(
            tenant_id     = body["tenant_id"],
            pool_id       = body["pool_id"],
            session_id    = body["session_id"],
            instance_id   = body["instance_id"],
            conference_id = body.get("conference_id", ""),
        )
        return web.json_response(result)

    async def release(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await _read_body(request)
        if body is None:
            return web.json_response({"error": "bad_json"}, status=400)
        missing = [f for f in _REQUIRED if not body.get(f)]
        if missing:
            return web.json_response({"error": "missing_fields", "fields": missing}, status=400)
        result = await router.work_task_release(
            tenant_id   = body["tenant_id"],
            pool_id     = body["pool_id"],
            session_id  = body["session_id"],
            instance_id = body["instance_id"],
        )
        return web.json_response(result)

    async def holder(request: web.Request) -> web.Response:
        # A5 — leitura da lease (holder) p/ o check caller==claimant no ingress de aprovação.
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await _read_body(request)
        if body is None:
            return web.json_response({"error": "bad_json"}, status=400)
        req = ("tenant_id", "pool_id", "session_id")
        missing = [f for f in req if not body.get(f)]
        if missing:
            return web.json_response({"error": "missing_fields", "fields": missing}, status=400)
        result = await router.work_task_holder(
            tenant_id  = body["tenant_id"],
            pool_id    = body["pool_id"],
            session_id = body["session_id"],
        )
        return web.json_response(result)

    app.router.add_get("/health", health)
    app.router.add_post("/v1/work_queue/claim", claim)
    app.router.add_post("/v1/work_queue/release", release)
    app.router.add_post("/v1/work_queue/holder", holder)
    return app


async def start_http_api(router, port: int) -> web.AppRunner:
    """Inicia o servidor HTTP (background). Retorna o AppRunner para cleanup no shutdown."""
    runner = web.AppRunner(build_app(router))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Routing HTTP API on :%d — POST /v1/work_queue/{claim,release,holder}", port)
    return runner
