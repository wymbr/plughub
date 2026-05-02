"""
Response models for the Dashboard API.
Mirrors what the UI components expect.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── Pool-level view ────────────────────────────────────────────────────────────

class PoolSummary(BaseModel):
    """One row per pool — top-level dashboard."""
    pool_id: str
    agent_count: int
    evaluation_count: int
    avg_score: float
    p25_score: float
    p75_score: float
    last_evaluated_at: Optional[datetime]


class PoolListResponse(BaseModel):
    pools: list[PoolSummary]
    total: int


# ── Agent-level view ───────────────────────────────────────────────────────────

class AgentScoreTrend(BaseModel):
    """Score per day for sparkline charts."""
    date: str          # YYYY-MM-DD
    avg_score: float
    evaluation_count: int


class SectionScore(BaseModel):
    section_id: str
    score_type: str    # "base_score" | "context_score"
    avg_score: float
    evaluation_count: int
    triggered_by_key: Optional[str]
    triggered_by_val: Optional[str]


class AgentProfile(BaseModel):
    agent_id: str
    agent_type: str
    pool_id: str
    evaluation_count: int
    avg_score: float
    trend: list[AgentScoreTrend]
    section_scores: list[SectionScore]


class AgentListResponse(BaseModel):
    agents: list[AgentProfile]
    total: int
    pool_id: str


# ── Contact drill-down ──────────────────────────────────────────────────────────

class EvalItemDetail(BaseModel):
    section_id: str
    subsection_id: str
    item_id: str
    value: int
    weight: int
    justification: Optional[str]


class ContactEvaluation(BaseModel):
    evaluation_id: str
    contact_id: str
    agent_id: str
    pool_id: str
    skill_id: str
    evaluated_at: datetime
    overall_score: float
    items: list[EvalItemDetail]


class ContactListResponse(BaseModel):
    contacts: list[ContactEvaluation]
    total: int
    agent_id: str
