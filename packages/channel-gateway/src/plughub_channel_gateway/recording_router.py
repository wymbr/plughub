"""
recording_router.py — OUVIR e EXPORTAR a gravação da chamada (VOZ-36).

A gravação (VOZ-06) é guardada como `call_recording` e a porta pública de anexos a recusa: o file_id
não é credencial para a voz do cliente e do atendente. Esta é a porta de quem TEM direito.

Decisão do dono (2026-09-18) — UM campo ABAC, `contacts.recording`, com dois níveis ORDENADOS:

    read_only   OUVIR  (streaming, dentro da plataforma)
    read_write  EXPORTAR uma cópia (download) — exportar IMPLICA ouvir, por construção

Três eixos, TODOS por PARTE, e nenhum vale sozinho:

  1. credencial — Bearer do usuário (`plughub_authz`, nunca uma cópia)             → 401
  2. capacidade — `contacts.recording` ≥ nível, recortada ao pool (`scope` do grant) → 403
  3. linha      — o pool no domínio do chamador (`accessible_pools`)                 → 403

O pool que decide é o que ATENDEU a parte (`attrs.pools`, gravado pelo `CallRecorder`), nunca o
de entrada da sessão — um supervisor do pool de retenção ouve a parte que o pool dele atendeu, não
a triagem de antes. Parte sem `pools` (não deveria existir) é RECUSADA: sem saber de quem é, não se
entrega.

CADA acesso vai à trilha LGPD (`audit_access_log`), INCLUSIVE a recusa e a sem credencial — a
lição de 2026-08 foi a recusa que subia antes do corpo e nunca chegava à trilha. A trilha é da
analytics-api (única escritora); daqui ela recebe um evento `audit.access` pelo Kafka. Publicar
não bloqueia a resposta, mas nunca some calado: falha vira ERROR nomeando o acesso perdido.

⚠️ A LISTA de partes de uma sessão (metadados: duração, pools, datas) não vai à trilha — não é o
conteúdo. Ouvir e exportar vão.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from plughub_authz import abac_can, bearer_from_header, pool_in_scope, verify_user_jwt

from . import main as _main_module

logger = logging.getLogger("plughub.channel-gateway.recording")

router = APIRouter(prefix="/v1/recordings")

ARTIFACT_CLASS = "call_recording"
MODULE, FIELD = "contacts", "recording"
AUDIT_TOPIC = "audit.access"
LISTEN, EXPORT = "read_only", "read_write"


# ── trilha ────────────────────────────────────────────────────────────────────

async def _audit(*, tenant_id: str, actor_sub: str, actor_kind: str, action: str,
                 target_id: str, result: str) -> None:
    """Um fato de acesso a dado pessoal → `audit.access` → `audit_access_log` (analytics-api)."""
    evento = {
        "event_id":    str(uuid.uuid4()),
        "tenant_id":   tenant_id,
        "actor_sub":   actor_sub,
        "actor_kind":  actor_kind,
        "endpoint":    f"channel-gateway:recording.{action}",
        "target_kind": "recording",
        "target_id":   target_id,
        "result":      result,
        "row_count":   1 if result == "ok" else 0,
        "accessed_at": datetime.now(timezone.utc).isoformat(),
    }
    producer = _main_module._producer
    if producer is None:
        logger.error("recording: acesso %s de %s a %s (%s) NAO foi a trilha — sem produtor Kafka",
                     action, actor_sub or "-", target_id, result)
        return
    try:
        await producer.send(AUDIT_TOPIC, key=tenant_id.encode("utf-8"),
                            value=json.dumps(evento).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — dito, nunca calado
        logger.error("recording: acesso %s de %s a %s (%s) NAO foi a trilha: %s",
                     action, actor_sub or "-", target_id, result, exc)


# ── portão ────────────────────────────────────────────────────────────────────

def _claims(request: Request) -> dict[str, Any] | None:
    secret = _main_module.get_settings().auth_jwt_secret
    if not secret:
        # dado pessoal: sem como provar quem é, não se entrega — e a falha é do SERVIÇO (503),
        # a mesma postura do `_check_audit_access` da analytics-api
        logger.error("recording: PLUGHUB_AUTH_JWT_SECRET vazio — toda escuta de gravação recusada")
        raise HTTPException(status_code=503, detail="auth_jwt_secret nao configurado")
    tok = bearer_from_header(request.headers.get("authorization"))
    payload = verify_user_jwt(tok, secret) if tok else None
    if not payload or not str(payload.get("sub") or ""):
        return None
    return payload


def _pode(claims: dict[str, Any], pools: list[str], nivel: str) -> bool:
    """O chamador exerce `contacts.recording` ≥ `nivel` em ALGUM pool que atendeu a parte, e esse
    pool está no domínio de linhas dele. Sem pools, ninguém."""
    return any(p and abac_can(claims, MODULE, FIELD, nivel, scope_id=p)
               and pool_in_scope(claims, p, "recording") for p in pools)


def _pools(meta: Any) -> list[str]:
    attrs = getattr(meta, "attrs", None) or {}
    return [str(p) for p in (attrs.get("pools") or []) if p]


async def _parte_autorizada(request: Request, file_id: str, nivel: str, action: str):
    """(claims, meta) de uma parte que o chamador pode `action` — ou HTTPException, com a trilha."""
    settings = _main_module.get_settings()
    claims = _claims(request)
    if claims is None:
        await _audit(tenant_id=settings.tenant_id, actor_sub="", actor_kind="anonymous",
                     action=action, target_id=file_id, result="denied")
        raise HTTPException(status_code=401, detail="gravacao exige credencial")
    tenant = str(claims.get("tenant_id") or "")
    sub = str(claims.get("sub"))
    # capacidade ANTES de resolver: quem não tem o campo recebe 403 exista ou não o id — senão
    # 404 × 403 viraria oráculo de existência para quem não pode nem ouvir
    if not abac_can(claims, MODULE, FIELD, nivel):
        await _audit(tenant_id=tenant, actor_sub=sub, actor_kind="user", action=action,
                     target_id=file_id, result="denied")
        logger.warning("recording %s NEGADO: sub=%s sem %s.%s >= %s", action, sub, MODULE, FIELD, nivel)
        raise HTTPException(status_code=403, detail=f"capability_denied: {MODULE}.{FIELD} ({nivel})")
    store = _main_module._attachment_store
    if store is None:
        raise HTTPException(status_code=503, detail="attachment store not available")
    try:
        meta = await store.resolve(file_id=file_id, tenant_id=tenant)
    except (ValueError, TypeError):
        meta = None     # file_id que nem é UUID: mesma resposta do inexistente
    # outra classe, outro tenant ou inexistente: a MESMA resposta — não se confirma que o id existe
    if meta is None or (getattr(meta, "artifact_class", "") or "") != ARTIFACT_CLASS:
        await _audit(tenant_id=tenant, actor_sub=sub, actor_kind="user", action=action,
                     target_id=file_id, result="denied")
        raise HTTPException(status_code=404, detail="gravacao nao encontrada")
    alvo = f"{meta.session_id}/{file_id}"
    pools = _pools(meta)
    if not _pode(claims, pools, nivel):
        await _audit(tenant_id=tenant, actor_sub=sub, actor_kind="user", action=action,
                     target_id=alvo, result="denied")
        logger.warning("recording %s NEGADO: sub=%s fora dos pools da parte %s", action, sub, pools or "-")
        raise HTTPException(status_code=403, detail="pool_scope_denied: a parte foi atendida por "
                                                    "pool fora do seu escopo")
    if meta.deleted_at is not None:
        raise HTTPException(status_code=410, detail="gravacao expirada")
    return claims, meta, alvo


async def _entrega(meta: Any, disposition: str) -> StreamingResponse:
    store = _main_module._attachment_store
    try:
        corpo = await store.stream_bytes(file_id=meta.file_id, tenant_id=meta.tenant_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(corpo, media_type=meta.mime_type, headers={
        "Content-Disposition": f'{disposition}; filename="{meta.original_name}"',
        "Content-Length":      str(meta.size_bytes),
        # dado pessoal: nada de cache compartilhado nem reaproveitado
        "Cache-Control":       "no-store",
    })


# ── rotas ─────────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}")
async def list_session_recordings(session_id: str, request: Request) -> dict:
    """As partes gravadas de uma sessão que o chamador pode OUVIR, e se pode exportar cada uma.
    Partes fora do escopo não aparecem; `omitted` diz quantas — "não há gravação" e "há, mas não é
    sua" são respostas diferentes."""
    claims = _claims(request)
    if claims is None:
        raise HTTPException(status_code=401, detail="gravacao exige credencial")
    if not abac_can(claims, MODULE, FIELD, LISTEN):
        raise HTTPException(status_code=403, detail=f"capability_denied: {MODULE}.{FIELD}")
    store = _main_module._attachment_store
    if store is None:
        raise HTTPException(status_code=503, detail="attachment store not available")
    metas = await store.list_session(tenant_id=str(claims.get("tenant_id") or ""),
                                     session_id=session_id, artifact_class=ARTIFACT_CLASS)
    partes, omitidas = [], 0
    for m in metas:
        pools = _pools(m)
        if not _pode(claims, pools, LISTEN):
            omitidas += 1
            continue
        a = m.attrs or {}
        partes.append({
            "file_id": m.file_id, "part": a.get("part"), "pools": pools,
            "duration_ms": a.get("duration_ms"), "started_at": a.get("started_at"),
            "ended_at": a.get("ended_at"), "size_bytes": m.size_bytes, "mime_type": m.mime_type,
            "expires_at": m.expires_at.isoformat() if m.expires_at else None,
            "can_export": _pode(claims, pools, EXPORT),
        })
    return {"session_id": session_id, "parts": partes, "omitted": omitidas}


@router.get("/{file_id}/audio")
async def listen_recording(file_id: str, request: Request) -> StreamingResponse:
    claims, meta, alvo = await _parte_autorizada(request, file_id, LISTEN, "listen")
    await _audit(tenant_id=meta.tenant_id, actor_sub=str(claims.get("sub")), actor_kind="user",
                 action="listen", target_id=alvo, result="ok")
    return await _entrega(meta, "inline")


@router.get("/{file_id}/export")
async def export_recording(file_id: str, request: Request) -> StreamingResponse:
    claims, meta, alvo = await _parte_autorizada(request, file_id, EXPORT, "export")
    await _audit(tenant_id=meta.tenant_id, actor_sub=str(claims.get("sub")), actor_kind="user",
                 action="export", target_id=alvo, result="ok")
    logger.info("recording EXPORTADA: sub=%s alvo=%s — a copia sai do controle da plataforma",
                claims.get("sub"), alvo)
    return await _entrega(meta, "attachment")
