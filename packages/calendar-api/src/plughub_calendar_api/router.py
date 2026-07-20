"""
router.py
FastAPI routes for the Calendar API.

Endpoints:
  Holiday Sets  — CRUD under /v1/holiday-sets
  Calendars     — CRUD under /v1/calendars
  Associations  — CRUD under /v1/associations
  Engine        — read-only queries under /v1/engine
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .db import (
    db_create_association,
    db_create_calendar,
    db_create_holiday_set,
    db_delete_association,
    db_delete_associations_for_entity,
    db_delete_calendar,
    db_delete_holiday_set,
    db_get_associations_for_engine,
    db_get_calendar,
    db_get_holiday_set,
    db_get_holidays_for_sets,
    db_get_tenant_config,
    db_list_associations,
    db_list_calendars,
    db_list_holiday_sets,
    db_update_association,
    db_update_calendar,
    db_update_holiday_set,
    db_upsert_pool_association,
    db_upsert_tenant_config,
)
from .engine import (
    add_business_duration,
    business_duration,
    get_open_status,
    is_open,
    next_open_slot,
)

logger = logging.getLogger("plughub.calendar.router")
router = APIRouter()


def _pool(request: Request):
    return request.app.state.pool


def _settings(request: Request):
    return request.app.state.settings


# ── Holiday Sets ──────────────────────────────────────────────────────────────

class HolidaySetCreate(BaseModel):
    organization_id: str
    tenant_id:       str | None = None
    scope:           str = "tenant"
    name:            str
    description:     str = ""
    year:            int | None = None
    holidays:        list[dict] = Field(default_factory=list)


class HolidaySetUpdate(BaseModel):
    name:        str | None = None
    description: str | None = None
    year:        int | None = None
    holidays:    list[dict] | None = None


@router.get("/v1/holiday-sets")
async def list_holiday_sets(
    organization_id: str,
    tenant_id: str | None = None,
    pool=Depends(_pool),
):
    return await db_list_holiday_sets(pool, organization_id, tenant_id)


@router.post("/v1/holiday-sets", status_code=201)
async def create_holiday_set(
    body: HolidaySetCreate,
    request: Request,
    pool=Depends(_pool),
):
    settings = _settings(request)
    data = body.model_dump()
    data["installation_id"] = settings.installation_id
    return await db_create_holiday_set(pool, data)


@router.get("/v1/holiday-sets/{id}")
async def get_holiday_set(id: str, pool=Depends(_pool)):
    row = await db_get_holiday_set(pool, id)
    if not row:
        raise HTTPException(404, "holiday_set not found")
    return row


@router.patch("/v1/holiday-sets/{id}")
async def update_holiday_set(id: str, body: HolidaySetUpdate, pool=Depends(_pool)):
    row = await db_update_holiday_set(pool, id, body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "holiday_set not found")
    return row


@router.delete("/v1/holiday-sets/{id}", status_code=204)
async def delete_holiday_set(id: str, pool=Depends(_pool)):
    deleted = await db_delete_holiday_set(pool, id)
    if not deleted:
        raise HTTPException(404, "holiday_set not found")


# ── Tenant Config ─────────────────────────────────────────────────────────────

class TenantConfigUpdate(BaseModel):
    tenant_id:        str
    default_timezone: str


@router.get("/v1/tenant-config")
async def get_tenant_config(
    tenant_id: str,
    pool=Depends(_pool),
) -> dict:
    """
    Return the default timezone configuration for a tenant.
    Falls back to 'America/Sao_Paulo' if no explicit config has been set.
    """
    return await db_get_tenant_config(pool, tenant_id)


@router.patch("/v1/tenant-config")
async def update_tenant_config(
    body: TenantConfigUpdate,
    pool=Depends(_pool),
) -> dict:
    """
    Create or update the tenant's default timezone.
    This timezone is used as the default for new calendars created without an
    explicit timezone field, replacing the platform-wide 'America/Sao_Paulo' default.
    """
    try:
        pytz.timezone(body.default_timezone)
    except pytz.UnknownTimeZoneError:
        raise HTTPException(422, f"Unknown timezone: {body.default_timezone!r}")
    return await db_upsert_tenant_config(pool, body.tenant_id, body.default_timezone)


# ── Calendars ─────────────────────────────────────────────────────────────────

class CalendarCreate(BaseModel):
    organization_id: str
    tenant_id:       str | None = None
    scope:           str = "tenant"
    name:            str
    description:     str = ""
    timezone:        str | None = None  # None → inherit tenant default (or platform default)
    always_open:     bool = False        # True = 24/7; holidays/exceptions still apply
    weekly_schedule: list[dict] = Field(default_factory=list)
    holiday_set_ids: list[str]  = Field(default_factory=list)
    exceptions:      list[dict] = Field(default_factory=list)


class CalendarUpdate(BaseModel):
    name:            str | None = None
    description:     str | None = None
    timezone:        str | None = None
    always_open:     bool | None = None
    weekly_schedule: list[dict] | None = None
    holiday_set_ids: list[str]  | None = None
    exceptions:      list[dict] | None = None


@router.get("/v1/calendars")
async def list_calendars(
    organization_id: str,
    tenant_id: str | None = None,
    pool=Depends(_pool),
):
    return await db_list_calendars(pool, organization_id, tenant_id)


@router.post("/v1/calendars", status_code=201)
async def create_calendar(
    body: CalendarCreate,
    request: Request,
    pool=Depends(_pool),
):
    settings = _settings(request)
    data = body.model_dump()
    data["installation_id"] = settings.installation_id

    # If the caller did not provide an explicit timezone, inherit the tenant's
    # configured default (falls back to 'America/Sao_Paulo' if not set).
    if data.get("timezone") is None and data.get("tenant_id"):
        tenant_cfg = await db_get_tenant_config(pool, data["tenant_id"])
        data["timezone"] = tenant_cfg["default_timezone"]
    elif data.get("timezone") is None:
        data["timezone"] = "America/Sao_Paulo"

    return await db_create_calendar(pool, data)


@router.get("/v1/calendars/{id}")
async def get_calendar(id: str, pool=Depends(_pool)):
    row = await db_get_calendar(pool, id)
    if not row:
        raise HTTPException(404, "calendar not found")
    return row


@router.patch("/v1/calendars/{id}")
async def update_calendar(id: str, body: CalendarUpdate, pool=Depends(_pool)):
    row = await db_update_calendar(pool, id, body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "calendar not found")
    return row


@router.delete("/v1/calendars/{id}", status_code=204)
async def delete_calendar(id: str, pool=Depends(_pool)):
    deleted = await db_delete_calendar(pool, id)
    if not deleted:
        raise HTTPException(404, "calendar not found")


# ── Associations ──────────────────────────────────────────────────────────────

class AssociationCreate(BaseModel):
    tenant_id:   str
    entity_type: str
    entity_id:   str
    calendar_id: str
    operator:    str = "UNION"
    priority:    int = 1
    exceptions:  list[dict] = Field(default_factory=list)


class AssociationUpdate(BaseModel):
    exceptions: list[dict] | None = None


class AssociationUpsert(BaseModel):
    """Idempotent upsert — replaces any existing association for this entity."""
    tenant_id:   str
    entity_type: str
    entity_id:   str
    calendar_id: str
    operator:    str = "UNION"
    priority:    int = 1
    exceptions:  list[dict] = Field(default_factory=list)


@router.get("/v1/associations")
async def list_associations(
    tenant_id:   str,
    entity_type: str,
    entity_id:   str,
    pool=Depends(_pool),
):
    return await db_list_associations(pool, tenant_id, entity_type, entity_id)


@router.post("/v1/associations", status_code=201)
async def create_association(body: AssociationCreate, pool=Depends(_pool)):
    return await db_create_association(pool, body.model_dump())


@router.patch("/v1/associations/{id}")
async def update_association(id: str, body: AssociationUpdate, pool=Depends(_pool)):
    row = await db_update_association(pool, id, body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "association not found")
    return row


@router.put("/v1/associations/upsert")
async def upsert_association(body: AssociationUpsert, pool=Depends(_pool)) -> dict[str, Any]:
    """
    Idempotent upsert: replace the association for this entity with the given calendar.
    First removes any existing associations for (tenant_id, entity_type, entity_id),
    then creates a fresh one with the supplied parameters and exceptions.

    Use this from pool/channel/workflow editors so they don't need to track assoc IDs.
    """
    # Clear old associations for this entity (a pool has at most one calendar in the UI)
    await db_delete_associations_for_entity(pool, body.tenant_id, body.entity_type, body.entity_id)
    data = body.model_dump()
    return await db_create_association(pool, data)


@router.delete("/v1/associations/entity", status_code=204)
async def delete_entity_associations(
    tenant_id:   str,
    entity_type: str,
    entity_id:   str,
    pool=Depends(_pool),
) -> None:
    """Remove ALL calendar associations for a given entity."""
    await db_delete_associations_for_entity(pool, tenant_id, entity_type, entity_id)


@router.delete("/v1/associations/{id}", status_code=204)
async def delete_association(id: str, pool=Depends(_pool)):
    deleted = await db_delete_association(pool, id)
    if not deleted:
        raise HTTPException(404, "association not found")


# ── Engine ────────────────────────────────────────────────────────────────────

async def _load_engine_data(
    pool, tenant_id: str, entity_type: str, entity_id: str
) -> tuple[list[dict], dict[str, list]]:
    """Load associations + holidays for engine queries."""
    associations = await db_get_associations_for_engine(
        pool, tenant_id, entity_type, entity_id
    )
    # Collect all holiday_set_ids across all associated calendars
    all_hs_ids: list[str] = []
    for a in associations:
        all_hs_ids.extend(a.get("holiday_set_ids", []))

    hs_rows = await db_get_holidays_for_sets(pool, list(set(all_hs_ids)))
    holidays_by_cal: dict[str, list] = {}
    # Build per-calendar index
    for assoc in associations:
        cal_hs_ids = assoc.get("holiday_set_ids", [])
        merged: list[dict] = []
        for hs in hs_rows:
            if hs["id"] in cal_hs_ids:
                merged.extend(hs["holidays"])
        holidays_by_cal[assoc["calendar_id"]] = merged

    return associations, holidays_by_cal


@router.get("/v1/engine/is-open")
async def engine_is_open(
    tenant_id:   str,
    entity_type: str,
    entity_id:   str,
    at:          str | None = None,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Query the open/closed/holiday status of an entity.

    Returns:
      status        — "open" | "closed" | "holiday"
      open          — boolean (deprecated; use status)
      evaluated_at  — ISO-8601 timestamp of evaluation
      entity_type, entity_id, calendars_count
    """
    at_dt: datetime | None = None
    if at:
        at_dt = datetime.fromisoformat(at)
        if at_dt.tzinfo is None:
            at_dt = pytz.UTC.localize(at_dt)

    assocs, hols = await _load_engine_data(pool, tenant_id, entity_type, entity_id)
    status = get_open_status(assocs, hols, at_dt)
    evaluated_at = (at_dt or datetime.utcnow().replace(tzinfo=pytz.UTC)).isoformat()

    return {
        "status":          status,
        "open":            status == "open",  # deprecated — kept for backward compatibility
        "evaluated_at":    evaluated_at,
        "entity_type":     entity_type,
        "entity_id":       entity_id,
        "calendars_count": len(assocs),
    }


