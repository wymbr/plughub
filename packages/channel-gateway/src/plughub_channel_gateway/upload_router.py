"""
upload_router.py
HTTP endpoints for the two-step file upload flow.

Routes:
  POST /webchat/v1/upload/{file_id}
      Receives binary body for a previously reserved slot (upload.request).
      On success calls attachment_store.commit() and publishes upload.committed
      back to the WebSocket via SessionRegistry.

  GET  /webchat/v1/attachments/{file_id}
      Streams the binary content of a committed attachment.
      Returns 410 Gone if the file has been soft-deleted (expired).
      Returns 404 if the file_id is unknown.

Security:
  Upload endpoint validates that the file_id was reserved by a known session
  (status='pending' in session_attachments).  The commit() call enforces this.
  Serving endpoint requires no auth in phase 1 — file_id acts as an opaque
  capability token (high-entropy UUID).  Add signed URL verification in phase 2.

Content-Type enforcement:
  The actual MIME type of uploaded bytes is currently trusted from the original
  reserve request (phase 1).  Phase 2 will add magic-byte validation.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from . import main as _main_module  # for access to _attachment_store + _registry
from .attachment_store import MIME_TO_CONTENT_TYPE
from .usage_emitter import emit_attachment

logger = logging.getLogger("plughub.channel-gateway.upload")

router = APIRouter(prefix="/webchat/v1")


# ── POST /webchat/v1/upload/{file_id} ─────────────────────────────────────────

@router.post("/upload/{file_id}", status_code=204)
async def upload_file(file_id: str, request: Request) -> Response:
    """
    Accepts the binary body for a previously reserved upload slot.
    The client sends this after receiving upload.ready from the WebSocket.
    On success, delivers upload.committed to the client's WebSocket.
    """
    store    = _main_module._attachment_store
    registry = _main_module._registry
    settings = _main_module.get_settings()

    if store is None:
        raise HTTPException(status_code=503, detail="attachment store not available")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")

    try:
        meta = await store.commit(
            file_id   = file_id,
            tenant_id = settings.tenant_id,
            data      = data,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("commit failed file_id=%s: %s", file_id, exc)
        raise HTTPException(status_code=500, detail="upload commit failed") from exc

    # Notify client via WebSocket
    content_type = MIME_TO_CONTENT_TYPE.get(meta.mime_type, "document")
    committed_msg = {
        "type":         "upload.committed",
        "file_id":      meta.file_id,
        "url":          meta.serving_url,
        "mime_type":    meta.mime_type,
        "size_bytes":   meta.size_bytes,
        "content_type": content_type,
    }
    if registry is not None:
        # contact_id is stored in Redis under session meta; look it up via file's session_id
        contact_id = await _main_module._redis.get(
            f"session:{meta.session_id}:contact_id"
        )
        if contact_id:
            await registry.send(contact_id, committed_msg)
        else:
            logger.warning("upload.committed: no contact_id for session=%s", meta.session_id)

    # Metering: emit webchat_attachments usage event (fire-and-forget)
    if _main_module._producer is not None:
        await emit_attachment(
            producer   = _main_module._producer,
            tenant_id  = settings.tenant_id,
            session_id = meta.session_id,
            file_id    = meta.file_id,
            mime_type  = meta.mime_type,
            size_bytes = meta.size_bytes,
        )

    logger.info(
        "upload committed file_id=%s session=%s size=%d",
        file_id, meta.session_id, meta.size_bytes,
    )
    return Response(status_code=204)


# ── GET /webchat/v1/attachments/{file_id} ─────────────────────────────────────

@router.get("/attachments/{file_id}")
async def serve_attachment(file_id: str) -> StreamingResponse:
    """
    Streams the binary content of a committed attachment.
    The file_id is the capability token — no additional auth required in phase 1.
    """
    store    = _main_module._attachment_store
    settings = _main_module.get_settings()

    if store is None:
        raise HTTPException(status_code=503, detail="attachment store not available")

    meta = await store.resolve(file_id=file_id, tenant_id=settings.tenant_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")
    if meta.deleted_at is not None:
        raise HTTPException(status_code=410, detail="attachment expired")
    if meta.file_path is None:
        raise HTTPException(status_code=404, detail="file not committed")

    try:
        stream: AsyncIterator[bytes] = await store.stream_bytes(
            file_id=file_id, tenant_id=settings.tenant_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return StreamingResponse(
        stream,
        media_type = meta.mime_type,
        headers    = {
            "Content-Disposition": f'inline; filename="{meta.original_name}"',
            "Content-Length":      str(meta.size_bytes),
            "Cache-Control":       "private, max-age=3600",
        },
    )
