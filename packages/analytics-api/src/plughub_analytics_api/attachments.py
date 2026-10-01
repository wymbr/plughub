"""
attachments.py — a porta INTERNA do anexo do cliente (ATT-02, 2026-10-01).

    GET /v1/attachments/{file_id}      (pela borda: /analytics/v1/attachments/{file_id})

Até aqui o Console e o transcript abriam o anexo pela porta PÚBLICA do gateway, com a URL
absoluta gravada no stream e um `<img src>` sem credencial: quem tivesse o `file_id` via o arquivo,
e ninguém ficava sabendo. Esta porta decide como a transcrição decide, porque o anexo é conteúdo da
mesma conversa (decisão do dono, 2026-10-01 — `contacts.transcricao`, sem campo novo):

  1. credencial — Bearer de usuário (`optional_pool_principal`)                     → 401
  2. capacidade — `contacts.transcricao`, conferida ANTES de resolver o id: quem não
     pode ler não aprende se o id existe                                           → 403
  3. linha      — o pool da SESSÃO do anexo no escopo do chamador
     (`authorize_session_scope`, viva e fechada — a mesma resposta do transcript)   → 403

TODO desfecho vai à trilha (`audit_access_log`, de que esta API é a única escritora): servido,
recusado, desconhecido, expirado, indisponível. A recusa grava ANTES de responder — portão que
levanta antes do corpo deixa a recusa fora da trilha (lição da Audit LGPD).

Os bytes vêm do gateway, por rota interna com `X-Service-Token`; a decisão nunca mora lá.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from plughub_authz import abac_can

from .audit import _record_access
from .config import get_settings
from .pool_auth import PoolPrincipal, authorize_session_scope, optional_pool_principal

logger = logging.getLogger("plughub.analytics.attachments")

router = APIRouter(prefix="/v1/attachments", tags=["attachments"])

ENDPOINT = "analytics-api:attachments.view"
FIELD = "transcricao"

# Cabeçalhos que a resposta repassa do gateway (que já os calcula pela regra da ATT-01) —
# e os de segurança, que esta porta garante por conta própria, mesmo que o gateway os perca.
_PASS_THROUGH = ("content-type", "content-disposition", "content-length")
_SECURITY = {
    "X-Content-Type-Options":  "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cache-Control":           "no-store",
}


async def _gateway_get(path: str, tenant_id: str) -> httpx.Response:
    s = get_settings()
    if not s.channel_gateway_url or not s.channel_gateway_service_token:
        raise RuntimeError("PLUGHUB_CHANNEL_GATEWAY_URL/SERVICE_TOKEN ausentes na analytics-api")
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.get(
            f"{s.channel_gateway_url}/v1/attachments/{path}",
            params={"tenant_id": tenant_id},
            headers={"X-Service-Token": s.channel_gateway_service_token,
                     "X-Service-Name": "analytics-api"},
        )


@router.get("/{file_id}")
async def view_attachment(
    file_id:   str,
    request:   Request,
    tenant_id: str | None = Query(None),
    principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    # O tenant é o do TOKEN de usuário (TNT-01; `optional_pool_principal` já recusa a query que
    # o contradiz). Só principal de serviço escolhe o tenant pela query.
    tenant_id = principal.tenant_id or tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    actor_sub = principal.sub or ""
    actor_kind = "user" if principal.module_config is not None else "service"

    async def trilha(result: str, target: str, rows: int = 0) -> None:
        await _record_access(
            request, tenant_id=tenant_id, actor_sub=actor_sub, actor_kind=actor_kind,
            endpoint=ENDPOINT, target_kind="attachment", target_id=target,
            result=result, row_count=rows,
        )

    # 2 · capacidade, antes de qualquer pergunta sobre o id
    if principal.module_config is not None and not abac_can(
            {"module_config": principal.module_config}, "contacts", FIELD, "read_only"):
        await trilha("denied", file_id)
        raise HTTPException(status_code=403, detail=f"capability_denied: contacts.{FIELD}")

    try:
        meta_r = await _gateway_get(f"{file_id}/meta", tenant_id)
    except Exception as exc:  # noqa: BLE001 — degrada nomeando, e a trilha registra
        logger.error("attachment %s: gateway inalcançável para meta — %s", file_id, exc)
        await trilha("unavailable", file_id)
        raise HTTPException(status_code=503, detail="attachment_backend_unavailable") from exc
    if meta_r.status_code == 404:
        await trilha("not_found", file_id)
        raise HTTPException(status_code=404, detail="not found")
    if meta_r.status_code != 200:
        logger.error("attachment %s: meta respondeu %s", file_id, meta_r.status_code)
        await trilha("unavailable", file_id)
        raise HTTPException(status_code=503, detail="attachment_backend_unavailable")

    meta = meta_r.json()
    session_id = str(meta.get("session_id") or "")
    target = f"{session_id}/{file_id}"

    # 3 · escopo pela sessão do anexo (a capacidade já passou; o campo é repetido de propósito,
    # para o decisor único continuar dono dos dois eixos)
    try:
        await authorize_session_scope(
            principal, tenant_id, session_id, rota="attachments.view", campo=FIELD,
            redis=request.app.state.redis, store=request.app.state.store,
        )
    except HTTPException:
        await trilha("denied", target)
        raise

    if meta.get("expired"):
        await trilha("expired", target)
        raise HTTPException(status_code=410, detail="attachment expired")
    try:
        content_r = await _gateway_get(f"{file_id}/content", tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("attachment %s: gateway inalcançável para conteúdo — %s", file_id, exc)
        await trilha("unavailable", target)
        raise HTTPException(status_code=503, detail="attachment_backend_unavailable") from exc
    if content_r.status_code == 423:
        # ATT-05: o gateway (serve_refusal, a regra única) não serve o que o antivírus não liberou
        await trilha("pending_scan", target)
        raise HTTPException(status_code=423, detail="attachment_pending_scan")
    if content_r.status_code != 200:
        logger.error("attachment %s: conteúdo respondeu %s", file_id, content_r.status_code)
        await trilha("unavailable", target)
        raise HTTPException(status_code=content_r.status_code if content_r.status_code in (404, 410)
                            else 503, detail="attachment_unavailable")

    await trilha("ok", target, rows=1)
    headers = {k: v for k, v in content_r.headers.items() if k.lower() in _PASS_THROUGH}
    headers.update(_SECURITY)
    return Response(content=content_r.content, status_code=200, headers=headers,
                    media_type=content_r.headers.get("content-type"))
