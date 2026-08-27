"""
supervisor.py
Supervisor intervention API — operator-console users join live sessions as
supervisors, send coaching/intervention messages, and leave cleanly.

Write path: direct XADD to session:{id}:stream using the same field format
that StreamSubscriber._map_event() expects.  Human operators are not AI agents,
so they bypass the MCP lifecycle (agent_login / agent_ready / agent_done).

Three endpoints:
  POST /supervisor/join     — join session, write participant_joined to stream
  POST /supervisor/message  — XADD message (agents_only or all visibility)
  POST /supervisor/leave    — write participant_left, clean up Redis state

Redis keys:
  supervisor:{session_id}:active  →  JSON { participant_id, tenant_id, operator_id, joined_at }
  TTL: 4h (same as other session-scoped data)

Audit: Kafka mcp.audit integration deferred to next iteration.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from .pool_auth import (
    PoolPrincipal,
    require_pool_principal,
    resolve_live_session_pools,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("plughub.analytics.supervisor")

router = APIRouter(prefix="/supervisor", tags=["supervisor"])

_SESSION_TTL = 14_400   # 4 h
_VIS_ALLOWED = {"agents_only", "all"}


# ── Pydantic models ────────────────────────────────────────────────────────────

class JoinRequest(BaseModel):
    tenant_id:   str
    session_id:  str
    operator_id: str = "operator"

class MessageRequest(BaseModel):
    tenant_id:      str
    session_id:     str
    participant_id: str
    text:           str
    visibility:     str = "agents_only"

class LeaveRequest(BaseModel):
    tenant_id:      str
    session_id:     str
    participant_id: str


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _xadd(redis, session_id: str, fields: dict[str, str]) -> str:
    """Append to session stream; return the Redis entry ID (string)."""
    key = f"session:{session_id}:stream"
    eid = await redis.xadd(key, fields)
    return eid if isinstance(eid, str) else eid.decode()


async def _get_state(redis, session_id: str) -> dict | None:
    raw = await redis.get(f"supervisor:{session_id}:active")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _authorize_live_session(
    redis, principal: PoolPrincipal, tenant_id: str, session_id: str,
) -> None:
    """
    Portao de ESCOPO DE POOL para uma sessao VIVA. Levanta 403 nomeado; nunca degrada.

    Decisao do dono (2026-08-26): **o admin respeita a ABAC como qualquer um; nao ha
    bypass por papel.** Por isso nada aqui le `roles` — o unico eixo e o escopo.

    Conjunto vazio de pools => RECUSA (`session_pools_undeterminable`). Nao e a mesma
    coisa que "nenhum pool bate": e "nao consegui determinar", e numa fronteira de
    ESCRITA em conferencia de cliente "nao sei" tem de reprovar. O irmao aberto
    (`optional_pool_principal`) existe para leitura de relatorio, nao para isto.
    """
    if principal.is_unrestricted:
        return

    pools = await resolve_live_session_pools(redis, tenant_id, session_id)
    if not pools:
        logger.warning(
            "supervisor: RECUSADO sub=%s session=%s — nao foi possivel determinar pool "
            "algum da sessao (meta sem `pool_id` e nenhuma instancia nos SETs de agente). "
            "Recusa deliberada: escopo indeterminado nao autoriza escrita.",
            principal.sub, session_id,
        )
        raise HTTPException(status_code=403, detail="session_pools_undeterminable")

    allowed = set(principal.accessible_pools or [])
    if not (pools & allowed):
        logger.warning(
            "supervisor: RECUSADO sub=%s session=%s — pools da sessao %s nao intersectam "
            "o escopo do chamador %s",
            principal.sub, session_id, sorted(pools), sorted(allowed),
        )
        raise HTTPException(status_code=403, detail="pool_scope_denied")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/join")
async def join_session(
    body: JoinRequest,
    request: Request,
    principal: PoolPrincipal = Depends(require_pool_principal),
) -> JSONResponse:
    """
    Join a live session as supervisor.
    Creates supervisor state in Redis and appends participant_joined to stream.

    AUTORIZACAO (2026-08-27) — antes desta data a rota nao tinha `Depends` NENHUM:
    qualquer um que alcancasse a porta entrava numa conferencia de cliente ao vivo e
    ESCREVIA no stream dela, declarando o proprio `tenant_id` e o proprio
    `operator_id` no corpo. Agora:
      1. token valido e obrigatorio (`require_pool_principal`, 401 sem header);
      2. o tenant vem do TOKEN — o do corpo so pode CONCORDAR, nunca decidir;
      3. o escopo de pool da sessao e conferido (`_authorize_live_session`);
      4. a identidade gravada no stream e o `sub` do token, nao a string do corpo.
    """
    redis = request.app.state.redis

    # O tenant e fato da CREDENCIAL, nunca do corpo. Manter o campo no corpo e
    # exigir concordancia (em vez de ignora-lo) faz a tentativa de declarar outro
    # tenant virar uma RECUSA NOMEADA, em vez de sumir em silencio.
    token_tenant = (principal.tenant_id or "").strip()
    if not token_tenant:
        logger.warning(
            "supervisor/join RECUSADO: token sub=%s sem `tenant_id`. Sem tenant no "
            "token nao ha contra o que conferir o meta da sessao.", principal.sub,
        )
        raise HTTPException(status_code=403, detail="token_without_tenant")
    if body.tenant_id and body.tenant_id != token_tenant:
        logger.warning(
            "supervisor/join RECUSADO: corpo declara tenant=%s, token diz %s (sub=%s)",
            body.tenant_id, token_tenant, principal.sub,
        )
        raise HTTPException(status_code=403, detail="tenant_mismatch_token")

    # Verify session exists
    meta_raw = await redis.get(f"session:{body.session_id}:meta")
    if not meta_raw:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    # ── O ÚNICO ponto autoritativo de tenant desta cadeia ─────────────────────
    # `/message` e `/leave` comparam `body.tenant_id` com o estado gravado em
    # `supervisor:{sid}:active` — que foi escrito, no join, a partir do corpo do
    # PRÓPRIO chamador. Logo aqueles dois checam consistência, não autoridade: o
    # único confronto contra um fato da plataforma é este, contra o meta da sessão.
    #
    # E ele se auto-anulava. Era:
    #     meta.get("tenant_id", body.tenant_id) != body.tenant_id
    # com o campo ausente, o default É o valor comparado, então a igualdade é
    # sempre verdadeira e o 403 NUNCA dispara. O `except: meta = {}` produzia o
    # mesmo efeito a partir de JSON malformado. Fail-open numa fronteira de
    # isolamento, e escrito de uma forma que parece uma comparação.
    #
    # Alcance MEDIDO em 2026-08-21 (`infra/test/probe_session_meta_ownership.sh`):
    # 8 metas vivos, 8 COM `tenant_id`, 0 sem, 0 malformados. O defeito é REAL no
    # código e LATENTE nesta população — 0 em 8 é evidência fraca de "nunca", e
    # por isso o conserto é fail-closed, não "não precisa".
    try:
        meta = json.loads(meta_raw)
        if not isinstance(meta, dict):
            raise ValueError(f"meta não é objeto JSON (é {type(meta).__name__})")
    except Exception as exc:
        logger.warning(
            "supervisor/join RECUSADO: session=%s meta ILEGÍVEL (%s). Sem o meta não "
            "há contra quem conferir o tenant declarado no corpo — e aceitar seria "
            "deixar o chamador afirmar o próprio escopo.",
            body.session_id, exc,
        )
        raise HTTPException(status_code=403, detail="tenant_unverifiable")

    meta_tenant = meta.get("tenant_id")
    if not meta_tenant:
        logger.warning(
            "supervisor/join RECUSADO: session=%s meta SEM `tenant_id` (campos: %s). "
            "Era exatamente este caminho que o guard antigo deixava passar.",
            body.session_id, sorted(meta.keys()),
        )
        raise HTTPException(status_code=403, detail="tenant_unverifiable")

    if meta_tenant != token_tenant:
        logger.warning(
            "supervisor/join RECUSADO: session=%s tenant do meta=%s != tenant do TOKEN=%s",
            body.session_id, meta_tenant, token_tenant,
        )
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    # Escopo de pool — depois do tenant (nao adianta conferir pool de outro tenant).
    await _authorize_live_session(redis, principal, token_tenant, body.session_id)

    # Reject double-join for the same session (idempotency guard)
    existing = await _get_state(redis, body.session_id)
    if existing:
        # Return existing state — caller may retry safely
        return JSONResponse(content={
            "participant_id": existing["participant_id"],
            "session_id":     body.session_id,
            "joined_at":      existing["joined_at"],
            "already_active": True,
        })

    participant_id = str(uuid.uuid4())
    joined_at      = datetime.now(timezone.utc).isoformat()

    # Identidade AUTORITATIVA = `sub` do token. O `operator_id` do corpo tinha default
    # literal `"operator"` (`hooks.ts:443`), entao a trilha de auditoria do stream
    # registrava uma constante, nao uma pessoa. Guardamos o declarado ao lado — util
    # para depurar cliente desatualizado — mas quem manda e o token.
    operator_id = principal.sub or body.operator_id
    if body.operator_id and body.operator_id != operator_id:
        logger.info(
            "supervisor/join: `operator_id` do corpo (%s) ignorado em favor do sub do "
            "token (%s) — cliente provavelmente desatualizado",
            body.operator_id, operator_id,
        )

    await redis.set(
        f"supervisor:{body.session_id}:active",
        json.dumps({
            "participant_id":       participant_id,
            "session_id":           body.session_id,
            "tenant_id":            token_tenant,
            "operator_id":          operator_id,
            "declared_operator_id": body.operator_id,
            "joined_at":            joined_at,
        }),
        ex=_SESSION_TTL,
    )

    # participant_joined — agents_only so it doesn't reach the customer
    await _xadd(redis, body.session_id, {
        "type":       "participant_joined",
        "visibility": "agents_only",
        "author":     json.dumps({"role": "supervisor", "participant_id": participant_id}),
        "payload":    json.dumps({"operator_id": operator_id}),
        "event_id":   participant_id,
        "timestamp":  joined_at,
    })

    logger.info("supervisor joined session=%s participant=%s", body.session_id, participant_id)

    return JSONResponse(content={
        "participant_id": participant_id,
        "session_id":     body.session_id,
        "joined_at":      joined_at,
    })


@router.post("/message")
async def send_message(
    body: MessageRequest,
    request: Request,
    principal: PoolPrincipal = Depends(require_pool_principal),
) -> JSONResponse:
    """
    Send a supervisor message into the session stream.
    visibility="agents_only" → coaching (agents see it, customer does not).
    visibility="all"         → direct intervention (everyone sees it).
    """
    redis = request.app.state.redis

    state = await _get_state(redis, body.session_id)
    if state is None:
        raise HTTPException(status_code=403, detail="Not joined to session as supervisor")
    if state["participant_id"] != body.participant_id:
        raise HTTPException(status_code=403, detail="participant_id mismatch")
    if state["tenant_id"] != (principal.tenant_id or ""):
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    # Author-bound: so o supervisor QUE ENTROU fala. Sem isto, conhecer o par
    # (session_id, participant_id) bastaria para escrever — e os dois viajam no corpo.
    if (state.get("operator_id") or "") != principal.sub:
        logger.warning(
            "supervisor/message RECUSADO: sessao entrou com operator=%s, chamador e %s",
            state.get("operator_id"), principal.sub,
        )
        raise HTTPException(status_code=403, detail="not_your_supervisor_session")

    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Message text cannot be empty")

    visibility = body.visibility if body.visibility in _VIS_ALLOWED else "agents_only"
    event_id   = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    # Field format matches StreamSubscriber._map_event / _map_message
    stream_eid = await _xadd(redis, body.session_id, {
        "type":       "message",
        "visibility": visibility,
        "author":     json.dumps({"role": "supervisor", "participant_id": body.participant_id}),
        "payload":    json.dumps({"content": {"type": "text", "text": body.text.strip()}}),
        "event_id":   event_id,
        "timestamp":  timestamp,
    })

    logger.debug(
        "supervisor message session=%s vis=%s eid=%s",
        body.session_id, visibility, stream_eid,
    )

    return JSONResponse(content={"event_id": stream_eid, "timestamp": timestamp})


@router.post("/leave")
async def leave_session(
    body: LeaveRequest,
    request: Request,
    principal: PoolPrincipal = Depends(require_pool_principal),
) -> JSONResponse:
    """
    Leave the session as supervisor.  Idempotent — returns acknowledged:true
    even if the supervisor has already left or was never joined.
    """
    redis = request.app.state.redis

    state = await _get_state(redis, body.session_id)
    if state is None:
        return JSONResponse(content={"acknowledged": True})

    if state["participant_id"] != body.participant_id:
        raise HTTPException(status_code=403, detail="participant_id mismatch")
    if (state.get("operator_id") or "") != principal.sub:
        raise HTTPException(status_code=403, detail="not_your_supervisor_session")

    timestamp = datetime.now(timezone.utc).isoformat()

    await _xadd(redis, body.session_id, {
        "type":       "participant_left",
        "visibility": "agents_only",
        "author":     json.dumps({"role": "supervisor", "participant_id": body.participant_id}),
        "event_id":   body.participant_id,
        "timestamp":  timestamp,
    })

    await redis.delete(f"supervisor:{body.session_id}:active")

    logger.info("supervisor left session=%s participant=%s", body.session_id, body.participant_id)

    return JSONResponse(content={"acknowledged": True})
