"""
session_metrics_extractor.py
Arc 13 Fase B — SessionMetricsExtractor

Computes session_metric.* values for an EvaluationInstance after session_closed.
Sources: PostgreSQL stream (transcript_messages), ClickHouse analytics tables.
Output: dict matching session_metric.{metric_name} keys — stored in
        evaluation.instances.session_metrics JSONB.

Metrics computed (reference: arc13-review-contestation.md):

Time metrics:
  first_response_time_s     — time between first customer message and first agent reply
  avg_response_time_s       — mean time between customer msg and subsequent agent reply
  max_response_time_s       — maximum single response latency
  total_session_duration_s  — total session open duration
  customer_wait_time_s      — sum of all customer wait intervals

Message composition:
  total_messages            — all visibility=all messages
  agent_messages            — messages from agent
  customer_messages         — messages from customer
  agent_message_pct         — agent share
  customer_message_pct      — customer share
  avg_agent_message_length  — average agent message char count
  turns_to_resolution       — number of full turn pairs

Result / escalation:
  escalated                 — bool — transferred to human
  escalation_reason         — handoff_reason or null

LLM cost:
  llm_calls_total           — total LLM calls from usage.events
  tokens_input_total        — total input tokens
  tokens_output_total       — total output tokens

Design notes:
- Reads from PostgreSQL (same `plughub` DB): `session_stream_events` (canonical durable
  stream, JSONB author/payload/visibility) + `usage_events`. Contact-scope (R1); segment-scope = R4.
- All metrics are best-effort: missing data → metric is omitted (not 0).
- Never writes directly to the canonical stream or Redis.
- Called from /v1/evaluation/ingest after create_result.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.evaluation.session_metrics")


class SessionMetricsExtractor:
    """
    Computes session_metric.* values for one session.
    Dependencies injected: asyncpg pool (PostgreSQL), optional ClickHouse client.
    """

    def __init__(
        self,
        pg_pool: asyncpg.Pool,
        clickhouse_dsn: str | None = None,
    ) -> None:
        self._pg = pg_pool
        self._ch_dsn = clickhouse_dsn

    async def extract(
        self,
        *,
        session_id: str,
        tenant_id: str,
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Compute and return session_metric.* dict for a session.
        Returns empty dict on total failure (individual metric failures are non-fatal).
        """
        metrics: dict[str, Any] = {}

        try:
            await self._compute_time_metrics(session_id, tenant_id, metrics)
        except Exception as exc:
            logger.warning("time metrics failed for session=%s: %s", session_id, exc)

        try:
            await self._compute_message_metrics(session_id, tenant_id, metrics)
        except Exception as exc:
            logger.warning("message metrics failed for session=%s: %s", session_id, exc)

        try:
            await self._compute_outcome_metrics(session_id, tenant_id, metrics)
        except Exception as exc:
            logger.warning("outcome metrics failed for session=%s: %s", session_id, exc)

        try:
            await self._compute_llm_metrics(session_id, tenant_id, metrics)
        except Exception as exc:
            logger.warning("llm metrics failed for session=%s: %s", session_id, exc)

        logger.info(
            "session_metrics extracted: session=%s metrics_count=%d",
            session_id, len(metrics),
        )
        return metrics

    # ─── Time metrics ────────────────────────────────────────────────────────────

    async def _compute_time_metrics(
        self,
        session_id: str,
        tenant_id: str,
        out: dict[str, Any],
    ) -> None:
        """
        Reads timeline from analytics.session_timeline (ClickHouse) or
        falls back to PostgreSQL stream-persisted data.
        """
        async with self._pg.acquire() as conn:
            # Session open/close times from the canonical durable stream, with a
            # fallback to the first/last event timestamp when session_opened/closed
            # are not persisted in the stream (observed in the demo).
            row = await conn.fetchrow(
                """
                SELECT
                    MIN("timestamp") FILTER (WHERE event_type = 'session_opened') AS opened_at,
                    MAX("timestamp") FILTER (WHERE event_type = 'session_closed') AS closed_at,
                    MIN("timestamp") AS first_evt,
                    MAX("timestamp") AS last_evt
                FROM session_stream_events
                WHERE session_id = $1 AND tenant_id = $2
                """,
                session_id, tenant_id,
            )

            if row:
                opened = row["opened_at"] or row["first_evt"]
                closed = row["closed_at"] or row["last_evt"]
                if opened and closed and closed > opened:
                    out["total_session_duration_s"] = round((closed - opened).total_seconds(), 2)

            # First response time: first customer msg → first agent msg.
            # Canonical stream: event_type='message', role in author->>'role'.
            timing_row = await conn.fetchrow(
                """
                WITH customer_first AS (
                    SELECT MIN("timestamp") AS t
                    FROM session_stream_events
                    WHERE session_id = $1 AND tenant_id = $2
                      AND author->>'role' IS NULL AND event_type = 'message'
                ),
                agent_first AS (
                    SELECT MIN("timestamp") AS t
                    FROM session_stream_events
                    WHERE session_id = $1 AND tenant_id = $2
                      AND author->>'role' IN ('primary', 'specialist')
                      AND event_type = 'message'
                )
                SELECT
                    EXTRACT(EPOCH FROM (agent_first.t - customer_first.t)) AS first_response_s
                FROM customer_first, agent_first
                WHERE customer_first.t IS NOT NULL AND agent_first.t IS NOT NULL
                  AND agent_first.t > customer_first.t
                """,
                session_id, tenant_id,
            )
            if timing_row and timing_row["first_response_s"] is not None:
                out["first_response_time_s"] = round(float(timing_row["first_response_s"]), 2)

    # ─── Message composition metrics ─────────────────────────────────────────────

    async def _compute_message_metrics(
        self,
        session_id: str,
        tenant_id: str,
        out: dict[str, Any],
    ) -> None:
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    author->>'role'                  AS author_role,
                    COUNT(*)                         AS msg_count,
                    AVG(LENGTH(payload->>'content')) AS avg_len
                FROM session_stream_events
                WHERE session_id = $1 AND tenant_id = $2
                  AND event_type = 'message'
                  AND visibility = '"all"'::jsonb
                GROUP BY author->>'role'
                """,
                session_id, tenant_id,
            )

            total = 0
            agent_count = 0
            customer_count = 0
            agent_len_weighted = 0.0   # Σ(avg_len × count) p/ média ponderada entre roles de agente

            for r in rows:
                role = r["author_role"]
                count = int(r["msg_count"])
                total += count
                if role in ("primary", "specialist"):
                    agent_count += count
                    if r["avg_len"] is not None:
                        agent_len_weighted += float(r["avg_len"]) * count
                # Cliente não é participante nomeado → author/role nulo no stream.
                elif role is None or role in ("customer", "caller", "client"):
                    customer_count += count

            if total > 0:
                out["total_messages"] = total
                out["agent_messages"] = agent_count
                out["customer_messages"] = customer_count
                out["agent_message_pct"] = round(agent_count / total * 100, 1)
                out["customer_message_pct"] = round(customer_count / total * 100, 1)
                if agent_count > 0 and agent_len_weighted > 0:
                    out["avg_agent_message_length"] = round(agent_len_weighted / agent_count, 1)

                # turns = min(agent_msgs, customer_msgs) — a full turn requires both sides
                out["turns_to_resolution"] = min(agent_count, customer_count)

    # ─── Outcome / escalation metrics ────────────────────────────────────────────

    async def _compute_outcome_metrics(
        self,
        session_id: str,
        tenant_id: str,
        out: dict[str, Any],
    ) -> None:
        async with self._pg.acquire() as conn:
            # Contact-level outcome/close_reason from the session_closed payload.
            # (agent_done is NOT a canonical stream event — segment-level outcome
            #  comes from analytics.segments; deferred to R4 segment-scope.)
            row = await conn.fetchrow(
                """
                SELECT
                    payload ->> 'outcome'      AS outcome,
                    payload ->> 'close_reason' AS close_reason
                FROM session_stream_events
                WHERE session_id = $1 AND tenant_id = $2
                  AND event_type = 'session_closed'
                ORDER BY "timestamp" DESC
                LIMIT 1
                """,
                session_id, tenant_id,
            )
            if row:
                outcome = row["outcome"]
                close_reason = row["close_reason"]
                if outcome:
                    out["outcome"] = outcome
                if close_reason:
                    out["close_reason"] = close_reason
                # escalated: contact-level heuristic from close_reason/outcome
                escalated = (close_reason == "agent_transfer") or (
                    outcome is not None and "escal" in outcome.lower()
                )
                out["escalated"] = bool(escalated)
                if escalated and close_reason:
                    out["escalation_reason"] = close_reason

    # ─── LLM cost metrics ────────────────────────────────────────────────────────

    async def _compute_llm_metrics(
        self,
        session_id: str,
        tenant_id: str,
        out: dict[str, Any],
    ) -> None:
        """
        Reads from usage.events persisted data.
        Falls back to ClickHouse if available; otherwise uses PostgreSQL fallback table.
        """
        async with self._pg.acquire() as conn:
            # usage_events pode não existir (ex.: demo sem usage-aggregator) → guard
            # silencioso para não poluir o log (a métrica é simplesmente omitida).
            if await conn.fetchval("SELECT to_regclass('public.usage_events')") is None:
                return
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)               FILTER (WHERE dimension IN ('llm_tokens_input', 'llm_tokens_output')) AS llm_calls,
                    SUM(quantity::numeric) FILTER (WHERE dimension = 'llm_tokens_input')  AS input_tokens,
                    SUM(quantity::numeric) FILTER (WHERE dimension = 'llm_tokens_output') AS output_tokens
                FROM usage_events
                WHERE session_id = $1 AND tenant_id = $2
                """,
                session_id, tenant_id,
            )
            if row:
                if row["llm_calls"]:
                    out["llm_calls_total"] = int(row["llm_calls"])
                if row["input_tokens"]:
                    out["tokens_input_total"] = int(row["input_tokens"])
                if row["output_tokens"]:
                    out["tokens_output_total"] = int(row["output_tokens"])


