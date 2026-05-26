"""
groups_router.py
Arc 9 — Agent Groups & Supervisor Scope

CRUD for AgentGroup entities and their members/supervisors/shifts.
All endpoints are admin-authenticated (X-Admin-Token header).

Routes:
  GET    /auth/v1/groups                             — list groups for tenant
  POST   /auth/v1/groups                             — create group
  GET    /auth/v1/groups/{group_id}                  — get group detail
  PUT    /auth/v1/groups/{group_id}                  — update group name/description
  DELETE /auth/v1/groups/{group_id}                  — delete group

  GET    /auth/v1/groups/{group_id}/members          — list agent_type members
  POST   /auth/v1/groups/{group_id}/members          — add member
  DELETE /auth/v1/groups/{group_id}/members/{agent_type_id} — remove member

  GET    /auth/v1/groups/{group_id}/users            — list human agent users
  POST   /auth/v1/groups/{group_id}/users            — add user
  DELETE /auth/v1/groups/{group_id}/users/{user_id}  — remove user

  GET    /auth/v1/groups/{group_id}/supervisors      — list supervisors
  POST   /auth/v1/groups/{group_id}/supervisors      — add supervisor
  DELETE /auth/v1/groups/{group_id}/supervisors/{user_id} — remove supervisor

  GET    /auth/v1/groups/{group_id}/shifts           — list shifts
  POST   /auth/v1/groups/{group_id}/shifts           — create shift
  PUT    /auth/v1/groups/{group_id}/shifts/{shift_id} — update shift
  DELETE /auth/v1/groups/{group_id}/shifts/{shift_id} — delete shift
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from . import db as db_mod
from .config import Settings, get_settings

logger = logging.getLogger("plughub.auth_api.groups")

groups_router = APIRouter(prefix="/auth/v1/groups", tags=["groups"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _settings() -> Settings:
    return get_settings()


def _require_admin(
    x_admin_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(_settings),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _serialize_group(row: dict[str, Any]) -> dict[str, Any]:
    """Convert asyncpg types for JSON serialization."""
    result = dict(row)
    if "group_id" in result and result["group_id"] is not None:
        result["group_id"] = str(result["group_id"])
    if "created_at" in result and hasattr(result["created_at"], "isoformat"):
        result["created_at"] = result["created_at"].isoformat()
    if "updated_at" in result and hasattr(result["updated_at"], "isoformat"):
        result["updated_at"] = result["updated_at"].isoformat()
    return result


def _serialize_user(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if "id" in result and result["id"] is not None:
        result["id"] = str(result["id"])
    if "user_id" in result and result["user_id"] is not None:
        result["user_id"] = str(result["user_id"])
    return result


# ─── Pydantic models ──────────────────────────────────────────────────────────

class CreateGroupRequest(BaseModel):
    tenant_id:   str
    name:        str
    description: str = ""


class UpdateGroupRequest(BaseModel):
    name:        str | None = None
    description: str | None = None


class AddMemberRequest(BaseModel):
    agent_type_id: str
    is_human:      bool = False


class AddUserRequest(BaseModel):
    user_id: str


class AddSupervisorRequest(BaseModel):
    user_id: str


class CreateShiftRequest(BaseModel):
    supervisor_user_id: str
    days_of_week:       list[int]
    time_start:         str        # HH:MM or HH:MM:SS
    time_end:           str
    timezone:           str = "UTC"
    active:             bool = True


class UpdateShiftRequest(BaseModel):
    supervisor_user_id: str | None = None
    days_of_week:       list[int] | None = None
    time_start:         str | None = None
    time_end:           str | None = None
    timezone:           str | None = None
    active:             bool | None = None


# ─── Group CRUD ───────────────────────────────────────────────────────────────

@groups_router.get("", response_model=list[dict],
                   dependencies=[Depends(_require_admin)])
async def list_groups(
    request: Request,
    tenant_id: str = "tenant_demo",
) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_groups(pool, tenant_id)
    return [_serialize_group(r) for r in rows]


@groups_router.post("", response_model=dict, status_code=201,
                    dependencies=[Depends(_require_admin)])
async def create_group(body: CreateGroupRequest, request: Request) -> dict:
    pool = _get_pool(request)
    row = await db_mod.create_group(
        pool,
        tenant_id=body.tenant_id,
        name=body.name,
        description=body.description,
    )
    return _serialize_group(row)


@groups_router.get("/{group_id}", response_model=dict,
                   dependencies=[Depends(_require_admin)])
async def get_group(group_id: str, request: Request) -> dict:
    pool = _get_pool(request)
    row = await db_mod.get_group(pool, group_id)
    if not row:
        raise HTTPException(status_code=404, detail="Group not found")
    # Enrich with members, users, supervisors, shifts
    members   = await db_mod.list_group_members(pool, group_id)
    users     = await db_mod.list_group_users(pool, group_id)
    supers    = await db_mod.list_group_supervisors(pool, group_id)
    shifts    = await db_mod.list_group_shifts(pool, group_id)
    result = _serialize_group(row)
    result["members"]     = [_serialize_member(m) for m in members]
    result["users"]       = [_serialize_user(u) for u in users]
    result["supervisors"] = [_serialize_user(s) for s in supers]
    result["shifts"]      = shifts
    return result


def _serialize_member(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if "group_id" in result and result["group_id"] is not None:
        result["group_id"] = str(result["group_id"])
    return result


@groups_router.put("/{group_id}", response_model=dict,
                   dependencies=[Depends(_require_admin)])
async def update_group(
    group_id: str,
    body: UpdateGroupRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.update_group(pool, group_id, name=body.name, description=body.description)
    if not row:
        raise HTTPException(status_code=404, detail="Group not found")
    return _serialize_group(row)


@groups_router.delete("/{group_id}", status_code=204,
                      dependencies=[Depends(_require_admin)])
async def delete_group(group_id: str, request: Request) -> None:
    pool = _get_pool(request)
    deleted = await db_mod.delete_group(pool, group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")


# ─── Members ──────────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/members", response_model=list[dict],
                   dependencies=[Depends(_require_admin)])
async def list_members(group_id: str, request: Request) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_group_members(pool, group_id)
    return [_serialize_member(r) for r in rows]


@groups_router.post("/{group_id}/members", response_model=dict, status_code=201,
                    dependencies=[Depends(_require_admin)])
async def add_member(
    group_id: str,
    body: AddMemberRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.add_group_member(pool, group_id, body.agent_type_id, body.is_human)
    return _serialize_member(row)


@groups_router.delete("/{group_id}/members/{agent_type_id}", status_code=204,
                      dependencies=[Depends(_require_admin)])
async def remove_member(
    group_id: str,
    agent_type_id: str,
    request: Request,
) -> None:
    pool = _get_pool(request)
    await db_mod.remove_group_member(pool, group_id, agent_type_id)


# ─── Users ────────────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/users", response_model=list[dict],
                   dependencies=[Depends(_require_admin)])
async def list_users(group_id: str, request: Request) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_group_users(pool, group_id)
    return [_serialize_user(r) for r in rows]


@groups_router.post("/{group_id}/users", response_model=dict, status_code=201,
                    dependencies=[Depends(_require_admin)])
async def add_user(
    group_id: str,
    body: AddUserRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.add_group_user(pool, group_id, body.user_id)
    return _serialize_user(row)


@groups_router.delete("/{group_id}/users/{user_id}", status_code=204,
                      dependencies=[Depends(_require_admin)])
async def remove_user(
    group_id: str,
    user_id: str,
    request: Request,
) -> None:
    pool = _get_pool(request)
    await db_mod.remove_group_user(pool, group_id, user_id)


# ─── Supervisors ──────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/supervisors", response_model=list[dict],
                   dependencies=[Depends(_require_admin)])
async def list_supervisors(group_id: str, request: Request) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_group_supervisors(pool, group_id)
    return [_serialize_user(r) for r in rows]


@groups_router.post("/{group_id}/supervisors", response_model=dict, status_code=201,
                    dependencies=[Depends(_require_admin)])
async def add_supervisor(
    group_id: str,
    body: AddSupervisorRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.add_group_supervisor(pool, group_id, body.user_id)
    return _serialize_user(row)


@groups_router.delete("/{group_id}/supervisors/{user_id}", status_code=204,
                      dependencies=[Depends(_require_admin)])
async def remove_supervisor(
    group_id: str,
    user_id: str,
    request: Request,
) -> None:
    pool = _get_pool(request)
    await db_mod.remove_group_supervisor(pool, group_id, user_id)


# ─── Shifts ───────────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/shifts", response_model=list[dict],
                   dependencies=[Depends(_require_admin)])
async def list_shifts(group_id: str, request: Request) -> list[dict]:
    pool = _get_pool(request)
    return await db_mod.list_group_shifts(pool, group_id)


@groups_router.post("/{group_id}/shifts", response_model=dict, status_code=201,
                    dependencies=[Depends(_require_admin)])
async def create_shift(
    group_id: str,
    body: CreateShiftRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    return await db_mod.create_group_shift(
        pool,
        group_id=group_id,
        supervisor_user_id=body.supervisor_user_id,
        days_of_week=body.days_of_week,
        time_start=body.time_start,
        time_end=body.time_end,
        timezone=body.timezone,
        active=body.active,
    )


@groups_router.put("/{group_id}/shifts/{shift_id}", response_model=dict,
                   dependencies=[Depends(_require_admin)])
async def update_shift(
    group_id: str,
    shift_id: str,
    body: UpdateShiftRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.update_group_shift(
        pool, shift_id,
        supervisor_user_id=body.supervisor_user_id,
        days_of_week=body.days_of_week,
        time_start=body.time_start,
        time_end=body.time_end,
        timezone=body.timezone,
        active=body.active,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Shift not found")
    return row


@groups_router.delete("/{group_id}/shifts/{shift_id}", status_code=204,
                      dependencies=[Depends(_require_admin)])
async def delete_shift(
    group_id: str,
    shift_id: str,
    request: Request,
) -> None:
    pool = _get_pool(request)
    await db_mod.delete_group_shift(pool, shift_id)
