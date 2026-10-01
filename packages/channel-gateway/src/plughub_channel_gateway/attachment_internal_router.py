"""
attachment_internal_router.py — os BYTES do anexo do cliente para quem já decidiu (ATT-02).

Quem decide se um operador ou supervisor pode VER um anexo é a analytics-api: a capacidade
(`contacts.transcricao`, decisão do dono em 2026-10-01 — o anexo é conteúdo da conversa), o escopo
pelo pool da sessão (`authorize_session_scope`, a mesma resposta do transcript) e a trilha
(`audit_access_log`, de que ela é a única escritora). Este router não decide nada disso: ele só
entrega, e só a quem apresenta a credencial de SERVIÇO do gateway.

    GET /v1/attachments/{file_id}/meta     → de qual sessão é, tipo, tamanho, se expirou
    GET /v1/attachments/{file_id}/content  → os bytes, com os cabeçalhos da porta (ATT-01)
        ?variant=blurred                    → a PRÉVIA BORRADA de uma imagem (ATT-06), para quem
                                              não atende o contato; quem decide é a analytics-api

O tenant vem da query: principal de serviço escolhe o tenant (TNT-01). Usuário recebe 403 aqui
mesmo com grant — rota interna não é porta alternativa para quem a analytics-api recusaria.

Só `webchat_attachment`: a gravação de chamada tem porta própria (VOZ-36) e não sai por aqui.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from . import main as _main_module
from .attachment_store import (
    SERVE_SECURITY_HEADERS,
    content_disposition,
    serve_refusal,
    served_media_type,
)
from .identity_auth import identity_principal
from .media_sanitize import blurred_preview, is_image

logger = logging.getLogger("plughub.channel-gateway.attachment-internal")

router = APIRouter(prefix="/v1/attachments")

ARTIFACT_CLASS = "webchat_attachment"


def _service_only(request: Request) -> None:
    s = _main_module.get_settings()
    identity_principal(request, service_token=s.channel_gateway_service_token,
                       jwt_secret=s.auth_jwt_secret)


async def _meta(file_id: str, tenant_id: str):
    store = _main_module._attachment_store
    if store is None:
        raise HTTPException(status_code=503, detail="attachment store not available")
    meta = await store.resolve(file_id=file_id, tenant_id=tenant_id)
    klass = (getattr(meta, "artifact_class", None) or ARTIFACT_CLASS) if meta else None
    if meta is None or klass != ARTIFACT_CLASS:
        raise HTTPException(status_code=404, detail="not found")
    return store, meta


@router.get("/{file_id}/meta")
async def attachment_meta(file_id: str, request: Request, tenant_id: str = Query(...)) -> dict:
    _service_only(request)
    _, meta = await _meta(file_id, tenant_id)
    return {
        "file_id":    file_id,
        "tenant_id":  tenant_id,
        "session_id": meta.session_id,
        "mime_type":  meta.mime_type,
        "size_bytes": meta.size_bytes,
        "expired":    meta.deleted_at is not None,
        "committed":  meta.file_path is not None,
        "scan_status": getattr(meta, "scan_status", None),   # ATT-05
    }


@router.get("/{file_id}/content")
async def attachment_content(file_id: str, request: Request,
                             tenant_id: str = Query(...),
                             variant: str | None = Query(None)) -> Response:
    _service_only(request)
    if variant not in (None, "", "blurred"):
        raise HTTPException(status_code=400, detail=f"unknown variant: {variant}")
    store, meta = await _meta(file_id, tenant_id)
    if meta.deleted_at is not None:
        raise HTTPException(status_code=410, detail="attachment expired")
    recusa = serve_refusal(meta)   # ATT-05: só sai o que o antivírus disse `clean`
    if recusa is not None:
        raise HTTPException(status_code=recusa[0], detail=recusa[1])
    if meta.file_path is None:
        raise HTTPException(status_code=404, detail="file not committed")
    if variant == "blurred" and not is_image(meta.mime_type):
        # ATT-06: só imagem tem prévia borrada; o resto se revela ou não se vê
        raise HTTPException(status_code=415, detail="no_blurred_variant")
    try:
        stream: AsyncIterator[bytes] = await store.stream_bytes(file_id=file_id, tenant_id=tenant_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if variant == "blurred":
        # ATT-06: a prévia de quem não atende — reduzida e borrada AQUI; o original não sai
        try:
            borrada = blurred_preview(b"".join([c async for c in stream]), meta.mime_type)
        except ValueError as exc:
            logger.warning("attachment %s: prévia borrada falhou — %s", file_id, exc)
            raise HTTPException(status_code=422, detail="blurred_preview_failed") from exc
        return Response(content=borrada, media_type="image/jpeg", headers={
            "Content-Disposition": content_disposition("preview.jpg", "image/jpeg"),
            "Cache-Control":       "no-store",
            **SERVE_SECURITY_HEADERS,
        })
    media_type = served_media_type(meta.mime_type)
    return StreamingResponse(
        stream,
        media_type = media_type,
        headers    = {
            "Content-Disposition": content_disposition(meta.original_name, media_type),
            "Content-Length":      str(meta.size_bytes),
            "Cache-Control":       "no-store",
            **SERVE_SECURITY_HEADERS,
        },
    )