def compute_auto_criterion_score(
    metric_value: float,
    *,
    threshold_pass: float | None,
    threshold_fail: float | None,
    comparison: str,  # "lt" | "gt" | "lte" | "gte"
) -> float:
    """
    Compute score (0.0–1.0) for an auto_computed criterion.
    Linear interpolation between threshold_fail and threshold_pass.

    comparison="lt": metric must be LESS THAN threshold_pass to score well
    comparison="gt": metric must be GREATER THAN threshold_pass to score well
    """
    if threshold_pass is None:
        return 1.0  # no threshold defined → always pass

    def passes(v: float, threshold: float) -> bool:
        if comparison == "lt":  return v < threshold
        if comparison == "lte": return v <= threshold
        if comparison == "gt":  return v > threshold
        if comparison == "gte": return v >= threshold
        return True

    if passes(metric_value, threshold_pass):
        return 1.0

    if threshold_fail is not None and not passes(metric_value, threshold_fail):
        return 0.0

    if threshold_fail is not None:
        # Linear interpolation between threshold_pass and threshold_fail
        span = abs(threshold_fail - threshold_pass)
        if span == 0:
            return 0.0
        dist = abs(metric_value - threshold_pass)
        return round(max(0.0, 1.0 - dist / span), 4)

    return 0.5  # between thresholds but no fail threshold defined → midpoint


