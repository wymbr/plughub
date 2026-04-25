"""
kafka_emitter.py
Publishes workflow.* events to Kafka (topic: workflow.events)
and collect.* events (topic: collect.events).

All functions are fire-and-forget — a Kafka failure never blocks
the HTTP response. Errors are logged at WARNING level.

workflow.events:
  workflow.started    — instance created, execution began
  workflow.suspended  — flow hit a suspend step
  workflow.resumed    — external signal received, flow continuing
  workflow.completed  — flow reached a complete step
  workflow.timed_out  — suspend deadline expired, on_timeout path triggered
  workflow.failed     — unrecoverable error
  workflow.cancelled  — operator cancelled the instance

collect.events:
  collect.requested   — collect_instance created, waiting for send_at
  collect.sent        — outbound contact initiated via channel-gateway
  collect.responded   — target replied, workflow resumed
  collect.timed_out   — no response before expires_at, on_timeout triggered
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.workflow.kafka")

_EMIT_TIMEOUT_S = 5.0   # never block the HTTP handler for more than 5s


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(producer: Any | None, topic: str, event: dict) -> None:
    if producer is None:
        logger.debug("Kafka disabled — skipping event: %s", event.get("event_type"))
        return
    try:
        await asyncio.wait_for(
            producer.send_and_wait(topic, json.dumps(event).encode()),
            timeout=_EMIT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("Kafka emit timed out for %s (topic=%s)", event.get("event_type"), topic)
    except Exception as exc:
        logger.warning("Failed to emit %s: %s", event.get("event_type"), exc)


async def emit_started(
    producer:        Any | None,
    topic:           str,
    installation_id: str,
    organization_id: str,
    tenant_id:       str,
    instance_id:     str,
    flow_id:         str,
    session_id:      str | None,
    trigger_type:    str,
) -> None:
    await _emit(producer, topic, {
        "event_type":      "workflow.started",
        "timestamp":       _now(),
        "installation_id": installation_id,
        "organization_id": organization_id,
        "tenant_id":       tenant_id,
        "instance_id":     instance_id,
        "flow_id":         flow_id,
        "session_id":      session_id,
        "trigger_type":    trigger_type,
    })


async def emit_suspended(
    producer:          Any | None,
    topic:             str,
    tenant_id:         str,
    instance_id:       str,
    flow_id:           str,
    current_step:      str,
    suspend_reason:    str,
    resume_expires_at: str,
) -> None:
    # resume_token is intentionally NOT included in Kafka events
    # — it is delivered only via the notify mechanism or direct API response
    await _emit(producer, topic, {
        "event_type":        "workflow.suspended",
        "timestamp":         _now(),
        "tenant_id":         tenant_id,
        "instance_id":       instance_id,
        "flow_id":           flow_id,
        "current_step":      current_step,
        "suspend_reason":    suspend_reason,
        "resume_expires_at": resume_expires_at,
    })


async def emit_resumed(
    producer:         Any | None,
    topic:            str,
    tenant_id:        str,
    instance_id:      str,
    flow_id:          str,
    decision:         str,
    resumed_from:     str,
    next_step:        str,
    wait_duration_ms: int,
) -> None:
    await _emit(producer, topic, {
        "event_type":       "workflow.resumed",
        "timestamp":        _now(),
        "tenant_id":        tenant_id,
        "instance_id":      instance_id,
        "flow_id":          flow_id,
        "decision":         decision,
        "resumed_from":     resumed_from,
        "next_step":        next_step,
        "wait_duration_ms": wait_duration_ms,
    })


async def emit_completed(
    producer:     Any | None,
    topic:        str,
    tenant_id:    str,
    instance_id:  str,
    flow_id:      str,
    outcome:      str,
    duration_ms:  int,
) -> None:
    await _emit(producer, topic, {
        "event_type":   "workflow.completed",
        "timestamp":    _now(),
        "tenant_id":    tenant_id,
        "instance_id":  instance_id,
        "flow_id":      flow_id,
        "outcome":      outcome,
        "duration_ms":  duration_ms,
    })


async def emit_timed_out(
    producer:     Any | None,
    topic:        str,
    tenant_id:    str,
    instance_id:  str,
    flow_id:      str,
    current_step: str | None,
    suspended_at: str | None,
    next_open:    str | None,
) -> None:
    await _emit(producer, topic, {
        "event_type":   "workflow.timed_out",
        "timestamp":    _now(),
        "tenant_id":    tenant_id,
        "instance_id":  instance_id,
        "flow_id":      flow_id,
        "current_step": current_step,
        "suspended_at": suspended_at,
        "next_open":    next_open,
    })


async def emit_failed(
    producer:     Any | None,
    topic:        str,
    tenant_id:    str,
    instance_id:  str,
    flow_id:      str,
    current_step: str | None,
    error:        str,
) -> None:
    await _emit(producer, topic, {
        "event_type":   "workflow.failed",
        "timestamp":    _now(),
        "tenant_id":    tenant_id,
        "instance_id":  instance_id,
        "flow_id":      flow_id,
        "current_step": current_step,
        "error":        error,
    })


async def emit_cancelled(
    producer:      Any | None,
    topic:         str,
    tenant_id:     str,
    instance_id:   str,
    flow_id:       str,
    cancelled_by:  str,
    reason:        str | None,
) -> None:
    await _emit(producer, topic, {
        "event_type":   "workflow.cancelled",
        "timestamp":    _now(),
        "tenant_id":    tenant_id,
        "instance_id":  instance_id,
        "flow_id":      flow_id,
        "cancelled_by": cancelled_by,
        "reason":       reason,
    })


# ── Collect events (topic: collect.events) ────────────────────────────────────

async def emit_collect_requested(
    producer:      Any | None,
    topic:         str,
    tenant_id:     str,
    instance_id:   str,
    flow_id:       str,
    campaign_id:   str | None,
    step_id:       str,
    collect_token: str,
    target_type:   str,
    target_id:     str,
    channel:       str,
    interaction:   str,
    prompt:        str,
    options:       list,
    fields:        list,
    send_at:       str,
    expires_at:    str,
) -> None:
    event: dict = {
        "event_type":    "collect.requested",
        "timestamp":     _now(),
        "tenant_id":     tenant_id,
        "instance_id":   instance_id,
        "flow_id":       flow_id,
        "step_id":       step_id,
        "collect_token": collect_token,
        "target_type":   target_type,
        "target_id":     target_id,
        "channel":       channel,
        "interaction":   interaction,
        "prompt":        prompt,
        "send_at":       send_at,
        "expires_at":    expires_at,
    }
    if campaign_id:
        event["campaign_id"] = campaign_id
    if options:
        event["options"] = options
    if fields:
        event["fields"] = fields
    await _emit(producer, topic, event)


async def emit_collect_sent(
    producer:      Any | None,
    topic:         str,
    tenant_id:     str,
    instance_id:   str,
    collect_token: str,
    channel:       str,
    session_id:    str | None = None,
) -> None:
    event: dict = {
        "event_type":    "collect.sent",
        "timestamp":     _now(),
        "tenant_id":     tenant_id,
        "instance_id":   instance_id,
        "collect_token": collect_token,
        "channel":       channel,
    }
    if session_id:
        event["session_id"] = session_id
    await _emit(producer, topic, event)


async def emit_collect_responded(
    producer:      Any | None,
    topic:         str,
    tenant_id:     str,
    instance_id:   str,
    collect_token: str,
    channel:       str,
    response_data: dict,
    elapsed_ms:    int,
) -> None:
    await _emit(producer, topic, {
        "event_type":    "collect.responded",
        "timestamp":     _now(),
        "tenant_id":     tenant_id,
        "instance_id":   instance_id,
        "collect_token": collect_token,
        "channel":       channel,
        "response_data": response_data,
        "elapsed_ms":    elapsed_ms,
    })


async def emit_collect_timed_out(
    producer:      Any | None,
    topic:         str,
    tenant_id:     str,
    instance_id:   str,
    collect_token: str,
    channel:       str,
    elapsed_ms:    int,
) -> None:
    await _emit(producer, topic, {
        "event_type":    "collect.timed_out",
        "timestamp":     _now(),
        "tenant_id":     tenant_id,
        "instance_id":   instance_id,
        "collect_token": collect_token,
        "channel":       channel,
        "elapsed_ms":    elapsed_ms,
    })
