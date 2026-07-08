"""
router.py
FastAPI routes for the Dialog API — generic scripted-dialog form store.

Tenant scoping via header X-Tenant-ID (all endpoints).
Writes require X-Admin-Token when settings.admin_token is set; reads are open
(content is masked-by-construction — no PII values stored).

Endpoints (all under /v1/dialog/forms):
  GET    /                         → list latest version metadata per form
  GET    /{form_id}                → resolve one form (?status=published&version=N)
  POST   /                         → create a new draft version
  PUT    /{form_id}                → edit (draft in place, or new draft version)
  POST   /{form_id}/publish        → publish a version (?version=N; default latest draft)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .db import (
    db_create_form,
    db_get_form,
    db_list_forms,
    db_publish_form,
    db_put_form,
)

logger = logging.getLogger("plughub.dialog.router")
router = APIRouter(prefix="/v1/dialog/forms")


def _pool(request: Request):
    return request.app.state.pool


def _require_tenant(x_tenant_id: str | None) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return x_tenant_id


def _require_admin(request: Request, x_admin_token: str | None) -> None:
    expected = request.app.state.settings.admin_token
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


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
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict:
    tenant_id = _require_tenant(x_tenant_id)
    forms = await db_list_forms(_pool(request), tenant_id)
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
    return await db_create_form(_pool(request), tenant_id, body.to_doc())


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
    return await db_put_form(_pool(request), tenant_id, form_id, body.to_doc())


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
    form = await db_publish_form(_pool(request), tenant_id, form_id, version)
    if form is None:
        raise HTTPException(status_code=404, detail=f"no publishable version for form: {form_id}")
    return form
