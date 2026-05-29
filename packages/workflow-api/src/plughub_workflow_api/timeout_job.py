"""
timeout_job.py
Background task that scans for:
  1. Suspended WorkflowInstances whose resume_expires_at has passed → timed_out
  2. Collect instances whose expires_at has passed → timed_out + resume parent workflow

Design:
  - Runs every settings.timeout_scan_interval_s seconds (default 60)
  - Both UPDATEs are atomic (WHERE status='suspended' AND expires < now())
    so concurrent workers cannot double-process the same row
  - Publishes workflow.timed_out and collect.timed_out to Kafka
  - Non-fatal errors are logged — the job never stops the application

Timed-out workflow instances: the on_timeout path is triggered when the worker
receives workflow.timed_out and calls engine.run() with
resumeContext = { decision: "timeout", step_id, payload: {} }.

Timed-out collect instances: the collect.timed_out event + workflow.resumed
(decision="timeout") is published so the engine follows on_timeout.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .db import db_timeout_expired_collects, db_timeout_expired_instances, db_resume_instance
from .kafka_emitter import emit_collect_timed_out, emit_resumed, emit_timed_out

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

    # ── 1. Suspended workflow instances ──────────────────────────────────────
    timed_out = await db_timeout_expired_instances(pool)

    if timed_out:
        logger.info("Timeout scanner: %d workflow instance(s) timed out", len(timed_out))

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

    # ── 2. Collect instances ──────────────────────────────────────────────────
    timed_out_collects = await db_timeout_expired_collects(pool)

    if timed_out_collects:
        logger.info(
            "Timeout scanner: %d collect instance(s) timed out", len(timed_out_collects)
        )

    for collect in timed_out_collects:
        try:
            elapsed_ms = 0
            if collect.get("created_at"):
                created_dt = datetime.fromisoformat(collect["created_at"])
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                elapsed_ms = int(
                    (datetime.now(timezone.utc) - created_dt).total_seconds() * 1000
                )

            # Publish collect.timed_out
            await emit_collect_timed_out(
                producer, settings.collect_topic,
                tenant_id=collect["tenant_id"],
                instance_id=collect["instance_id"],
                collect_token=collect["collect_token"],
                channel=collect["channel"],
                elapsed_ms=elapsed_ms,
            )

            # Resume parent workflow instance via on_timeout path
            # (only if still suspended — another scanner may have already handled it)
            from .db import db_get_instance
            instance = await db_get_instance(pool, collect["instance_id"])
            if instance and instance["status"] == "suspended":
                wait_ms = 0
                if instance.get("suspended_at"):
                    suspended_dt = datetime.fromisoformat(instance["suspended_at"])
                    if suspended_dt.tzinfo is None:
                        suspended_dt = suspended_dt.replace(tzinfo=timezone.utc)
                    wait_ms = int(
                        (datetime.now(timezone.utc) - suspended_dt).total_seconds() * 1000
                    )

                updated = await db_resume_instance(
                    pool,
                    instance_id=instance["id"],
                    pipeline_state=instance["pipeline_state"],
                )
                if updated:
                    await emit_resumed(
                        producer, settings.kafka_topic,
                        tenant_id=instance["tenant_id"],
                        instance_id=instance["id"],
                        flow_id=instance["flow_id"],
                        decision="timeout",
                        resumed_from=instance.get("current_step") or "unknown",
                        next_step="__pending_engine__",
                        wait_duration_ms=wait_ms,
                    )
                    logger.info(
                        "Collect %s timed out — resumed instance %s with decision=timeout",
                        collect["collect_token"], instance["id"],
                    )
        except Exception as exc:
            logger.warning(
                "Failed to process timed-out collect %s: %s",
                collect.get("collect_token"), exc,
            )
