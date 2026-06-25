"""
router.py
The open ingest endpoint of the module.

POST /v1/ingest/events
  Header: X-Tenant-ID (required) — tenant resolved from request context, NOT the
          event body (consistent with the other curation endpoints).
  Body:   a JSON list of ingestion_event_v1 events, OR {"events": [...]}.
          Events may be interleaved across contacts (a stream, not a batch
          document) — the module correlates by external_contact_id.

The handler validates each event against the ingestion_event_v1 contract, maps the
stream to internal canonical events, and emits them. It returns a small receipt.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from .config import get_settings
from .events import IngestionEvent
from .mapper import map_events

logger = logging.getLogger("plughub.quality_ingest.router")

router = APIRouter()

_EVENTS_ADAPTER = TypeAdapter(list[IngestionEvent])


@router.post("/v1/ingest/events")
async def ingest_events(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    raw = body.get("events") if isinstance(body, dict) else body
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=400,
            detail="expected a JSON list of events or an object {\"events\": [...]}",
        )

    try:
        events = _EVENTS_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    settings = get_settings()

    # R13c — per-source identity/pool/version map (graceful: {} → pass-through).
    source_map: dict = {}
    config_client = getattr(request.app.state, "config_client", None)
    if config_client is not None:
        source_map = await config_client.get_source_map(x_tenant_id)

    pairs = map_events(events, tenant_id=x_tenant_id, settings=settings, source_map=source_map)

    emitter = request.app.state.emitter
    emitted = await emitter.emit_many(pairs)

    session_ids = sorted({
        payload["session_id"]
        for topic, payload in pairs
        if topic == settings.topic_session_closed and "session_id" in payload
    })

    logger.info(
        "ingest: tenant=%s accepted=%d emitted=%d/%d sessions=%d",
        x_tenant_id, len(events), emitted, len(pairs), len(session_ids),
    )
    return {
        "schema": "ingestion_event_v1",
        "accepted": len(events),
        "canonical_emitted": emitted,
        "canonical_total": len(pairs),
        "session_ids": session_ids,
    }
