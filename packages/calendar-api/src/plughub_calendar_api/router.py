"""
router.py
FastAPI routes for the Calendar API.

Endpoints:
  Holiday Sets  — CRUD under /v1/holiday-sets
  Calendars     — CRUD under /v1/calendars
  Associations  — CRUD under /v1/associations
  Engine        — read-only queries under /v1/engine

Portão em TODA rota (AUT-63, 2026-09-29) — ver `_caller`. Antes, só a escrita tinha
portão (e ele abria com `admin_token` vazio); leituras e motor respondiam ao anônimo pela
borda, e as rotas por id não olhavam de quem era a linha.
"""
from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, HTTPException, Request
from plughub_authz import abac_can, bearer_from_header, verify_user_jwt
from pydantic import BaseModel, Field

from .db import (
    db_create_association,
    db_create_calendar,
    db_create_holiday_set,
    db_delete_association,
    db_delete_associations_for_entity,
    db_delete_calendar,
    db_delete_holiday_set,
    db_get_association,
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


def _pool(request: Request):
    return request.app.state.pool


def _settings(request: Request):
    return request.app.state.settings


async def _caller(request: Request) -> dict:
    """Dependência do ROUTER (AUT-63, 2026-09-29): QUEM chama, antes do corpo e da rota.

    Medido antes, pela borda pública (5174): `GET /v1/tenant-config` respondia 200 ao
    anônimo, e listas, leituras por id e o motor inteiro (`is-open`, `next-open-slot`,
    `add-business-duration`) também. A escrita tinha o `enforce_write`, que DESLIGAVA o
    portão com `admin_token` vazio. E o calendário decide coisas de efeito: a janela em que
    o outbound contata cliente, o `business_day_policy` das agendas, o prazo do `suspend`.

    Portas, nesta ordem (o resultado fica em `request.state.caller`):
      1. `X-Admin-Token` → `system` (seed/sistema; lê e escreve). Vazio FECHA a porta.
      2. `X-Service-Token` → `service` (mailing, scheduler, evaluation, workflow,
         skill-flow-service, mcp-server): SÓ LÊ e consulta o motor.
      3. `Bearer` → `user`, com o tenant do TOKEN. `tenant_id` da query ou do corpo que
         divergir dele é 403 `tenant_mismatch`. Ler não pede campo: Pools, Agendas,
         Outbound e Campanhas escolhem calendário sem ter `config.calendars`.
    """
    st = _settings(request)
    adm = request.headers.get("x-admin-token")
    if adm and st.admin_token and hmac.compare_digest(adm, st.admin_token):
        caller = {"kind": "system", "tenant": None, "claims": None}
    else:
        svc = request.headers.get("x-service-token")
        if svc and st.service_token and hmac.compare_digest(svc, st.service_token):
            caller = {"kind": "service", "tenant": None, "claims": None}
        else:
            token = bearer_from_header(request.headers.get("authorization"))
            if not token:
                raise HTTPException(401, "calendar-api exige credencial")
            if not st.jwt_secret:
                raise HTTPException(503, "calendar-api sem PLUGHUB_CALENDAR_JWT_SECRET — nao consigo verificar credencial")
            claims = verify_user_jwt(token, st.jwt_secret)
            if not claims:
                raise HTTPException(401, "credencial invalida ou expirada")
            tenant = str(claims.get("tenant_id") or "")
            if not tenant:
                raise HTTPException(401, "credencial sem tenant")
            caller = {"kind": "user", "tenant": tenant, "claims": claims}
            pedidos = [request.query_params.get("tenant_id")]
            if "json" in (request.headers.get("content-type") or ""):
                try:
                    corpo = json.loads(await request.body() or b"null")
                except (ValueError, UnicodeDecodeError):
                    corpo = None
                if isinstance(corpo, dict):
                    pedidos.append(corpo.get("tenant_id"))
            for pedido in pedidos:
                if pedido and pedido != tenant:
                    logger.warning("calendar RECUSA: sub=%s pediu tenant=%s com credencial de %s (%s)",
                                   claims.get("sub"), pedido, tenant, request.url.path)
                    raise HTTPException(403, "tenant_mismatch")
    request.state.caller = caller
    return caller


def _require_calendars_write(request: Request) -> None:
    """Escrita: sistema, ou usuário com `config.calendars` (read_write). Serviço NÃO escreve.

    O que a escrita destrancada custava: a janela de contato do outbound é decidida por
    `campaign.contact_calendar_id` (`db_contact_eligibility` consulta `is_open` deste
    serviço). Reescrever um calendário ABRE a janela em que clientes podem ser contatados.
    """
    caller = request.state.caller
    if caller["kind"] == "system":
        return
    if caller["kind"] == "service":
        raise HTTPException(403, "credencial de servico so le o calendar-api")
    if not abac_can(caller["claims"], "config", "calendars", "read_write"):
        raise HTTPException(403, "forbidden: exige config.calendars (read_write)")


def _visible(request: Request, row: dict | None) -> dict | None:
    """Posse de linha por id: usuário só alcança linha do PRÓPRIO tenant ou da organização
    (`tenant_id` nulo). As rotas por id não olhavam isso — com o grant, um usuário lia e
    editava o calendário de outro tenant pelo id. Linha alheia vira 404, não 403: dizer
    "existe, mas não é sua" confirmaria o id."""
    if row is None:
        return None
    caller = request.state.caller
    if caller["kind"] != "user":
        return row
    dono = row.get("tenant_id")
    return row if (not dono or dono == caller["tenant"]) else None


async def _own(request: Request, pool, getter, id: str, what: str) -> dict:
    try:
        row = await getter(pool, id)
    except ValueError:
        row = None   # id não-UUID: 404 limpo, não 500
    row = _visible(request, row)
    if not row:
        raise HTTPException(404, f"{what} not found")
    return row


router = APIRouter(dependencies=[Depends(_caller)])
_WRITE = Depends(_require_calendars_write)


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


@router.post("/v1/holiday-sets", status_code=201, dependencies=[_WRITE])
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
async def get_holiday_set(id: str, request: Request, pool=Depends(_pool)):
    return await _own(request, pool, db_get_holiday_set, id, "holiday_set")


@router.patch("/v1/holiday-sets/{id}", dependencies=[_WRITE])
async def update_holiday_set(id: str, body: HolidaySetUpdate, request: Request, pool=Depends(_pool)):
    await _own(request, pool, db_get_holiday_set, id, "holiday_set")
    row = await db_update_holiday_set(pool, id, body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "holiday_set not found")
    return row


@router.delete("/v1/holiday-sets/{id}", status_code=204, dependencies=[_WRITE])
async def delete_holiday_set(id: str, request: Request, pool=Depends(_pool)):
    await _own(request, pool, db_get_holiday_set, id, "holiday_set")
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


@router.patch("/v1/tenant-config", dependencies=[_WRITE])
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


@router.post("/v1/calendars", status_code=201, dependencies=[_WRITE])
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
async def get_calendar(id: str, request: Request, pool=Depends(_pool)):
    return await _own(request, pool, db_get_calendar, id, "calendar")


@router.patch("/v1/calendars/{id}", dependencies=[_WRITE])
async def update_calendar(id: str, body: CalendarUpdate, request: Request, pool=Depends(_pool)):
    await _own(request, pool, db_get_calendar, id, "calendar")
    row = await db_update_calendar(pool, id, body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "calendar not found")
    return row


@router.delete("/v1/calendars/{id}", status_code=204, dependencies=[_WRITE])
async def delete_calendar(id: str, request: Request, pool=Depends(_pool)):
    await _own(request, pool, db_get_calendar, id, "calendar")
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


@router.post("/v1/associations", status_code=201, dependencies=[_WRITE])
async def create_association(body: AssociationCreate, pool=Depends(_pool)):
    return await db_create_association(pool, body.model_dump())


@router.patch("/v1/associations/{id}", dependencies=[_WRITE])
async def update_association(id: str, body: AssociationUpdate, request: Request, pool=Depends(_pool)):
    await _own(request, pool, db_get_association, id, "association")
    row = await db_update_association(pool, id, body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "association not found")
    return row


@router.put("/v1/associations/upsert", dependencies=[_WRITE])
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


@router.delete("/v1/associations/entity", status_code=204, dependencies=[_WRITE])
async def delete_entity_associations(
    tenant_id:   str,
    entity_type: str,
    entity_id:   str,
    pool=Depends(_pool),
) -> None:
    """Remove ALL calendar associations for a given entity."""
    await db_delete_associations_for_entity(pool, tenant_id, entity_type, entity_id)


@router.delete("/v1/associations/{id}", status_code=204, dependencies=[_WRITE])
async def delete_association(id: str, request: Request, pool=Depends(_pool)):
    await _own(request, pool, db_get_association, id, "association")
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
    pool, calendar_id: str, request: Request,
) -> tuple[list[dict] | None, dict[str, list]]:
    try:
        cal = await db_get_calendar(pool, calendar_id)
    except ValueError:
        # Non-UUID id → treat as not found (clean 404, not a 500).
        cal = None
    cal = _visible(request, cal)   # AUT-63: calendário de outro tenant = não encontrado
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
    calendar_id: str, request: Request, at: str | None = None, pool=Depends(_pool),
) -> dict[str, Any]:
    at_dt: datetime | None = None
    if at:
        at_dt = datetime.fromisoformat(at)
        if at_dt.tzinfo is None:
            at_dt = pytz.UTC.localize(at_dt)
    assocs, hols = await _load_engine_data_by_calendar(pool, calendar_id, request)
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
    calendar_id: str, request: Request, after: str | None = None, pool=Depends(_pool),
) -> dict[str, Any]:
    after_dt: datetime | None = None
    if after:
        after_dt = datetime.fromisoformat(after)
        if after_dt.tzinfo is None:
            after_dt = pytz.UTC.localize(after_dt)
    assocs, hols = await _load_engine_data_by_calendar(pool, calendar_id, request)
    if assocs is None:
        raise HTTPException(404, "calendar not found")
    nxt = next_open_slot(assocs, hols, after_dt)
    return {"next_open": nxt.isoformat() if nxt else None, "calendar_id": calendar_id}


