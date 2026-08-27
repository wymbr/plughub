"""
sessions.py
FastAPI router for session drill-down endpoints (Phase 2 — read-only).

Routes:
  GET /sessions/active?tenant_id=xxx&pool_id=xxx&limit=N
      List of active sessions for a pool, ordered by worst sentiment first.
      Queries ClickHouse for sessions opened in the last 24h with closed_at IS NULL,
      then overlays latest sentiment score from Redis.

  GET /sessions/customer/{customer_id}?tenant_id=xxx&limit=N
      Contact history for a customer (closed sessions, most recent first).
      Returns session_id, channel, pool_id, opened_at, closed_at, duration_ms,
      outcome, close_reason for each past contact.

  GET /sessions/customer/{customer_id}/search?tenant_id=xxx&q=term&...
      Customer History H2 — keyword search over the customer's closed contacts,
      matching MASKED message content only (LGPD-safe by construction). One hit
      per session with a masked snippet + match count. Filters: from/to/channel/
      outcome/pool; paginated by limit/offset.

  GET /sessions/{session_id}/stream?tenant_id=xxx
      SSE stream of the Redis session stream (session:{id}:stream).
      First event type "history" delivers all existing entries.
      Subsequent events type "entry" deliver new entries as they arrive.
      Sends ':keepalive' comment every 15s to prevent proxy timeouts.
      Read-only — no participant is registered in the session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re as _re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .pool_auth import PoolPrincipal, optional_pool_principal

logger = logging.getLogger("plughub.analytics.sessions")

router = APIRouter(prefix="/sessions")

_STREAM_BLOCK_MS   = 2_000   # XREAD blocking timeout
_SSE_KEEPALIVE_S   = 15      # send comment to keep connection alive
_ACTIVE_WINDOW_H   = 24      # look back N hours for "active" sessions
_WRAPUP_GRACE_MIN  = 10      # keep recently-closed sessions visible during hook wrap-up
_DEFAULT_LIMIT     = 50
_MAX_LIMIT         = 200


# ─── GET /sessions/active ─────────────────────────────────────────────────────

@router.get("/active")
async def list_active_sessions(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
    pool_id:   str = Query(..., description="Pool to list sessions for"),
    limit:     int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> JSONResponse:
    """
    Active sessions for a pool, worst sentiment first.

    A session is considered 'active' when it appears in ClickHouse with
    closed_at IS NULL and was opened within the last 24 hours.

    Each entry includes:
      session_id, channel, opened_at, handle_time_ms (running),
      latest_score (from Redis), latest_category
    """
    store = request.app.state.store
    redis = request.app.state.redis
    try:
        sessions = await asyncio.to_thread(
            _fetch_active_sessions,
            store.new_client(), store._database, tenant_id, pool_id, limit,
        )
        # Overlay sentiment scores from Redis (pipeline for efficiency)
        if sessions:
            sessions = await _overlay_sentiment(redis, sessions)
        return JSONResponse(content=sessions)
    except Exception as exc:
        logger.warning("list_active_sessions failed tenant=%s pool=%s: %s", tenant_id, pool_id, exc)
        return JSONResponse(content=[], status_code=200)


def _fetch_active_sessions(
    client: Any, db: str, tenant_id: str, pool_id: str, limit: int,
) -> list[dict]:
    from datetime import timedelta
    since = (datetime.utcnow() - timedelta(hours=_ACTIVE_WINDOW_H)).strftime("%Y-%m-%d %H:%M:%S")
    # Use FINAL to force deduplication of ReplacingMergeTree versions at query time,
    # preventing sessions from flickering in/out during background ClickHouse merges.
    # Include a grace window (_WRAPUP_GRACE_MIN) for recently-closed sessions that
    # may still have wrap-up/NPS hooks running — Core fires session_closed (contact
    # layer) before all posatt hooks complete, so ClickHouse briefly has closed_at set
    # while the Console still shows the contact as active.
    result = client.query(f"""
        SELECT
            session_id,
            channel,
            opened_at,
            wait_time_ms
        FROM {db}.sessions FINAL
        WHERE tenant_id = {{tenant_id:String}}
          AND pool_id   = {{pool_id:String}}
          AND (
            closed_at IS NULL
            OR closed_at >= now() - INTERVAL {_WRAPUP_GRACE_MIN} MINUTE
          )
          AND opened_at >= '{since}'
        ORDER BY opened_at ASC
        LIMIT {limit}
    """, parameters={"tenant_id": tenant_id, "pool_id": pool_id})

    now_ms = int(time.time() * 1000)
    rows   = []
    for r in result.result_rows:
        session_id, channel, opened_at, wait_time_ms = r
        # opened_at comes back as a datetime object from clickhouse-connect
        if isinstance(opened_at, datetime):
            opened_ts_ms = int(opened_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
        else:
            opened_ts_ms = 0
        handle_time_ms = now_ms - opened_ts_ms if opened_ts_ms else None
        rows.append({
            "session_id":    session_id,
            "channel":       channel,
            "opened_at":     opened_at.replace(tzinfo=timezone.utc).isoformat() if isinstance(opened_at, datetime) and opened_at.tzinfo is None else (opened_at.isoformat() if isinstance(opened_at, datetime) else str(opened_at)),
            "handle_time_ms": handle_time_ms,
            "wait_time_ms":  wait_time_ms,
            "latest_score":  None,   # filled by _overlay_sentiment
            "latest_category": None,
        })
    return rows


async def _overlay_sentiment(redis: Any, sessions: list[dict]) -> list[dict]:
    """
    Fetches the latest sentiment score for each session from Redis.
    Key: session:{id}:sentiment  (list of {score, timestamp} JSON objects)
    Gets only the last element via LRANGE ... -1 -1 (pipeline).
    """
    keys = [f"session:{s['session_id']}:sentiment" for s in sessions]
    try:
        pipe = redis.pipeline()
        for key in keys:
            pipe.lrange(key, -1, -1)
        results = await pipe.execute()

        for session, raw_list in zip(sessions, results):
            if raw_list:
                try:
                    entry = json.loads(raw_list[0])
                    score = float(entry.get("score", 0.0))
                    session["latest_score"]    = round(score, 4)
                    session["latest_category"] = _classify(score)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("_overlay_sentiment failed: %s", exc)

    # Sort: worst score first (None at end)
    sessions.sort(key=lambda s: (s["latest_score"] is None, s["latest_score"] or 0))
    return sessions


def _classify(score: float) -> str:
    if score >=  0.3: return "satisfied"
    if score >= -0.3: return "neutral"
    if score >= -0.6: return "frustrated"
    return "angry"


# ─── GET /sessions/customer/{customer_id} ────────────────────────────────────

_DEFAULT_HISTORY_LIMIT = 20
_MAX_HISTORY_LIMIT     = 100


@router.get("/customer/{customer_id}")
async def customer_history(
    customer_id: str,
    request:     Request,
    tenant_id:   str = Query(..., description="Tenant identifier"),
    limit:       int = Query(_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
    pool_principal: "PoolPrincipal" = Depends(optional_pool_principal),
) -> JSONResponse:
    """
    Contact history for a customer — last N closed sessions, most recent first.

    Each entry includes:
      session_id, channel, pool_id, opened_at, closed_at,
      duration_ms, outcome, close_reason
    """
    store = request.app.state.store
    # E2f — pools internos (wrap-up destacado) fora do histórico do cliente. Esta é
    # a superfície mais sensível do problema: o wrap-up herda o `customer_id` da
    # origem, então cada atendimento real gerava um "contato" a mais na lista que o
    # atendente vê durante o atendimento seguinte — e que não tem conversa nenhuma.
    from .reports_query import _internal_pools_for
    internal_pools = await _internal_pools_for(tenant_id)

    # ABAC de pool (2026-08-27). Ate hoje este endpoint nao tinha portao NENHUM:
    # respondia 200 a qualquer chamador, servindo historico de contato chaveado por
    # `customer_id`. Lista VAZIA de pools = alcanca nada, e a resposta e vazia SEM
    # tocar o ClickHouse — mesma postura dos `/reports/*`.
    accessible = pool_principal.accessible_pools
    if accessible is not None and not accessible:
        return JSONResponse(content=[])
    try:
        rows = await asyncio.to_thread(
            _fetch_customer_history,
            store.new_client(), store._database, tenant_id, customer_id, limit,
            internal_pools, accessible,
        )
        return JSONResponse(content=rows)
    except Exception as exc:
        # NAO devolver `[]` com 200: para a tela isso e indistinguivel de "este
        # cliente nunca ligou", e o atendente decide diferente com essa informacao.
        # A `HistoricoTab` ja renderiza `historico.historyError` — o ramo existia e
        # era codigo morto porque este endpoint nunca falhava visivelmente.
        logger.warning(
            "customer_history failed tenant=%s customer=%s: %s",
            tenant_id, customer_id, exc,
        )
        raise HTTPException(
            status_code=502,
            detail="analytics store failed to answer the customer history query",
        ) from exc


def _fetch_customer_history(
    client: Any, db: str, tenant_id: str, customer_id: str, limit: int,
    internal_pools: "frozenset[str] | None" = None,
    accessible_pools: "list[str] | None" = None,
) -> list[dict]:
    """
    Queries ClickHouse for closed sessions belonging to the given customer,
    ordered by opened_at DESC.  Uses FINAL to force ReplacingMergeTree dedup
    so we don't return stale open-row duplicates that haven't merged yet.

    E2f: sessions from INTERNAL pools (detached wrap-up) are excluded — they carry
    the origin contact's `customer_id` but are not contacts of the customer.
    """
    internal_clause = ""
    if internal_pools:
        lst = ", ".join(f"'{p}'" for p in sorted(internal_pools))
        internal_clause = f"AND s.pool_id NOT IN ({lst})"

    # Predicado canonico de escopo de SESSAO — o mesmo dos `/reports/*`. Nao e
    # `pool_id IN (...)`: `sessions.pool_id` e o pool de ENTRADA, e autorizar so por
    # ele faz o supervisor perder contatos que os agentes DELE atenderam (medido em
    # 2026-08-14: 52 de 67 contatos sairiam do escopo). O helper cobre as tres razoes
    # de uma sessao ser minha — entrou por pool meu, ainda nao tem pool, ou um pool
    # meu PARTICIPOU. Reusado, nunca reimplementado.
    from .reports_query import _session_scope_clause
    scope = _session_scope_clause(db, accessible_pools, alias="s")
    scope_clause = f"AND {scope}" if scope else ""

    result = client.query(f"""
        SELECT
            s.session_id,
            s.channel,
            s.pool_id,
            s.opened_at,
            s.closed_at,
            s.handle_time_ms,
            s.outcome,
            s.close_reason,
            s.root_session_id
        -- alias ANTES de FINAL: `FROM t FINAL AS s` e SYNTAX_ERROR 62 no ClickHouse.
        FROM {db}.sessions AS s FINAL
        WHERE s.tenant_id   = {{tenant_id:String}}
          AND s.customer_id = {{customer_id:String}}
          AND s.closed_at IS NOT NULL
          {internal_clause}
          {scope_clause}
        ORDER BY s.opened_at DESC
        LIMIT {limit}
    """, parameters={"tenant_id": tenant_id, "customer_id": customer_id})

    rows = []
    for r in result.result_rows:
        session_id, channel, pool_id, opened_at, closed_at, handle_time_ms, outcome, close_reason, root_session_id = r

        def _dt(val: Any) -> str | None:
            if val is None:
                return None
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
                return val.isoformat()
            return str(val)

        # ── D9 — este era o QUARTO nome da mesma grandeza ──────────────────────
        # A saída chamava `duration_ms` o que a tabela chama `handle_time_ms` e o
        # `/reports/sessions` recomputa por canal — quatro nomes, três comportamentos.
        # `duration_ms` era o pior deles: já é o nome da coluna de SEGMENTO, então a
        # mesma palavra significava "tempo do contato" aqui e "tempo de um
        # participante" ali (engano já catalogado: uma query pediu `duration_ms` a
        # `sessions`, coluna que não existe, e a aba Análise ficou vazia sem erro).
        # Passa a sair como `elapsed_time_ms`, com `duration_ms` mantido como ALIAS
        # DE COMPAT enquanto a `HistoricoTab` não migra.
        if handle_time_ms is not None:
            duration_ms: int | None = int(handle_time_ms)
        elif opened_at and closed_at:
            try:
                o = opened_at if isinstance(opened_at, datetime) else datetime.fromisoformat(str(opened_at))
                c = closed_at if isinstance(closed_at, datetime) else datetime.fromisoformat(str(closed_at))
                duration_ms = int((c - o).total_seconds() * 1000)
            except Exception:
                duration_ms = None
        else:
            duration_ms = None

        rows.append({
            "session_id":   session_id,
            "channel":      channel,
            "pool_id":      pool_id,
            "opened_at":    _dt(opened_at),
            "closed_at":    _dt(closed_at),
            "duration_ms":      duration_ms,   # alias de compat — não ganhar leitor novo
            "elapsed_time_ms":  duration_ms,   # D9: o nome que diz o que é
            "outcome":      outcome,
            "close_reason": close_reason,
            # HJ / bidirectional nav: the process (journey) this contact belongs to.
            # When != session_id the contact is a member of a multi-session process →
            # the UI shows a PRC- link to its Vista Processos.
            "root_session_id": root_session_id,
        })
    return rows


# ─── GET /sessions/customer/{customer_id}/search ─────────────────────────────
#
# Customer History H2 — keyword search over a customer's past contacts.
# Matches the MASKED message content only (analytics.messages has no
# original_content column), so this is LGPD-safe by construction — same posture
# as the transcript drill (H1): no unmasked exposure, no audit_access_log.
# Substring match (positionCaseInsensitiveUTF8), one hit per session with a
# representative masked snippet + match count (score). Filters mirror the list.

_SEARCH_DEFAULT_LIMIT = 20
_SEARCH_MAX_LIMIT     = 100
_SEARCH_SCAN_CAP      = 2_000   # max matching messages scanned before collapsing
_SNIPPET_RADIUS       = 60      # chars of context on each side of the match


def _iso(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return str(val)


def _make_snippet(content: str, term: str, radius: int = _SNIPPET_RADIUS) -> str:
    """Window of masked content around the first case-insensitive match of `term`."""
    if not content:
        return ""
    idx = content.lower().find(term.lower())
    if idx < 0:
        head = content[: radius * 2].strip()
        return head + ("…" if len(content) > radius * 2 else "")
    start = max(0, idx - radius)
    end   = min(len(content), idx + len(term) + radius)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


@router.get("/customer/{customer_id}/search")
async def customer_history_search(
    customer_id: str,
    request:     Request,
    pool_principal: "PoolPrincipal" = Depends(optional_pool_principal),
    tenant_id:   str = Query(..., description="Tenant identifier"),
    q:           str = Query(..., min_length=1, description="Free-text term (masked content)"),
    date_from:   str | None = Query(None, alias="from", description="Lower bound on opened_at (ISO)"),
    date_to:     str | None = Query(None, alias="to",   description="Upper bound on opened_at (ISO)"),
    channel:     str | None = Query(None, description="Filter by channel"),
    outcome:     str | None = Query(None, description="Filter by outcome"),
    pool:        str | None = Query(None, description="Filter by pool_id"),
    limit:       int = Query(_SEARCH_DEFAULT_LIMIT, ge=1, le=_SEARCH_MAX_LIMIT),
    offset:      int = Query(0, ge=0),
) -> JSONResponse:
    """
    Keyword search over a customer's closed contacts. Returns one hit per session:
      { session_id, opened_at, channel, outcome, pool_id, snippet, score }
    `snippet` and the search index are MASKED content only — the original is never
    read (the column does not exist in analytics.messages). Graceful on failure.
    """
    store = request.app.state.store
    accessible = pool_principal.accessible_pools
    if accessible is not None and not accessible:
        return JSONResponse(content=[])
    try:
        hits = await asyncio.to_thread(
            _search_customer_history,
            store.new_client(), store._database, tenant_id, customer_id,
            q, date_from, date_to, channel, outcome, pool, limit, offset,
            accessible,
        )
        return JSONResponse(content=hits)
    except Exception as exc:
        # Mesma razao do `customer_history`: busca que falha nao pode devolver
        # "nenhum resultado". Aqui o engano e ainda mais caro — o atendente conclui
        # que o termo nao aparece no historico, quando a busca nem rodou.
        logger.warning(
            "customer_history_search failed tenant=%s customer=%s q=%r: %s",
            tenant_id, customer_id, q, exc,
        )
        raise HTTPException(
            status_code=502,
            detail="analytics store failed to answer the customer history search",
        ) from exc


def _search_customer_history(
    client: Any, db: str, tenant_id: str, customer_id: str, q: str,
    date_from: str | None, date_to: str | None,
    channel: str | None, outcome: str | None, pool: str | None,
    limit: int, offset: int,
    accessible_pools: "list[str] | None" = None,
) -> list[dict]:
    """
    Scan matching MASKED messages for a customer's closed sessions, then collapse
    to one hit per session (most recent first). LIMIT/OFFSET paginate sessions,
    not raw message matches.
    """
    where = [
        "s.tenant_id   = {tenant_id:String}",
        "s.customer_id = {customer_id:String}",
        "s.closed_at IS NOT NULL",
        "m.content IS NOT NULL",
        "positionCaseInsensitiveUTF8(m.content, {q:String}) > 0",
    ]
    params: dict[str, Any] = {
        "tenant_id": tenant_id, "customer_id": customer_id, "q": q,
    }
    if channel:
        where.append("s.channel = {channel:String}");  params["channel"] = channel
    if outcome:
        where.append("s.outcome = {outcome:String}");  params["outcome"] = outcome
    if pool:
        where.append("s.pool_id = {pool:String}");     params["pool"] = pool
    if date_from:
        where.append("s.opened_at >= parseDateTimeBestEffort({date_from:String})")
        params["date_from"] = date_from
    if date_to:
        where.append("s.opened_at <= parseDateTimeBestEffort({date_to:String})")
        params["date_to"] = date_to

    # Mesmo predicado canonico do `customer_history` — o JOIN ja aliasa `sessions`
    # como `s`, entao ele encaixa sem tocar na forma da query.
    from .reports_query import _session_scope_clause
    _scope = _session_scope_clause(db, accessible_pools, alias="s")
    if _scope:
        where.append(_scope)

    result = client.query(
        f"""
        SELECT
            m.session_id,
            s.opened_at,
            s.channel,
            s.outcome,
            s.pool_id,
            m.content,
            m.timestamp
        FROM {db}.messages AS m
        INNER JOIN (SELECT * FROM {db}.sessions FINAL) AS s
            ON m.session_id = s.session_id AND m.tenant_id = s.tenant_id
        WHERE {' AND '.join(where)}
        ORDER BY s.opened_at DESC, m.timestamp ASC
        LIMIT {_SEARCH_SCAN_CAP}
        """,
        parameters=params,
    )

    by_session: dict[str, dict] = {}
    order: list[str] = []
    for r in result.result_rows:
        session_id, opened_at, channel_v, outcome_v, pool_id, content, _ts = r
        if session_id not in by_session:
            order.append(session_id)
            by_session[session_id] = {
                "session_id": session_id,
                "opened_at":  _iso(opened_at),
                "channel":    channel_v,
                "outcome":    outcome_v,
                "pool_id":    pool_id,
                "snippet":    _make_snippet(str(content or ""), q),
                "score":      0,
            }
        by_session[session_id]["score"] += 1

    hits = [by_session[s] for s in order]
    return hits[offset : offset + limit]


# ─── GET /sessions/{session_id}/stream ───────────────────────────────────────

@router.get("/{session_id}/stream")
async def session_stream(
    session_id: str,
    request:    Request,
    tenant_id:  str = Query(..., description="Tenant identifier"),
) -> StreamingResponse:
    """
    SSE stream of Redis session stream (read-only XREAD).

    First sends a 'history' event with all existing entries,
    then sends 'entry' events as new entries arrive.
    Sends ':keepalive' comment every 15s to prevent proxy timeouts.
    """
    redis      = request.app.state.redis
    store      = request.app.state.store
    stream_key = f"session:{session_id}:stream"

    async def event_generator():
        yield f"retry: 3000\n\n"

        # ── history ───────────────────────────────────────────────────────────
        try:
            raw_entries = await redis.xrange(stream_key, "-", "+")
            history     = [_parse_entry(e_id, e_data) for e_id, e_data in raw_entries]

            # Fallback: when Redis stream has expired (closed sessions), query ClickHouse.
            if not history:
                try:
                    ch_history = await asyncio.to_thread(
                        store.query_session_messages,
                        store.new_client(), tenant_id, session_id,
                    )
                    if ch_history:
                        history = ch_history
                        logger.debug(
                            "session_stream: Redis empty, loaded %d msgs from ClickHouse id=%s",
                            len(history), session_id,
                        )
                except Exception as ch_exc:
                    logger.debug(
                        "session_stream: ClickHouse fallback failed id=%s: %s",
                        session_id, ch_exc,
                    )

            yield f"event: history\ndata: {json.dumps(history)}\nid: 0\n\n"
            cursor = raw_entries[-1][0] if raw_entries else "0"
        except Exception as exc:
            logger.warning("session_stream history failed id=%s: %s", session_id, exc)
            yield f"event: history\ndata: []\nid: 0\n\n"
            cursor = "0"

        # ── live tail ─────────────────────────────────────────────────────────
        last_keepalive = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break

                # keepalive comment
                if time.time() - last_keepalive >= _SSE_KEEPALIVE_S:
                    yield ": keepalive\n\n"
                    last_keepalive = time.time()

                try:
                    result = await redis.xread(
                        {stream_key: cursor},
                        block=_STREAM_BLOCK_MS,
                        count=20,
                    )
                except Exception as exc:
                    logger.warning("xread failed id=%s: %s", session_id, exc)
                    await asyncio.sleep(1)
                    continue

                if not result:
                    continue

                for _stream, entries in result:
                    for e_id, e_data in entries:
                        parsed = _parse_entry(e_id, e_data)
                        yield f"event: entry\ndata: {json.dumps(parsed)}\nid: {e_id}\n\n"
                        cursor = e_id

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("session_stream error id=%s: %s", session_id, exc)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_entry(entry_id: str | bytes, data: dict) -> dict:
    """Converts a raw Redis stream entry to a clean dict for the frontend.

    Handles two XADD field conventions:
      1. message_send (session.ts): uses ``author`` (JSON object) and ``payload``
      2. Human agent bridge / new code: uses ``author_id``, ``author_role``, ``content``
    """
    if isinstance(entry_id, bytes):
        entry_id = entry_id.decode()

    # Decode bytes values if needed
    clean: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(k, bytes): k = k.decode()
        if isinstance(v, bytes): v = v.decode()
        clean[k] = v

    # ── Resolve author_id / author_role ─────────────────────────────────────
    author_id   = clean.get("author_id")
    author_role = clean.get("author_role")

    # Fallback: parse the ``author`` JSON field written by message_send
    if not author_id and clean.get("author"):
        author_obj = _safe_json(clean["author"])
        if isinstance(author_obj, dict):
            author_id   = author_obj.get("participant_id") or author_obj.get("instance_id") or author_obj.get("id")
            author_role = author_obj.get("role") or author_obj.get("type")

    # ── Resolve content ─────────────────────────────────────────────────────
    content = _safe_json(clean.get("content"))

    # Fallback: extract text from ``payload`` when content is absent
    if content is None and clean.get("payload"):
        payload_obj = _safe_json(clean["payload"])
        if isinstance(payload_obj, dict) and "text" in payload_obj:
            content = {"text": payload_obj["text"]}
        elif payload_obj is not None:
            content = payload_obj

    # ── Resolve visibility ──────────────────────────────────────────────────
    vis_raw = clean.get("visibility", "all")
    visibility = _safe_json(vis_raw) if vis_raw else "all"

    entry_type = clean.get("type", "unknown")
    payload_parsed = _safe_json(clean.get("payload"))


    # ── Mask sensitive data in any entry ─────────────────────────────────────
    # Customer form submissions may arrive as type "message" (not just
    # "interaction_result") because notification_send and menu_submit both
    # write type: "message" to the stream.  Check ALL entries for sensitive
    # field patterns (passwords, PINs, 2FA codes, etc.).
    content, payload_parsed = _mask_interaction_result(content, payload_parsed)

    result: dict[str, Any] = {
        "entry_id":    entry_id,
        "type":        entry_type,
        "timestamp":   clean.get("timestamp"),
        "author_id":   author_id,
        "author_role": author_role,
        "visibility":  visibility,
        "content":     content,
        "payload":     payload_parsed,
    }
    # Include segment_id when present (written by notification_send)
    seg_id = clean.get("segment_id")
    if seg_id:
        result["segment_id"] = seg_id
    return result


_SENSITIVE_FIELD_RE = _re.compile(
    r"\b(senha|password|pin|codigo_2fa|otp|token|secret|cvv|cvc)\b",
    _re.IGNORECASE,
)


def _mask_interaction_result(
    content: Any, payload: Any
) -> tuple[Any, Any]:
    """Replace sensitive form data in interaction_result entries with a placeholder."""
    text_parts: list[str] = []
    for obj in (content, payload):
        if isinstance(obj, str):
            text_parts.append(obj)
        elif isinstance(obj, dict):
            text_parts.append(json.dumps(obj))

    combined = " ".join(text_parts)
    if _SENSITIVE_FIELD_RE.search(combined):
        return (
            {"text": "[Dados sensíveis omitidos]"},
            None,
        )
    return content, payload



def _safe_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ─── GET /sessions/{session_id}/workflow-trace ────────────────────────────────
#
# Arc 19: Returns the ordered list of trace nodes for a webhook workflow session.
#
# Response shape:
#   { nodes: [ TraceNode, ... ] }
#
# TraceNode:
#   {
#     node_type:      "input_origin" | "webhook_exec" | "specialist_output"
#     segment_id:     str
#     session_id:     str          -- may differ from the webhook session for input_origin
#     agent_type_id:  str
#     agent_type:     "human" | "ai"
#     role:           str
#     pool_id:        str
#     started_at:     str (ISO)
#     ended_at:       str | null
#     duration_ms:    int | null
#     outcome:        str | null
#     close_reason:   str | null
#     sequence_index: int
#     is_origin:      bool          -- true only for the input_origin node
#   }
#
# Ordering:
#   1. input_origin node (from origin_session_id's primary segment), if present
#   2. Webhook primary segments ordered by started_at ascending
#   3. Specialist sub-segments interleaved at their parent's position

@router.get("/{session_id}/workflow-trace")
async def get_workflow_trace(
    session_id: str,
    request:    Request,
    tenant_id:  str = Query(...),
) -> JSONResponse:
    """
    Returns the ordered trace node list for a webhook workflow session.

    Fetches:
      1. origin_session_id from ClickHouse sessions table
      2. Primary segment of the origin session (the intake agent)
      3. All segments of the webhook session, ordered by started_at
    """
    store = request.app.state.store
    redis = request.app.state.redis

    # Redis fallback: ClickHouse may have lost origin_session_id for sessions
    # created before the consumer fix (parse_routed overwrote it with NULL).
    # Read it from the ContextStore, which is the authoritative source.
    origin_session_id_override: str | None = None
    try:
        ctx_raw = await redis.hget(
            f"{tenant_id}:ctx:{session_id}",
            "session.origin_session_id",
        )
        if ctx_raw:
            import json as _json
            entry = _json.loads(ctx_raw)
            origin_session_id_override = entry.get("value") or None
    except Exception:
        pass  # non-fatal — ClickHouse value used as primary

    # Delegate child sessions: the delegate step writes
    # {step_id}:__child_session_id__ to pipeline_state.results.
    # Session C has origin_session_id = Session A (star topology), so the
    # standard reverse-lookup (WHERE origin_session_id = Session B) misses it.
    # Read pipeline_state from Redis to find delegate children directly.
    delegate_child_ids: list[str] = []
    try:
        pipeline_key = f"{tenant_id}:pipeline:{session_id}"
        ps_raw = await redis.get(pipeline_key)
        if not ps_raw:
            # Fallback for pre-fix sessions with segment-suffixed key
            seg_keys = await redis.keys(f"{pipeline_key}--seg--*")
            if seg_keys:
                ps_raw = await redis.get(seg_keys[0])
        if ps_raw:
            import json as _json2
            ps = _json2.loads(ps_raw)
            results = ps.get("results") or {}
            for k, v in results.items():
                if isinstance(k, str) and k.endswith(":__child_session_id__") and v:
                    delegate_child_ids.append(str(v))
    except Exception:
        pass  # non-fatal

    try:
        nodes = await asyncio.to_thread(
            _build_workflow_trace,
            store.new_client(), store._database, tenant_id, session_id,
            origin_session_id_override,
            delegate_child_ids,
        )
        return JSONResponse(content={"nodes": nodes})
    except Exception as exc:
        logger.warning("workflow_trace failed session=%s: %s", session_id, exc)
        return JSONResponse(content={"nodes": [], "error": "data_unavailable"})


def _build_workflow_trace(
    client:                     Any,
    db:                         str,
    tenant_id:                  str,
    session_id:                 str,
    origin_session_id_override: str | None = None,
    delegate_child_ids:         list[str] | None = None,
) -> list[dict]:
    """
    Synchronous helper — builds the ordered trace node list for a webhook session.

    Star topology (see docs/arcos/delegate-workflow-io.md):
      All child sessions (B, C, ...) have origin_session_id = root session A.
      The trace for Session A shows intake origin + all child sessions flat.
      The trace for Session B (webhook) shows its own segments + intake origin.

    Steps:
      1. Look up origin_session_id (is this session a child of another root?)
      2. If origin exists → fetch intake origin segment (from origin session)
      3. Fetch own segments (webhook execution windows)
      4. Reverse lookup → find child sessions where origin_session_id = this session
         (only for root sessions: Session A if it IS the root, or Session B when
          viewing from Session B's perspective)
      5. Assemble ordered nodes:
           [input_origin?]
           + [webhook_exec segments] interleaved with [delegate_child sessions]
           all ordered by started_at ASC
    """
    nodes: list[dict] = []
    _seg_cols = """
        segment_id, session_id, tenant_id,
        participant_id, pool_id, agent_type_id,
        instance_id, role, agent_type,
        parent_segment_id, sequence_index,
        started_at, ended_at, duration_ms,
        outcome, close_reason, handoff_reason
    """

    # ── Step 1: resolve origin_session_id of this session ────────────────────
    # Primary: ClickHouse sessions FINAL (may be NULL for pre-fix sessions
    # where parse_routed overwrote it). Fallback: override from Redis ContextStore
    # passed by the async caller (get_workflow_trace).
    origin_session_id: str | None = None
    try:
        res = client.query(
            f"SELECT origin_session_id FROM {db}.sessions FINAL"
            " WHERE tenant_id = {{tenant_id:String}} AND session_id = {{session_id:String}}"
            " LIMIT 1",
            parameters={"tenant_id": tenant_id, "session_id": session_id},
        )
        rows = _rows_to_dicts_local(res)
        if rows:
            origin_session_id = rows[0].get("origin_session_id") or None
    except Exception as exc:
        logger.debug("workflow_trace: could not fetch origin_session_id: %s", exc)

    # Apply Redis fallback when ClickHouse returned NULL
    if not origin_session_id and origin_session_id_override:
        origin_session_id = origin_session_id_override
        logger.debug(
            "workflow_trace: using Redis fallback for origin_session_id "
            "session=%s origin=%s", session_id, origin_session_id,
        )

    # ── Step 2: origin segment (intake agent from root session) ───────────────
    if origin_session_id:
        try:
            res = client.query(
                f"""
                SELECT {_seg_cols}
                FROM {db}.segments FINAL
                WHERE tenant_id = {{tenant_id:String}}
                  AND session_id = {{origin_session_id:String}}
                  AND role = 'primary'
                ORDER BY started_at ASC
                LIMIT 1
                """,
                parameters={"tenant_id": tenant_id, "origin_session_id": origin_session_id},
            )
            origin_rows = _rows_to_dicts_local(res)
            if origin_rows:
                n = origin_rows[0]
                n["node_type"] = "input_origin"
                n["is_origin"] = True
                nodes.append(n)
        except Exception as exc:
            logger.debug("workflow_trace: could not fetch origin segment: %s", exc)

    # ── Step 3: own segments (webhook execution windows) ──────────────────────
    try:
        res = client.query(
            f"""
            SELECT {_seg_cols}
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND session_id = {{session_id:String}}
            ORDER BY started_at ASC
            """,
            parameters={"tenant_id": tenant_id, "session_id": session_id},
        )
        seg_rows = _rows_to_dicts_local(res)
    except Exception as exc:
        logger.warning("workflow_trace: could not fetch segments: %s", exc)
        seg_rows = []

    for seg in seg_rows:
        if seg.get("parent_segment_id"):
            seg["node_type"] = "specialist_output"
        else:
            seg["node_type"] = "webhook_exec"
        seg["is_origin"] = False
        nodes.append(seg)

    # ── Step 4: delegate child sessions ───────────────────────────────────────
    # Two sources for delegate children (star topology means children point
    # to the ROOT session, not to Session B, so reverse lookup by
    # origin_session_id = session_id would miss them):
    #
    #   a) pipeline_state.results: {step_id}:__child_session_id__ keys
    #      written by executeDelegate — passed in as delegate_child_ids.
    #      This is the primary, most reliable source.
    #
    #   b) ClickHouse reverse lookup origin_session_id = session_id (legacy:
    #      catches any children that DO point to this session directly).
    child_session_ids: set[str] = set(delegate_child_ids or [])

    # Legacy reverse lookup (kept for backward compat)
    try:
        res = client.query(
            f"""
            SELECT s.session_id AS child_session_id
            -- alias ANTES de FINAL. Esta query estava com a ordem invertida desde
            -- sempre e NUNCA devolveu linha: o `except` abaixo loga em DEBUG,
            -- invisivel no nivel padrao. Ver CHANGELOG 2026-08-27.
            FROM {db}.sessions AS s FINAL
            WHERE s.tenant_id = {{tenant_id:String}}
              AND s.origin_session_id = {{session_id:String}}
            ORDER BY s.opened_at ASC
            """,
            parameters={"tenant_id": tenant_id, "session_id": session_id},
        )
        for row in _rows_to_dicts_local(res):
            sid = row.get("child_session_id") or ""
            if sid:
                child_session_ids.add(sid)
    except Exception as exc:
        logger.debug("workflow_trace: could not fetch child sessions by origin: %s", exc)

    for child_sid in child_session_ids:
        if not child_sid:
            continue
        try:
            res = client.query(
                f"""
                SELECT {_seg_cols}
                FROM {db}.segments FINAL
                WHERE tenant_id = {{tenant_id:String}}
                  AND session_id = {{child_session_id:String}}
                  AND role = 'primary'
                ORDER BY started_at ASC
                LIMIT 1
                """,
                parameters={"tenant_id": tenant_id, "child_session_id": child_sid},
            )
            child_segs = _rows_to_dicts_local(res)
            for seg in child_segs:
                seg["node_type"] = "delegate_child"
                seg["is_origin"] = False
                nodes.append(seg)
        except Exception as exc:
            logger.debug("workflow_trace: could not fetch child segment session=%s: %s", child_sid, exc)

    # ── Step 5: sort all nodes by started_at (chronological) ─────────────────
    # input_origin is always first; rest by started_at ASC
    origin_nodes = [n for n in nodes if n.get("node_type") == "input_origin"]
    other_nodes  = [n for n in nodes if n.get("node_type") != "input_origin"]
    other_nodes.sort(key=lambda n: n.get("started_at") or "")

    return origin_nodes + other_nodes


def _rows_to_dicts_local(result: Any) -> list[dict]:
    """Convert ClickHouse query result rows to list of dicts (local helper)."""
    if not result or not result.result_rows:
        return []
    cols = result.column_names
    out  = []
    for row in result.result_rows:
        d: dict = {}
        for col, val in zip(cols, row):
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            d[col] = val
        out.append(d)
    return out


# ─── GET /sessions/{session_id}/pipeline-state ────────────────────────────────
#
# Arc 19: Returns the pipeline_state (step transitions) and ContextStore entries
# for a webhook workflow session. Used by WebhookSegmentDetail to show the rich
# step-level view without accessing Redis from the browser.
#
# Response shape:
#   {
#     pipeline_state: {
#       flow_id:         str
#       current_step_id: str
#       status:          str
#       started_at:      str
#       updated_at:      str
#       transitions:     [ { from_step, to_step, reason, timestamp }, ... ]
#     } | null,
#     context: { [tag]: { value, confidence, source, visibility, updated_at } }
#   }

@router.get("/{session_id}/pipeline-state")
async def get_pipeline_state(
    session_id: str,
    request:    Request,
    tenant_id:  str = Query(...),
) -> JSONResponse:
    """
    Reads pipeline_state from Redis for a webhook workflow session.
    Also reads ContextStore entries (agents_only subset for analytics access).

    Falls back to empty on error — Redis keys may have expired for old sessions.
    """
    redis = request.app.state.redis
    try:
        pipeline_key = f"{tenant_id}:pipeline:{session_id}"
        ctx_key      = f"{tenant_id}:ctx:{session_id}"

        pipeline_raw, ctx_raw = await asyncio.gather(
            redis.get(pipeline_key),
            redis.hgetall(ctx_key),
            return_exceptions=True,
        )

        # Backward-compat: pre-fix bridge stored webhook pipeline under
        # {session_id}--seg--{8chars} (conference-specialist logic applied
        # incorrectly to primary webhook agents).  Scan for the old key.
        if not pipeline_raw or isinstance(pipeline_raw, Exception):
            try:
                seg_keys = await redis.keys(f"{pipeline_key}--seg--*")
                if seg_keys:
                    pipeline_raw = await redis.get(seg_keys[0])
            except Exception:
                pass

        # ── Pipeline state ───────────────────────────────────────────────────
        pipeline_state = None
        # Fase E.1: I/O por step (o que cada suspend/delegate recebeu no resume).
        # Extraído de pipeline_state.results, que guarda por step:
        #   {step}:__resume_decision__  → decision (input|approved|rejected|timeout)
        #   {step}:__resume_payload__   → payload recebido no resume
        #   {step}:__child_session_id__ → sessão-filho/specialist do delegate
        step_io: dict = {}
        if pipeline_raw and not isinstance(pipeline_raw, Exception):
            try:
                ps = json.loads(pipeline_raw)
                pipeline_state = {
                    "flow_id":         ps.get("flow_id", ""),
                    "current_step_id": ps.get("current_step_id", ""),
                    "status":          ps.get("status", ""),
                    "started_at":      ps.get("started_at", ""),
                    "updated_at":      ps.get("updated_at", ""),
                    "transitions":     ps.get("transitions", []),
                }
                results = ps.get("results", {}) or {}
                for _key, _val in results.items():
                    if not isinstance(_key, str):
                        continue
                    for _suffix, _field in (
                        (":__resume_decision__",   "decision"),
                        (":__resume_payload__",    "payload"),
                        (":__child_session_id__",  "child_session_id"),
                    ):
                        if _key.endswith(_suffix):
                            _step = _key[: -len(_suffix)]
                            step_io.setdefault(_step, {})[_field] = _val
                # Fase E.3: resumed_by = source do payload de resume
                # (agent | external | timeout_scanner | customer_reconnect).
                for _io in step_io.values():
                    _pl = _io.get("payload")
                    if isinstance(_pl, dict) and _pl.get("source"):
                        _io["resumed_by"] = str(_pl["source"])
            except Exception:
                pass

        # ── ContextStore — only agents_only or all visibility entries ────────
        context: dict = {}
        if ctx_raw and not isinstance(ctx_raw, Exception):
            for tag, raw_val in ctx_raw.items():
                if isinstance(tag, bytes):
                    tag = tag.decode()
                if isinstance(raw_val, bytes):
                    raw_val = raw_val.decode()
                try:
                    entry = json.loads(raw_val)
                    vis   = entry.get("visibility", "all")
                    # Exclude customer-visible PII (visibility=all) from analytics access
                    if vis in ("agents_only", "all"):
                        context[tag] = {
                            "value":      entry.get("value"),
                            "confidence": entry.get("confidence"),
                            "source":     entry.get("source"),
                            "visibility": vis,
                            "updated_at": entry.get("updated_at"),
                        }
                except Exception:
                    pass

        return JSONResponse(content={
            "pipeline_state": pipeline_state,
            "context":        context,
            "step_io":        step_io,
        })

    except Exception as exc:
        logger.warning("pipeline_state failed session=%s: %s", session_id, exc)
        return JSONResponse(content={"pipeline_state": None, "context": {}, "step_io": {}})
