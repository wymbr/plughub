"""
registry.py
Instance state and pool configurations — exclusively via Redis.
Spec: PlugHub v24.0 sections 3.3, 4.5 and 4.6

Rule: never access PostgreSQL directly.
Pool configs read from Redis cache (populated by kafka_listener from agent.registry.events).
Instance state read from Redis (populated by kafka_listener from agent.lifecycle).

Redis key structure:
  {tenant_id}:instance:{instance_id}                        — instance state (TTL 30s)
  {tenant_id}:pool:{pool_id}:instances                      — set of instance_ids in pool
  {tenant_id}:pool_config:{pool_id}                         — pool config JSON (TTL 24h, via PLUGHUB_POOL_CONFIG_TTL_SECONDS)
  {tenant_id}:pools                                         — set of pool_ids for the tenant
  {tenant_id}:pool:{pool_id}:queue                          — sorted set of contacts (score = queued_at_ms)
  {tenant_id}:queue_contact:{session_id}                    — queued contact JSON
  session_instance:{session_id}                             — session affinity (stateful)
  {tenant_id}:routing:instance:{instance_id}:meta           — HASH no TTL (pools, agent_type_id)
  {tenant_id}:routing:instance:{instance_id}:conversations  — SET no TTL of active conversation_ids
"""

from __future__ import annotations
import json
from datetime import datetime, timezone

import redis.asyncio as aioredis

from .models import AgentInstance, InstanceMeta, PoolConfig, QueuedContact, RoutingExpression
from .config import get_settings


# ─────────────────────────────────────────────
# Redis key helpers
# ─────────────────────────────────────────────

def _instance_key(tenant_id: str, instance_id: str) -> str:
    """Spec: {tenant_id}:instance:{instance_id}"""
    return f"{tenant_id}:instance:{instance_id}"

def _pool_instances_key(tenant_id: str, pool_id: str) -> str:
    """Set of instance_ids present (ready) in the pool."""
    return f"{tenant_id}:pool:{pool_id}:instances"

def _pool_config_key(tenant_id: str, pool_id: str) -> str:
    """Pool configuration cache — populated by kafka_listener."""
    return f"{tenant_id}:pool_config:{pool_id}"

def _pool_set_key(tenant_id: str) -> str:
    """Set of all pool_ids for the tenant."""
    return f"{tenant_id}:pools"

def _queue_key(tenant_id: str, pool_id: str) -> str:
    """Sorted set of queued contacts (score = queued_at_ms)."""
    return f"{tenant_id}:pool:{pool_id}:queue"

def _queue_contact_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:queue_contact:{session_id}"

def _session_instance_key(session_id: str) -> str:
    """Session affinity for stateful agents."""
    return f"session_instance:{session_id}"

def _instance_meta_key(tenant_id: str, instance_id: str) -> str:
    """HASH with no TTL: instance pools and agent_type_id. Used by CrashDetector."""
    return f"{tenant_id}:routing:instance:{instance_id}:meta"

def _instance_conversations_key(tenant_id: str, instance_id: str) -> str:
    """SET with no TTL of active conversation_ids on the instance. Used by CrashDetector."""
    return f"{tenant_id}:routing:instance:{instance_id}:conversations"

def _pool_snapshot_key(tenant_id: str, pool_id: str) -> str:
    """Operational snapshot — written by router after each routing event. TTL 120s."""
    return f"{tenant_id}:pool:{pool_id}:snapshot"

def _agent_perf_key(tenant_id: str, agent_type_id: str) -> str:
    """
    Arc 7d: historical performance score for an agent type.
    Written by analytics-api performance_job every 5 minutes.
    Value: str(float) in [0.0, 1.0].
    TTL: 6 hours (refreshed by performance_job before expiry).
    """
    return f"{tenant_id}:agent_perf:{agent_type_id}"


# ─────────────────────────────────────────────
# InstanceRegistry
# ─────────────────────────────────────────────

