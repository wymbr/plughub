"""
sampling.py
Sampling engine for evaluation-api.

Determines whether a closed session should be scheduled for evaluation
based on the campaign's SamplingRules.

Sampling modes:
  percentage  — sample N% of sessions randomly (default 10%)
  fixed       — sample every N-th session
  all         — sample every session

Filters applied before sampling:
  min_duration_s  — session must be at least this long (seconds)
  agent_type_ids  — whitelist of agent_type_ids (empty = any)
  pool_ids        — whitelist of pool_ids (empty = any)
  channels        — whitelist of channels (empty = any)
  outcome_filter  — whitelist of session outcomes (empty = any)

Business-hours deadline:
  If the campaign schedule defines business hours, the expires_at deadline
  for the instance is calculated using the calendar-api.
  Fallback: wall-clock hours.
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger("plughub.evaluation.sampling")


# ─── Should sample? ───────────────────────────────────────────────────────────

def should_sample(
    session_id: str,
    session_meta: dict[str, Any],
    sampling_rules: dict[str, Any],
    *,
    counter: int = 0,
) -> bool:
    """
    Returns True if this session should be sampled for evaluation.

    Args:
        session_id:    session identifier (used for deterministic hashing)
        session_meta:  dict with keys: duration_s, agent_type_id, pool_id,
                       channel, outcome
        sampling_rules: SamplingRules JSONB from the campaign
        counter:       running count of sessions evaluated in this campaign
                       (used for 'fixed' mode)
    """
    if not sampling_rules:
        # Default: sample 10% randomly
        return _sample_percentage(session_id, 0.1)

    mode = sampling_rules.get("mode", "percentage")

    # ── Filters ────────────────────────────────────────────────────────────
    min_dur = sampling_rules.get("min_duration_s", 0)
    if session_meta.get("duration_s", 0) < min_dur:
        return False

    agent_ids = sampling_rules.get("agent_type_ids") or []
    if agent_ids and session_meta.get("agent_type_id") not in agent_ids:
        return False

    pool_ids = sampling_rules.get("pool_ids") or []
    if pool_ids and session_meta.get("pool_id") not in pool_ids:
        return False

    channels = sampling_rules.get("channels") or []
    if channels and session_meta.get("channel") not in channels:
        return False

    outcomes = sampling_rules.get("outcome_filter") or []
    if outcomes and session_meta.get("outcome") not in outcomes:
        return False

    # ── Sampling mode ──────────────────────────────────────────────────────
    if mode == "all":
        return True

    if mode == "fixed":
        n = max(1, int(sampling_rules.get("every_n", 5)))
        return counter > 0 and counter % n == 0

    # default: percentage
    rate = float(sampling_rules.get("rate", 0.1))
    rate = max(0.0, min(1.0, rate))
    return _sample_percentage(session_id, rate)


def _sample_percentage(session_id: str, rate: float) -> bool:
    """Deterministic: hash session_id → bucket 0–99 → compare to rate."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    bucket = int(digest[:4], 16) % 100  # 0–99
    return bucket < int(rate * 100)


# ─── Deadline calculation ─────────────────────────────────────────────────────

async def compute_expires_at(
    campaign: dict[str, Any],
    calendar_api_url: str,
    *,
    default_ttl_hours: int = 72,
) -> datetime:
    """
    Calculate expires_at for a new evaluation instance.

    If the campaign schedule specifies business_hours=True, call the
    calendar-api to add business-hours duration. Otherwise, add wall-clock hours.
    """
    schedule = campaign.get("schedule") or {}
    ttl_hours = schedule.get("ttl_hours", default_ttl_hours)
    use_business = schedule.get("business_hours", False)

    now = datetime.now(tz=timezone.utc)

    if not use_business:
        return now + timedelta(hours=ttl_hours)

    calendar_id = schedule.get("calendar_id")
    if not calendar_id:
        return now + timedelta(hours=ttl_hours)

    # Call calendar-api: POST /v1/calendar/business-deadline
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{calendar_api_url}/v1/calendar/business-deadline",
                json={
                    "calendar_id": calendar_id,
                    "from_dt": now.isoformat(),
                    "hours": ttl_hours,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                deadline_str = data.get("deadline")
                if deadline_str:
                    return datetime.fromisoformat(deadline_str)
    except Exception as exc:
        logger.warning("calendar-api call failed, using wall-clock: %s", exc)

    return now + timedelta(hours=ttl_hours)


# ─── Priority scoring ─────────────────────────────────────────────────────────

def compute_priority(
    session_meta: dict[str, Any],
    sampling_rules: dict[str, Any],
) -> int:
    """
    Priority 1 (highest) – 10 (lowest).

    Rules applied in order:
    - priority_overrides: list of {field, value, priority} mappings
    - Default: 5
    """
    overrides = sampling_rules.get("priority_overrides") or []
    for override in overrides:
        field = override.get("field")
        value = override.get("value")
        if field and session_meta.get(field) == value:
            return int(override.get("priority", 5))
    return int(sampling_rules.get("default_priority", 5))
