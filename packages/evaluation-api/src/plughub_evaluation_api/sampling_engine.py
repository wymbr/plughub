"""
sampling_engine.py
Arc 13 Fase H — Post-finalization Curation Sampling Engine.

Evaluates CurationSamplingRules for a campaign after evaluation_finalized
(Fluxo 2 — AI agent evaluated). Creates one CurationReview when any rule
matches; multiple matches produce a composite trigger string.

Rule types:
  score_extremes   — top/bottom absolute percentile detection
  deploy_baseline  — first N finalized instances in the campaign (baseline calibration)
  score_outlier    — score deviates > X std deviations from campaign rolling average
  na_excess        — evaluator marked >= N criteria as NA (possible evasion)
  random_baseline  — deterministic random sample (hash-based) for drift monitoring
  reviewer_signal  — handled separately by Fluxo 1 pre_review flow; skipped here

Entry point: run_curation_sampling() — call as asyncio.create_task().
"""
from __future__ import annotations

import hashlib
import logging
import math
import statistics
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.evaluation.sampling_engine")


# ─── DB helpers (inline to avoid circular imports) ────────────────────────────

async def _get_campaign_score_stats(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str,
    lookback_days: int = 30,
) -> dict[str, float | int]:
    """
    Return {avg, std_dev, count} of normalized_scores for recently
    finalized results in this campaign (up to 500 rows, last 30 days).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT normalized_score
              FROM evaluation.results
             WHERE campaign_id = $1
               AND tenant_id   = $2
               AND finalized_at IS NOT NULL
               AND finalized_at >= now() - ($3 || ' days')::interval
               AND normalized_score IS NOT NULL
             ORDER BY finalized_at DESC
             LIMIT 500
            """,
            campaign_id, tenant_id, str(lookback_days),
        )
    scores = [float(r["normalized_score"]) for r in rows]
    if not scores:
        return {"avg": 0.5, "std_dev": 0.0, "count": 0}
    avg = statistics.mean(scores)
    std_dev = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    return {"avg": avg, "std_dev": std_dev, "count": len(scores)}


async def _count_finalized_instances(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str,
) -> int:
    """Count all finalized evaluation instances for this campaign (before this one)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt
              FROM evaluation.results
             WHERE campaign_id = $1
               AND tenant_id   = $2
               AND finalized_at IS NOT NULL
            """,
            campaign_id, tenant_id,
        )
    return int(row["cnt"]) if row else 0


async def _count_na_criteria(
    pool: asyncpg.Pool,
    tenant_id: str,
    instance_id: str,
) -> int:
    """Count criterion_responses marked NA for this evaluation instance."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt
              FROM evaluation.criterion_responses
             WHERE instance_id = $1
               AND tenant_id   = $2
               AND na = TRUE
            """,
            instance_id, tenant_id,
        )
    return int(row["cnt"]) if row else 0


# ─── Individual rule evaluators ───────────────────────────────────────────────

def _eval_score_extremes(rule: dict, normalized_score: float) -> bool:
    """
    Flag if the score is suspiciously high (permissiveness) or low (excessive strictness).

    Params:
      top_pct    (default 0.1): flag if normalized_score >= 1 - top_pct
      bottom_pct (default 0.1): flag if normalized_score <= bottom_pct
    """
    params = rule.get("params") or {}
    top_pct    = max(0.0, min(1.0, float(params.get("top_pct",    0.10))))
    bottom_pct = max(0.0, min(1.0, float(params.get("bottom_pct", 0.10))))
    return normalized_score >= (1.0 - top_pct) or normalized_score <= bottom_pct


def _eval_random_baseline(rule: dict, instance_id: str) -> bool:
    """
    Deterministic random sample — hash(instance_id) into bucket 0–99.

    Params:
      rate (default 0.05): fraction of all evaluations to flag
    """
    params = rule.get("params") or {}
    rate = max(0.0, min(1.0, float(params.get("rate", 0.05))))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(instance_id.encode()).hexdigest()
    bucket = int(digest[:4], 16) % 100
    return bucket < int(rate * 100)