class InstanceRegistry:
    """
    Queries and updates agent instance state in Redis.
    Key: {tenant_id}:instance:{instance_id} — TTL 30s (spec 4.5).
    Populated by kafka_listener from agent.lifecycle events.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis    = redis_client
        self._settings = get_settings()

    async def get_ready_instances(
        self, tenant_id: str, pool_id: str
    ) -> list[AgentInstance]:
        """Returns instances with state=ready and available capacity."""
        instance_ids = await self._redis.smembers(
            _pool_instances_key(tenant_id, pool_id)
        )
        instances: list[AgentInstance] = []
        for iid in instance_ids:
            raw = await self._redis.get(_instance_key(tenant_id, iid))
            if not raw:
                # Instance key expired (TTL ran out) but ID is still in the pool set.
                # Remove the stale entry to keep the set consistent.
                await self._redis.srem(_pool_instances_key(tenant_id, pool_id), iid)
                continue
            try:
                data = json.loads(raw)
                # Normalise 'status' (mcp-server) → 'state' (internal model)
                if "status" in data and "state" not in data:
                    data["state"] = data["status"]
                inst = AgentInstance.model_validate(data)
                if inst.state == "ready" and inst.current_sessions < inst.max_concurrent:
                    instances.append(inst)
            except Exception:
                continue
        return instances

    async def get_instance(
        self, tenant_id: str, instance_id: str
    ) -> AgentInstance | None:
        """Returns an instance by ID."""
        raw = await self._redis.get(_instance_key(tenant_id, instance_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if "status" in data and "state" not in data:
                data["state"] = data["status"]
            return AgentInstance.model_validate(data)
        except Exception:
            return None

    async def set_instance(
        self, instance: AgentInstance
    ) -> None:
        """
        Persists instance state in Redis.

        - AI agents: TTL = instance_ttl_seconds (30s), renewed on each heartbeat.
        - Human agents (source = "human_login" in the existing Redis key): TTL is
          preserved with KEEPTTL.  The mcp-server writes the key with no TTL
          (permanent) and owns the lifetime; overwriting with 30s would expire the
          key and make the orchestrator-bridge unable to read execution_model when
          it processes conversations.routed.
          KEEPTTL on a key with no TTL keeps it permanent.
          KEEPTTL on a missing key creates a key with no TTL — also correct.

        The AgentInstance model does not carry the source field, so we read the
        existing Redis key to detect human agents before overwriting it.
        """
        key  = _instance_key(instance.tenant_id, instance.instance_id)
        data = instance.model_dump()
        # Alias 'state' → 'status' for mcp-server compatibility
        data["status"] = data.pop("state")

        # Detect human agents: check existing key for source="human_login".
        is_human = False
        try:
            existing_raw = await self._redis.get(key)
            if existing_raw:
                existing = json.loads(existing_raw)
                if existing.get("source") == "human_login":
                    is_human = True
                    # Re-inject source so bridge can still detect it after update.
                    data["source"] = "human_login"
                    # Also preserve execution_model from the original key.
                    # The agent_ready Kafka event may carry execution_model="stateless"
                    # (the kafka_listener default) even for human agents.  If we let
                    # the AgentInstance value overwrite the key, the bridge's fallback-2
                    # check (execution_model == "stateful") will fail and the contact
                    # will never be delivered to the Agent Assist UI.
                    if existing.get("execution_model"):
                        data["execution_model"] = existing["execution_model"]
        except Exception:
            pass

        if is_human:
            # Preserve whatever TTL the mcp-server set (typically none = permanent).
            await self._redis.set(key, json.dumps(data), keepttl=True)
        else:
            await self._redis.set(
                key,
                json.dumps(data),
                ex=self._settings.instance_ttl_seconds,
            )
        # Update the pool instance set if the instance is ready
        for pool_id in instance.pools:
            pool_key = _pool_instances_key(instance.tenant_id, pool_id)
            if data["status"] == "ready":
                await self._redis.sadd(pool_key, instance.instance_id)
            else:
                await self._redis.srem(pool_key, instance.instance_id)

    # ── Instance meta (no TTL) ────────────────────────────────────────────────

    async def update_instance_meta(
        self, tenant_id: str, instance_id: str, pools: list[str], agent_type_id: str
    ) -> None:
        """
        Persists static instance metadata with no TTL.
        Called on agent_ready — pools and agent_type_id do not change during the instance lifetime.
        """
        await self._redis.hset(
            _instance_meta_key(tenant_id, instance_id),
            mapping={"pools": json.dumps(pools), "agent_type_id": agent_type_id},
        )

    async def add_conversation(
        self, tenant_id: str, instance_id: str, conversation_id: str
    ) -> None:
        """
        Registers an active conversation on the instance.
        Called on agent_busy. SADD is atomic — no race condition.
        """
        await self._redis.sadd(
            _instance_conversations_key(tenant_id, instance_id), conversation_id
        )

    async def remove_conversation(
        self, tenant_id: str, instance_id: str, conversation_id: str
    ) -> None:
        """
        Removes a completed conversation from the instance.
        Called on agent_done. SREM is atomic — no race condition.
        """
        await self._redis.srem(
            _instance_conversations_key(tenant_id, instance_id), conversation_id
        )

    async def get_instance_meta(
        self, tenant_id: str, instance_id: str
    ) -> InstanceMeta | None:
        """
        Returns persistent instance metadata.
        Returns None if the instance was never registered via agent_ready.
        """
        meta_key  = _instance_meta_key(tenant_id, instance_id)
        conv_key  = _instance_conversations_key(tenant_id, instance_id)

        raw_meta  = await self._redis.hgetall(meta_key)
        if not raw_meta:
            return None

        # Decode bytes keys/values — hgetall returns bytes when decode_responses=False.
        # This ensures .get("pools") and .get("agent_type_id") work regardless of
        # the Redis client configuration.
        decoded_meta: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw_meta.items()
        }

        conversations_raw = await self._redis.smembers(conv_key)
        # Decode bytes from smembers — same reason as above.
        conversations = [
            v.decode() if isinstance(v, bytes) else v
            for v in conversations_raw
        ]

        return InstanceMeta(
            pools                = json.loads(decoded_meta.get("pools", "[]")),
            agent_type_id        = decoded_meta.get("agent_type_id", ""),
            active_conversations = conversations,
        )

    async def delete_instance_meta(
        self, tenant_id: str, instance_id: str
    ) -> None:
        """
        Removes instance metadata and its conversations set.
        Called by CrashDetector after recovering orphaned conversations.
        """
        await self._redis.delete(
            _instance_meta_key(tenant_id, instance_id),
            _instance_conversations_key(tenant_id, instance_id),
        )

    async def get_session_affinity(self, session_id: str) -> str | None:
        """
        Returns instance_id with affinity for the session (stateful agents).
        Spec 4.6: Routing Engine guarantees session affinity for stateful agents.
        """
        return await self._redis.get(_session_instance_key(session_id))

    async def set_session_affinity(
        self, session_id: str, instance_id: str, ttl_seconds: int = 86_400
    ) -> None:
        await self._redis.set(
            _session_instance_key(session_id), instance_id, ex=ttl_seconds
        )

    async def mark_busy(
        self, tenant_id: str, pool_id: str, instance_id: str
    ) -> None:
        """
        Increments current_sessions on the instance.

        Uses KEEPTTL so the original TTL (e.g. 24h for demo-seeded instances or 30s
        for live-registered agents) is preserved.  Production agents renew their TTL
        via subsequent agent_busy/agent_heartbeat events (which call set_instance).
        Without KEEPTTL, mark_busy would overwrite a 24h seed TTL with 30s — causing
        the instance to vanish before the next escalation could be routed to it.
        """
        key = _instance_key(tenant_id, instance_id)
        raw = await self._redis.get(key)
        if not raw:
            return
        data = json.loads(raw)
        if "status" in data and "state" not in data:
            data["state"] = data["status"]
        inst = AgentInstance.model_validate(data)
        inst.current_sessions += 1
        if inst.current_sessions >= inst.max_concurrent:
            inst.state = "busy"

        # Serialize and update in Redis — preserve the existing TTL
        out = inst.model_dump()
        out["status"] = out.pop("state")   # alias for mcp-server compat
        await self._redis.set(key, json.dumps(out), keepttl=True)

        # Sync pool membership: remove if at capacity or not ready
        pool_key = _pool_instances_key(tenant_id, pool_id)
        if inst.state != "ready" or inst.current_sessions >= inst.max_concurrent:
            await self._redis.srem(pool_key, instance_id)
        # (no sadd needed — the instance was already in the set before mark_busy)

    async def add_queued_contact(
        self,
        tenant_id:    str,
        pool_id:      str,
        session_id:   str,
        contact_data: dict,
        queued_at_ms: int,
        ttl:          int = 14_400,
    ) -> None:
        """
        Persist a queued contact.
        Sorted set score = queued_at_ms (lowest = oldest = served first for FIFO
        base, though queue_scorer may override with priority).
        Full event JSON is stored separately so it can be re-published verbatim
        to conversations.inbound when the contact is dequeued.
        """
        await self._redis.zadd(
            _queue_key(tenant_id, pool_id), {session_id: queued_at_ms}
        )
        await self._redis.set(
            _queue_contact_key(tenant_id, session_id),
            json.dumps(contact_data),
            ex=ttl,
        )

    async def remove_queued_contact(
        self, tenant_id: str, pool_id: str, session_id: str
    ) -> None:
        """Remove contact from sorted set and delete stored JSON."""
        await self._redis.zrem(_queue_key(tenant_id, pool_id), session_id)
        await self._redis.delete(_queue_contact_key(tenant_id, session_id))

    async def get_full_queued_contact(
        self, tenant_id: str, session_id: str
    ) -> dict | None:
        """
        Returns the full stored dict for a queued contact (used for re-routing).
        Includes all original ConversationInboundEvent fields plus queued_at_ms.
        """
        raw = await self._redis.get(_queue_contact_key(tenant_id, session_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def get_oldest_queue_wait_ms(
        self, tenant_id: str, pool_id: str
    ) -> int | None:
        """
        Returns the queued_at_ms timestamp of the oldest contact in queue.
        Used to compute sla_urgency = (now_ms - oldest_ms) / sla_target_ms.
        """
        members = await self._redis.zrange(
            _queue_key(tenant_id, pool_id), 0, 0, withscores=True
        )
        if not members:
            return None
        # ZRANGE score = queued_at_ms (lowest = oldest)
        _, oldest_score = members[0]
        return int(oldest_score)

    async def get_queued_contacts(
        self, tenant_id: str, pool_id: str, top_n: int = 10
    ) -> list[QueuedContact]:
        """Returns top_n contacts from queue by score (highest priority first).
        Uses ZREVRANGE for backwards compatibility with redis-py < 4.2."""
        members = await self._redis.zrevrange(
            _queue_key(tenant_id, pool_id), 0, top_n - 1
        )
        contacts: list[QueuedContact] = []
        for session_id in members:
            raw = await self._redis.get(_queue_contact_key(tenant_id, session_id))
            if not raw:
                continue
            try:
                contacts.append(QueuedContact.model_validate_json(raw))
            except Exception:
                continue
        return contacts

    async def get_available_count(self, tenant_id: str, pool_id: str) -> int:
        """Returns count of ready instances in the pool."""
        return await self._redis.scard(_pool_instances_key(tenant_id, pool_id))

    async def get_queue_length(self, tenant_id: str, pool_id: str) -> int:
        """Returns the number of contacts waiting in the pool queue."""
        return await self._redis.zcard(_queue_key(tenant_id, pool_id))

    async def write_pool_snapshot(
        self,
        tenant_id:     str,
        pool_id:       str,
        sla_target_ms: int,
        channel_types: list[str],
        snapshot_ttl:  int = 120,
    ) -> None:
        """
        Writes an operational pool snapshot to Redis after each routing event.
        TTL: 120s — refreshed on every route() or dequeue() call.
        Key: {tenant_id}:pool:{pool_id}:snapshot
        """
        available    = await self.get_available_count(tenant_id, pool_id)
        queue_length = await self.get_queue_length(tenant_id, pool_id)
        snapshot = {
            "pool_id":       pool_id,
            "tenant_id":     tenant_id,
            "available":     available,
            "queue_length":  queue_length,
            "sla_target_ms": sla_target_ms,
            "channel_types": channel_types,
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }
        await self._redis.set(
            _pool_snapshot_key(tenant_id, pool_id),
            json.dumps(snapshot),
            ex=snapshot_ttl,
        )

    async def get_pool_snapshot(
        self, tenant_id: str, pool_id: str
    ) -> dict | None:
        """Returns the most recent operational snapshot for a pool."""
        raw = await self._redis.get(_pool_snapshot_key(tenant_id, pool_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def get_agent_performance_score(
        self,
        tenant_id:    str,
        agent_type_id: str,
        default:      float = 0.5,
    ) -> float:
        """
        Arc 7d — Historical performance score for an agent type.

        Written by analytics-api performance_job every 5 minutes.
        Key:   {tenant_id}:agent_perf:{agent_type_id}
        Value: str(float) in [0.0, 1.0], TTL 6 h.

        Returns `default` (0.5 = neutral) when:
          - No data yet (new agent type, first 7 days of operation)
          - Redis read fails (transient error)
          - Score cannot be parsed

        Default of 0.5 is intentionally neutral — does not favour or penalise
        agents without sufficient data.
        """
        try:
            raw = await self._redis.get(_agent_perf_key(tenant_id, agent_type_id))
            if raw is not None:
                score = float(raw)
                # Clamp in case Redis was written by a different version
                return max(0.0, min(1.0, score))
        except Exception:
            pass
        return default


# ─────────────────────────────────────────────
# PoolRegistry — reads from Redis cache (never direct HTTP)
# ─────────────────────────────────────────────

class PoolRegistry:
    """
    Queries pool configurations exclusively via Redis cache.
    Cache populated by kafka_listener when processing agent.registry.events.
    Spec: "Never access PostgreSQL directly".
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis    = redis_client
        self._settings = get_settings()

    async def get_pool(
        self, tenant_id: str, pool_id: str
    ) -> PoolConfig | None:
        """
        Returns the configuration for a single, explicitly identified pool.
        Used when the inbound event already carries pool_id (entry point config
        or escalation target) — avoids scanning all tenant pools.
        """
        return await self._get_pool_config(tenant_id, pool_id)

    async def get_candidate_pools(
        self, tenant_id: str, channel: str
    ) -> list[PoolConfig]:
        """
        Returns candidate pools for the conversation.
        Filters: supported channel + Redis cache available.
        """
        pool_ids = await self._redis.smembers(_pool_set_key(tenant_id))
        if not pool_ids:
            return []

        pools: list[PoolConfig] = []
        for pool_id in pool_ids:
            config = await self._get_pool_config(tenant_id, pool_id)
            if config and channel in config.channel_types:
                pools.append(config)
        return pools

    async def _get_pool_config(
        self, tenant_id: str, pool_id: str
    ) -> PoolConfig | None:
        """Reads pool configuration from Redis cache."""
        raw = await self._redis.get(_pool_config_key(tenant_id, pool_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            # Coerce routing_expression from the Redis payload:
            #   dict  → RoutingExpression instance (normal case after first reconcile)
            #   None  → delete key so Pydantic uses default_factory=RoutingExpression
            #           (Agent Registry returns null when pool was registered without it)
            if "routing_expression" in data:
                if isinstance(data["routing_expression"], dict):
                    data["routing_expression"] = RoutingExpression(**data["routing_expression"])
                elif data["routing_expression"] is None:
                    del data["routing_expression"]
            return PoolConfig.model_validate(data)
        except Exception as exc:
            import logging as _log
            _log.getLogger("plughub.routing.registry").warning(
                "pool_config validation failed pool=%s tenant=%s exc=%s",
                pool_id, tenant_id, str(exc).replace("\n", " | "),
            )
            return None

    async def save_pool_config(self, config: PoolConfig) -> None:
        """
        Persists pool configuration to Redis.
        Called by kafka_listener on receiving agent.registry.events.
        """
        key  = _pool_config_key(config.tenant_id, config.pool_id)
        data = config.model_dump()
        await self._redis.set(
            key,
            json.dumps(data),
            ex=self._settings.pool_config_ttl_seconds,
        )
        # Register pool_id in the tenant set
        await self._redis.sadd(_pool_set_key(config.tenant_id), config.pool_id)

    async def get_queued_contacts(
        self, tenant_id: str, pool_id: str, top_n: int = 10
    ) -> list[QueuedContact]:
        """Returns top_n contacts from the pool queue (highest score first).
        Uses ZREVRANGE for backwards compatibility with redis-py < 4.2."""
        members = await self._redis.zrevrange(
            _queue_key(tenant_id, pool_id), 0, top_n - 1
        )
        contacts: list[QueuedContact] = []
        for session_id in members:
            raw = await self._redis.get(_queue_contact_key(tenant_id, session_id))
            if not raw:
                continue
            try:
                contacts.append(QueuedContact.model_validate_json(raw))
            except Exception:
                continue
        return contacts