@router.get("/v1/engine/next-open-slot")
async def engine_next_open_slot(
    tenant_id:   str,
    entity_type: str,
    entity_id:   str,
    after:       str | None = None,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """When does the entity next open?"""
    after_dt: datetime | None = None
    if after:
        after_dt = datetime.fromisoformat(after)
        if after_dt.tzinfo is None:
            after_dt = pytz.UTC.localize(after_dt)

    assocs, hols = await _load_engine_data(pool, tenant_id, entity_type, entity_id)
    nxt = next_open_slot(assocs, hols, after_dt)

    return {
        "next_open":   nxt.isoformat() if nxt else None,
        "entity_type": entity_type,
        "entity_id":   entity_id,
    }


# ── Engine — direct by calendar_id (no association) ───────────────────────────
# Used by scheduler-api: an Agenda references a calendar_id directly (not via an
# entity association). Thin wrappers that build a single-calendar assoc list and
# reuse the same engine functions — the engine stays the sole "when" authority.

async def _load_engine_data_by_calendar(
    pool, calendar_id: str
) -> tuple[list[dict] | None, dict[str, list]]:
    try:
        cal = await db_get_calendar(pool, calendar_id)
    except ValueError:
        # Non-UUID id → treat as not found (clean 404, not a 500).
        cal = None
    if not cal:
        return None, {}
    assoc = {
        "assoc_id":        "direct",
        "operator":        "UNION",
        "priority":        1,
        "calendar_id":     cal["id"],
        "timezone":        cal["timezone"],
        "always_open":     cal["always_open"],
        "weekly_schedule": cal["weekly_schedule"],
        "holiday_set_ids": cal["holiday_set_ids"],
        "exceptions":      cal["exceptions"],
    }
    hs_rows = await db_get_holidays_for_sets(pool, cal["holiday_set_ids"])
    merged: list[dict] = []
    for hs in hs_rows:
        merged.extend(hs["holidays"])
    return [assoc], {cal["id"]: merged}


@router.get("/v1/engine/is-open-calendar")
async def engine_is_open_calendar(
    calendar_id: str, at: str | None = None, pool=Depends(_pool),
) -> dict[str, Any]:
    at_dt: datetime | None = None
    if at:
        at_dt = datetime.fromisoformat(at)
        if at_dt.tzinfo is None:
            at_dt = pytz.UTC.localize(at_dt)
    assocs, hols = await _load_engine_data_by_calendar(pool, calendar_id)
    if assocs is None:
        raise HTTPException(404, "calendar not found")
    status = get_open_status(assocs, hols, at_dt)
    evaluated_at = (at_dt or datetime.utcnow().replace(tzinfo=pytz.UTC)).isoformat()
    return {
        "status":       status,
        "open":         status == "open",
        "evaluated_at": evaluated_at,
        "calendar_id":  calendar_id,
    }


@router.get("/v1/engine/next-open-slot-calendar")
async def engine_next_open_slot_calendar(
    calendar_id: str, after: str | None = None, pool=Depends(_pool),
) -> dict[str, Any]:
    after_dt: datetime | None = None
    if after:
        after_dt = datetime.fromisoformat(after)
        if after_dt.tzinfo is None:
            after_dt = pytz.UTC.localize(after_dt)
    assocs, hols = await _load_engine_data_by_calendar(pool, calendar_id)
    if assocs is None:
        raise HTTPException(404, "calendar not found")
    nxt = next_open_slot(assocs, hols, after_dt)
    return {"next_open": nxt.isoformat() if nxt else None, "calendar_id": calendar_id}


class AddBusinessDurationRequest(BaseModel):
    tenant_id:   str
    entity_type: str
    entity_id:   str
    from_dt:     str   # ISO 8601
    hours:       float


@router.post("/v1/engine/add-business-duration")
async def engine_add_business_duration(
    body: AddBusinessDurationRequest,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """Calculate the deadline that is N business hours after from_dt."""
    from_dt = datetime.fromisoformat(body.from_dt)
    if from_dt.tzinfo is None:
        from_dt = pytz.UTC.localize(from_dt)

    assocs, hols = await _load_engine_data(
        pool, body.tenant_id, body.entity_type, body.entity_id
    )
    deadline = add_business_duration(assocs, hols, from_dt, body.hours)

    return {
        "from_dt":       body.from_dt,
        "hours":         body.hours,
        "deadline":      deadline.isoformat(),
        "entity_type":   body.entity_type,
        "entity_id":     body.entity_id,
    }


class BusinessDurationRequest(BaseModel):
    tenant_id:   str
    entity_type: str
    entity_id:   str
    from_dt:     str
    to_dt:       str


@router.post("/v1/engine/business-duration")
async def engine_business_duration(
    body: BusinessDurationRequest,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """How many business hours are there between from_dt and to_dt?"""
    from_dt = datetime.fromisoformat(body.from_dt)
    to_dt   = datetime.fromisoformat(body.to_dt)
    if from_dt.tzinfo is None:
        from_dt = pytz.UTC.localize(from_dt)
    if to_dt.tzinfo is None:
        to_dt = pytz.UTC.localize(to_dt)

    assocs, hols = await _load_engine_data(
        pool, body.tenant_id, body.entity_type, body.entity_id
    )
    hours = business_duration(assocs, hols, from_dt, to_dt)

    return {
        "from_dt":         body.from_dt,
        "to_dt":           body.to_dt,
        "business_hours":  round(hours, 4),
        "business_minutes": round(hours * 60, 2),
        "entity_type":     body.entity_type,
        "entity_id":       body.entity_id,
    }
