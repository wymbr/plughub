"""
router.py
FastAPI routes for the Scheduler / Agenda API.

Endpoints (all tenant-scoped via the X-Tenant-ID header):
  Agendas    — CRUD under /v1/agendas
  Lifecycle  — pause / resume / cancel
  Ledger     — GET /v1/agendas/{id}/dispatches

Pydantic models mirror the Zod contract in @plughub/schemas/scheduler.ts.
next_fire_at for `once` mode is set here (= fire_at); for `recurring` it is computed
by the rule evaluator (Fase 1, task 4) and left null on create until then.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .evaluator import compute_next_fire
from .db import (
    db_create_agenda,
    db_delete_agenda,
    db_get_agenda,
    db_list_agendas,
    db_list_dispatches,
    db_set_agenda_status,
    db_set_next_fire_at,
    db_update_agenda,
)

logger = logging.getLogger("plughub.scheduler.router")
router = APIRouter()


def _pool(request: Request):
    return request.app.state.pool


def _scheduler(request: Request):
    return getattr(request.app.state, "scheduler", None)


async def _reconcile_timer(request: Request, agenda: dict) -> None:
    """Arm the timer when the agenda is active with a next_fire_at; disarm otherwise.
    Idempotent — safe to call after any create/update/lifecycle change."""
    sched = _scheduler(request)
    if sched is None:
        return
    if agenda.get("status") == "active" and agenda.get("next_fire_at"):
        await sched.arm(agenda["id"], agenda["tenant_id"], agenda["next_fire_at"])
    else:
        await sched.disarm(agenda["id"])


def _tenant(x_tenant_id: str | None) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")
    return x_tenant_id


# ── Contract models (mirror @plughub/schemas/scheduler.ts) ────────────────────

DayOfWeek = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]


class MonthByDate(BaseModel):
    kind: Literal["by_date"]
    days: list[Union[int, Literal["last"]]] = Field(min_length=1)


class MonthByPosition(BaseModel):
    kind:    Literal["by_position"]
    nth:     Union[int, Literal["last"]]
    weekday: DayOfWeek


MonthBy = Annotated[Union[MonthByDate, MonthByPosition], Field(discriminator="kind")]


class RecurrenceRule(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    interval:  int = 1
    weekdays:  list[DayOfWeek] | None = None
    month_by:  MonthBy | None = None
    times:     list[str] = Field(min_length=1)
    business_day_policy: Literal[
        "ignore", "only_business_days", "shift_next", "shift_previous"
    ] = "ignore"
    month_overflow: Literal["clamp", "skip"] = "clamp"


class Validity(BaseModel):
    starts_at: datetime
    ends_at:   datetime | None = None


class OnceSchedule(BaseModel):
    mode:    Literal["once"]
    fire_at: datetime


class RecurringSchedule(BaseModel):
    mode: Literal["recurring"]
    rule: RecurrenceRule


Schedule = Annotated[
    Union[OnceSchedule, RecurringSchedule], Field(discriminator="mode")
]

MisfirePolicy = Literal["fire_late", "skip", "fire_all_missed"]


class CreateAgendaBody(BaseModel):
    name:           str
    target_pool_id: str
    payload:        dict[str, Any] = Field(default_factory=dict)
    timezone:       str | None = None
    calendar_id:    str | None = None
    validity:       Validity
    schedule:       Schedule
    misfire_policy: MisfirePolicy | None = None


class UpdateAgendaBody(BaseModel):
    name:           str | None = None
    target_pool_id: str | None = None
    payload:        dict[str, Any] | None = None
    timezone:       str | None = None
    calendar_id:    str | None = None
    validity:       Validity | None = None
    schedule:       Schedule | None = None
    misfire_policy: MisfirePolicy | None = None
    status:         Literal["active", "paused"] | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _provisional_next_fire_at(schedule: dict) -> datetime | None:
    """For `once`, next_fire_at = fire_at (trivial, no evaluator)."""
    if schedule.get("mode") == "once":
        raw = schedule.get("fire_at")
        return datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    return None


async def _next_fire_for(
    request: Request, *, schedule: dict | None, validity: dict | None,
    timezone_str: str | None, calendar_id: str | None,
) -> datetime | None:
    """Compute next_fire_at: `once` = fire_at; `recurring` = rule evaluator (calendar-aware)."""
    schedule = schedule or {}
    if schedule.get("mode") == "once":
        return _provisional_next_fire_at(schedule)
    cal_client = getattr(request.app.state, "calendar_client", None)
    return await compute_next_fire(
        {
            "schedule":    schedule,
            "validity":    validity or {},
            "timezone":    timezone_str or "America/Sao_Paulo",
            "calendar_id": calendar_id,
        },
        datetime.now(timezone.utc),
        cal_client,
    )


# ── Agendas — CRUD ────────────────────────────────────────────────────────────

@router.post("/v1/agendas", status_code=201)
async def create_agenda(
    body: CreateAgendaBody,
    request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    # NOTE: server-side webhook-capability validation of target_pool_id (channel_types
    # ∋ "webhook") is deferred — the Fase 3 UI filters the pool selector. Add an
    # agent-registry check here when needed.
    data = body.model_dump(mode="json", exclude_none=True)
    data["next_fire_at"] = await _next_fire_for(
        request,
        schedule=data.get("schedule"), validity=data.get("validity"),
        timezone_str=data.get("timezone"), calendar_id=data.get("calendar_id"),
    )
    agenda = await db_create_agenda(_pool(request), tenant, data)
    await _reconcile_timer(request, agenda)
    return agenda


@router.get("/v1/agendas")
async def list_agendas(
    request: Request,
    x_tenant_id: str | None = Header(default=None),
    status: str | None = None,
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_agendas(_pool(request), tenant, status)
    return {"agendas": items, "total": len(items)}


@router.get("/v1/agendas/{agenda_id}")
async def get_agenda(
    agenda_id: str,
    request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    agenda = await db_get_agenda(_pool(request), tenant, agenda_id)
    if not agenda:
        raise HTTPException(status_code=404, detail="Agenda not found")
    return agenda


@router.patch("/v1/agendas/{agenda_id}")
async def update_agenda(
    agenda_id: str,
    body: UpdateAgendaBody,
    request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _tenant(x_tenant_id)
    data = body.model_dump(mode="json", exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    agenda = await db_update_agenda(_pool(request), tenant, agenda_id, data)
    if not agenda:
        raise HTTPException(status_code=404, detail="Agenda not found")
    # Recompute next_fire_at when timing inputs change (from the merged agenda).
    if any(k in data for k in ("schedule", "validity", "calendar_id", "timezone")):
        nf = await _next_fire_for(
            request,
            schedule=agenda["schedule"], validity=agenda["validity"],
            timezone_str=agenda["timezone"], calendar_id=agenda["calendar_id"],
        )
        await db_set_next_fire_at(_pool(request), agenda_id, nf)
        agenda["next_fire_at"] = nf.isoformat() if nf else None
    await _reconcile_timer(request, agenda)
    return agenda


@router.delete("/v1/agendas/{agenda_id}", status_code=204)
async def delete_agenda(
    agenda_id: str,
    request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> None:
    tenant = _tenant(x_tenant_id)
    ok = await db_delete_agenda(_pool(request), tenant, agenda_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Agenda not found")
    sched = _scheduler(request)
    if sched is not None:
        await sched.disarm(agenda_id)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def _set_status(request: Request, tenant: str, agenda_id: str, status: str) -> dict:
    agenda = await db_set_agenda_status(_pool(request), tenant, agenda_id, status)
    if not agenda:
        raise HTTPException(status_code=404, detail="Agenda not found")
    await _reconcile_timer(request, agenda)
    return agenda


@router.post("/v1/agendas/{agenda_id}/pause")
async def pause_agenda(
    agenda_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    return await _set_status(request, _tenant(x_tenant_id), agenda_id, "paused")


@router.post("/v1/agendas/{agenda_id}/resume")
async def resume_agenda(
    agenda_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    return await _set_status(request, _tenant(x_tenant_id), agenda_id, "active")


@router.post("/v1/agendas/{agenda_id}/cancel")
async def cancel_agenda(
    agenda_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    return await _set_status(request, _tenant(x_tenant_id), agenda_id, "cancelled")


# ── Dispatch ledger ───────────────────────────────────────────────────────────

@router.get("/v1/agendas/{agenda_id}/dispatches")
async def list_dispatches(
    agenda_id: str,
    request: Request,
    x_tenant_id: str | None = Header(default=None),
    limit: int = 100,
) -> dict:
    tenant = _tenant(x_tenant_id)
    items = await db_list_dispatches(_pool(request), tenant, agenda_id, limit)
    return {"dispatches": items, "total": len(items)}
