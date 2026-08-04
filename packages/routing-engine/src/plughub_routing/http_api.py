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
            tenant_id        = body["tenant_id"],
            pool_id          = body["pool_id"],
            session_id       = body["session_id"],
            instance_id      = body["instance_id"],
            conference_id    = body.get("conference_id", ""),
            # Camada B — identidade do claimant p/ casar com assigned_to (ramal).
            # Ausente → o engine deriva de instance_id (`human-{userId}`).
            claimant_user_id = body.get("claimant_user_id") or None,
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
            # Fase C (D3) — só a QUEDA reserva o item ao dono anterior. Ausente =
            # False = desistência deliberada (botão "Return to queue"), que é o
            # comportamento anterior: chamador que não conhece o campo não muda.
            reserve_to_previous = bool(body.get("reserve_to_previous", False)),
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

    async def expire(request: web.Request) -> web.Response:
        # I5 — encerra o item de trabalho (prazo vencido / supervisor). Idempotente.
        # Não exige `instance_id`: o caso que motiva o endpoint é justamente o item
        # que NINGUÉM reivindicou. Quando houve claim, o dono sai da lease.
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await _read_body(request)
        if body is None:
            return web.json_response({"error": "bad_json"}, status=400)
        req = ("tenant_id", "pool_id", "session_id")
        missing = [f for f in req if not body.get(f)]
        if missing:
            return web.json_response({"error": "missing_fields", "fields": missing}, status=400)
        result = await router.work_task_expire(
            tenant_id  = body["tenant_id"],
            pool_id    = body["pool_id"],
            session_id = body["session_id"],
            reason     = body.get("reason") or "expired",
        )
        return web.json_response(result)

    async def capacity(request: web.Request) -> web.Response:
        """F4b — rollup de capacidade, opcionalmente restrito a um DOMÍNIO de pools.

        Existe porque a deduplicação **não projeta**: depois de agregar, não se sabe
        mais qual instância pertencia a qual pool, então o rollup do tenant não pode
        ser recortado para um subconjunto. A conta restrita tem de ser refeita — e é
        refeita AQUI, com o mesmo `compute_tenant_capacity`, para que a regra continue
        existindo num lugar só. Reimplementá-la no agent-registry (que é quem tem o
        `accessible_pools`) criaria dois números que divergem no primeiro ajuste.

        `pools` ausente → tenant inteiro (lê a chave publicada, sem recomputar).
        `pools` presente → recompute escopado, sem cache no engine: o chamador é quem
        sabe por quanto tempo pode segurar a resposta.
        """
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        tenant_id = request.query.get("tenant_id") or ""
        if not tenant_id:
            return web.json_response({"error": "missing_fields", "fields": ["tenant_id"]}, status=400)
        raw_pools = request.query.get("pools")
        if raw_pools is None:
            roll = await router._instances.get_tenant_capacity(tenant_id)
            if roll is None:
                return web.json_response({"capacity": None, "reason": "no_rollup"})
            return web.json_response({"capacity": roll, "reason": None})
        pools = [p for p in raw_pools.split(",") if p.strip()]
        if not pools:
            # Domínio vazio ≠ domínio irrestrito. Devolver o tenant aqui vazaria
            # capacidade para um chamador que declarou não alcançar pool nenhum.
            return web.json_response({"capacity": None, "reason": "empty_scope"})
        roll = await router._instances.compute_tenant_capacity(tenant_id, only_pools=pools)
        if not roll:
            return web.json_response({"capacity": None, "reason": "no_rollup"})
        return web.json_response({"capacity": roll, "reason": None})

    app.router.add_get("/health", health)
    app.router.add_get("/v1/capacity", capacity)
    app.router.add_post("/v1/work_queue/claim", claim)
    app.router.add_post("/v1/work_queue/release", release)
    app.router.add_post("/v1/work_queue/holder", holder)
    app.router.add_post("/v1/work_queue/expire", expire)
    return app


async def start_http_api(router, port: int) -> web.AppRunner:
    """Inicia o servidor HTTP (background). Retorna o AppRunner para cleanup no shutdown."""
    runner = web.AppRunner(build_app(router))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(
        "Routing HTTP API on :%d — POST /v1/work_queue/{claim,release,holder,expire}", port
    )
    return runner