async def _eval_deploy_baseline(
    rule: dict,
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str,
) -> bool:
    """
    Flag if this is among the first N finalized instances in the campaign
    (provides calibration baseline when a new skill version is deployed).

    Params:
      first_n (default 10): number of leading instances to flag
    """
    params = rule.get("params") or {}
    first_n = max(1, int(params.get("first_n", 10)))
    # count is the count BEFORE this instance is finalized
    count = await _count_finalized_instances(pool, tenant_id, campaign_id)
    return (count + 1) <= first_n


async def _eval_score_outlier(
    rule: dict,
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str,
    normalized_score: float,
) -> bool:
    """
    Flag if the score deviates more than std_dev_threshold standard deviations
    from the rolling campaign average (last 30 days, min 5 samples).

    Params:
      std_dev_threshold (default 2.0): number of std deviations to consider an outlier
    """
    params = rule.get("params") or {}
    threshold = max(0.5, float(params.get("std_dev_threshold", 2.0)))
    stats = await _get_campaign_score_stats(pool, tenant_id, campaign_id)
    if stats["count"] < 5:
        return False  # Not enough data for statistical significance
    std = stats["std_dev"]
    if std < 0.001:
        return False  # All scores are identical — no meaningful outlier possible
    deviation = abs(normalized_score - float(stats["avg"])) / std
    return deviation > threshold


async def _eval_na_excess(
    rule: dict,
    pool: asyncpg.Pool,
    tenant_id: str,
    instance_id: str,
) -> bool:
    """
    Flag if the evaluator marked >= min_na_count criteria as NA — possible
    evasion of judgment.

    Params:
      min_na_count (default 3): minimum number of NA criteria to trigger
    """
    params = rule.get("params") or {}
    min_na = max(1, int(params.get("min_na_count", 3)))
    na_count = await _count_na_criteria(pool, tenant_id, instance_id)
    return na_count >= min_na


# ─── Main entry point ─────────────────────────────────────────────────────────

async def run_curation_sampling(
    pool: asyncpg.Pool,
    *,
    instance_id: str,
    tenant_id: str,
    campaign_id: str,
    normalized_score: float,
) -> None:
    """
    Background task triggered after evaluation_finalized for AI agents (Fluxo 2).

    Evaluates all enabled CurationSamplingRules for the campaign in priority order.
    If one or more rules match, creates a single CurationReview with a composite
    trigger string (e.g. "score_extremes,random_baseline").

    Errors are logged and never raised — this function must never fail the caller.
    """
    try:
        from . import db as _db

        rules = await _db.list_sampling_rules(pool, tenant_id, campaign_id)
        enabled = [r for r in rules if r.get("enabled", True)]
        if not enabled:
            return

        triggered: list[str] = []

        for rule in enabled:
            rule_type = rule.get("rule_type", "")
            matched = False

            if rule_type == "score_extremes":
                matched = _eval_score_extremes(rule, normalized_score)

            elif rule_type == "random_baseline":
                matched = _eval_random_baseline(rule, instance_id)

            elif rule_type == "deploy_baseline":
                matched = await _eval_deploy_baseline(rule, pool, tenant_id, campaign_id)

            elif rule_type == "score_outlier":
                matched = await _eval_score_outlier(
                    rule, pool, tenant_id, campaign_id, normalized_score
                )

            elif rule_type == "na_excess":
                matched = await _eval_na_excess(rule, pool, tenant_id, instance_id)

            elif rule_type == "reviewer_signal":
                pass  # Handled by Fluxo 1 pre_review — skip for Fluxo 2

            if matched:
                triggered.append(rule_type)

        if not triggered:
            return

        trigger = ",".join(triggered)
        await _db.create_curation_review(
            pool,
            tenant_id=tenant_id,
            evaluation_instance_id=instance_id,
            trigger=trigger,
        )
        logger.info(
            "curation review created: instance=%s campaign=%s trigger=%s",
            instance_id, campaign_id, trigger,
        )

    except Exception as exc:
        logger.error(
            "curation sampling failed (non-blocking): instance=%s err=%s",
            instance_id, exc,
        )
