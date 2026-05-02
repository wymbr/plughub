"""
evaluation_sampler.py
Evaluation Sampling — decides whether a closed contact should be evaluated.
Spec: PlugHub v24.0 section 10.2 (Amostragem)

Responsibilities:
1. On agent_login: initialise sampling counters in Redis for the session
2. On contact_closed: apply quota-based sampling algorithm, publish
   evaluation.requested if the quota is not yet satisfied

Quota algorithm:
    should_evaluate = floor(contacts_handled × sampling_rate) > contacts_evaluated

This produces deterministic convergence: over the session, the number of
evaluations equals exactly floor(total_contacts × rate).
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from .models import ContactClosedEvent, PoolEvaluationConfig

logger = logging.getLogger("plughub.rules.evaluation_sampler")

# ─────────────────────────────────────────────
# Redis key
# ─────────────────────────────────────────────

def _counter_key(tenant_id: str, agent_session_id: str) -> str:
    return f"eval:sampling:{tenant_id}:{agent_session_id}"


# ─────────────────────────────────────────────
# EvaluationSampler
# ─────────────────────────────────────────────

class EvaluationSampler:
    """
    Stateless sampler — all state lives in Redis.
    Injected into the Kafka consumer loop.
    """

    def __init__(
        self,
        redis:          aioredis.Redis,
        kafka_producer,                  # aiokafka AIOKafkaProducer
        evaluation_topic:  str,
        counter_ttl_s:     int = 172_800,  # 48h
    ) -> None:
        self._redis           = redis
        self._kafka           = kafka_producer
        self._evaluation_topic = evaluation_topic
        self._counter_ttl_s   = counter_ttl_s
        # In-memory cache of PoolEvaluationConfig, populated by pool.registered events
        self._pool_configs: dict[str, dict[str, PoolEvaluationConfig]] = {}  # tenant → pool → cfg

    # ── Public handlers ───────────────────────────────────────────────────────

    async def on_agent_login(self, tenant_id: str, agent_session_id: str) -> None:
        """Initialise (or reset) sampling counters for a new agent session."""
        key = _counter_key(tenant_id, agent_session_id)
        await self._redis.hset(key, mapping={"contacts_handled": 0, "contacts_evaluated": 0})
        await self._redis.expire(key, self._counter_ttl_s)
        logger.debug("Sampling counters initialised: tenant=%s session=%s", tenant_id, agent_session_id)

    async def on_contact_closed(self, event: ContactClosedEvent) -> None:
        """
        Apply quota-based sampling decision.
        Publishes evaluation.requested if floor(handled * rate) > evaluated.
        """
        cfg = self._get_pool_config(event.tenant_id, event.pool_id)
        if cfg.sampling_rate == 0.0:
            logger.debug("Sampling disabled for pool %s — skipping", event.pool_id)
            return

        key = _counter_key(event.tenant_id, event.agent_session_id)

        # Increment contacts_handled atomically
        handled = await self._redis.hincrby(key, "contacts_handled", 1)
        # Ensure TTL is refreshed (counter may have been created by a previous session)
        await self._redis.expire(key, self._counter_ttl_s)

        evaluated_raw = await self._redis.hget(key, "contacts_evaluated")
        evaluated     = int(evaluated_raw or 0)

        quota_target = math.floor(handled * cfg.sampling_rate)

        if quota_target > evaluated:
            # This contact should be evaluated
            await self._redis.hincrby(key, "contacts_evaluated", 1)
            await self._publish_evaluation_requested(event, cfg)
            logger.info(
                "Evaluation triggered: tenant=%s session=%s handled=%d evaluated=%d→%d",
                event.tenant_id, event.agent_session_id, handled, evaluated, evaluated + 1,
            )
        else:
            logger.debug(
                "Evaluation skipped: tenant=%s session=%s handled=%d evaluated=%d quota=%d",
                event.tenant_id, event.agent_session_id, handled, evaluated, quota_target,
            )

    def on_pool_config(self, tenant_id: str, pool_id: str, cfg: PoolEvaluationConfig) -> None:
        """Updates in-memory pool config cache (called by RegistryEventHandler)."""
        if tenant_id not in self._pool_configs:
            self._pool_configs[tenant_id] = {}
        self._pool_configs[tenant_id][pool_id] = cfg
        logger.debug("Pool eval config cached: tenant=%s pool=%s rate=%s", tenant_id, pool_id, cfg.sampling_rate)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_pool_config(self, tenant_id: str, pool_id: str) -> PoolEvaluationConfig:
        """Returns the pool eval config or a default (evaluate all) if not cached."""
        return (
            self._pool_configs.get(tenant_id, {}).get(pool_id)
            or PoolEvaluationConfig()
        )

    async def _publish_evaluation_requested(
        self, event: ContactClosedEvent, cfg: PoolEvaluationConfig
    ) -> None:
        evaluation_id = str(uuid.uuid4())
        triggered_at  = datetime.now(timezone.utc).isoformat()

        payload: dict = {
            "evaluation_id":  evaluation_id,
            "triggered_by":   "contact_closed",
            "triggered_at":   triggered_at,
            "tenant_id":      event.tenant_id,

            "contact": {
                **event.contact,
                "contact_id": event.contact_id,
            },

            "agent": {
                "agent_id":         event.agent_id,
                "agent_session_id": event.agent_session_id,
                "agent_type":       event.agent_type,
                "pool_id":          event.pool_id,
            },

            "skill_id":        cfg.resolve_skill_id(event.pool_id),
            "context_package": event.context_package,
            "transcript_id":   event.transcript_id,
        }

        await self._kafka.send_and_wait(
            self._evaluation_topic,
            value=json.dumps(payload).encode("utf-8"),
        )
        logger.debug("evaluation.requested published: id=%s", evaluation_id)
