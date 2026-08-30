"""
transcript.py — Evaluation transcript read (T9-C / blueprint D2+D3)

Route (prefixed /v1/transcript):
  GET /v1/transcript/sessions/{session_id}
      Returns the MASKED message transcript for a session, optionally windowed
      to a ContactSegment. Source: analytics.messages (ClickHouse, persisted,
      masked-only — there is NO original_content column, so D3 "blind review /
      masked for everyone" is enforced by construction at the storage layer).

      Query params:
        tenant_id   — tenant scope (default tenant_demo; overridden by principal)
        segment_id  — when given with scope=segment, window the transcript to the
                      segment's [started_at, ended_at] (resolved from
                      analytics.segments). Falls back to full session if the
                      segment is unknown or still open.
        scope       — "segment" (default, requires segment_id) | "contact"
                      (full session, ignores the window).

      Each message carries stream_entry_id (== analytics.messages.message_id ==
      canonical-stream event_id) so evidence chips align with the transcript
      (blueprint C.3).

Design notes:
  - This endpoint is the clean port the evaluation-api delegates to (blueprint D2):
    all ClickHouse / segment-window logic lives here; evaluation-api orchestrates
    (resolves session_id+segment_id from the result, gates by evaluation ABAC) and
    never touches ClickHouse nor reinvents masking.
  - The role gate for T9-C is enforced in evaluation-api (module_config.evaluation.*),
    NOT here and NOT audit.sessions. This read only enforces tenant isolation; the
    content is masked, so it is low-sensitivity by D3.
  - In analytics_open_access=True mode (dev/demo) the principal is optional.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from .pool_auth import PoolPrincipal, authorize_session_scope, optional_pool_principal

logger = logging.getLogger("plughub.analytics.transcript")

router = APIRouter(prefix="/v1/transcript", tags=["transcript"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(request: Request):
    return request.app.state.store


def _isoformat(ts: Any) -> str:
    """Convert a ClickHouse DateTime to ISO8601 string."""
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=timezone.utc).isoformat()
    return str(ts)


def _normalise_content(content_raw: Any) -> str:
    """Messages content is stored as a JSON string in some events; normalise to text."""
    if not content_raw:
        return ""
    try:
        parsed = json.loads(content_raw)
    except Exception:
        return str(content_raw)
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict) and "text" in parsed:
        return str(parsed["text"])
    return str(content_raw)


def _fetch_segment_window(
    client: Any, db: str, tenant_id: str, session_id: str, segment_id: str
) -> tuple[Any, Any]:
    """
    Resolve (started_at, ended_at) for a ContactSegment from analytics.segments.
    Returns (None, None) when the segment is unknown. ended_at may be None for a
    still-open segment — the caller then leaves the window open-ended.
    """
    result = client.query(
        f"""
        SELECT started_at, ended_at
        FROM {db}.segments FINAL
        WHERE tenant_id  = {{tenant_id:String}}
          AND session_id = {{session_id:String}}
          AND segment_id = {{segment_id:String}}
        LIMIT 1
        """,
        parameters={"tenant_id": tenant_id, "session_id": session_id, "segment_id": segment_id},
    )
    if not result.result_rows:
        return None, None
    started_at, ended_at = result.result_rows[0]
    return started_at, ended_at


def _fetch_messages(
    client: Any,
    db: str,
    tenant_id: str,
    session_id: str,
    ts_start: Any = None,
    ts_end: Any = None,
) -> list[dict]:
    """
    Query analytics.messages for a session, optionally windowed by timestamp.
    Returns masked content only (the table has no original_content column).
    """
    where = ["tenant_id = {tenant_id:String}", "session_id = {session_id:String}"]
    params: dict[str, Any] = {"tenant_id": tenant_id, "session_id": session_id}
    if ts_start is not None:
        where.append("timestamp >= {ts_start:DateTime64(3)}")
        params["ts_start"] = ts_start
    if ts_end is not None:
        where.append("timestamp <= {ts_end:DateTime64(3)}")
        params["ts_end"] = ts_end

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
        WHERE {' AND '.join(where)}
        ORDER BY timestamp ASC
        LIMIT 5000
        """,
        parameters=params,
    )
    rows: list[dict] = []
    for r in result.result_rows:
        msg_id, author_id, author_role, content_type, visibility, content_raw, ts = r
        rows.append({
            "stream_entry_id": str(msg_id),
            "event_type":      str(content_type or "message"),
            "author_id":       str(author_id) if author_id else None,
            "author_role":     str(author_role) if author_role else None,
            "visibility":      str(visibility or "all"),
            "content":         _normalise_content(content_raw),
            "created_at":      _isoformat(ts),
        })
    return rows


