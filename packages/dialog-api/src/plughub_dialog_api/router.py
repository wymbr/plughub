"""
router.py
FastAPI routes for the Dialog API — generic scripted-dialog form store.

Tenant scoping via header X-Tenant-ID (all endpoints).
Escrita = portao DUAL (X-Admin-Token de sistema OU Bearer + ABAC
`config.dialog_forms` read_write), delegado ao verificador canonico
`plughub_authz`. Leituras sao ABERTAS: o `form_get` do mcp-server e o survey
web do channel-gateway sao chamadores de runtime sem credencial, e o conteudo
e masked-by-construction (nenhum valor de PII no store).

Endpoints (all under /v1/dialog/forms):
  GET    /                         → list latest version metadata per form (?include_deleted)
  GET    /{form_id}                → resolve one form (?status=published&version=N)
  POST   /                         → create a new draft version
  PUT    /{form_id}                → edit (draft in place, or new draft version)
  POST   /{form_id}/publish        → publish a version (?version=N; default latest draft)
  DELETE /{form_id}                → arquiva (ou PURGA, se nunca publicado)
  POST   /{form_id}/undelete       → restaura form arquivado

Arquivamento (ADR adr-dialog-form-deletion): o CATÁLOGO (`GET /`) esconde arquivados; a
RESOLUÇÃO (`GET /{form_id}`) continua servindo, com `deleted_at` no corpo — quem resolve por
id já tem vínculo, e fechar essa porta derrubaria contato em andamento e leitura de história
encerrada. Escrita sobre arquivado → 409, com o caminho do restauro na mensagem.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from plughub_authz import enforce_write
from pydantic import BaseModel, Field

from .format_guard import conflitos_de_formato
from .db import (
    FormArchivedError,
    db_create_form,
    db_delete_form,
    db_get_form,
    db_list_forms,
    db_publish_form,
    db_put_form,
    db_undelete_form,
)

logger = logging.getLogger("plughub.dialog.router")
router = APIRouter(prefix="/v1/dialog/forms")


def _pool(request: Request):
    return request.app.state.pool


def _require_tenant(x_tenant_id: str | None) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return x_tenant_id


def _archived_409(exc: FormArchivedError) -> HTTPException:
    """409 que NOMEIA a saída. Recusa sem caminho de volta seria a mesma degradação muda que
    o resto do serviço evita — o operador precisa saber que existe restauro, e onde."""
    return HTTPException(
        status_code=409,
        detail=(
            f"dialog form '{exc.form_id}' esta arquivado (deleted_at={exc.deleted_at}) — "
            f"restaure com POST /v1/dialog/forms/{exc.form_id}/undelete antes de escrever"
        ),
    )


def _require_admin(request: Request, x_admin_token: str | None) -> None:
    """
    Portao de escrita. O `x_admin_token` fica na assinatura para o header aparecer no
    OpenAPI; quem o LE e o `enforce_write`, a partir do request — uma so leitura, para
    nao existirem duas respostas para "este header confere?".
    """
    settings = request.app.state.settings
    enforce_write(
        request     = request,
        admin_token = settings.admin_token,
        jwt_secret  = getattr(settings, "jwt_secret", ""),
        module      = "config",
        field       = "dialog_forms",
        what        = "escrita de DialogForm",
    )


class FormUpsert(BaseModel):
    """Loose upsert body — the canonical validator is the Zod DialogFormSchema
    (@plughub/schemas) on the TS side. The store persists + serves; it does not
    re-implement deep validation, only structural minimums."""
    form_id:        str
    name:           str = ""
    description:    str | None = None
    default_locale: str
    locales:        list[str] = Field(min_length=1)
    nodes:          list[dict[str, Any]] = Field(min_length=1)
    # Composed instruments (survey_definition layer). Opaque to the store — the
    # canonical validator is the Zod DialogFormSchema on the TS side; here it is
    # persisted+served as-is so form_get / survey_record can compose. Default []
    # keeps plain dialogs (OTP) and legacy per-question-metric surveys unchanged.
    dimensions:     list[dict[str, Any]] = []
    # Optional form-level composite (health score); opaque to the store.
    composite:      dict[str, Any] | None = None
    tags:           list[str] = []

    def to_doc(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "form_id":        self.form_id,
            "name":           self.name,
            "default_locale": self.default_locale,
            "locales":        self.locales,
            "nodes":          self.nodes,
            "dimensions":     self.dimensions,
            "tags":           self.tags,
        }
        if self.description is not None:
            doc["description"] = self.description
        if self.composite is not None:
            doc["composite"] = self.composite
        return doc


@router.get("")
async def list_forms(
    request: Request,
    include_deleted: bool = Query(default=False),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict:
    tenant_id = _require_tenant(x_tenant_id)
    forms = await db_list_forms(_pool(request), tenant_id, include_deleted=include_deleted)
    return {"forms": forms}


@router.get("/{form_id}")
async def get_form(
    request: Request,
    form_id: str,
    status: str | None = Query(default=None),
    version: int | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict:
    tenant_id = _require_tenant(x_tenant_id)
    form = await db_get_form(_pool(request), tenant_id, form_id, status=status, version=version)
    if form is None:
        raise HTTPException(status_code=404, detail=f"dialog form not found: {form_id}")
    return form


@router.post("")
async def create_form(
    request: Request,
    body: FormUpsert,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    tenant_id = _require_tenant(x_tenant_id)
    _require_admin(request, x_admin_token)
    try:
        return await db_create_form(_pool(request), tenant_id, body.to_doc())
    except FormArchivedError as exc:
        raise _archived_409(exc) from exc


@router.put("/{form_id}")
async def put_form(
    request: Request,
    form_id: str,
    body: FormUpsert,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    tenant_id = _require_tenant(x_tenant_id)
    _require_admin(request, x_admin_token)
    if body.form_id != form_id:
        raise HTTPException(status_code=400, detail="form_id in body must match path")
    try:
        return await db_put_form(_pool(request), tenant_id, form_id, body.to_doc())
    except FormArchivedError as exc:
        raise _archived_409(exc) from exc


@router.post("/{form_id}/publish")
async def publish_form(
    request: Request,
    form_id: str,
    version: int | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    tenant_id = _require_tenant(x_tenant_id)
    _require_admin(request, x_admin_token)

    # §D8 — o campo nomeia o tipo UMA vez. `masked: "cpf"` ja deriva o formato;
    # declarar `format` junto e DIFERENTE e contradicao que o schema aceitaria,
    # e o efeito seria mascara dizendo uma coisa e veredicto julgando outra.
    # Recusa aqui, no publish, que e onde ha autor para ler — e nomeando os DOIS
    # lados, porque "conflito de formato" sem os nomes devolve a mesma
    # investigacao que a ausencia de mensagem.
    rascunho = await db_get_form(_pool(request), tenant_id, form_id, version=version)
    if rascunho:
        conflitos = conflitos_de_formato(rascunho.get("nodes") or [])
        if conflitos:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "format_declaration_conflict",
                    "message": (
                        "A declaracao de formato contradiz o tipo mascarado. "
                        "Remova `validation.format` (o tipo ja o deriva) ou troque o tipo."
                    ),
                    "conflicts": conflitos,
                },
            )

    try:
        form = await db_publish_form(_pool(request), tenant_id, form_id, version)
    except FormArchivedError as exc:
        raise _archived_409(exc) from exc
    if form is None:
        raise HTTPException(status_code=404, detail=f"no publishable version for form: {form_id}")
    return form


@router.delete("/{form_id}")
async def delete_form(
    request: Request,
    form_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """
    Arquiva o form — ou o PURGA, se ele nunca teve versão publicada (ADR D2).

    A resposta declara qual dos dois aconteceu (`purged`), porque só um deles é reversível e
    a tela precisa avisar ANTES no caso irreversível. Arquivar NÃO tira o form do ar para
    quem já está vinculado: ele sai do catálogo e de vínculos novos, e segue resolvível por
    id (é o que mantém contato em andamento, composição de nota e histórico de pé).
    """
    tenant_id = _require_tenant(x_tenant_id)
    _require_admin(request, x_admin_token)
    result = await db_delete_form(_pool(request), tenant_id, form_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"dialog form not found: {form_id}")
    return result


@router.post("/{form_id}/undelete")
async def undelete_form(
    request: Request,
    form_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """Restaura form arquivado. Rota própria, e não flag no DELETE: restaurar é ato, aparece
    no OpenAPI e é auditável como tal."""
    tenant_id = _require_tenant(x_tenant_id)
    _require_admin(request, x_admin_token)
    result = await db_undelete_form(_pool(request), tenant_id, form_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"dialog form not found: {form_id}")
    return result
