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

import asyncio
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from ..config import Settings
from ..identity import IdentityIndex, PendingEntry
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
        db_pool:  Any = None,
    ) -> None:
        self._producer = producer
        self._redis    = redis
        self._settings = settings

        # Identity Resolver (Fase A) — co-located module. Redis index (Slice 1) +
        # optional PG durability (Slice 2, reuses the gateway's asyncpg pool).
        # Flag-gated so the legacy pending_workflow path is unaffected when off.
        # Salt is a SECRET → env only (PLUGHUB_IDENTITY_SALT); TTLs are tuning.
        self._identity_enabled = os.getenv("PLUGHUB_IDENTITY_RESOLVER_ENABLED", "true").lower() in ("1", "true", "yes")
        salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
        self._identity = IdentityIndex(
            redis=redis,
            salt=salt,
            prospect_ttl_s=int(os.getenv("PLUGHUB_IDENTITY_PROSPECT_TTL_S", "2592000")),
            resolution_index_ttl_s=int(os.getenv("PLUGHUB_IDENTITY_INDEX_TTL_S", "2592000")),
            db_pool=db_pool,
        )

    async def ensure_identity_schema(self) -> None:
        """Create the PG `identity` schema/tables (idempotent). Called at startup."""
        if self._identity_enabled:
            await self._identity.ensure_schema()

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

        now_str = datetime.now(timezone.utc).isoformat()
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
            "timestamp":         now_str,
            # Arc 19: required by ConversationInboundEvent schema in routing-engine.
            # For webhook sessions there is no prior wait time — started_at == trigger time.
            "started_at":        now_str,
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
        # Fase E.3: garante uma fonte de resume (resumed_by). Quem entra aqui sem
        # source é o resume externo (curl/operador/API); o tool workflow_resume marca
        # "agent" e o timeout scanner marca "timeout_scanner".
        payload = dict(payload or {})
        payload.setdefault("source", "external")

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

        # Resolve the session's REAL channel + pool — a resume must NOT redefine them.
        # A webchat session being resumed (e.g. Session A-new's delegate step) must
        # stay "webchat"; only genuine webhook workflows stay "webhook". Likewise the
        # pool_id must be preserved: parse_inbound writes pool_id from this event, so
        # omitting it makes the ReplacingMergeTree overwrite the sessions row's pool
        # with '' on every resume.
        resume_channel = "webhook"
        resume_pool    = ""
        try:
            raw_meta = await self._redis.get(f"session:{session_id}:meta")
            if raw_meta:
                _meta_r       = json.loads(raw_meta)
                resume_channel = _meta_r.get("channel", "webhook") or "webhook"
                resume_pool    = _meta_r.get("pool_id", "") or ""
        except Exception:
            pass

        # Publish session_resumed to the canonical stream via conversations.inbound
        # The Core / orchestrator-bridge will handle re-allocation.
        event = {
            "event_id":     str(uuid.uuid4()),
            "session_id":   session_id,
            "tenant_id":    tenant_id,
            "channel":      resume_channel,
            "pool_id":      resume_pool,
            "event_type":   "session_resumed",
            "resume_token": resume_token,
            "step_id":      step_id,
            "payload":      payload or {},
            "timestamp":    now_iso,
            # Arc 19: required by ConversationInboundEvent schema in routing-engine.
            # On resume, elapsed_ms could reflect the suspend duration, but the
            # routing engine does not use it for webhook re-allocation. Use now_iso.
            "started_at":   now_iso,
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
    # Delegate — create a child session in a normal (non-webhook) pool
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_delegate(
        self,
        tenant_id:         str,
        pool_id:           str,
        customer_id:       str,
        origin_session_id: str,
        resume_token:      str,
        context:           dict[str, str],
        timeout_hours:     float,
    ) -> str:
        """
        Create a child session in a specific (non-webhook) pool for delegate I/O.

        Called by the skill-flow-service persistDelegate callback when a workflow
        delegate step executes for the first time.

        Responsibilities:
          1. Generate child_session_id
          2. Write workflow_resume_token + context entries to child ContextStore
          3. Publish conversations.inbound with pool_id set directly
             (routing engine uses pool_id as direct assignment)
          4. Return child_session_id

        The context keys are written as "session.{key}" in ContextStore so the
        child session's agent can read them via @ctx.session.{key}.
        workflow_resume_token is always written as session.workflow_resume_token.
        origin_session_id is always written as session.origin_session_id.

        Args:
            tenant_id:         tenant identifier
            pool_id:           target pool for the child session (direct assignment)
            customer_id:       customer identifier (same as parent sessions)
            origin_session_id: root session (Session A) — star topology
            resume_token:      token for the agent to resume the parent workflow
            context:           key→value pairs to seed in child ContextStore
                               (keys WITHOUT "session." prefix — added automatically)
            timeout_hours:     child session TTL hint (+1h buffer added)
        Returns the new child_session_id.
        """
        child_session_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # ── Seed ContextStore before publishing to Kafka ─────────────────────
        ctx_key = f"{tenant_id}:ctx:{child_session_id}"
        ctx_writes: dict[str, str] = {}

        # Always write workflow_resume_token so the agent can call workflow_resume
        ctx_writes["session.workflow_resume_token"] = json.dumps({
            "value":      resume_token,
            "confidence": 1.0,
            "source":     "delegate_step",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

        # Always write origin_session_id (star topology root)
        ctx_writes["session.origin_session_id"] = json.dumps({
            "value":      origin_session_id,
            "confidence": 1.0,
            "source":     "delegate_step",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

        # Write caller-provided context entries with session. prefix
        for key, value in context.items():
            # Avoid double-prefix if caller already used "session." prefix
            store_key = key if key.startswith("session.") else f"session.{key}"
            ctx_writes[store_key] = json.dumps({
                "value":      str(value),
                "confidence": 1.0,
                "source":     "delegate_step",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        ttl_s = int(timeout_hours * 3600) + 3600  # +1h buffer
        if ctx_writes:
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            await self._redis.expire(ctx_key, ttl_s)

        # ── Publish conversations.inbound with direct pool_id ─────────────────
        # pool_id set directly → routing engine assigns to this pool without
        # skill_id resolution. channel=webchat so pool agents can handle it.
        event = {
            "event_id":          str(uuid.uuid4()),
            "session_id":        child_session_id,
            "tenant_id":         tenant_id,
            "channel":           "webchat",     # delegate sessions use webchat
            "pool_id":           pool_id,        # direct pool assignment
            "skill_id":          None,           # not a webhook pool
            "customer_id":       customer_id,
            "trigger_type":      "delegate",
            "metadata":          {},
            "origin_session_id": origin_session_id,
            "timestamp":         now_iso,
            "started_at":        now_iso,
        }
        await self._publish(event, topic="conversations.inbound")

        logger.info(
            "webhook delegate: child_session=%s pool=%s origin=%s customer=%s tenant=%s ctx_tags=%d",
            child_session_id, pool_id, origin_session_id, customer_id, tenant_id, len(ctx_writes),
        )

        # ── Pending workflow lookup key (customer reconnect detection) ─────────
        # When the customer reconnects and their intake agent collects
        # contact_identifier, the agent calls pending_workflow_get which reads
        # this key to find the active resume_token without scanning the full
        # ContextStore.  Key deleted by get_pending_workflow when token is consumed.
        contact_id = context.get("contact_identifier") or context.get("session.contact_identifier")
        if contact_id:
            pending_key   = f"{tenant_id}:pending_workflow:{contact_id}"
            pending_value = json.dumps({
                "resume_token":     resume_token,
                "child_session_id": child_session_id,
                "pool":             pool_id,          # ← pool to delegate to on reconnect
                "context":          dict(context),
            })
            try:
                await self._redis.set(pending_key, pending_value, ex=ttl_s)
                logger.debug(
                    "webhook delegate: pending_workflow key written contact=%s session=%s",
                    contact_id, child_session_id,
                )
            except Exception as _e:
                logger.warning("webhook delegate: could not write pending_workflow key: %s", _e)

        # ── Identity Resolver dual-write (Fase A · Slice 1) ───────────────────
        # Generalize the pending lookup: resolve/provision a native customer_id
        # from the context anchors and register the pending under it, so a
        # reconnect from ANOTHER channel (different handle resolving to the same
        # customer) finds it. Additive to the legacy contact_id key above;
        # flag-gated and best-effort (never breaks the delegate path).
        if self._identity_enabled:
            try:
                anchors = self._anchors_from_context(context)
                if anchors:
                    ref = await self._identity.resolve_or_provision(tenant_id, anchors, provision=True)
                    if ref.customer_id:
                        await self._identity.write_pending(
                            tenant_id, ref.customer_id,
                            PendingEntry(
                                session_id=origin_session_id,
                                customer_id=ref.customer_id,
                                resume_token=resume_token,
                                pool=pool_id,
                                skill_id=context.get("skill_id"),
                                intent=context.get("intent"),
                            ),
                            ttl_s=ttl_s,
                        )
                        # Concrete trigger (§5): a registered pending must survive the
                        # ephemeral window → promote the customer to the durable PG store.
                        await self._identity.promote_to_durable(tenant_id, ref.customer_id, anchors)
                        logger.info(
                            "identity: pending_by_customer written customer=%s session=%s matched_by=%s",
                            ref.customer_id, origin_session_id, ref.matched_by,
                        )
            except Exception as _e:
                logger.warning("identity: dual-write failed (non-fatal): %s", _e)

        return child_session_id

    @staticmethod
    def _anchors_from_context(context: dict[str, Any]) -> list[dict[str, str]]:
        """
        Extract identity anchors from a delegate context. Explicit typed hints
        (phone/email/cpf/princ) win; otherwise a bare contact_identifier is
        treated as a phone (the common intake case).
        """
        anchors: list[dict[str, str]] = []
        for kind in ("phone", "email", "cpf", "princ"):
            val = context.get(kind) or context.get(f"session.{kind}")
            if val:
                anchors.append({"kind": kind, "value": str(val)})
        if not anchors:
            ci = context.get("contact_identifier") or context.get("session.contact_identifier")
            if ci:
                anchors.append({"kind": "phone", "value": str(ci)})
        return anchors

    # ──────────────────────────────────────────────────────────────────────────
    # Identity Resolver — public methods for HTTP endpoints (Fase A · Slice 1)
    # ──────────────────────────────────────────────────────────────────────────

    async def resolve_customer(
        self, tenant_id: str, anchors: list[dict[str, str]], provision: bool = True,
    ) -> dict:
        """Lookup 1 — resolve/provision a customer_id from anchors."""
        ref = await self._identity.resolve_or_provision(tenant_id, anchors, provision=provision)
        return {
            "customer_id": ref.customer_id,
            "status":      ref.status,
            "matched_by":  ref.matched_by,
            "confidence":  ref.confidence,
        }

    async def find_pending_by_customer(self, tenant_id: str, customer_id: str) -> dict:
        """Lookup 2 — pending workflows for a resolved customer_id."""
        pendings = await self._identity.find_pending(tenant_id, customer_id)
        return {
            "found": len(pendings) > 0,
            "count": len(pendings),
            "pendings": [
                {
                    "session_id":      p.session_id,
                    "resume_token":    p.resume_token,
                    "pool":            p.pool,
                    "skill_id":        p.skill_id,
                    "intent":          p.intent,
                    "suspended_at":    p.suspended_at,
                    "context_preview": p.context_preview,
                }
                for p in pendings
            ],
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Pending workflow lookup (customer reconnect)
    # ──────────────────────────────────────────────────────────────────────────

    async def get_pending_workflow(
        self,
        tenant_id:          str,
        contact_identifier: str,
    ) -> dict | None:
        """
        Look up whether a customer has an active pending workflow awaiting
        their confirmation.

        Returns a dict with resume_token, child_session_id, and context when
        a valid (unconsumed) pending workflow is found, or None otherwise.

        Validation: verifies that the resume_token still exists in the
        {tenant_id}:resume_tokens hash.  If the token was already consumed
        (workflow resumed by other means), cleans up the stale lookup key and
        returns None.
        """
        pending_key = f"{tenant_id}:pending_workflow:{contact_identifier}"
        raw = await self._redis.get(pending_key)
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except Exception:
            await self._redis.delete(pending_key)
            return None

        resume_token = data.get("resume_token", "")
        if not resume_token:
            await self._redis.delete(pending_key)
            return None

        # Verify token is still in resume_tokens (not yet consumed)
        hash_key    = f"{tenant_id}:resume_tokens"
        token_entry = await self._redis.hget(hash_key, resume_token)
        if not token_entry:
            # Token was consumed — remove stale lookup key
            await self._redis.delete(pending_key)
            return None

        return {
            "resume_token":     resume_token,
            "child_session_id": data.get("child_session_id", ""),
            "pool":             data.get("pool", ""),
            "context":          data.get("context", {}),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Delegate-as-conference — specialist in an existing (agent) session
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_delegate_conference(
        self,
        tenant_id:     str,
        pool_id:       str,
        session_id:    str,    # PARENT session — customer is connected here
        customer_id:   str,
        resume_token:  str,    # delegate step resume token (for parent session)
        step_id:       str = "",  # parent's delegate step id (for the resume_token value)
        context:       dict[str, str] = {},
        timeout_hours: float = 1.0,
    ) -> str:
        """
        Create a conference specialist in an existing agent (webchat) session.

        Unlike handle_delegate (which creates an independent session), this
        routes an agent from pool_id INTO the existing parent session as a
        conference specialist. All messages from the specialist go directly
        to the parent session's stream — the customer stays on the same
        WebSocket connection.

        Used when the delegate step fires in a non-webhook (agent) session,
        e.g. the intake reconnect session (Session A-new). The specialist
        (agente_confirmacao) runs inline in Session A-new and sends its
        notify/menu messages there.

        Returns the parent session_id (conference runs inside it).
        """
        now_iso       = datetime.now(timezone.utc).isoformat()
        conference_id = str(uuid.uuid4())   # unique specialist invitation

        # ── Write context to parent ContextStore ─────────────────────────────
        # Specialist reads workflow_resume_token + context via @ctx.session.*
        ctx_key    = f"{tenant_id}:ctx:{session_id}"
        ctx_writes: dict[str, str] = {}

        # The delegate resume_token for the PARENT delegate step goes to
        # resume_tokens so the channel-gateway can resume Session A-new's
        # delegate step when the specialist finishes. Written with TTL matching
        # the timeout_hours budget.
        ttl_s   = int(timeout_hours * 3600) + 3600
        # expires_at é o DEADLINE real (now + timeout_hours), não a hora de criação.
        # O timeout scanner lê este campo; gravar now() fazia o token nascer "vencido"
        # e o scanner disparava o timeout no primeiro ciclo (~60s) em vez de honrar o
        # timeout_hours configurado.
        exp_at  = (datetime.now(timezone.utc) + timedelta(hours=timeout_hours)).isoformat()
        # The resume_token must carry the PARENT's REAL delegate step_id so that
        # handle_resume → engine resumeContext.step_id matches the suspended step.
        # (Using a literal "delegate_conference" here broke the resume — the engine
        # could not match the step and the parent never finalized.)
        try:
            hash_key    = f"{tenant_id}:resume_tokens"
            token_value = f"{session_id}:{step_id or 'delegate_conference'}:{exp_at}"
            await self._redis.hset(hash_key, resume_token, token_value)
            await self._redis.expire(hash_key, ttl_s)
        except Exception as _e:
            logger.warning("delegate_conference: could not write resume_token: %s", _e)

        # Write context entries so specialist reads @ctx.session.* correctly
        for key, value in context.items():
            store_key = key if key.startswith("session.") else f"session.{key}"
            ctx_writes[store_key] = json.dumps({
                "value":      str(value),
                "confidence": 1.0,
                "source":     "delegate_conference",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })

        # Write Session A-new's delegate resume_token so the specialist can
        # call workflow_resume a second time to close Session A-new properly.
        ctx_writes["session.delegate_resume_token"] = json.dumps({
            "value":      resume_token,
            "confidence": 1.0,
            "source":     "delegate_conference",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

        if ctx_writes:
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            await self._redis.expire(ctx_key, ttl_s)

        # ── Resolve the PARENT's real channel ─────────────────────────────────
        # A specialist invite must NOT redefine the parent's channel. A webhook
        # workflow (Session B) stays "webhook"; a webchat reconnect (Session A-new)
        # stays "webchat". Read it from the parent session meta (fallback webchat).
        parent_channel = "webchat"
        try:
            raw_meta = await self._redis.get(f"session:{session_id}:meta")
            if raw_meta:
                parent_channel = json.loads(raw_meta).get("channel", "webchat") or "webchat"
        except Exception:
            pass

        # ── Publish conversations.inbound as conference specialist ────────────
        # conference_id signals to routing engine + bridge that this is a
        # specialist joining an existing session (not a new contact).
        # Messages from the specialist go to session:{session_id}:stream.
        event = {
            "event_id":    str(uuid.uuid4()),
            "session_id":  session_id,       # PARENT — conference in this session
            "tenant_id":   tenant_id,
            "channel":     parent_channel,   # ← preserve parent channel (never flip)
            "pool_id":     pool_id,
            "conference_id": conference_id,  # ← specialist routing
            "customer_id": customer_id,
            "trigger_type": "delegate",
            "metadata":    {},
            "timestamp":   now_iso,
            "started_at":  now_iso,
        }
        await self._publish(event, topic="conversations.inbound")

        # ── Pending workflow lookup key (customer reconnect detection) ─────────
        # When the delegating caller is a workflow that captured a contact_identifier
        # (Session B → inbound_only confirmation), the customer is NOT connected here.
        # Write the pending_workflow key so the customer's later reconnect (Session
        # A-new intake) finds the parent resume_token via pending_workflow_get.
        # Only written when contact_identifier is present (absent for the A-new→C
        # reconnect delegate, where the customer is already connected).
        contact_id = context.get("contact_identifier") or context.get("session.contact_identifier")
        if contact_id:
            pending_key   = f"{tenant_id}:pending_workflow:{contact_id}"
            pending_value = json.dumps({
                "resume_token":     resume_token,
                "child_session_id": session_id,   # parent session hosts the specialist
                "pool":             pool_id,
                "context":          dict(context),
            })
            try:
                await self._redis.set(pending_key, pending_value, ex=ttl_s)
                logger.debug(
                    "delegate_conference: pending_workflow key written contact=%s parent=%s",
                    contact_id, session_id,
                )
            except Exception as _e:
                logger.warning("delegate_conference: could not write pending_workflow key: %s", _e)

        logger.info(
            "delegate_conference: specialist=%s pool=%s parent=%s channel=%s tenant=%s",
            conference_id, pool_id, session_id, parent_channel, tenant_id,
        )
        return session_id   # specialist runs inside the parent session

    # ──────────────────────────────────────────────────────────────────────────
    # Timeout scanner (Arc 19 Fase D) — expira suspends/delegates vencidos
    # ──────────────────────────────────────────────────────────────────────────

    async def run_timeout_scanner(self, interval_s: int = 60) -> None:
        """
        Background task: expira tokens de resume vencidos.

        Varre periodicamente os hashes {tenant}:resume_tokens. Para cada token
        cujo expires_at (3º campo do valor) já passou, dispara handle_resume com
        decision="timeout" — o engine roteia para o on_timeout do step suspenso
        (suspend da operadora OU delegate de confirmação). Sem isso, uma sessão
        webhook suspensa cujo sinal externo nunca chega (operadora não aprova,
        cliente não reconecta) ficaria suspensa para sempre.

        handle_resume consome o token (HDEL), então cada expiração dispara uma vez.
        """
        logger.info("webhook timeout scanner started (interval=%ds)", interval_s)
        while True:
            try:
                await asyncio.sleep(interval_s)
                await self._scan_expired_resume_tokens()
            except asyncio.CancelledError:
                logger.info("webhook timeout scanner stopped")
                raise
            except Exception as exc:
                logger.warning("webhook timeout scanner iteration error: %s", exc)

    async def _scan_expired_resume_tokens(self) -> None:
        now = datetime.now(timezone.utc)
        async for raw_key in self._redis.scan_iter(match="*:resume_tokens", count=100):
            key       = raw_key if isinstance(raw_key, str) else raw_key.decode()
            tenant_id = key.rsplit(":resume_tokens", 1)[0]
            try:
                entries = await self._redis.hgetall(key)
            except Exception:
                continue
            for raw_token, raw_value in entries.items():
                token = raw_token if isinstance(raw_token, str) else raw_token.decode()
                value = raw_value if isinstance(raw_value, str) else raw_value.decode()
                # value: {session_id}:{step_id}:{expires_at_iso}  (split com maxsplit=2
                # preserva os ':' do timestamp ISO no terceiro campo)
                parts = value.split(":", 2)
                if len(parts) < 3:
                    continue
                try:
                    expires_at = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
                except Exception:
                    continue
                if now <= expires_at:
                    continue
                logger.info(
                    "webhook timeout scanner: expiring token session=%s step=%s tenant=%s deadline=%s",
                    parts[0], parts[1], tenant_id, parts[2],
                )
                try:
                    await self.handle_resume(
                        resume_token=token,
                        tenant_id=tenant_id,
                        payload={"decision": "timeout", "source": "timeout_scanner"},
                    )
                except Exception as exc:
                    logger.warning(
                        "webhook timeout scanner: handle_resume failed token=%s: %s", token, exc,
                    )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _publish(self, event: dict, topic: str) -> None:
        """Publish a JSON event to a Kafka topic."""
        await self._producer.send_and_wait(
            topic,
            value=json.dumps(event).encode(),
        )
