"""
groups_router.py
Arc 9 — Agent Groups & Supervisor Scope

CRUD for AgentGroup entities and their users (members) / supervisors.
All endpoints are admin-authenticated (X-Admin-Token header).

Note (2026-07-02): the agent_type "members" and "shifts" sub-resources were
removed — see docs/arcos/arc9-agent-groups.md. Human/AI typing is owned by
Pool.agent_kind (was duplicated/unvalidated here); differing shift needs are
now modeled as separate groups instead of per-member time windows.

Routes:
  GET    /auth/v1/groups                             — list groups for tenant
  POST   /auth/v1/groups                             — create group
  GET    /auth/v1/groups/{group_id}                  — get group detail
  PUT    /auth/v1/groups/{group_id}                  — update group name/description
  DELETE /auth/v1/groups/{group_id}                  — delete group

  GET    /auth/v1/groups/{group_id}/users            — list human agent users
  POST   /auth/v1/groups/{group_id}/users            — add user
  DELETE /auth/v1/groups/{group_id}/users/{user_id}  — remove user

  GET    /auth/v1/groups/{group_id}/supervisors      — list supervisors
  POST   /auth/v1/groups/{group_id}/supervisors      — add supervisor
  DELETE /auth/v1/groups/{group_id}/supervisors/{user_id} — remove supervisor
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from . import db as db_mod
from .config import Settings, get_settings
# G-PROBE platform-wide: grupos autorizam pelo JWT do operador + ABAC `config.usuarios`
# (mesmo gate de users/permissions); sem fallback de admin-token.
from .router import _PERMS_WRITE, _USUARIOS_READ, _USUARIOS_WRITE

logger = logging.getLogger("plughub.auth_api.groups")

groups_router = APIRouter(prefix="/auth/v1/groups", tags=["groups"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _settings() -> Settings:
    return get_settings()


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


class AddUserRequest(BaseModel):
    user_id: str


class AddSupervisorRequest(BaseModel):
    user_id: str


# ─── Group CRUD ───────────────────────────────────────────────────────────────

@groups_router.get("", response_model=list[dict],
                   dependencies=[Depends(_USUARIOS_READ)])
async def list_groups(
    request: Request,
    tenant_id: str = "tenant_demo",
) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_groups(pool, tenant_id)
    return [_serialize_group(r) for r in rows]


@groups_router.post("", response_model=dict, status_code=201,
                    dependencies=[Depends(_USUARIOS_WRITE)])
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
                   dependencies=[Depends(_USUARIOS_READ)])
async def get_group(group_id: str, request: Request) -> dict:
    pool = _get_pool(request)
    row = await db_mod.get_group(pool, group_id)
    if not row:
        raise HTTPException(status_code=404, detail="Group not found")
    # Enrich with users (members) and supervisors
    users     = await db_mod.list_group_users(pool, group_id)
    supers    = await db_mod.list_group_supervisors(pool, group_id)
    result = _serialize_group(row)
    result["users"]       = [_serialize_user(u) for u in users]
    result["supervisors"] = [_serialize_user(s) for s in supers]
    return result


@groups_router.put("/{group_id}", response_model=dict,
                   dependencies=[Depends(_USUARIOS_WRITE)])
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
                      dependencies=[Depends(_USUARIOS_WRITE)])
async def delete_group(group_id: str, request: Request) -> None:
    pool = _get_pool(request)
    deleted = await db_mod.delete_group(pool, group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")


# ─── Users ────────────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/users", response_model=list[dict],
                   dependencies=[Depends(_USUARIOS_READ)])
async def list_users(group_id: str, request: Request) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_group_users(pool, group_id)
    return [_serialize_user(r) for r in rows]


@groups_router.post("/{group_id}/users", response_model=dict, status_code=201,
                    dependencies=[Depends(_USUARIOS_WRITE)])
async def add_user(
    group_id: str,
    body: AddUserRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.add_group_user(pool, group_id, body.user_id)
    return _serialize_user(row)


@groups_router.delete("/{group_id}/users/{user_id}", status_code=204,
                      dependencies=[Depends(_USUARIOS_WRITE)])
async def remove_user(
    group_id: str,
    user_id: str,
    request: Request,
) -> None:
    pool = _get_pool(request)
    await db_mod.remove_group_user(pool, group_id, user_id)


# ─── Supervisors ──────────────────────────────────────────────────────────────

@groups_router.get("/{group_id}/supervisors", response_model=list[dict],
                   dependencies=[Depends(_USUARIOS_READ)])
async def list_supervisors(group_id: str, request: Request) -> list[dict]:
    pool = _get_pool(request)
    rows = await db_mod.list_group_supervisors(pool, group_id)
    return [_serialize_user(r) for r in rows]


# ⚠️ NOMEAR SUPERVISOR e conceder, nao administrar (split de 2026-08-27).
# `resolve_supervisor_scope` deriva `supervised_user_ids` dos grupos que a pessoa
# SUPERVISIONA, e a evaluation-api (`router.py:465`) usa esse claim para decidir de quem
# ela ve as avaliacoes. Sob `config.users` isso era auto-concessao de escopo: crio um
# grupo, me nomeio supervisor, passo a ver as avaliacoes de quem esta nele.
#
# A membership (`/users`) FICA em `config.users`: alargar por ali so alcanca grupos que
# voce ja supervisiona — e isso e o seu escopo, nao uma extensao dele.
@groups_router.post("/{group_id}/supervisors", response_model=dict, status_code=201,
                    dependencies=[Depends(_PERMS_WRITE)])
async def add_supervisor(
    group_id: str,
    body: AddSupervisorRequest,
    request: Request,
) -> dict:
    pool = _get_pool(request)
    row = await db_mod.add_group_supervisor(pool, group_id, body.user_id)
    return _serialize_user(row)


@groups_router.delete("/{group_id}/supervisors/{user_id}", status_code=204,
                      dependencies=[Depends(_PERMS_WRITE)])
async def remove_supervisor(
    group_id: str,
    user_id: str,
    request: Request,
) -> None:
    pool = _get_pool(request)
    await db_mod.remove_group_supervisor(pool, group_id, user_id)
