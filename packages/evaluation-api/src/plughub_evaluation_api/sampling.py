"""
sampling.py
Sampling engine for evaluation-api.

Determines whether a closed session should be scheduled for evaluation
based on the campaign's SamplingRules.

Sampling modes:
  percentage  — sample N% of sessions randomly (default 10%) — stateless, hash-based
  fixed       — sample every N-th session — stateless
  all         — sample every session — stateless
  quota       — per-agent cumulative deficit quota (R10) — STATEFUL (Redis INCR);
                see should_sample_quota(). Guarantees fair coverage (every agent
                audited) instead of statistical representativeness.

Filters applied before sampling (shared by every mode, incl. quota — a filtered
contact never inflates the quota denominator):
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
    if not _passes_filters(session_meta, sampling_rules):
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


def _passes_filters(session_meta: dict[str, Any], sampling_rules: dict[str, Any]) -> bool:
    """Shared eligibility gate for every sampling mode.

    A contact that fails any filter is NOT eligible — and (for quota mode)
    therefore never increments the quota denominator (ADR §5).
    """
    if session_meta.get("duration_s", 0) < sampling_rules.get("min_duration_s", 0):
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

    return True


# ─── Quota mode (R10) — per-agent cumulative deficit, STATEFUL ────────────────
#
# Paradigm shift vs. the stateless hash: the first eligible contact of each agent
# is ALWAYS sampled (floor); thereafter, on each eligible contact, we recompute
# evaluated/total and sample the trigger contact whenever evaluated/total < x%.
# Cumulative (not daily). Converges to x% while guaranteeing the floor.
#
# Counter key (Redis hash, atomic HINCRBY):
#   Humano: (campaign, user_id)                          → "h:{user_id}"
#   IA:     (campaign, pool_id, skill_id, deploy_version) → "ai:{pool}:{skill}:{ver}"
# deploy_version unresolved → "_nover" sentinel collapses to the
# (campaign, pool_id, skill_id) bucket (ADR borda) — never blocks sampling.

_QUOTA_TTL_S = 90 * 24 * 3600          # ~90d — cumulative, campaign-lived
_QUOTA_NO_VERSION = "_nover"           # deploy_version fallback bucket
_DEFAULT_QUOTA_RATE_HUMAN = 0.10
_DEFAULT_QUOTA_RATE_AI = 0.05          # IA opera 24×7 → tipicamente menor (R11)


def quota_agent_key(
    *,
    evaluated_user_id: str | None,
    pool_id: str | None,
    skill_id: str | None,
    deploy_version: str | None,
) -> tuple[str, bool]:
    """Build the per-agent quota sub-key and whether it is a human agent.

    Human is identified by presence of ``evaluated_user_id`` (human segments carry
    user_id; AI segments carry skill_id/flow_id). Returns ``(agent_key, is_human)``.
    """
    if evaluated_user_id:
        return f"h:{evaluated_user_id}", True
    ver = deploy_version or _QUOTA_NO_VERSION
    return f"ai:{pool_id or ''}:{skill_id or ''}:{ver}", False


def quota_rate(sampling_rules: dict[str, Any], *, is_human: bool) -> float:
    """Per-agent target rate (R11). Separate human/AI keys, fallback to ``rate``.

    Config keys (in SamplingRules JSONB): ``quota_rate_human`` / ``quota_rate_ai``
    (0–1). Absent → legacy ``rate`` → mode default (human 10%, AI 5%)."""
    if is_human:
        raw = sampling_rules.get("quota_rate_human")
        default = _DEFAULT_QUOTA_RATE_HUMAN
    else:
        raw = sampling_rules.get("quota_rate_ai")
        default = _DEFAULT_QUOTA_RATE_AI
    if raw is None:
        raw = sampling_rules.get("rate", default)
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        rate = default
    return max(0.0, min(1.0, rate))


def _quota_decide(total: int, sampled_before: int, rate: float) -> bool:
    """Pure deficit decision (testable without I/O). ``total`` already counts the
    trigger contact; ``sampled_before`` excludes it.

    - first eligible (total<=1) → always sampled (floor / coverage);
    - rate>=1 → all; rate<=0 → only the floor;
    - else → sample iff cumulative coverage is still below target.
    """
    if total <= 1:
        return True
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return (sampled_before / total) < rate


async def should_sample_quota(
    redis_client,
    *,
    tenant_id: str,
    campaign_id: str,
    target_id: str,
    session_meta: dict[str, Any],
    sampling_rules: dict[str, Any],
    evaluated_user_id: str | None = None,
    pool_id: str | None = None,
    skill_id: str | None = None,
    deploy_version: str | None = None,
) -> bool:
    """R10 — per-agent cumulative deficit quota decision (stateful).

    ``target_id`` is the segment_id (preferred) or session_id — used both as the
    idempotency token (a Kafka redelivery must not double-count) and, on Redis
    failure, as the deterministic fallback hash seed.

    Best-effort: if Redis is unavailable, degrade to deterministic percentage on
    the same per-agent rate so coverage is never silently lost.
    """
    if not _passes_filters(session_meta, sampling_rules):
        return False  # ineligible → does NOT touch the counter (denominator)

    agent_key, is_human = quota_agent_key(
        evaluated_user_id=evaluated_user_id, pool_id=pool_id,
        skill_id=skill_id, deploy_version=deploy_version,
    )
    rate = quota_rate(sampling_rules, is_human=is_human)

    hkey = f"{tenant_id}:eval:quota:{campaign_id}:{agent_key}"
    seen_key = f"{tenant_id}:eval:quota:seen:{campaign_id}:{agent_key}"

    if redis_client is None:
        return _sample_percentage(target_id, rate)

    try:
        # Idempotency: count each unique target once (Kafka at-least-once redelivery).
        added = await redis_client.sadd(seen_key, target_id)
        if not added:
            return False  # already decided for this contact → no re-sample
        await redis_client.expire(seen_key, _QUOTA_TTL_S)

        total = int(await redis_client.hincrby(hkey, "total", 1))
        sampled_before = int(await redis_client.hget(hkey, "sampled") or 0)
        decision = _quota_decide(total, sampled_before, rate)
        if decision:
            await redis_client.hincrby(hkey, "sampled", 1)
        await redis_client.expire(hkey, _QUOTA_TTL_S)
        return decision
    except Exception as exc:  # noqa: BLE001 — best-effort, never block sampling
        logger.warning("quota sampling Redis error (%s) — falling back to hash", exc)
        return _sample_percentage(target_id, rate)


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

    # evaluation_calendar_id (campaign-level) takes precedence over
    # schedule.calendar_id (legacy field inside SamplingRules JSONB).
    calendar_id = campaign.get("evaluation_calendar_id") or schedule.get("calendar_id")
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


async def compute_deadline_at(
    campaign: dict[str, Any],
    calendar_api_url: str,
    *,
    hours: int,
) -> datetime:
    """T4 — deadline de contestação/revisão em horário comercial.
    Usa contestation_policy.use_business_hours + evaluation_calendar_id da campanha;
    sem calendário/flag ou hours<=0 → wall-clock. Best-effort (fallback wall-clock)."""
    now = datetime.now(tz=timezone.utc)
    if hours <= 0:
        return now + timedelta(hours=1)  # degenerate; evita deadline no passado
    policy = campaign.get("contestation_policy") or {}
    use_business = policy.get("use_business_hours", False)
    calendar_id = campaign.get("evaluation_calendar_id")
    if not use_business or not calendar_id:
        return now + timedelta(hours=hours)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{calendar_api_url}/v1/calendar/business-deadline",
                json={"calendar_id": calendar_id, "from_dt": now.isoformat(), "hours": hours},
            )
            if resp.status_code == 200:
                deadline_str = resp.json().get("deadline")
                if deadline_str:
                    return datetime.fromisoformat(deadline_str)
    except Exception as exc:
        logger.warning("calendar-api deadline call failed, wall-clock: %s", exc)
    return now + timedelta(hours=hours)


# ─── T15 — janela de despacho (calendar window) ───────────────────────────────

async def campaign_dispatch_open(
    campaign: dict[str, Any],
    calendar_api_url: str,
    *,
    at: datetime | None = None,
) -> bool:
    """T15 (§18.4) — a campanha está DENTRO da janela de despacho agora?

    A janela é definida por associações de calendário na entidade
    ``evaluation_campaign:{campaign_id}`` na calendar-api (mesmo padrão da workflow-api,
    entity_type=workflow). Consulta ``GET /v1/engine/is-open``:

    - sem associação (``calendars_count == 0``) → **aberto** (campanha sem janela
      configurada despacha sempre — comportamento default e caso comum);
    - com associação → honra ``status == 'open'`` (fora de horário/feriado → fechado);
    - calendar-api inacessível/erro → **aberto** (best-effort, nunca bloqueia o despacho,
      no mesmo espírito do fallback wall-clock dos deadlines).

    `evaluation_calendar_id` na campanha continua sendo o ponteiro de calendário (usado em
    deadlines/SLA); a janela de despacho usa a associação da entidade da campanha.
    """
    campaign_id = campaign.get("id")
    tenant_id   = campaign.get("tenant_id")
    if not campaign_id or not tenant_id:
        return True
    when = at or datetime.now(tz=timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{calendar_api_url}/v1/engine/is-open",
                params={
                    "tenant_id":   tenant_id,
                    "entity_type": "evaluation_campaign",
                    "entity_id":   campaign_id,
                    "at":          when.isoformat(),
                },
            )
            if resp.status_code != 200:
                logger.debug("is-open %s for campaign %s — open (best-effort)",
                             resp.status_code, campaign_id)
                return True
            data = resp.json()
            if int(data.get("calendars_count", 0)) == 0:
                return True  # nenhuma janela configurada → despacha
            return data.get("status") == "open"
    except Exception as exc:
        logger.warning("is-open call failed for campaign %s — open (best-effort): %s",
                       campaign_id, exc)
        return True


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
