"""
adapters/webhook.py
Webhook channel adapter — Arc 19 Unified Session Model.

Architecture: docs/arcos/arc19-unified-session-model.md

This adapter treats workflow execution as a channel, exactly like WhatsApp or
voice.  Each skill registered in a webhook pool is an "endpoint" (analogous to
a DIN in voice or a WA number).  Triggering a workflow creates a normal PlugHub
session with channel_type="webhook".

Inbound / trigger flow:
  POST /v1/channels/webhook/{skill_id}
    { tenant_id, trigger_type, metadata?, customer_id? }
    → session created → conversations.inbound published → returns session_id

Resume flow (after a suspend step):
  POST /v1/channels/webhook/resume/{resume_token}
    → Redis hash lookup: {tenant}:resume_tokens → session_id
    → session_resumed event published → routing engine reallocates
    → returns session_id

Status query:
  GET /v1/channels/webhook/{session_id}/status
    → reads session status from Redis stream metadata
    → returns { session_id, status: "active"|"suspended"|"closed" }

Outbound (ChannelAdapter interface):
  Webhook workflows do not deliver messages to an external channel — they
  orchestrate agents that do.  Therefore deliver_text / deliver_menu /
  deliver_typing are no-ops.  deliver_session_closed is also a no-op because
  session closure is managed by the orchestrator-bridge directly.

Resume token storage (written by skill-flow-engine suspend executor):
  Redis hash: {tenant_id}:resume_tokens
    field: {resume_token}  (opaque 43-char)
    value: {session_id}:{step_id}:{expires_at_iso}
  TTL: same as the session (set by suspend executor via EXPIRE)
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from ..config import Settings
from .base import ChannelAdapter

logger = logging.getLogger("plughub.channel-gateway.webhook")

# Trigger types understood by the webhook adapter
TriggerType = Literal["api", "webhook", "task", "scheduled", "yaml_auto"]


class WebhookAdapter(ChannelAdapter):
    """
    Channel-level singleton adapter for the 'webhook' channel (Arc 19).

    Exposes HTTP endpoints for triggering, resuming, and querying the status
    of webhook-based workflow sessions.  The adapter itself is stateless —
    all session state lives in the Core Redis stream and the routing engine.

    Args:
        producer:  Kafka producer for publishing normalised inbound events.
        redis:     Async Redis client.
        settings:  Gateway settings (env vars).
    """

    channel = "webhook"

    def __init__(
        self,
        producer: AIOKafkaProducer,
        redis:    aioredis.Redis,
        settings: Settings,
    ) -> None:
        self._producer = producer
        self._redis    = redis
        self._settings = settings

    # ──────────────────────────────────────────────────────────────────────────
    # Trigger — create a new webhook session
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_trigger(
        self,
        skill_id:          str,
        tenant_id:         str,
        trigger_type:      TriggerType = "api",
        metadata:          dict[str, Any] | None = None,
        customer_id:       str | None = None,
        origin_session_id: str | None = None,
        context:           dict[str, Any] | None = None,
    ) -> str:
        """
        Create a new webhook session for the given skill_id.

        The skill_id acts as the "phone number" / "DNIS" for the webhook pool.
        The customer_id is the "ANI" — optional, defaults to a generated UUID
        when the trigger is not customer-initiated (e.g. scheduled, api).

        origin_session_id: Arc 19 — session that triggered this workflow
          (e.g. a webchat intake session). Written to ContextStore as
          session.origin_session_id so agents can trace the provenance.

        context: Arc 19 — seed ContextStore entries for the new session.
          Dict of {tag: value} pairs (string values). Written atomically
          before the routing engine allocates an instance, so the skill-flow
          can read them from step 1 via @ctx.* resolution.
          Example: {"session.numero_atual": "11999999999"}

        Returns the new session_id.
        """
        session_id  = str(uuid.uuid4())
        customer_id = customer_id or f"sys:{trigger_type}:{uuid.uuid4().hex[:8]}"

        event = {
            "event_id":          str(uuid.uuid4()),
            "session_id":        session_id,
            "tenant_id":         tenant_id,
            "channel":           "webhook",
            "pool_id":           None,        # routing engine resolves pool via skill_id
            "skill_id":          skill_id,    # DNIS for webhook channel — routing key
            "customer_id":       customer_id,
            "trigger_type":      trigger_type,
            "metadata":          metadata or {},
            "origin_session_id": origin_session_id,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }

        # ── Seed ContextStore before publishing to Kafka ─────────────────────
        # Writing context entries BEFORE routing ensures that when the routing
        # engine allocates a skill-flow instance and the first step runs, all
        # seeded tags are already available via @ctx.* resolution.
        #
        # context_entries format: {tag: value} — both strings.
        # origin_session_id is always written as session.origin_session_id
        # (confidence 1.0, visibility agents_only) when provided.
        ctx_key   = f"{tenant_id}:ctx:{session_id}"
        now_iso   = datetime.now(timezone.utc).isoformat()
        ctx_writes: dict[str, str] = {}

        if origin_session_id:
            ctx_writes["session.origin_session_id"] = json.dumps({
                "value":      origin_session_id,
                "confidence": 1.0,
                "source":     "webhook_trigger",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        for tag, value in (context or {}).items():
            ctx_writes[tag] = json.dumps({
                "value":      str(value),
                "confidence": 1.0,
                "source":     "webhook_trigger",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        if ctx_writes:
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            # TTL 24h — extended by skill-flow-engine suspend executor if needed
            await self._redis.expire(ctx_key, 86_400)

        await self._publish(event, topic="conversations.inbound")

        logger.info(
            "webhook trigger: session=%s skill=%s trigger_type=%s tenant=%s "
            "origin=%s ctx_tags=%d",
            session_id, skill_id, trigger_type, tenant_id,
            origin_session_id or "-", len(ctx_writes),
        )
        return session_id

    # ──────────────────────────────────────────────────────────────────────────
    # Resume — wake a suspended session
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_resume(
        self,
        resume_token: str,
        tenant_id:    str,
        payload:      dict[str, Any] | None = None,
    ) -> str | None:
        """
        Resolve a resume_token to a session_id and publish a session_resumed event.

        The resume_token was generated by the skill-flow-engine suspend executor
        and stored in Redis hash {tenant_id}:resume_tokens.

        Returns session_id on success, None if the token is unknown/expired.
        """
        hash_key    = f"{tenant_id}:resume_tokens"
        token_value = await self._redis.hget(hash_key, resume_token)

        if not token_value:
            logger.warning(
                "webhook resume: unknown or expired token=%s tenant=%s",
                resume_token, tenant_id,
            )
            return None

        # token_value format: "{session_id}:{step_id}:{expires_at_iso}"
        parts = token_value.split(":", 2)
        if len(parts) < 2:
            logger.error(
                "webhook resume: malformed token value=%r token=%s",
                token_value, resume_token,
            )
            return None

        session_id = parts[0]
        step_id    = parts[1]

        now_iso = datetime.now(timezone.utc).isoformat()

        # ── Arc 19 Fase B: write session_resumed to canonical stream ─────────
        # Published BEFORE the conversations.inbound event so that consumers
        # (analytics-api, Monitor) see the transition before re-allocation fires.
        try:
            await self._redis.xadd(
                f"session:{session_id}:stream",
                {
                    "event_id":    str(uuid.uuid4()),
                    "type":        "session_resumed",
                    "timestamp":   now_iso,
                    "author_id":   "webhook_adapter",
                    "author_role": "system",
                    "visibility":  json.dumps("agents_only"),
                    "segment_id":  "",
                    "payload":     json.dumps({
                        "step_id":      step_id,
                        "resume_token": resume_token,
                        "payload":      payload or {},
                    }),
                },
                maxlen=500,
            )
            # Restore status key to "active" so get_status() reflects the transition.
            # keepttl=True preserves the existing TTL (set by persistSuspendWebhook in Fase C).
            await self._redis.set(
                f"{tenant_id}:session:{session_id}:status",
                "active",
                keepttl=True,
            )
        except Exception as _exc:
            # Non-fatal: stream write failure must not block the resume flow.
            logger.warning(
                "Could not write session_resumed to stream: session=%s — %s",
                session_id, _exc,
            )

        # Publish session_resumed to the canonical stream via conversations.inbound
        # The Core / orchestrator-bridge will handle re-allocation.
        event = {
            "event_id":     str(uuid.uuid4()),
            "session_id":   session_id,
            "tenant_id":    tenant_id,
            "channel":      "webhook",
            "event_type":   "session_resumed",
            "resume_token": resume_token,
            "step_id":      step_id,
            "payload":      payload or {},
            "timestamp":    now_iso,
        }

        await self._publish(event, topic="conversations.inbound")

        # Clean up the token after successful resume (one-shot)
        await self._redis.hdel(hash_key, resume_token)

        logger.info(
            "webhook resume: session=%s step=%s token=%s tenant=%s",
            session_id, step_id, resume_token, tenant_id,
        )
        return session_id

    # ──────────────────────────────────────────────────────────────────────────
    # Status query
    # ──────────────────────────────────────────────────────────────────────────

    async def get_status(
        self,
        session_id: str,
        tenant_id:  str,
    ) -> dict[str, str]:
        """
        Return the current status of a webhook session.

        Reads the session status from the Redis stream metadata key written by
        Core.  Falls back to "closed" when the key has expired (TTL elapsed).

        Returns { "session_id": ..., "status": "active"|"suspended"|"closed" }.
        """
        # Core writes session status at: {tenant_id}:session:{session_id}:status
        # (a simple Redis string, TTL same as the stream)
        status_key = f"{tenant_id}:session:{session_id}:status"
        status     = await self._redis.get(status_key)

        if status is None:
            # Key expired → session is closed (or never existed)
            status = "closed"

        return {"session_id": session_id, "status": status}

    # ──────────────────────────────────────────────────────────────────────────
    # ChannelAdapter interface — no-ops for webhook channel
    # ──────────────────────────────────────────────────────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Webhook workflows do not deliver messages to external channels.
        Outbound notifications are handled by agent-skill task steps.
        """

    async def deliver_menu(self, payload: dict) -> None:
        """
        Menu steps are forbidden in workflow profile (Arc 19 segregation).
        No-op in case an event leaks through.
        """

    async def deliver_typing(self, payload: dict) -> None:
        """Typing indicators have no meaning in the webhook channel."""

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Session closure for webhook sessions is managed by the
        orchestrator-bridge (via complete() step → agent_done → session_closed).
        No external party needs to be notified here.
        """

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _publish(self, event: dict, topic: str) -> None:
        """Publish a JSON event to a Kafka topic."""
        await self._producer.send_and_wait(
            topic,
            value=json.dumps(event).encode(),
        )
