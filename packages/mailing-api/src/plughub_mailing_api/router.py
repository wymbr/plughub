"""
router.py
FastAPI routes for the Outbound Mailing API.

Endpoints (all tenant-scoped via the X-Tenant-ID header):
  Mailings   — CRUD under /v1/mailings
  Entries    — POST /v1/mailings/{id}/entries (backing of mailing_add) + list
  Campaigns  — CRUD under /v1/campaigns
  Drain      — POST /v1/campaigns/{id}/drain  (atomic claim of a batch)
  Delivery   — POST /v1/deliveries/{id}/result + GET /v1/campaigns/{id}/deliveries

Pydantic models mirror the Zod contract in @plughub/schemas/outbound.ts.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .db import (
    db_add_entry,
    db_contact_eligibility,
    db_create_campaign,
    db_create_mailing,
    db_create_policy,
    db_delete_mailing,
    db_delete_policy,
    db_drain_campaign,
    db_get_campaign,
    db_get_mailing,
    db_list_campaigns,
    db_list_deliveries,
    db_list_entries,
    db_list_mailings,
    db_list_policies,
    db_set_delivery_result,
    db_unsubscribe,
    db_update_campaign,
    db_update_mailing,
    db_update_policy,
)

logger = logging.getLogger("plughub.mailing.router")
router = APIRouter()


def _pool(request: Request):
    return request.app.state.pool


def _tenant(x_tenant_id: str | None) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")
    return x_tenant_id


# ── Contract models (mirror @plughub/schemas/outbound.ts) ─────────────────────

class CreateMailingBody(BaseModel):
    name:              str
    description:       str | None = None
    dedup_policy:      str | None = None   # customer | customer_context | none
    metadata_contract: str | None = None
    entry_ttl_seconds: int | None = None


class UpdateMailingBody(BaseModel):
    name:              str | None = None
    description:       str | None = None
    dedup_policy:      str | None = None
    metadata_contract: str | None = None
    entry_ttl_seconds: int | None = None


class AddEntryBody(BaseModel):
    customer_id: str | None = None
    contacts:    dict[str, str] = Field(default_factory=dict)
    metadata:    dict[str, Any]
    dedup_key:   str | None = None
    source:      str | None = None
    ttl_seconds: int | None = None


class CreateCampaignBody(BaseModel):
    name:           str
    mailing_id:     str
    pool_id:        str
    selection:      dict[str, Any] | None = None
    channel_policy: dict[str, Any] = Field(default_factory=dict)
    transactional:  bool = False
    batch_size:     int = 50
    retry:          dict[str, Any] = Field(default_factory=dict)
    agenda_id:      str | None = None


class UpdateCampaignBody(BaseModel):
    name:           str | None = None
    pool_id:        str | None = None
    selection:      dict[str, Any] | None = None
    channel_policy: dict[str, Any] | None = None
    transactional:  bool | None = None
    batch_size:     int | None = None
    retry:          dict[str, Any] | None = None
    agenda_id:      str | None = None
    status:         str | None = None   # active | paused | completed | archived


class DrainBody(BaseModel):
    limit: int | None = None


class DeliveryResultBody(BaseModel):
    result:          str   # claimed|pending|contacted|responded|failed|skipped_ineligible|suppressed
    session_id:      str | None = None
    root_session_id: str | None = None
    error:           str | None = None


# ── Mailings — CRUD ───────────────────────────────────────────────────────────

@router.post("/v1/mailings", status_code=201)
async def create_mailing(
    body: CreateMailingBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    return await db_create_mailing(_pool(request), tenant, body.model_dump(exclude_none=True))


@router.get("/v1/mailings")
async def list_mailings(
    request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_mailings(_pool(request), tenant)
    return {"mailings": items, "total": len(items)}


@router.get("/v1/mailings/{mailing_id}")
async def get_mailing(
    mailing_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    m = await db_get_mailing(_pool(request), tenant, mailing_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mailing not found")
    return m


@router.patch("/v1/mailings/{mailing_id}")
async def update_mailing(
    mailing_id: str, body: UpdateMailingBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    m = await db_update_mailing(_pool(request), tenant, mailing_id, data)
    if not m:
        raise HTTPException(status_code=404, detail="Mailing not found")
    return m


@router.delete("/v1/mailings/{mailing_id}", status_code=204)
async def delete_mailing(
    mailing_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> None:
    tenant = _tenant(x_tenant_id)
    ok = await db_delete_mailing(_pool(request), tenant, mailing_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Mailing not found")


# ── Entries — mailing_add + list ──────────────────────────────────────────────

@router.post("/v1/mailings/{mailing_id}/entries", status_code=201)
async def add_entry(
    mailing_id: str, body: AddEntryBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    mailing = await db_get_mailing(_pool(request), tenant, mailing_id)
    if not mailing:
        raise HTTPException(status_code=404, detail="Mailing not found")
    return await db_add_entry(_pool(request), tenant, mailing, body.model_dump(exclude_none=True))


@router.get("/v1/mailings/{mailing_id}/entries")
async def list_entries(
    mailing_id: str, request: Request,
    x_tenant_id: str | None = Header(default=None), status: str | None = None,
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_entries(_pool(request), tenant, mailing_id, status)
    return {"entries": items, "total": len(items)}


# ── Campaigns — CRUD ──────────────────────────────────────────────────────────

@router.post("/v1/campaigns", status_code=201)
async def create_campaign(
    body: CreateCampaignBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    # Validate the referenced mailing exists (tenant-scoped).
    if not await db_get_mailing(_pool(request), tenant, body.mailing_id):
        raise HTTPException(status_code=400, detail="mailing_id not found for tenant")
    return await db_create_campaign(_pool(request), tenant, body.model_dump(exclude_none=True))


@router.get("/v1/campaigns")
async def list_campaigns(
    request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_campaigns(_pool(request), tenant)
    return {"campaigns": items, "total": len(items)}


@router.get("/v1/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    c = await db_get_campaign(_pool(request), tenant, campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.patch("/v1/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: str, body: UpdateCampaignBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    c = await db_update_campaign(_pool(request), tenant, campaign_id, data)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


# ── Drain + delivery result ───────────────────────────────────────────────────

@router.post("/v1/campaigns/{campaign_id}/drain")
async def drain_campaign(
    campaign_id: str, body: DrainBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    campaign = await db_get_campaign(_pool(request), tenant, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["status"] != "active":
        # Draining a non-active campaign is a no-op batch, not an error (the skill
        # sees an empty batch and completes) — but be explicit in the log.
        logger.info("drain of non-active campaign %s (status=%s) → empty",
                    campaign_id, campaign["status"])
        return {"campaign_id": campaign_id, "drained": []}
    drained = await db_drain_campaign(_pool(request), tenant, campaign, body.limit)
    return {"campaign_id": campaign_id, "drained": drained}


@router.post("/v1/deliveries/{delivery_id}/result")
async def set_delivery_result(
    delivery_id: str, body: DeliveryResultBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    d = await db_set_delivery_result(
        _pool(request), tenant, delivery_id, body.model_dump(exclude_none=True),
    )
    if not d:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return d


@router.get("/v1/campaigns/{campaign_id}/deliveries")
async def list_deliveries(
    campaign_id: str, request: Request,
    x_tenant_id: str | None = Header(default=None), limit: int = 200,
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_deliveries(_pool(request), tenant, campaign_id, limit)
    return {"deliveries": items, "total": len(items)}


# ── Fase 2 — contact governance ───────────────────────────────────────────────

Window = str | int   # "24h" | "7d" | 86400


class FrequencyCap(BaseModel):
    window:      Window
    max:         int
    per_channel: bool = False


class ChannelCap(BaseModel):
    window: Window
    max:    int


class CreateContactPolicyBody(BaseModel):
    scope:            str                    # tenant | campaign
    scope_id:         str | None = None
    frequency_caps:   list[FrequencyCap] = Field(default_factory=list)
    quarantine_after: Window | None = None
    channel_caps:     dict[str, ChannelCap] = Field(default_factory=dict)


class UpdateContactPolicyBody(BaseModel):
    frequency_caps:   list[FrequencyCap] | None = None
    quarantine_after: Window | None = None
    channel_caps:     dict[str, ChannelCap] | None = None


class EligibilityBody(BaseModel):
    customer_id: str
    channel:     str
    campaign_id: str | None = None
    claim:       bool = True
    at:          datetime | None = None


class UnsubscribeBody(BaseModel):
    customer_id: str
    mailing_id:  str | None = None
    channel:     str | None = None


@router.post("/v1/contact-policies", status_code=201)
async def create_policy(
    body: CreateContactPolicyBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    if body.scope not in ("tenant", "campaign"):
        raise HTTPException(status_code=400, detail="scope must be 'tenant' or 'campaign'")
    if body.scope == "campaign" and not body.scope_id:
        raise HTTPException(status_code=400, detail="scope_id required for campaign scope")
    return await db_create_policy(_pool(request), tenant, body.model_dump(mode="json", exclude_none=True))


@router.get("/v1/contact-policies")
async def list_policies(
    request: Request, x_tenant_id: str | None = Header(default=None), scope: str | None = None,
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_policies(_pool(request), tenant, scope)
    return {"policies": items, "total": len(items)}


@router.patch("/v1/contact-policies/{policy_id}")
async def update_policy(
    policy_id: str, body: UpdateContactPolicyBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    data = body.model_dump(mode="json", exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    p = await db_update_policy(_pool(request), tenant, policy_id, data)
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    return p


@router.delete("/v1/contact-policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> None:
    tenant = _tenant(x_tenant_id)
    ok = await db_delete_policy(_pool(request), tenant, policy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Policy not found")


@router.post("/v1/contact/eligibility")
async def contact_eligibility(
    body: EligibilityBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    req = body.model_dump(exclude_none=True)   # keep `at` as datetime (not json mode)
    return await db_contact_eligibility(_pool(request), tenant, req)


@router.post("/v1/unsubscribe")
async def unsubscribe(
    body: UnsubscribeBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    return await db_unsubscribe(_pool(request), tenant, body.model_dump(exclude_none=True))