def _fetch_transcript(
    client: Any,
    db: str,
    tenant_id: str,
    session_id: str,
    segment_id: str | None,
    scope: str,
) -> dict:
    """Resolve the segment window (when scope=segment) and fetch the masked transcript."""
    ts_start = ts_end = None
    effective_scope = scope
    if scope == "segment" and segment_id:
        ts_start, ts_end = _fetch_segment_window(client, db, tenant_id, session_id, segment_id)
        if ts_start is None:
            # Unknown segment → fall back to the full contact, flagged so.
            effective_scope = "contact"
    else:
        effective_scope = "contact"

    messages = _fetch_messages(client, db, tenant_id, session_id, ts_start, ts_end)
    return {
        "session_id": session_id,
        "segment_id": segment_id,
        "scope":      effective_scope,
        "window":     {"start": _isoformat(ts_start), "end": _isoformat(ts_end)},
        "messages":   messages,
        "masked":     True,
    }


# ── GET /v1/transcript/sessions/{session_id} ───────────────────────────────────

@router.get("/sessions/{session_id}")
async def transcript_session_messages(
    session_id: str,
    request:    Request,
    tenant_id:  str = Query("tenant_demo"),
    segment_id: str | None = Query(None),
    scope:      str = Query("segment", pattern="^(segment|contact)$"),
    principal:  PoolPrincipal = Depends(optional_pool_principal),
):
    """
    Returns the masked transcript for a session, windowed to the segment when
    scope=segment + segment_id are given. Role gating is the caller's
    responsibility (evaluation-api).

    ── Escopo de pool (2026-08-30, peça 1 da (d) — decisão #6 do dono) ──────────
    A frase que estava aqui — *"this read enforces tenant isolation only"* — era
    verdadeira e era o defeito: qualquer token do tenant lia a transcrição INTEIRA
    de qualquer contato cujo `session_id` conhecesse. O que a protegia não era
    permissão, era o supervisor não RECEBER o id (uuid como barreira de capacidade).

    Duas portas serviam este mesmo dado e nenhuma conferia escopo: esta e a irmã
    `GET /sessions/{id}/stream`. Foram fechadas juntas, que é como a dívida estava
    registrada — fechar uma só deixaria a outra aberta parecendo protegida.

    O gate de PAPEL segue fora daqui (evaluation-api, `module_config.evaluation.*`);
    são eixos distintos, e este confere apenas *"esta sessão é dos meus pools?"*.

    ⚠️ **A delegação da evaluation-api chama esta rota SEM credencial** — medido
    2026-08-30 (`router.py:2221`, `client.get(url, params=...)`, nenhum header) —
    e desde 2026-08-27 `optional_pool_principal` responde **401** a requisição sem
    `Authorization` quando `analytics_open_access` está desligado, que é o default
    e não é ligado em `infra/` nenhum. Ou seja: aquela delegação já está quebrada,
    ANTES deste portão, e o 401 vira 502 na cara do usuário. Defeito próprio,
    registrado no `TODO.md`; não é regressão desta mudança, e esta mudança não o
    agrava.
    """
    effective_tenant = principal.tenant_id or tenant_id
    store = _store(request)

    await authorize_session_scope(
        principal, effective_tenant, session_id,
        rota="transcript.session", store=store,
    )
    try:
        payload = await asyncio.to_thread(
            _fetch_transcript,
            store.new_client(), store._database,
            effective_tenant, session_id, segment_id, scope,
        )
        return JSONResponse(content=payload)
    except Exception as exc:
        logger.error("transcript_session_messages error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})
