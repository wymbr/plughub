"""
kafka_emitter.py
Publishes evaluation lifecycle events to Kafka topic evaluation.events.

Event types:
  evaluation.instance.created    — new EvaluationInstance scheduled
  evaluation.instance.assigned   — instance claimed by evaluator agent
  evaluation.instance.completed  — evaluation result submitted
  evaluation.instance.expired    — instance TTL exceeded without completion
  evaluation.contestation.opened — human agent filed contestation
  evaluation.contestation.closed — contestation adjudicated
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.evaluation.kafka")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def _publish(
    producer: Any,
    topic: str,
    event: dict[str, Any],
) -> None:
    """Fire-and-forget publish. Logs errors but never raises."""
    try:
        value = json.dumps(event).encode()
        await producer.send(topic, value=value)
    except Exception as exc:
        logger.warning("kafka publish failed: topic=%s event_type=%s err=%s",
                       topic, event.get("event_type"), exc)


async def emit_instance_created(
    producer: Any,
    topic: str,
    *,
    instance_id: str,
    tenant_id: str,
    session_id: str,
    campaign_id: str,
    form_id: str,
    priority: int,
    expires_at: str | None,
) -> None:
    await _publish(producer, topic, {
        "event_type":  "evaluation.instance.created",
        "event_id":    str(uuid.uuid4()),
        "timestamp":   _now_iso(),
        "instance_id": instance_id,
        "tenant_id":   tenant_id,
        "session_id":  session_id,
        "campaign_id": campaign_id,
        "form_id":     form_id,
        "priority":    priority,
        "expires_at":  expires_at,
    })


async def emit_instance_assigned(
    producer: Any,
    topic: str,
    *,
    instance_id: str,
    tenant_id: str,
    session_id: str,
    evaluator_agent_id: str | None,
) -> None:
    await _publish(producer, topic, {
        "event_type":          "evaluation.instance.assigned",
        "event_id":            str(uuid.uuid4()),
        "timestamp":           _now_iso(),
        "instance_id":         instance_id,
        "tenant_id":           tenant_id,
        "session_id":          session_id,
        "evaluator_agent_id":  evaluator_agent_id,
    })


async def emit_instance_completed(
    producer: Any,
    topic: str,
    *,
    instance_id: str,
    result_id: str,
    tenant_id: str,
    session_id: str,
    campaign_id: str,
    overall_score: float | None,
    passed: bool | None,
    eval_status: str,
) -> None:
    await _publish(producer, topic, {
        "event_type":    "evaluation.instance.completed",
        "event_id":      str(uuid.uuid4()),
        "timestamp":     _now_iso(),
        "instance_id":   instance_id,
        "result_id":     result_id,
        "tenant_id":     tenant_id,
        "session_id":    session_id,
        "campaign_id":   campaign_id,
        "overall_score": overall_score,
        "passed":        passed,
        "eval_status":   eval_status,
    })


async def emit_instance_expired(
    producer: Any,
    topic: str,
    *,
    instance_id: str,
    tenant_id: str,
    session_id: str,
    campaign_id: str,
) -> None:
    await _publish(producer, topic, {
        "event_type":  "evaluation.instance.expired",
        "event_id":    str(uuid.uuid4()),
        "timestamp":   _now_iso(),
        "instance_id": instance_id,
        "tenant_id":   tenant_id,
        "session_id":  session_id,
        "campaign_id": campaign_id,
    })


async def emit_contestation_opened(
    producer: Any,
    topic: str,
    *,
    contestation_id: str,
    result_id: str,
    instance_id: str,
    tenant_id: str,
    session_id: str,
    contested_by: str,
) -> None:
    await _publish(producer, topic, {
        "event_type":        "evaluation.contestation.opened",
        "event_id":          str(uuid.uuid4()),
        "timestamp":         _now_iso(),
        "contestation_id":   contestation_id,
        "result_id":         result_id,
        "instance_id":       instance_id,
        "tenant_id":         tenant_id,
        "session_id":        session_id,
        "contested_by":      contested_by,
    })


async def emit_contestation_closed(
    producer: Any,
    topic: str,
    *,
    contestation_id: str,
    result_id: str,
    tenant_id: str,
    adjudicated_status: str,
    adjudicated_by: str,
) -> None:
    await _publish(producer, topic, {
        "event_type":           "evaluation.contestation.closed",
        "event_id":             str(uuid.uuid4()),
        "timestamp":            _now_iso(),
        "contestation_id":      contestation_id,
        "result_id":            result_id,
        "tenant_id":            tenant_id,
        "adjudicated_status":   adjudicated_status,
        "adjudicated_by":       adjudicated_by,
    })


# ─── Arc 13 — calibration.events ─────────────────────────────────────────────

_CALIBRATION_TOPIC = "calibration.events"


async def emit_calibration_reviewed(
    producer: Any,
    *,
    review_id: str,
    campaign_id: str,
    evaluation_instance_id: str,
    tenant_id: str,
    evaluator_id: str,
    skill_version: str,
    decision: str,
    calibration_note_id: str | None = None,
) -> None:
    """Emit calibration_reviewed event to calibration.events topic."""
    await _publish(producer, _CALIBRATION_TOPIC, {
        "event_type":              "calibration_reviewed",
        "event_id":                str(uuid.uuid4()),
        "timestamp":               _now_iso(),
        "review_id":               review_id,
        "campaign_id":             campaign_id,
        "evaluation_instance_id":  evaluation_instance_id,
        "tenant_id":               tenant_id,
        "evaluator_id":            evaluator_id,
        "skill_version":           skill_version,
        "decision":                decision,
        "calibration_note_id":     calibration_note_id,
    })


async def emit_calibration_note_published(
    producer: Any,
    *,
    note_id: str,
    campaign_id: str,
    evaluator_id: str,
    severity: str,
    tenant_id: str,
) -> None:
    """Emit calibration_note_published event to evaluation.events topic."""
    topic = "evaluation.events"
    await _publish(producer, topic, {
        "event_type":   "calibration_note_published",
        "event_id":     str(uuid.uuid4()),
        "timestamp":    _now_iso(),
        "note_id":      note_id,
        "campaign_id":  campaign_id,
        "evaluator_id": evaluator_id,
        "severity":     severity,
        "tenant_id":    tenant_id,
    })


async def emit_evaluation_finalized(
    producer: Any,
    *,
    instance_id: str,
    session_id: str,
    campaign_id: str,
    tenant_id: str,
    final_score: float,
    final_scores_by_dimension: list[dict],
    contestation_state: str,
    process_duration_ms: int,
) -> None:
    """
    Emit evaluation_finalized event to evaluation.events topic.
    This is the canonical event for quality analytics (Arc 6 Fase 2).
    Only emitted when the full contestation cycle is complete.
    """
    topic = "evaluation.events"
    await _publish(producer, topic, {
        "event_type":                 "evaluation_finalized",
        "event_id":                   str(uuid.uuid4()),
        "timestamp":                  _now_iso(),
        "instance_id":                instance_id,
        "session_id":                 session_id,
        "campaign_id":                campaign_id,
        "tenant_id":                  tenant_id,
        "final_score":                final_score,
        "final_scores_by_dimension":  final_scores_by_dimension,
        "contestation_state":         contestation_state,
        "process_duration_ms":        process_duration_ms,
    })
