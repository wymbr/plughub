"""
audit.py — LGPD Audit endpoints (Arc LGPD Fase 1)

Routes (all prefixed /v1/audit):
  GET /v1/audit/sessions/{session_id}/messages
      Returns masked message content for a session.
      Auth: Bearer JWT (auth-api HS256) — ABAC gate: module_config.audit.sessions
      Side-effect: writes an immutable row to audit_access_log (ClickHouse).

  GET /v1/audit/mcp-calls
      Returns MCP tool call audit records for a tenant.
      Auth: Bearer JWT (auth-api HS256) — ABAC gate: module_config.audit.mcp_calls
      Query: tenant_id, limit (default 100), masked_only (bool)

Notes:
  - In analytics_open_access=True mode all checks are bypassed (dev/demo only).
  - Tenant isolation is enforced: operators can only see their own tenant.
  - MCP call data is sourced from session_timeline (event_type = 'mcp.tool_call').
    The payload JSON carries server_name, tool_name, allowed, injection_detected,
    duration_ms — packed there by parse_mcp_audit_event() in models.py.
  - masked_input_fields is not currently persisted; all rows return [].
    This will be populated when dual-write to mcp_audit_log is implemented (Fase 2).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from .pool_auth import PoolPrincipal, optional_pool_principal

logger = logging.getLogger("plughub.analytics.audit")

router = APIRouter(prefix="/v1/audit", tags=["audit"])


# ══════════════════════════════════════════════════════════════════════════════
# Gate ABAC + registro de acesso
# ══════════════════════════════════════════════════════════════════════════════
#
# Até 2026-08-22 estas duas coisas existiam **só no docstring**. O de cima dizia
# *"ABAC gate: module_config.audit.sessions"* e *"Side-effect: writes an immutable
# row to audit_access_log"*; o código não fazia nem uma nem outra, e a tela de
# Auditoria mostra ao operador um banner afirmando que todo acesso fica
# registrado. Afirmação em prosa sem produtor é a mesma família do comentário que
# prometia ordenação de Kafka sem chave (§ Postura de Engenharia).
#
# O que a medição mostrou, e por que o `401` escondia o defeito
# (`infra/test/probe_audit_surface.sh`):
#   · sem header            → 200
#   · com token INVÁLIDO    → 401
# O 401 vem de `optional_pool_principal`, que confere a ASSINATURA do JWT quando
# há header. Isso é autenticação; autorização nunca acontecia. Um token válido de
# QUALQUER usuário do tenant — operador, business, quem for — lia mensagem de
# cliente e trilha de chamada MCP sem ter o módulo `audit` no seu `module_config`.
# O ambiente demo roda `PLUGHUB_ANALYTICS_OPEN_ACCESS=true`, então o 200 sem
# header é bypass declarado; o buraco é o outro, e ele NÃO depende dessa flag.

_ACCESS_LEVELS = ["none", "read_only", "write_only", "read_write"]


def _has_abac(module_config: dict, module: str, field: str) -> bool:
    """
    `module_config[module][field].access` ≥ read_only.

    Mesma hierarquia do `permissions.ts` da UI (`ACCESS_LEVELS`), e a paridade é
    obrigatória: gate de tela que discorda de gate de API é gate nenhum.
    `write_only` conta porque está acima de `read_only` na hierarquia declarada —
    não inventar uma segunda ordem aqui.
    """
    field_cfg = ((module_config or {}).get(module) or {}).get(field)
    if not isinstance(field_cfg, dict):
        return False
    access = field_cfg.get("access") or "none"
    if access not in _ACCESS_LEVELS:
        return False
    return _ACCESS_LEVELS.index(access) >= _ACCESS_LEVELS.index("read_only")


def _audit_actor(request: Request) -> tuple[str, str, dict]:
    """
    Devolve `(sub, kind, module_config)` do Bearer, sem levantar.

    `optional_pool_principal` já roda como dependência e devolve o `sub`, mas NÃO
    carrega `module_config` — decodificar aqui evita alargar o `PoolPrincipal`,
    que é lido por dezenas de rotas que não têm nada com auditoria.
    """
    # ⚠️ **PyJWT (`import jwt`), NÃO `python-jose`.** Os dois convivem no repo — o
    # auth-api usa `from jose import jwt`, a analytics-api usa PyJWT, e `jose` nem
    # está instalado neste container. A v1 desta função copiou o import do
    # auth-api; como ele é LOCAL e o demo roda com `open_access=true`, esta função
    # nunca era chamada e o `ImportError` só apareceria com o bypass desligado —
    # isto é, só em produção, na primeira vez que o gate fosse de fato exercido.
    # Foi o teste unitário que o pegou, justamente por não passar pelo bypass.
    import jwt

    from .config import get_settings

    settings = get_settings()
    header = request.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token or not settings.auth_jwt_secret:
        return "", "anonymous", {}
    try:
        claims = jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])
    except Exception:
        return "", "anonymous", {}
    return claims.get("sub") or "", "user", claims.get("module_config") or {}


class AuditDenied(Exception):
    """Recusa nomeada — o handler a converte em 403 e REGISTRA a tentativa."""


def _check_audit_access(request: Request, field: str) -> tuple[str, str]:
    """
    Portão de `/v1/audit/*`. Devolve `(actor_sub, actor_kind)` ou levanta `AuditDenied`.

    Precedência, e cada ramo é declarado:
      1. `analytics_open_access` → LIBERA, com ator `open_access`. É bypass de
         demo (`config.py:75`, "NEVER enable in production") e fica NOMEADO na
         trilha, para ninguém confundir acesso autenticado com acesso de demo.
      2. sem `auth_jwt_secret` → **RECUSA**. Aqui a postura é oposta à do
         `pool_auth`, que degrada aberto quando não há como verificar o token: lá
         o efeito é escopo de leitura operacional, aqui é dado pessoal sob LGPD.
         Identidade não tem fallback — sem como provar quem é, não se entrega.
      3. `module_config.audit.<field>` ≥ read_only → LIBERA.
      4. caso contrário → RECUSA.
    """
    from .config import get_settings

    if get_settings().analytics_open_access:
        return "", "open_access"

    actor_sub, actor_kind, module_config = _audit_actor(request)
    if actor_kind == "anonymous":
        raise AuditDenied("credencial ausente ou não verificável")
    if not _has_abac(module_config, "audit", field):
        raise AuditDenied(f"sem module_config.audit.{field}")
    return actor_sub, actor_kind


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(request: Request):
    return request.app.state.store


async def _record_access(
    request: Request, *, actor_sub: str, actor_kind: str, endpoint: str,
    target_kind: str, target_id: str, result: str, row_count: int,
) -> None:
    """
    Escreve a linha imutável em `audit_access_log`.

    Nunca derruba a resposta: a falha de gravação vira ERROR com prefixo estável.
    Mas ela é ERROR, não `pass` — uma trilha de auditoria que some em silêncio é
    pior que não ter trilha, porque a tela continua prometendo que ela existe.
    """
    import uuid
    from datetime import datetime as _dt, timezone as _tz

    try:
        await _store(request).insert_audit_access_log({
            "access_id":   str(uuid.uuid4()),
            "tenant_id":   request.query_params.get("tenant_id") or "",
            "actor_sub":   actor_sub,
            "actor_kind":  actor_kind,
            "endpoint":    endpoint,
            "target_kind": target_kind,
            "target_id":   target_id,
            "result":      result,
            "row_count":   row_count,
            "accessed_at": _dt.now(_tz.utc),
        })
    except Exception as exc:
        logger.error(
            "[audit_access_log] NÃO registrado endpoint=%s actor=%s — %s: %s",
            endpoint, actor_sub or actor_kind, type(exc).__name__, exc,
        )


def _isoformat(ts: Any) -> str:
    """Convert a ClickHouse DateTime to ISO8601 string."""
    if ts is None:
        return ""
    from datetime import datetime as _dt, timezone as _tz
    if isinstance(ts, _dt):
        return ts.replace(tzinfo=_tz.utc).isoformat()
    return str(ts)


# ── GET /v1/audit/sessions/{session_id}/messages ──────────────────────────────

def _fetch_audit_messages(client: Any, db: str, tenant_id: str, session_id: str) -> list[dict]:
    """Query the messages table for a session — used for LGPD audit trail."""
    result = client.query(
        f"""
        SELECT
            message_id,
            author_id,
            author_role,
            content_type,
            visibility,
            content,
            timestamp
        FROM {db}.messages FINAL
        WHERE tenant_id  = {{tenant_id:String}}
          AND session_id = {{session_id:String}}
        ORDER BY timestamp ASC
        LIMIT 2000
        """,
        parameters={"tenant_id": tenant_id, "session_id": session_id},
    )
    rows = []
    for r in result.result_rows:
        msg_id, author_id, author_role, content_type, visibility, content_raw, ts = r
        # Normalise content — stored as JSON string in some events
        content_str = ""
        if content_raw:
            try:
                parsed = json.loads(content_raw)
                if isinstance(parsed, str):
                    content_str = parsed
                elif isinstance(parsed, dict) and "text" in parsed:
                    content_str = str(parsed["text"])
                else:
                    content_str = content_raw
            except Exception:
                content_str = content_raw
        rows.append({
            "stream_entry_id": str(msg_id),
            "event_type":      str(content_type or "message"),
            "author_id":       str(author_id) if author_id else None,
            "author_role":     str(author_role) if author_role else None,
            "content":         content_str,
            "created_at":      _isoformat(ts),
        })
    return rows


@router.get("/sessions/{session_id}/messages")
async def audit_session_messages(
    session_id:  str,
    request:     Request,
    tenant_id:   str = Query("tenant_demo"),
    principal:   PoolPrincipal = Depends(optional_pool_principal),
):
    """
    Returns masked message content for a session for LGPD audit purposes.
    Gate: module_config.audit.sessions (ABAC) or analytics_open_access.
    Side-effect: grava uma linha imutável em audit_access_log — inclusive na recusa.
    """
    try:
        actor_sub, actor_kind = _check_audit_access(request, "sessions")
    except AuditDenied as denied:
        await _record_access(
            request, actor_sub="", actor_kind="anonymous",
            endpoint="audit.sessions.messages", target_kind="session",
            target_id=session_id, result="denied", row_count=0,
        )
        return JSONResponse(status_code=403, content={"detail": str(denied)})

    # Tenant isolation
    effective_tenant = principal.tenant_id or tenant_id
    store = _store(request)
    try:
        messages = await asyncio.to_thread(
            _fetch_audit_messages,
            store.new_client(), store._database, effective_tenant, session_id,
        )
    except Exception as exc:
        logger.error("audit_session_messages error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    await _record_access(
        request, actor_sub=actor_sub, actor_kind=actor_kind,
        endpoint="audit.sessions.messages", target_kind="session",
        target_id=session_id, result="ok", row_count=len(messages),
    )
    return JSONResponse(content={"session_id": session_id, "messages": messages})


# ── GET /v1/audit/mcp-calls ───────────────────────────────────────────────────

def _fetch_mcp_calls(
    client:      Any,
    db:          str,
    tenant_id:   str,
    limit:       int,
    masked_only: bool,
    session_id:  str | None = None,
) -> list[dict]:
    """
    Query session_timeline for mcp.tool_call events, extract audit fields from
    the packed JSON payload. Returns records matching the McpCall interface.

    When session_id is provided, results are scoped to that session — this is the
    path used by the evaluator (R5: tool_trace) to fetch only the calls of the
    session under evaluation, in chronological order (ASC).

    Note: masked_input_fields is not yet persisted (Fase 2 pending).
    All returned rows carry masked_input_fields=[].
    """
    params: dict[str, Any] = {"tenant_id": tenant_id, "limit": limit}
    session_filter = ""
    # When scoped to a session, order ASC so the evaluator reads the tool calls in
    # the order they happened (trajectory evidence); otherwise newest-first.
    order_clause = "ORDER BY timestamp DESC"
    if session_id:
        session_filter = "AND session_id = {session_id:String}"
        params["session_id"] = session_id
        order_clause = "ORDER BY timestamp ASC"

    result = client.query(
        f"""
        SELECT
            event_id,
            session_id,
            actor_id,
            payload,
            timestamp
        FROM {db}.session_timeline FINAL
        WHERE tenant_id  = {{tenant_id:String}}
          AND event_type = 'mcp.tool_call'
          {session_filter}
        {order_clause}
        LIMIT {{limit:UInt32}}
        """,
        parameters=params,
    )

    rows = []
    for r in result.result_rows:
        event_id, session_id, actor_id, payload_raw, ts = r
        try:
            p = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            p = {}

        masked_fields: list[str] = p.get("masked_input_fields") or []
        if masked_only and not masked_fields:
            continue

        rows.append({
            "event_id":            str(event_id),
            "server_name":         p.get("server_name") or "",
            "tool_name":           p.get("tool_name") or "",
            "allowed":             bool(p.get("allowed", True)),
            "injection_detected":  bool(p.get("injection_detected", False)),
            "masked_input_fields": masked_fields,
            "duration_ms":         p.get("duration_ms") or 0,
            "tenant_id":           tenant_id,
            "session_id":          str(session_id) if session_id else None,
            "created_at":          _isoformat(ts),
        })
    return rows


@router.get("/mcp-calls")
async def audit_mcp_calls(
    request:     Request,
    tenant_id:   str  = Query("tenant_demo"),
    session_id:  str | None = Query(None, description="Scope to one session (R5 tool_trace)."),
    limit:       int  = Query(100, ge=1, le=1000),
    masked_only: bool = Query(False),
    principal:   PoolPrincipal = Depends(optional_pool_principal),
):
    """
    Returns MCP tool call audit records for a tenant.
    Gate: module_config.audit.mcp_calls (ABAC) or analytics_open_access.
    Sourced from session_timeline (event_type = 'mcp.tool_call').

    When session_id is provided, results are scoped to that session and ordered
    chronologically — the path the evaluator uses to build tool_trace (R5).
    """
    try:
        actor_sub, actor_kind = _check_audit_access(request, "mcp_calls")
    except AuditDenied as denied:
        await _record_access(
            request, actor_sub="", actor_kind="anonymous",
            endpoint="audit.mcp_calls", target_kind="mcp_calls",
            target_id=session_id or "", result="denied", row_count=0,
        )
        return JSONResponse(status_code=403, content={"detail": str(denied)})

    effective_tenant = principal.tenant_id or tenant_id
    store = _store(request)
    try:
        calls = await asyncio.to_thread(
            _fetch_mcp_calls,
            store.new_client(), store._database,
            effective_tenant, limit, masked_only, session_id,
        )
    except Exception as exc:
        logger.error("audit_mcp_calls error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    await _record_access(
        request, actor_sub=actor_sub, actor_kind=actor_kind,
        endpoint="audit.mcp_calls", target_kind="mcp_calls",
        target_id=session_id or "", result="ok", row_count=len(calls),
    )
    return JSONResponse(content={"calls": calls, "total": len(calls)})