class AddBusinessDurationCalendarRequest(BaseModel):
    calendar_id: str
    from_dt:     str   # ISO 8601
    hours:       float


@router.post("/v1/engine/add-business-duration-calendar")
async def engine_add_business_duration_calendar(
    body: AddBusinessDurationCalendarRequest, request: Request, pool=Depends(_pool),
) -> dict[str, Any]:
    """Prazo `hours` horas úteis depois de `from_dt`, pelo calendário `calendar_id` (CAL-03).

    Gêmea de `is-open-calendar` e `next-open-slot-calendar`: quem guarda um ponteiro de
    calendário (a campanha de avaliação, com `evaluation_calendar_id`) pergunta por ele,
    sem montar associação. A evaluation chamava `POST /v1/calendar/business-deadline`, rota
    que nunca existiu, e todo prazo em horário comercial caía no relógio de parede em
    silêncio. Calendário inexistente (ou de outro tenant) é 404 — nunca o relógio de parede,
    que é o que o engine devolve para lista vazia de associações.
    """
    from_dt = datetime.fromisoformat(body.from_dt)
    if from_dt.tzinfo is None:
        from_dt = pytz.UTC.localize(from_dt)
    assocs, hols = await _load_engine_data_by_calendar(pool, body.calendar_id, request)
    if assocs is None:
        raise HTTPException(404, "calendar not found")
    deadline = add_business_duration(assocs, hols, from_dt, body.hours)
    return {
        "from_dt":     body.from_dt,
        "hours":       body.hours,
        "deadline":    deadline.isoformat(),
        "calendar_id": body.calendar_id,
    }


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
