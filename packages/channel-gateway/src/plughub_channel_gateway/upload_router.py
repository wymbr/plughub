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

Content-Type enforcement (ATT-01, 2026-10-01):
  commit() checks the class allowlist, the REAL size and the magic bytes (fail-closed) for
  every writer. Serving adds nosniff + CSP sandbox, and only images go inline.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from . import main as _main_module  # for access to _attachment_store + _registry
from .attachment_store import (
    MIME_TO_CONTENT_TYPE,
    SERVE_SECURITY_HEADERS,
    content_disposition,
    AttachmentInfected,
    webchat_upload_limit,
    serve_refusal,
    served_media_type,
    sign_attachment_url,
    verify_attachment_signature,
)
from .usage_emitter import emit_attachment
from .webchat_config import webchat_config

logger = logging.getLogger("plughub.channel-gateway.upload")

router = APIRouter(prefix="/webchat/v1")


def public_attachment_url(file_id: str, session_id: str) -> str:
    """A URL da porta pública para ESTE anexo desta sessão, assinada agora (ATT-03)."""
    s = _main_module.get_settings()
    url = sign_attachment_url(s.webchat_serving_base_url, file_id, session_id, secret=s.jwt_secret)
    if not url:
        logger.error("ATT-03: sem PLUGHUB_JWT_SECRET o anexo %s sai SEM link (nao ha como assinar)",
                     file_id)
    return url


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

    # ATT-07: o tamanho REAL contra o teto do tenant — o do reserve é só o declarado pelo cliente.
    # O teto da plataforma o commit confere de qualquer jeito (validate_content).
    slot = await store.resolve(file_id=file_id, tenant_id=settings.tenant_id)
    if slot is not None:
        limite = webchat_upload_limit(slot.mime_type, webchat_config.get("upload_limits_mb"))
        if limite is not None and len(data) > limite:
            raise HTTPException(status_code=413,
                                detail=f"arquivo muito grande: {len(data)} > {limite} bytes")

    try:
        meta = await store.commit(
            file_id   = file_id,
            tenant_id = settings.tenant_id,
            data      = data,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AttachmentInfected as exc:
        # ATT-05: o antivírus achou assinatura — nada gravado; a linha fica `rejected` com o motivo
        logger.warning("upload recusado pelo antivírus file_id=%s: %s", file_id, exc)
        raise HTTPException(status_code=422, detail="attachment_infected") from exc
    except ValueError as exc:
        # magic bytes mismatch — declared MIME does not match actual content
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("commit failed file_id=%s: %s", file_id, exc)
        raise HTTPException(status_code=500, detail="upload commit failed") from exc

    # Notify client via WebSocket
    content_type = MIME_TO_CONTENT_TYPE.get(meta.mime_type, "document")
    committed_msg = {
        "type":         "upload.committed",
        "file_id":      meta.file_id,
        # ATT-03: a URL que o cliente recebe é ASSINADA e curta, cunhada aqui
        "url":          public_attachment_url(meta.file_id, meta.session_id),
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
async def serve_attachment(
    file_id: str,
    exp: str | None = Query(None),
    sig: str | None = Query(None),
) -> StreamingResponse:
    """
    Streams the binary content of a committed attachment — to whoever holds a SIGNED URL.

    ATT-03 (2026-10-01): the bare `file_id` stopped being the credential. The URL carries `exp` and
    `sig` over `(file_id, session_id, exp)`; missing or wrong signature answers like an unknown id
    (404, no existence oracle), an expired one answers 403 `link_expired`.
    """
    store    = _main_module._attachment_store
    settings = _main_module.get_settings()

    if store is None:
        raise HTTPException(status_code=503, detail="attachment store not available")

    meta = await store.resolve(file_id=file_id, tenant_id=settings.tenant_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")
    # VOZ-06: gravação de chamada NÃO sai por aqui. Esta porta toma o file_id como credencial,
    # que serve para o anexo que o próprio cliente mandou — não para a voz dele e do atendente.
    # 404 (e não 403) para não confirmar que o id existe; o motivo vai ao log.
    klass = getattr(meta, "artifact_class", None) or "webchat_attachment"
    if klass != "webchat_attachment":
        logger.warning("attachment %s de classe %s RECUSADO na porta publica de anexos",
                       file_id, klass)
        raise HTTPException(status_code=404, detail="not found")
    motivo = verify_attachment_signature(file_id, str(meta.session_id), exp, sig,
                                         secret=settings.jwt_secret)
    if motivo == "expired":
        raise HTTPException(status_code=403, detail="link_expired")
    if motivo is not None:
        logger.warning("ATT-03: anexo %s pedido na porta publica com assinatura %s — 404",
                       file_id, motivo)
        raise HTTPException(status_code=404, detail="not found")
    if meta.deleted_at is not None:
        raise HTTPException(status_code=410, detail="attachment expired")
    recusa = serve_refusal(meta)   # ATT-05: só sai o que o antivírus disse `clean`
    if recusa is not None:
        raise HTTPException(status_code=recusa[0], detail=recusa[1])
    if meta.file_path is None:
        raise HTTPException(status_code=404, detail="file not committed")

    try:
        stream: AsyncIterator[bytes] = await store.stream_bytes(
            file_id=file_id, tenant_id=settings.tenant_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    media_type = served_media_type(meta.mime_type)
    return StreamingResponse(
        stream,
        media_type = media_type,
        headers    = {
            "Content-Disposition": content_disposition(meta.original_name, media_type),
            "Content-Length":      str(meta.size_bytes),
            "Cache-Control":       "private, max-age=3600",
            **SERVE_SECURITY_HEADERS,
        },
    )
