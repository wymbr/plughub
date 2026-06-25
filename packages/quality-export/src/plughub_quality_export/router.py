"""
router.py
POST /v1/export/sessions — re-emit internal sessions through the quality-ingest
contract for re-evaluation.

  Body: {"tenant_id": "...", "session_ids": ["..."], "source": "internal:reeval"?}
  (tenant_id may also come from the X-Tenant-ID header.)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from .config import get_settings
from .ch_client import ClickHouseClient
from .exporter import InternalExporter

logger = logging.getLogger("plughub.quality_export.router")

router = APIRouter()


class ExportBody(BaseModel):
    tenant_id:   str | None = None
    session_ids: list[str]
    source:      str | None = None


@router.post("/v1/export/sessions")
async def export_sessions(
    body: ExportBody,
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict:
    tenant_id = body.tenant_id or x_tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id (body or X-Tenant-ID header) is required")
    if not body.session_ids:
        raise HTTPException(status_code=400, detail="session_ids must be non-empty")

    settings = get_settings()
    ch: ClickHouseClient = request.app.state.ch
    exporter = InternalExporter(
        ch, settings.quality_ingest_url, source=body.source or settings.export_source,
    )

    results = []
    for sid in body.session_ids:
        try:
            results.append(await exporter.export_session(tenant_id, sid))
        except Exception as exc:  # noqa: BLE001 — report per-session, never 500 the batch
            logger.warning("export failed for %s: %s", sid, exc)
            results.append({"session_id": sid, "status": "error", "error": str(exc)})

    exported = [r for r in results if r.get("status") == "exported"]
    reeval_ids = [s for r in exported for s in r.get("reeval_session_ids", [])]
    return {
        "tenant_id": tenant_id,
        "source": body.source or settings.export_source,
        "requested": len(body.session_ids),
        "exported": len(exported),
        "reeval_session_ids": reeval_ids,
        "results": results,
    }
