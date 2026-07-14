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
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt as pyjwt          # Journey J4c — mint the webchat JWT that pre-binds the survey session
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from ..config import Settings
from ..identity import IdentityIndex, OtpService, PendingEntry
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

        # OTP de posse de canal (Fase 2) — step-up componível, acionado pelo fluxo.
        # Entrega mockada no demo: PLUGHUB_OTP_DEV_RETURN_CODE=true loga+retorna o
        # código (default true no demo, DEVE ser false em produção).
        self._otp = OtpService(
            redis=redis,
            salt=salt,
            ttl_s=int(os.getenv("PLUGHUB_OTP_TTL_S", "300")),
            max_attempts=int(os.getenv("PLUGHUB_OTP_MAX_ATTEMPTS", "5")),
            rl_window_s=int(os.getenv("PLUGHUB_OTP_RL_WINDOW_S", "900")),
            rl_max=int(os.getenv("PLUGHUB_OTP_RL_MAX", "3")),
            code_digits=int(os.getenv("PLUGHUB_OTP_CODE_DIGITS", "6")),
            dev_return_code=os.getenv("PLUGHUB_OTP_DEV_RETURN_CODE", "true").lower() in ("1", "true", "yes"),
        )

    async def ensure_identity_schema(self) -> None:
        """Create the PG `identity` schema/tables (idempotent). Called at startup."""
        if self._identity_enabled:
            await self._identity.ensure_schema()

    async def _read_ctx_tag(
        self, tenant_id: str, session_id: str | None, tag: str
    ) -> str | None:
        """Read a single ContextStore tag of a session. Fail-soft → None."""
        if not session_id:
            return None
        try:
            raw = await self._redis.hget(f"{tenant_id}:ctx:{session_id}", tag)
            if not raw:
                return None
            entry = json.loads(raw)
            val = entry.get("value") if isinstance(entry, dict) else entry
            return str(val) if val else None
        except Exception:
            return None

    async def _read_ctx_root(self, tenant_id: str, session_id: str | None) -> str | None:
        """
        Journey J1: read `session.root_session_id` from a session's ContextStore.

        Returns the raw value, or None when absent/unreadable. Used to inherit the
        caller's TRANSITIVE root when spawning a child session (trigger-from-session
        / delegate): child.root = caller.root, not caller.session_id. Fail-soft — a
        missing/broken entry falls back to the caller resolving root = self upstream.
        """
        return await self._read_ctx_tag(tenant_id, session_id, "session.root_session_id")

    async def _resolve_signal_target(
        self,
        tenant_id:   str,
        session_id:  str,          # caller (workflow) session
        caller_root: str,
        grain:       str,
    ) -> str:
        """
        S2 — traduz o GRÃO do sinal na CHAVE contra a qual ele será gravado.

        Isto NÃO é regra de negócio no core: é a definição de o que cada grão SIGNIFICA
        no modelo de sessão da plataforma (a mesma natureza de `root_session_id`). O que
        é regra de negócio — pesquisar a journey ou a sessão — fica no `config_json` do
        deploy; aqui só se resolve o que a plataforma já sabe:

          journey  → a raiz canônica da journey        (caller.session.root_session_id)
          session  → a sessão de origem pesquisada     (caller.session.origin_session_id,
                     i.e. a sessão que disparou o workflow de survey)
          workflow → o próprio workflow                (a sessão chamadora)

        `segment` é REJEITADO aqui de propósito: `survey_record` exige `segment_id`, que o
        workflow outbound não conhece (ele foi disparado por um hook de fim de sessão, não
        de segmento). Suportá-lo exige o gatilho carimbar o segmento — outra fatia. Falhar
        alto é melhor do que gravar o sinal na chave errada.
        """
        if grain == "journey":
            return caller_root
        if grain == "workflow":
            return session_id
        if grain == "session":
            origin = await self._read_ctx_tag(
                tenant_id, session_id, "session.origin_session_id"
            )
            if not origin:
                raise ValueError(
                    "signal_grain='session' exige que o workflow tenha uma sessão de "
                    "origem (session.origin_session_id) — este collect não foi disparado "
                    "a partir de uma sessão."
                )
            return origin
        if grain == "segment":
            raise ValueError(
                "signal_grain='segment' não é suportado no collect outbound: "
                "survey_record exige `segment_id`, que o workflow não conhece. "
                "Um survey de segmento precisa que o gatilho carimbe o segmento."
            )
        raise ValueError(f"signal_grain desconhecido: '{grain}'")

    @staticmethod
    def _ctx_entry(value: str, source: str, now_iso: str) -> str:
        """JSON-encode a ContextStore entry (confidence 1.0, agents_only)."""
        return json.dumps({
            "value":      value,
            "confidence": 1.0,
            "source":     source,
            "visibility": "agents_only",
            "updated_at": now_iso,
        })

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
        root_session_id:   str | None = None,
        context:           dict[str, Any] | None = None,
        pool_id:           str | None = None,
    ) -> str:
        """
        Create a new webhook session for the given skill_id.

        The skill_id acts as the "phone number" / "DNIS" for the webhook pool.

        pool_id: Arc 19 (webhook channel endpoint) — when set, the routing engine
          assigns this pool DIRECTLY (the "expected path": the channel entry point
          declares the service pool). The pool runs whatever skill is currently
          DEPLOYED to it, so the endpoint URL stays stable across skill versions.
          This is the external slug→pool trigger. When None, the routing engine
          resolves the pool via skill_id (the internal workflow_trigger path).
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

        # Journey J1: resolve the transitive root. Explicit param wins; else inherit
        # the caller's root (trigger-from-session); else this session is its own root.
        if root_session_id:
            resolved_root = root_session_id
        elif origin_session_id:
            resolved_root = (
                await self._read_ctx_root(tenant_id, origin_session_id)
                or origin_session_id
            )
        else:
            resolved_root = session_id

        now_str = datetime.now(timezone.utc).isoformat()
        event = {
            "event_id":          str(uuid.uuid4()),
            "session_id":        session_id,
            "tenant_id":         tenant_id,
            "channel":           "webhook",
            # When pool_id is set (external slug→pool endpoint), the routing engine
            # assigns it directly and runs the pool's DEPLOYED skill (stable URL).
            # When None, routing resolves the pool via skill_id (internal trigger).
            "pool_id":           pool_id,
            "skill_id":          skill_id,    # DNIS for webhook channel — routing key
            "customer_id":       customer_id,
            "trigger_type":      trigger_type,
            "metadata":          metadata or {},
            "origin_session_id": origin_session_id,
            "root_session_id":   resolved_root,   # Journey J1
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

        # Journey J1: root is never null — always seed session.root_session_id so
        # any child spawned from THIS session inherits the transitive root.
        ctx_writes["session.root_session_id"] = self._ctx_entry(
            resolved_root, "webhook_trigger", now_iso
        )

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
        resume_token:  str,
        tenant_id:     str,
        payload:       dict[str, Any] | None = None,
        resume_origin: str = "token",
    ) -> str | None:
        """
        Resolve a resume_token to a session_id and publish a session_resumed event.

        The resume_token was generated by the skill-flow-engine suspend executor
        and stored in Redis hash {tenant_id}:resume_tokens.

        resume_origin (Identity Resolver nível b §11) tags how the resume was
        triggered: "token" (explicit resume_token — webhook endpoint / timeout
        scanner, the default), "same_channel" (intra-channel reconnect) or
        "identity" (cross-channel Lookup-2 offer). Slice 3 wires only "token";
        the reconnect-offer origins land with the Fase B resume path.

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
                        "step_id":       step_id,
                        "resume_token":  resume_token,
                        "resume_origin": resume_origin,
                        "payload":       payload or {},
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

        # Journey J1: a resume re-publishes conversations.inbound for the SAME session,
        # so parse_inbound would reset root to self. Preserve the session's own root
        # (read from its ContextStore; fallback self) to keep a resumed CHILD grouped.
        resume_root = await self._read_ctx_root(tenant_id, session_id) or session_id

        # Publish session_resumed to the canonical stream via conversations.inbound
        # The Core / orchestrator-bridge will handle re-allocation.
        event = {
            "event_id":     str(uuid.uuid4()),
            "session_id":   session_id,
            "tenant_id":    tenant_id,
            "channel":      resume_channel,
            "pool_id":      resume_pool,
            "event_type":    "session_resumed",
            "resume_token":  resume_token,
            "resume_origin": resume_origin,
            "step_id":       step_id,
            "payload":       payload or {},
            "root_session_id": resume_root,   # Journey J1: preserve on re-open
            "timestamp":     now_iso,
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
        tenant_id:          str,
        pool_id:            str,
        customer_id:        str,
        origin_session_id:  str,
        resume_token:       str,
        context:            dict[str, str],
        timeout_hours:      float,
        customer_resumable: bool = False,
        resume_policy:      str  = "offer",
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

        # Journey J1: child inherits the caller's TRANSITIVE root (not origin, which
        # is 1-hop). Read the caller's session.root_session_id; fallback = origin
        # (caller treated as its own root when it predates J1 / has no entry).
        caller_root = await self._read_ctx_root(tenant_id, origin_session_id) or origin_session_id

        # ── Seed ContextStore before publishing to Kafka ─────────────────────
        ctx_key = f"{tenant_id}:ctx:{child_session_id}"
        ctx_writes: dict[str, str] = {}

        # Journey J1: seed child root so a grandchild delegate inherits it too.
        ctx_writes["session.root_session_id"] = self._ctx_entry(
            caller_root, "delegate_step", now_iso
        )

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
            "root_session_id":   caller_root,   # Journey J1: transitive root
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

        # ── Identity Resolver dual-write (Fase A · Slice 1 + Slice 3 gate) ────
        # Generalize the pending lookup: resolve/provision a native customer_id
        # from the context anchors and register the pending under it, so a
        # reconnect from ANOTHER channel (different handle resolving to the same
        # customer) finds it. Additive to the legacy contact_id key above.
        # Slice 3: gated on customer_resumable — cross-channel indexing is a
        # per-delegation decision declared in the flow (spec §6), not automatic.
        # resume_policy is carried on the PendingEntry so the reconnect flow
        # (Fase B) knows whether to offer or auto-resume.
        if self._identity_enabled and customer_resumable:
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
                                policy=resume_policy,
                                context_preview=self._pending_context_preview(context),
                                root_session_id=caller_root,   # Journey J3
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

    # ── Journey J4c — collect handler (N2 resolver + routed child session) ────
    async def _reachable_channels(
        self, tenant_id: str, customer_id: str,
    ) -> list[str]:
        """
        Reachability slot (N2 input). Which channels can the platform reach this
        customer on? v1 = best-effort empty (the "web" survey surface needs no
        address and is added by the caller as universal fallback). Future: query
        the Identity Resolver secondary_keys (phone→sms, email→email). This is a
        cross-cutting fact — it never depends on which process is asking.
        """
        # TODO(J4c fase 2): consult Identity Resolver reachable keys.
        return []

    async def _negotiate_channel(
        self,
        tenant_id:      str,
        customer_id:    str,
        channel_policy: dict[str, Any] | None,
    ) -> tuple[str, str]:
        """
        Journey J4c — resolvedor N2. Devolve **(canal, pool)**.

        **CEGO AO PROCESSO**: nunca ramifica por `skill_id`/`campaign_id` nem por
        qualquer identidade de processo — repare que a assinatura sequer os recebe,
        então o invariante é estrutural, não uma convenção.

        Inputs, todos cross-cutting:
          - alcançabilidade (Resolvedor de Identidade — slot)
          - consentimento (slot — vazio v1)
          - política do tenant (slot — vazio v1)
          - o `channel_policy` DECLARATIVO, que é **config de negócio injetada no
            deploy** (`config_json` do slot → `$.config.*`), não conteúdo do skill.

        O mapa `channels` (canal → pool) é a peça central: suas CHAVES são os canais
        permitidos e seus VALORES, o pool que atende cada um. Antes o pool vinha de
        `ChannelEndpoint(channel, identifier="default")` — uma constante mágica no
        core, que ainda por cima só permitia UM pool de collect por canal. Quem atende
        varia por negócio: é config, não é problema do core.
        """
        policy    = channel_policy or {}
        channels  = policy.get("channels") or {}
        exclude   = set(policy.get("exclude") or [])
        preferred = policy.get("preferred_order") or []

        if not channels:
            raise ValueError(
                "collect sem `channel_policy.channels` (mapa canal→pool). Esse mapa é "
                "config de negócio e deve ser injetado no deploy do skill "
                "(config_json do slot → $.config.channel_policy)."
            )

        reachable = await self._reachable_channels(tenant_id, customer_id)

        # Candidatos = canais do mapa − exclude. Se soubermos a alcançabilidade do
        # cliente, ela filtra; `webchat` é o fallback universal (não exige endereço —
        # o próprio link tokenizado é o ponto de entrada).
        allowed = [c for c in channels.keys() if c not in exclude]
        if reachable:
            narrowed = [c for c in allowed if c in reachable or c == "webchat"]
            if narrowed:
                allowed = narrowed
        if not allowed:
            raise ValueError(
                f"nenhum canal elegível: mapa={list(channels)} exclude={sorted(exclude)} "
                f"alcançáveis={reachable}"
            )

        chosen = next((c for c in preferred if c in allowed), allowed[0])
        return chosen, channels[chosen]

    async def handle_collect(
        self,
        *,
        tenant_id:          str,
        session_id:         str,               # caller (N3 workflow) session
        customer_id:        str,
        step_id:            str,
        collect_token:      str,
        target:             dict[str, Any],
        interaction:        str,
        prompt:             str,
        channel:            str | None = None,
        requires:           list[str] | None = None,
        channel_policy:     dict[str, Any] | None = None,
        options:            list[dict[str, Any]] | None = None,
        fields:             list[dict[str, Any]] | None = None,
        dialog_form_id:     str = "",
        signal_grain:       str = "journey",
        timeout_hours:      float = 48.0,
        campaign_id:        str = "",
    ) -> dict[str, Any]:
        """
        N2 handler for a `collect` step (Journey J4c) — LAZY. Delivers the survey
        invitation link and SUSPENDS; it does NOT create a session or allocate any
        resource. The child contact session is created only when the customer
        engages (opens the link) — see GET /survey/{collect_token} (J4c-3):

          1. Negotiate the channel (N2 resolver — process-agnostic).
          2. Resolve the survey POOL for that channel (ChannelEndpoint) — stored on
             the pending so the click can route the inbound to it.
          3. Store the collect pending ({tenant}:collect:{collect_token}) with the
             caller-workflow resume mapping (caller_session/step_id), the inherited
             transitive root, the survey pool, form_id and negotiated channel.
          4. Deliver the invitation link `/survey/{collect_token}` (mock/dev).
          5. Return send_at/expires_at → the workflow suspends. No click by the
             deadline → nothing was allocated (only a pending key that expires).

        On click, a STANDARD inbound is created (routed → tenant quota + pool
        max_concurrent_sessions + Core `sessions` metering enforced only for real
        engagements) and the dialog_runner renders the DialogForm live (customer
        present). N3 stays channel-agnostic (sets `channel_policy`, never a channel).
        Returns { send_at, expires_at, link }.
        """
        now_dt  = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        # Journey J1: the (future) child inherits the caller's TRANSITIVE root.
        caller_root = await self._read_ctx_root(tenant_id, session_id) or session_id

        # ── N2: negocia canal E pool a partir do mapa de negócio (config de deploy) ──
        # O `channel` fixo só existe para transporte realmente fixo (collect interno a
        # um sistema); para outbound-ao-cliente ele seria N3 escolhendo o canal.
        if channel:
            pool_from_map = ((channel_policy or {}).get("channels") or {}).get(channel)
            if not pool_from_map:
                raise ValueError(
                    f"`channel` fixo '{channel}' não está no mapa channel_policy.channels"
                )
            negotiated, pool_id = channel, pool_from_map
        else:
            negotiated, pool_id = await self._negotiate_channel(
                tenant_id, customer_id, channel_policy,
            )

        # ── S2: grão → CHAVE do sinal ─────────────────────────────────────────
        # Resolvido AQUI (e não no runner) porque a tradução grão→chave é semântica do
        # modelo de sessão, e só o chamador tem o contexto (raiz, sessão de origem). O
        # runner recebe o alvo pronto pelo ctx e continua 100% genérico — sem grão nem
        # métrica hardcoded. Falha alto em grão inválido/insuportável: gravar o sinal na
        # chave errada é pior do que não gravar (contamina o relatório em silêncio).
        signal_target_id = await self._resolve_signal_target(
            tenant_id, session_id, caller_root, signal_grain,
        )

        # ── LAZY: store the collect pending — NO session until the customer clicks ──
        ttl_s      = int(timeout_hours * 3600) + 3600
        expires_at = (now_dt + timedelta(hours=timeout_hours)).isoformat()
        await self._redis.set(
            f"{tenant_id}:collect:{collect_token}",
            json.dumps({
                "caller_session_id": session_id,     # N3 workflow to resume on completion
                "step_id":           step_id,
                "root_session_id":   caller_root,    # journey membership for the inbound
                "pool_id":           pool_id,        # survey pool for the click inbound
                "channel":           negotiated,
                "form_id":           dialog_form_id, # DialogForm the runner will render
                "signal_grain":      signal_grain,      # S2 — grão do sinal (config do deploy)
                "signal_target_id":  signal_target_id,  # S2 — chave já resolvida p/ o runner
                "customer_id":       customer_id,
                "tenant_id":         tenant_id,
                "status":            "pending",
                "created_at":        now_iso,
                "expires_at":        expires_at,
            }),
            ex=ttl_s,
        )

        # ── Resume: the collect_token DOUBLES AS the resume_token ─────────────
        # Reuses the existing webhook resume machinery end-to-end: handle_resume()
        # does HGET on {tenant}:resume_tokens → "{session_id}:{step_id}:{expires_at}"
        # and resumes the suspended caller with resumeContext{step_id, input, payload}.
        # So the survey runner just calls workflow_resume(collect_token, answers) at
        # the end — no new topic, no new consumer.
        await self._redis.hset(
            f"{tenant_id}:resume_tokens",
            collect_token,
            f"{session_id}:{step_id}:{expires_at}",
        )
        try:
            await self._redis.expire(f"{tenant_id}:resume_tokens", ttl_s)
        except Exception:   # non-fatal — hash shared across sessions
            pass

        # ── Deliver the invitation link (mock/dev). The collect_token IS the token. ──
        # TODO(J4c fase 2): real SMS/email delivery via the negotiated channel provider.
        link = f"/survey/{collect_token}"
        logger.info(
            "webhook collect (lazy): token=%s channel=%s pool=%s root=%s link=%s "
            "— suspended, no session/resource until click",
            collect_token, negotiated, pool_id, caller_root, link,
        )

        return {"send_at": now_iso, "expires_at": expires_at, "link": link}

    async def handle_collect_engage(
        self,
        *,
        tenant_id:          str,
        collect_token:      str,
        jwt_secret_default: str,
        session_ttl_s:      int = 4 * 3600,
    ) -> dict[str, Any] | None:
        """
        Journey J4c — the customer ENGAGED (opened the survey link). **This is the
        only place a session is created**: until now the collect was suspended with
        zero resource. The customer is present, so the survey is SYNCHRONOUS and the
        dialog_runner can render the DialogForm live (agent profile: `menu` works).

        Mechanism — reuses the whole existing webchat path, zero new adapter:
          • Pre-seed the session ContextStore. The analytics consumer's J1 root
            enrichment reads `session.root_session_id` from the ctx (not the event),
            so seeding it BEFORE the inbound makes the session a journey member N1
            by construction. `collect_token` + `dialog_form_id` are read by the runner.
          • Mint a webchat JWT carrying `session_id` — the webchat adapter honours
            that claim, so the page connects as a NORMAL webchat client and the
            existing inbound → Routing (quota + max_concurrent_sessions) → Core
            (`sessions` metering) path runs untouched. Limits apply to real
            engagements only.

        Idempotent: re-opening the link reuses the same survey session.
        Returns { jwt, pool_id, session_id, form_id } or None if unknown/expired.
        """
        raw = await self._redis.get(f"{tenant_id}:collect:{collect_token}")
        if not raw:
            return None
        pending = json.loads(raw if isinstance(raw, str) else raw.decode())

        now_iso    = datetime.now(timezone.utc).isoformat()
        session_id = pending.get("survey_session_id") or ""

        if not session_id:
            session_id = str(uuid.uuid4())
            ctx_key    = f"{tenant_id}:ctx:{session_id}"
            ctx_writes: dict[str, str] = {
                # Journey J1: root BEFORE the inbound → consumer enrichment stamps it.
                "session.root_session_id": self._ctx_entry(
                    pending.get("root_session_id") or session_id, "collect_engage", now_iso,
                ),
                "session.origin_session_id": json.dumps({
                    "value": pending.get("caller_session_id") or "", "confidence": 1.0,
                    "source": "collect_engage", "visibility": "agents_only",
                    "updated_at": now_iso,
                }),
                # The runner resumes N3 with this at the end (workflow_resume).
                "session.workflow_resume_token": json.dumps({
                    "value": collect_token, "confidence": 1.0, "source": "collect_engage",
                    "visibility": "agents_only", "updated_at": now_iso,
                }),
                "session.collect_token": json.dumps({
                    "value": collect_token, "confidence": 1.0, "source": "collect_engage",
                    "visibility": "agents_only", "updated_at": now_iso,
                }),
            }
            if pending.get("form_id"):
                # Dialog primitive binding — the single generic runner reads this.
                ctx_writes["session.dialog_form_id"] = json.dumps({
                    "value": pending["form_id"], "confidence": 1.0,
                    "source": "collect_engage", "visibility": "agents_only",
                    "updated_at": now_iso,
                })
            # S2 — grão + chave do sinal, já resolvidos no handle_collect. O runner lê
            # ambos daqui: ele não sabe (nem precisa saber) que grão está pesquisando.
            # Retrocompat: pendings criados antes do S2 não têm os campos → default
            # journey / raiz, que é exatamente o que eles faziam hardcoded.
            ctx_writes["session.survey_grain"] = self._ctx_entry(
                pending.get("signal_grain") or "journey", "collect_engage", now_iso,
            )
            ctx_writes["session.survey_target_id"] = self._ctx_entry(
                pending.get("signal_target_id")
                or pending.get("root_session_id")
                or session_id,
                "collect_engage", now_iso,
            )
            await self._redis.hset(ctx_key, mapping=ctx_writes)
            await self._redis.expire(ctx_key, session_ttl_s)

            pending["survey_session_id"] = session_id
            pending["status"]            = "engaged"
            pending["engaged_at"]        = now_iso
            try:
                await self._redis.set(
                    f"{tenant_id}:collect:{collect_token}",
                    json.dumps(pending), keepttl=True,
                )
            except TypeError:   # older redis-py without keepttl
                await self._redis.set(
                    f"{tenant_id}:collect:{collect_token}",
                    json.dumps(pending), ex=session_ttl_s,
                )
            logger.info(
                "collect engaged: token=%s survey_session=%s pool=%s root=%s "
                "— session created ONLY now (customer present)",
                collect_token, session_id, pending.get("pool_id"),
                pending.get("root_session_id"),
            )

        # ── Mint the webchat JWT (pre-binds session_id) ───────────────────────
        secret = jwt_secret_default
        try:
            per_tenant = await self._redis.get(f"{tenant_id}:config:webchat:jwt_secret")
            if per_tenant:
                secret = per_tenant if isinstance(per_tenant, str) else per_tenant.decode()
        except Exception:   # non-fatal — fall back to the default secret
            pass

        token = pyjwt.encode(
            {
                "sub":        pending.get("customer_id") or session_id,
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "exp":        int(time.time()) + session_ttl_s,
            },
            secret,
            algorithm="HS256",
        )
        return {
            "jwt":        token if isinstance(token, str) else token.decode(),
            "pool_id":    pending.get("pool_id") or "",
            "session_id": session_id,
            "form_id":    pending.get("form_id") or "",
        }

    @staticmethod
    def _pending_context_preview(context: dict[str, Any]) -> dict[str, str]:
        """
        Build a minimal, PII-conscious preview for the pending entry — shown to
        the customer in the cross-channel reconnect offer. operadora_destino is
        non-secret (kept in clear); numero_atual is a phone (masked to the last
        4 digits, e.g. ***4321). Absent keys are simply omitted.
        """
        preview: dict[str, str] = {}
        operadora = context.get("operadora_destino") or context.get("session.operadora_destino")
        if operadora:
            preview["operadora_destino"] = str(operadora)
        numero = context.get("numero_atual") or context.get("session.numero_atual")
        if numero:
            digits = "".join(ch for ch in str(numero) if ch.isdigit())
            preview["numero_atual"] = ("***" + digits[-4:]) if len(digits) >= 4 else "***"
        return preview

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
            "customer_id":        ref.customer_id,
            "status":             ref.status,
            "matched_by":         ref.matched_by,
            "confidence":         ref.confidence,
            "verification_class": ref.verification_class,
        }

    # ── OTP de posse de canal (Fase 2) ─────────────────────────────────────────

    async def otp_challenge(self, tenant_id: str, kind: str, value: str) -> dict:
        """Emite um desafio de posse para a âncora (kind, value)."""
        return await self._otp.challenge(tenant_id, kind, value)

    async def otp_verify(
        self, tenant_id: str, customer_id: str, kind: str, value: str, code: str,
    ) -> dict:
        """
        Confere o OTP. No sucesso, promove a âncora a `possessed` (única via de
        posse) e a torna durável no cadastro do `customer_id`. É o ponto onde
        "posse provada" vira "identidade confiável".
        """
        res = await self._otp.verify(tenant_id, kind, value, code)
        if res.get("verified") and customer_id:
            await self._identity.attach_anchor(
                tenant_id, customer_id, kind, value,
                verification_class="possessed", persist_durable=True,
            )
            res["verification_class"] = "possessed"
        return res

    async def attach_customer_key(
        self, tenant_id: str, customer_id: str, kind: str, value: str,
    ) -> dict:
        """
        Enriquecimento — anexa uma âncora ao cliente como `claimed` (não-verificada).
        `possessed` NUNCA sai daqui: exige OTP (invariante possessed ⟺ verificado).
        """
        ok = await self._identity.attach_anchor(
            tenant_id, customer_id, kind, value,
            verification_class="claimed", persist_durable=False,
        )
        return {"attached": ok, "verification_class": "claimed"}

    async def update_customer_attributes(
        self, tenant_id: str, customer_id: str, attributes: dict,
    ) -> dict:
        """Enriquecimento — merge de atributos mascarados/não-sensíveis no cadastro."""
        ok = await self._identity.update_attributes(tenant_id, customer_id, attributes)
        return {"updated": ok}

    async def find_pending_by_customer(self, tenant_id: str, customer_id: str) -> dict:
        """Lookup 2 — pending workflows for a resolved customer_id.

        Returns the full pendings[] plus, for reconnect ergonomics, a FLATTENED
        view of the first pending at the top level (found/resume_token/pool/
        context/policy) — shape-compatible with the legacy get_pending_workflow
        response so the intake flow reads `pendencia.resume_token` /
        `pendencia.context.*` / `pendencia.policy` without JSONPath array indexing.
        `context` is derived from the pending's context_preview (masked at write).
        """
        pendings = await self._identity.find_pending(tenant_id, customer_id)
        result: dict[str, Any] = {
            "found":       len(pendings) > 0,
            "count":       len(pendings),
            "customer_id": customer_id,
            "pendings": [
                {
                    "session_id":      p.session_id,
                    "resume_token":    p.resume_token,
                    "pool":            p.pool,
                    "skill_id":        p.skill_id,
                    "intent":          p.intent,
                    "policy":          p.policy,
                    "suspended_at":    p.suspended_at,
                    "context_preview": p.context_preview,
                    "root_session_id": p.root_session_id,   # Journey J3
                }
                for p in pendings
            ],
        }
        if pendings:
            first = pendings[0]
            result.update({
                "resume_token":    first.resume_token,
                "pool":            first.pool,
                "policy":          first.policy,
                "context":         first.context_preview,
                "root_session_id": first.root_session_id,   # Journey J3 (merge target)
            })
        return result

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
        tenant_id:          str,
        pool_id:            str,
        session_id:         str,    # PARENT session — customer is connected here
        customer_id:        str,
        resume_token:       str,    # delegate step resume token (for parent session)
        step_id:            str = "",  # parent's delegate step id (for the resume_token value)
        context:            dict[str, str] = {},
        timeout_hours:      float = 1.0,
        customer_resumable: bool = False,
        resume_policy:      str  = "offer",
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

        # ── Identity Resolver dual-write (Slice 3 gate) ───────────────────────
        # Cross-channel pending indexing under the native customer_id, gated on
        # customer_resumable (spec §6). Mirrors handle_delegate; best-effort.
        if self._identity_enabled and customer_resumable:
            try:
                anchors = self._anchors_from_context(context)
                if anchors:
                    ref = await self._identity.resolve_or_provision(tenant_id, anchors, provision=True)
                    if ref.customer_id:
                        # Journey J3: raiz canônica do parent (o especialista roda dentro dele).
                        _conf_root = await self._read_ctx_root(tenant_id, session_id) or session_id
                        await self._identity.write_pending(
                            tenant_id, ref.customer_id,
                            PendingEntry(
                                session_id=session_id,   # parent hosts the specialist
                                customer_id=ref.customer_id,
                                resume_token=resume_token,
                                pool=pool_id,
                                skill_id=context.get("skill_id"),
                                intent=context.get("intent"),
                                policy=resume_policy,
                                context_preview=self._pending_context_preview(context),
                                root_session_id=_conf_root,   # Journey J3
                            ),
                            ttl_s=ttl_s,
                        )
                        await self._identity.promote_to_durable(tenant_id, ref.customer_id, anchors)
                        logger.info(
                            "identity: pending_by_customer written (conference) customer=%s parent=%s matched_by=%s",
                            ref.customer_id, session_id, ref.matched_by,
                        )
            except Exception as _e:
                logger.warning("identity: dual-write failed (conference, non-fatal): %s", _e)

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
