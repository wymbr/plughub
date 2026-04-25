"""
crash_detector.py
Detects agent instances that stopped sending heartbeats and recovers orphaned conversations.
Spec: PlugHub v24.0 section 4.5

Flow:
  1. Every crash_check_interval_s, scans all pool instance sets via SCAN
  2. For each instance_id in the set: checks if {tenant_id}:instance:{instance_id} still exists
  3. If it does not exist (TTL 30s expired without heartbeat) → instance crashed
  4. Reads InstanceMeta: pools and active conversations
  5. Removes instance_id from all its pool sets
  6. Re-publishes each active conversation to conversations.inbound for re-routing
  7. Publishes agent_crash to agent.lifecycle for audit
  8. Deletes InstanceMeta

Does not rely on Redis keyspace notifications — uses periodic polling over pool sets.
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from .config import get_settings
from .registry import InstanceRegistry, _instance_key, _pool_instances_key

logger = logging.getLogger("plughub.routing.crash_detector")


class CrashDetector:
    """
    Background task that detects instance crashes and recovers orphaned conversations.
    Stateless — no in-memory state between cycles.
    """

    def __init__(
        self,
        redis_client:      aioredis.Redis,
        instance_registry: InstanceRegistry,
        kafka_producer:    AIOKafkaProducer,
    ) -> None:
        self._redis     = redis_client
        self._instances = instance_registry
        self._producer  = kafka_producer
        self._settings  = get_settings()

    async def run(self) -> None:
        """Main loop — runs indefinitely until cancelled."""
        logger.info(
            "CrashDetector started — interval=%ds",
            self._settings.crash_check_interval_s,
        )
        while True:
            await asyncio.sleep(self._settings.crash_check_interval_s)
            try:
                await self._scan_cycle()
            except Exception as exc:
                logger.error("Error in crash detection cycle: %s", exc)

    async def _scan_cycle(self) -> None:
        """
        One scan cycle: iterates all pool instance sets and detects crashes.
        Uses SCAN with pattern *:pool:*:instances to cover all tenants.
        """
        crashed: list[tuple[str, str]] = []  # (tenant_id, instance_id)

        # SCAN over pool instance sets — pattern: {tenant_id}:pool:{pool_id}:instances
        async for pool_set_key_raw in self._redis.scan_iter("*:pool:*:instances"):
            # scan_iter may return bytes or str depending on decode_responses setting
            pool_set_key = pool_set_key_raw.decode() if isinstance(pool_set_key_raw, bytes) else pool_set_key_raw

            instance_ids_raw = await self._redis.smembers(pool_set_key)
            if not instance_ids_raw:
                continue

            # Normalise to str — smembers returns bytes when decode_responses=False
            instance_ids = {
                v.decode() if isinstance(v, bytes) else v
                for v in instance_ids_raw
            }

            # Extract tenant_id from pattern {tenant_id}:pool:{pool_id}:instances
            parts = pool_set_key.split(":")
            if len(parts) < 4:
                continue
            tenant_id = parts[0]

            for instance_id in instance_ids:
                key_exists = await self._redis.exists(
                    _instance_key(tenant_id, instance_id)
                )
                if not key_exists:
                    crashed.append((tenant_id, instance_id))

        # Deduplicate — an instance may appear in multiple pools
        seen: set[tuple[str, str]] = set()
        for tenant_id, instance_id in crashed:
            if (tenant_id, instance_id) not in seen:
                seen.add((tenant_id, instance_id))
                await self._handle_crash(tenant_id, instance_id)

    async def _handle_crash(self, tenant_id: str, instance_id: str) -> None:
        """
        Recovers a crashed instance:
        - Removes from pool sets
        - Re-routes active conversations
        - Publishes audit event
        - Cleans up metadata
        """
        meta = await self._instances.get_instance_meta(tenant_id, instance_id)
        if meta is None:
            # Instance without meta: only clean up pool set (unlikely — agent never reached agent_ready)
            await self._remove_from_all_pools_by_scan(tenant_id, instance_id)
            logger.warning(
                "Crash detected without meta: tenant=%s instance=%s — pool cleanup only",
                tenant_id, instance_id,
            )
            return

        # 1. Remove from all pools declared in meta
        for pool_id in meta.pools:
            await self._redis.srem(
                _pool_instances_key(tenant_id, pool_id), instance_id
            )

        # 2. Re-publish active conversations to conversations.inbound.
        #    Skip conversations where the Skill Flow Engine still holds an
        #    execution lock — these belong to native AI agents that are still
        #    running their skill flow (the instance heartbeat key expired, but
        #    the engine is alive inside a long BLPOP / async task wait).
        #    Re-queuing those would create a duplicate execution.
        pool_id_for_requeue = meta.pools[0] if meta.pools else ""
        recovered: list[str] = []
        skipped_locked: list[str] = []
        for conversation_id in meta.active_conversations:
            lock_key     = f"{tenant_id}:pipeline:{conversation_id}:running"
            activity_key = f"{tenant_id}:session:{conversation_id}:active_instance:{instance_id}"

            engine_lock_exists    = await self._redis.exists(lock_key)
            session_active_exists = await self._redis.exists(activity_key)

            if engine_lock_exists or session_active_exists:
                # Agent still active (executing skill flow or waiting for menu reply).
                # The instance heartbeat may have expired but the agent is alive:
                #   engine_lock_exists  — Skill Flow engine is executing a step
                #   session_active_exists — agent is blocked in BLPOP (menu/collect wait)
                skipped_locked.append(conversation_id)
                logger.info(
                    "Crash recovery: skipping active session "
                    "tenant=%s instance=%s conversation=%s "
                    "lock=%s activity_flag=%s",
                    tenant_id, instance_id, conversation_id,
                    bool(engine_lock_exists), bool(session_active_exists),
                )
                continue
            await self._requeue_conversation(
                tenant_id, conversation_id,
                pool_id=pool_id_for_requeue,
                agent_type_id=meta.agent_type_id,
            )
            recovered.append(conversation_id)

        # 3. Publish audit event
        # NOTE: producer has value_serializer=json.dumps().encode — pass dict, NOT bytes
        await self._producer.send(
            self._settings.kafka_topic_lifecycle,
            value={
                "event":                    "agent_crash",
                "tenant_id":                tenant_id,
                "instance_id":              instance_id,
                "agent_type_id":            meta.agent_type_id,
                "recovered_conversation_ids": recovered,
                "timestamp":                datetime.now(timezone.utc).isoformat(),
            },
        )

        # 4. Clean up metadata
        await self._instances.delete_instance_meta(tenant_id, instance_id)

        logger.warning(
            "Crash recovery: tenant=%s instance=%s agent_type=%s "
            "conversations_requeued=%d conversations_skipped_locked=%d",
            tenant_id, instance_id, meta.agent_type_id,
            len(recovered), len(skipped_locked),
        )

    async def _requeue_conversation(
        self,
        tenant_id:      str,
        conversation_id: str,
        pool_id:        str = "",
        agent_type_id:  str = "",
    ) -> None:
        """
        Re-publishes a conversation to conversations.inbound for re-routing by the Router.
        Builds a minimal event — the Router will queue it if no agent is available.

        pool_id and agent_type_id are included so the Router can target the same
        pool that was serving the conversation before the crash, rather than scoring
        all pools from scratch (which may route to the wrong pool if the inbound
        event lacks intent context).
        """
        # NOTE: producer has value_serializer=json.dumps().encode — pass dict, NOT bytes
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            value={
                "session_id":    conversation_id,
                "tenant_id":     tenant_id,
                "customer_id":   "",
                "channel":       "webchat",
                "started_at":    datetime.now(timezone.utc).isoformat(),
                "elapsed_ms":    0,
                # Direct hints for the Router — avoids rescoring and misrouting
                "pool_id":       pool_id,
                "agent_type_id": agent_type_id,
                # No intent/confidence/profile — Router will use defaults and queue if necessary
            },
        )

    async def _remove_from_all_pools_by_scan(
        self, tenant_id: str, instance_id: str
    ) -> None:
        """Fallback: removes instance_id from all pool sets of the tenant via SCAN."""
        async for pool_set_key_raw in self._redis.scan_iter(f"{tenant_id}:pool:*:instances"):
            pool_set_key = pool_set_key_raw.decode() if isinstance(pool_set_key_raw, bytes) else pool_set_key_raw
            await self._redis.srem(pool_set_key, instance_id)
