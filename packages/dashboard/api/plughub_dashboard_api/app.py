"""
FastAPI application for the PlugHub Dashboard.
Exposes three read-only endpoints backed by ClickHouse queries.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import AgentListResponse, ContactListResponse, PoolListResponse
from .queries import get_agent_list, get_contact_list, get_pool_list

app = FastAPI(
    title="PlugHub Dashboard API",
    description="Read-only analytics API backed by ClickHouse.",
    version="0.1.0",
)

# CORS — allow the Vite dev server and the production build origin
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/pools", response_model=PoolListResponse)
def list_pools() -> PoolListResponse:
    """Return summary metrics for every pool with at least one evaluation."""
    try:
        return get_pool_list()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/pools/{pool_id}/agents", response_model=AgentListResponse)
def list_agents(
    pool_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AgentListResponse:
    """Return agent profiles (scores + trend + section breakdown) for a pool."""
    try:
        return get_agent_list(pool_id, limit=limit, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/agents/{agent_id}/contacts", response_model=ContactListResponse)
def list_contacts(
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ContactListResponse:
    """Return per-contact evaluations with item-level detail for an agent."""
    try:
        return get_contact_list(agent_id, limit=limit, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