def fill_auto_computed_criteria(
    criterion_responses: list[dict[str, Any]],
    form_dimensions: list[dict[str, Any]],
    session_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Fill in auto_computed criterion scores from session_metrics.
    Called before evaluation_submit on the ingest path.
    Returns the criterion_responses list with auto_computed entries filled in.
    """
    # Build lookup: criterion_id → criterion definition
    criteria_by_id: dict[str, dict[str, Any]] = {}
    for dim in form_dimensions:
        for crit in dim.get("criteria", []):
            criteria_by_id[crit["criterion_id"]] = crit

    existing_ids = {r["criterion_id"] for r in criterion_responses}

    for crit_id, crit in criteria_by_id.items():
        if crit.get("type") != "auto_computed":
            continue
        if crit_id in existing_ids:
            continue  # already filled

        source = crit.get("computation_source", "")
        if not source.startswith("session_metric."):
            continue

        metric_name = source[len("session_metric."):]
        metric_value = session_metrics.get(metric_name)
        if metric_value is None:
            continue  # metric not available

        score = compute_auto_criterion_score(
            float(metric_value),
            threshold_pass=crit.get("threshold_pass"),
            threshold_fail=crit.get("threshold_fail"),
            comparison=crit.get("comparison", "lt"),
        )

        criterion_responses.append({
            "criterion_id":  crit_id,
            "criterion_name": crit.get("label", crit_id),
            "dimension_id":  crit.get("dimension_id", ""),
            "na":            False,
            "score":         score * (crit.get("max_score", 10)),  # scale to max_score
            "max_score":     crit.get("max_score", 10),
            "notes":         f"Auto-computed from session_metric.{metric_name}={metric_value}",
            "weight":        crit.get("weight", 1.0),
            "evidence":      [],
            "auto_computed": True,
        })

    return criterion_responses
