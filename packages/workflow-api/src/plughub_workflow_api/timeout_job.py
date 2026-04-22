"""
timeout_job.py
Background task that scans for suspended WorkflowInstances whose
resume_expires_at has passed and transitions them to 'timed_out'.

Design:
  - Runs every settings.timeout_scan_interval_s seconds (default 60)
  - The UPDATE is atomic: WHERE status='suspended' AND expires < now()
    so concurrent workers cannot double-process the same row
  - Publishes workflow.timed_out to Kafka for each instance
  - Optionally queries calendar-api for next_open (informational, for notifications)
  - Non-fatal errors are logged — the job never stops the application

The timed-out instances are left in 'timed_out' status — the on_timeout
path in the Skill Flow is triggered when the worker receives the
workflow.timed_out Kafka event and calls engine.run() with
resumeContext = { decision: "timeout", step_id, payload: {} }.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .db import db_timeout_expired_instances
from .kafka_emitter import emit_timed_out

logger = logging.getLogger("plughub.workflow.timeout_job")


async def run_timeout_scanner(app: Any) -> None:
    """
    Infinite loop — runs as an asyncio background task.
    Cancellation is clean (CancelledError propagates normally).
    """
    settings = app.state.settings
    interval = settings.timeout_scan_interval_s

    logger.info("Timeout scanner started (interval=%ds)", interval)

    while True:
        await asyncio.sleep(interval)
        try:
            await _scan_once(app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Timeout scanner error: %s", exc)


async def _scan_once(app: Any) -> None:
    pool     = app.state.pool
    settings = app.state.settings
    producer = getattr(app.state, "producer", None)

    timed_out = await db_timeout_expired_instances(pool)

    if not timed_out:
        return

    logger.info("Timeout scanner: %d instance(s) timed out", len(timed_out))

    for instance in timed_out:
        try:
            await emit_timed_out(
                producer, settings.kafka_topic,
                tenant_id=instance["tenant_id"],
                instance_id=instance["id"],
                flow_id=instance["flow_id"],
                current_step=instance.get("current_step"),
                suspended_at=instance.get("suspended_at"),
                next_open=None,   # future: query calendar-api next_open_slot
            )
            logger.info(
                "Instance %s (flow=%s) timed out at step '%s'",
                instance["id"], instance["flow_id"], instance.get("current_step"),
            )
        except Exception as exc:
            logger.warning(
                "Failed to emit timed_out for instance %s: %s",
                instance["id"], exc,
            )
