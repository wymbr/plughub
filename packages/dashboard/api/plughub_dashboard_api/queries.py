"""
All ClickHouse queries for the Dashboard API.
Queries are plain SQL strings parameterised via clickhouse-connect's
parameter substitution to avoid injection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .db import get_client
from .models import (
    AgentListResponse,
    AgentProfile,
    AgentScoreTrend,
    ContactEvaluation,
    ContactListResponse,
    EvalItemDetail,
    PoolListResponse,
    PoolSummary,
    SectionScore,
)


# ── Pool list ──────────────────────────────────────────────────────────────────

def get_pool_list() -> PoolListResponse:
    client = get_client()

    rows = client.query(
        """
        SELECT
            pool_id,
            count(DISTINCT agent_id)                AS agent_count,
            count()                                  AS evaluation_count,
            avg(score)                       AS avg_score,
            quantile(0.25)(score)            AS p25_score,
            quantile(0.75)(score)            AS p75_score,
            max(evaluated_at)                        AS last_evaluated_at
        FROM evaluation_scores
        GROUP BY pool_id
        ORDER BY pool_id
        """
    ).result_rows

    pools = [
        PoolSummary(
            pool_id=r[0],
            agent_count=r[1],
            evaluation_count=r[2],
            avg_score=round(float(r[3]), 2),
            p25_score=round(float(r[4]), 2),
            p75_score=round(float(r[5]), 2),
            last_evaluated_at=r[6],
        )
        for r in rows
    ]

    return PoolListResponse(pools=pools, total=len(pools))


# ── Agent list for a pool ──────────────────────────────────────────────────────

def get_agent_list(pool_id: str, limit: int = 50, offset: int = 0) -> AgentListResponse:
    client = get_client()

    # Summary per agent
    summary_rows = client.query(
        """
        SELECT
            agent_id,
            agent_type,
            pool_id,
            count()                AS evaluation_count,
            avg(score)     AS avg_score
        FROM evaluation_scores
        WHERE pool_id = {pool_id:String}
        GROUP BY agent_id, agent_type, pool_id
        ORDER BY avg_score DESC
        LIMIT {limit:UInt32}
        OFFSET {offset:UInt32}
        """,
        parameters={"pool_id": pool_id, "limit": limit, "offset": offset},
    ).result_rows

    agent_ids = [r[0] for r in summary_rows]

    if not agent_ids:
        return AgentListResponse(agents=[], total=0, pool_id=pool_id)

    # Trend per agent (last 30 days)
    trend_rows = client.query(
        """
        SELECT
            agent_id,
            toDate(evaluated_at)   AS day,
            avg(score)     AS avg_score,
            count()                AS eval_count
        FROM evaluation_scores
        WHERE pool_id = {pool_id:String}
          AND agent_id IN {agent_ids:Array(String)}
          AND evaluated_at >= now() - INTERVAL 30 DAY
        GROUP BY agent_id, day
        ORDER BY agent_id, day
        """,
        parameters={"pool_id": pool_id, "agent_ids": agent_ids},
    ).result_rows

    # Section scores per agent
    section_rows = client.query(
        """
        SELECT
            agent_id,
            section_id,
            score_type,
            avg(score)             AS avg_score,
            count()                AS eval_count,
            any(triggered_by_key)  AS triggered_by_key,
            any(triggered_by_val)  AS triggered_by_val
        FROM evaluation_scores
        WHERE pool_id = {pool_id:String}
          AND agent_id IN {agent_ids:Array(String)}
        GROUP BY agent_id, section_id, score_type
        ORDER BY agent_id, section_id
        """,
        parameters={"pool_id": pool_id, "agent_ids": agent_ids},
    ).result_rows

    # Build per-agent dicts
    from collections import defaultdict

    trend_by_agent: dict[str, list[AgentScoreTrend]] = defaultdict(list)
    for r in trend_rows:
        trend_by_agent[r[0]].append(
            AgentScoreTrend(
                date=str(r[1]),
                avg_score=round(float(r[2]), 2),
                evaluation_count=r[3],
            )
        )

    section_by_agent: dict[str, list[SectionScore]] = defaultdict(list)
    for r in section_rows:
        section_by_agent[r[0]].append(
            SectionScore(
                section_id=r[1],
                score_type=r[2],
                avg_score=round(float(r[3]), 2),
                evaluation_count=r[4],
                triggered_by_key=r[5] or None,
                triggered_by_val=r[6] or None,
            )
        )

    # Total count
    total_row = client.query(
        """
        SELECT count(DISTINCT agent_id)
        FROM evaluation_scores
        WHERE pool_id = {pool_id:String}
        """,
        parameters={"pool_id": pool_id},
    ).result_rows
    total = int(total_row[0][0]) if total_row else 0

    agents = [
        AgentProfile(
            agent_id=r[0],
            agent_type=r[1],
            pool_id=r[2],
            evaluation_count=r[3],
            avg_score=round(float(r[4]), 2),
            trend=trend_by_agent[r[0]],
            section_scores=section_by_agent[r[0]],
        )
        for r in summary_rows
    ]

    return AgentListResponse(agents=agents, total=total, pool_id=pool_id)


# ── Contact list for an agent ──────────────────────────────────────────────────

def get_contact_list(
    agent_id: str, limit: int = 50, offset: int = 0
) -> ContactListResponse:
    client = get_client()

    # Header rows
    header_rows = client.query(
        """
        SELECT
            evaluation_id,
            contact_id,
            agent_id,
            pool_id,
            skill_id,
            evaluated_at,
            score           AS overall_score
        FROM evaluation_scores
        WHERE agent_id = {agent_id:String}
        ORDER BY evaluated_at DESC
        LIMIT {limit:UInt32}
        OFFSET {offset:UInt32}
        """,
        parameters={"agent_id": agent_id, "limit": limit, "offset": offset},
    ).result_rows

    evaluation_ids = [str(r[0]) for r in header_rows]

    if not evaluation_ids:
        total_row = client.query(
            "SELECT count() FROM evaluation_scores WHERE agent_id = {a:String}",
            parameters={"a": agent_id},
        ).result_rows
        total = int(total_row[0][0]) if total_row else 0
        return ContactListResponse(contacts=[], total=total, agent_id=agent_id)

    # Item rows
    item_rows = client.query(
        """
        SELECT
            evaluation_id,
            section_id,
            subsection_id,
            item_id,
            value,
            weight,
            justification
        FROM evaluation_items
        WHERE evaluation_id IN {eids:Array(String)}
        ORDER BY evaluation_id, section_id, subsection_id, item_id
        """,
        parameters={"eids": evaluation_ids},
    ).result_rows

    from collections import defaultdict

    items_by_eval: dict[str, list[EvalItemDetail]] = defaultdict(list)
    for r in item_rows:
        items_by_eval[str(r[0])].append(
            EvalItemDetail(
                section_id=r[1],
                subsection_id=r[2],
                item_id=r[3],
                value=r[4],
                weight=r[5],
                justification=r[6] or None,
            )
        )

    # Total count
    total_row = client.query(
        "SELECT count() FROM evaluation_scores WHERE agent_id = {a:String}",
        parameters={"a": agent_id},
    ).result_rows
    total = int(total_row[0][0]) if total_row else 0

    contacts = [
        ContactEvaluation(
            evaluation_id=str(r[0]),
            contact_id=str(r[1]),
            agent_id=r[2],
            pool_id=r[3],
            skill_id=r[4],
            evaluated_at=r[5],
            overall_score=round(float(r[6]), 2),
            items=items_by_eval[str(r[0])],
        )
        for r in header_rows
    ]

    return ContactListResponse(contacts=contacts, total=total, agent_id=agent_id)
