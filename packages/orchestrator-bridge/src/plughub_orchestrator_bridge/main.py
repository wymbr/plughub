"""
orchestrator_bridge.py
Bridges the Routing Engine output to agent activation.

Consumes two topics:

1. conversations.routed — routing decisions from the Routing Engine
   Reads the agent type from Agent Registry to determine activation:

   framework == "plughub-native"  → fetch skill flow → POST /execute on skill-flow-service
   framework == "human"           → publish conversation.assigned to Redis pub/sub
                                    so Agent Assist UI receives it via WebSocket
   framework == "external-mcp"   → LPUSH context_package to agent:queue:{instance_id}
                                    so the agent blocked in wait_for_assignment unblocks
   other frameworks               → logged as warning (LangGraph, CrewAI, etc. — NYI)

2. conversations.inbound (NormalizedInboundEvent from channel-gateway)
   If an active human agent session exists for that session_id, forwards
   customer messages to agent:events:{session_id} so the Agent Assist
   WebSocket delivers them in real time.

--- Agent Registry as single source of truth ---

The bridge does NOT maintain any list of agent types or AI/human flags locally.
Everything is derived from:

  GET /v1/agent-types/{agent_type_id}   (with x-tenant-id header)
    → framework, role, skills[]

  GET /v1/skills/{skill_id}             (with x-tenant-id header)
    → flow (JSON)

Fallback for dev: if Agent Registry is unreachable or returns 404 AND a
  SKILLS_DIR/{agent_type_id}.yaml file exists, the bridge treats the agent
  as plughub-native and loads the flow from YAML.  This allows running the
  demo before the registry is populated.

Environment variables (all optional, defaults shown):
    KAFKA_BROKERS          localhost:9092
    REDIS_URL              redis://localhost:6379
    SKILL_FLOW_URL         http://localhost:3400
    AGENT_REGISTRY_URL     http://localhost:3300
    SKILLS_DIR             <repo>/skill-flow-engine/skills   (dev fallback only)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import redis.asyncio as aioredis
import yaml
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .instance_bootstrap import InstanceBootstrap
from .registry_syncer import RegistrySyncer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("plughub.orchestrator-bridge")


# ── Config ────────────────────────────────────────────────────────────────────

KAFKA_BROKERS       = os.getenv("KAFKA_BROKERS",       "localhost:9092")
REDIS_URL           = os.getenv("REDIS_URL",            "redis://localhost:6379")
SKILL_FLOW_URL      = os.getenv("SKILL_FLOW_URL",       "http://localhost:3400")
AGENT_REGISTRY_URL  = os.getenv("AGENT_REGISTRY_URL",  "http://localhost:3300")

_default_skills_dir = str(Path(__file__).parent.parent.parent.parent / "skill-flow-engine" / "skills")
SKILLS_DIR          = os.getenv("SKILLS_DIR", _default_skills_dir)

# Path to the directory (or single YAML file) containing declarative pool and
# agent-type definitions. When set, the RegistrySyncer upserts all entities into
# the Agent Registry at startup, making external seed scripts unnecessary.
# Leave unset (or empty) to skip registry sync (e.g. in integration tests that
# pre-seed the DB via their own mechanism).
REGISTRY_CONFIG_DIR = os.getenv("REGISTRY_CONFIG_DIR", "")

# Comma-separated list of tenant IDs whose agent instances should be bootstrapped
# from the Agent Registry at startup. Billing is per configured instance — the
# Agent Registry is the source of truth, not the Redis seed.
BOOTSTRAP_TENANT_IDS: list[str] = [
    t.strip()
    for t in os.getenv("BOOTSTRAP_TENANT_IDS", "tenant_demo").split(",")
    if t.strip()
]

TOPIC_ROUTED            = "conversations.routed"
TOPIC_QUEUED            = "conversations.queued"
TOPIC_INBOUND           = "conversations.inbound"
TOPIC_EVENTS            = "conversations.events"
TOPIC_REGISTRY_CHANGED  = "registry.changed"
TOPIC_CONFIG_CHANGED    = "config.changed"
TOPIC_PARTICIPANTS      = "conversations.participants"
GROUP_ID                = "orchestrator-bridge"

# Namespaces whose changes directly affect how many agent instances should exist.
# Any change to these namespaces triggers a full reconciliation.
_BOOTSTRAP_NAMESPACES: frozenset[str] = frozenset({"quota"})

# Namespaces that are read at runtime via ConfigStore cache — no bootstrap action
# needed; the cache TTL (60s) handles propagation naturally. Listed here only for
# documentation purposes.
_RUNTIME_NAMESPACES: frozenset[str] = frozenset({
    "routing", "session", "masking", "webchat", "sentiment", "consumer", "dashboard",
})


# ── Agent type resolution ─────────────────────────────────────────────────────
# Cached in memory to avoid repeated Registry calls for the same agent type.
# Cache is not invalidated during the process lifetime — acceptable because
# agent type registrations are immutable (new version = new agent_type_id).

_agent_type_cache: dict[str, dict] = {}   # agent_type_id → agent type response body
_skill_flow_cache: dict[str, dict] = {}   # skill_id → flow dict

# Kafka producer — initialised in run(), used by fire_pool_hooks().
# None until run() starts; hooks silently skip if producer not ready.
_kafka_producer: AIOKafkaProducer | None = None


async def get_agent_type(
    http: aiohttp.ClientSession,
    tenant_id: str,
    agent_type_id: str,
) -> dict | None:
    """
    Fetch agent type metadata from the Agent Registry.
    Returns the response body dict, or None if not found / unreachable.
    Caches successful lookups in memory.
    """
    cache_key = f"{tenant_id}:{agent_type_id}"
    if cache_key in _agent_type_cache:
        return _agent_type_cache[cache_key]

    url = f"{AGENT_REGISTRY_URL}/v1/agent-types/{agent_type_id}"
    headers = {"x-tenant-id": tenant_id}
    try:
        async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                body = await resp.json()
                _agent_type_cache[cache_key] = body
                return body
            if resp.status == 404:
                logger.debug("Agent type not found in registry: tenant=%s agent=%s", tenant_id, agent_type_id)
            else:
                logger.warning(
                    "Agent Registry returned HTTP %d for agent-type=%s", resp.status, agent_type_id
                )
    except Exception as exc:
        logger.warning("Agent Registry unreachable (%s): %s", url, exc)

    return None


async def get_pool_config(
    http: aiohttp.ClientSession,
    tenant_id: str,
    pool_id: str,
) -> dict | None:
    """
    Fetch pool configuration from Agent Registry.
    Not cached — pool config may change at runtime (queue_config can be added/removed).
    """
    url     = f"{AGENT_REGISTRY_URL}/v1/pools/{pool_id}"
    headers = {"x-tenant-id": tenant_id}
    try:
        async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                return await resp.json()
            if resp.status == 404:
                logger.debug("Pool not found in registry: tenant=%s pool=%s", tenant_id, pool_id)
            else:
                logger.warning(
                    "Agent Registry returned HTTP %d for pool=%s", resp.status, pool_id
                )
    except Exception as exc:
        logger.warning("Agent Registry unreachable (pool config %s): %s", pool_id, exc)
    return None


async def get_skill_flow(
    http: aiohttp.ClientSession,
    tenant_id: str,
    skill_id: str,
) -> dict | None:
    """
    Fetch a skill's flow definition from the Agent Registry.
    Returns the flow dict, or None if not found / no flow defined.
    Caches successful lookups in memory.
    """
    if skill_id in _skill_flow_cache:
        return _skill_flow_cache[skill_id]

    url = f"{AGENT_REGISTRY_URL}/v1/skills/{skill_id}"
    headers = {"x-tenant-id": tenant_id}
    try:
        async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                body = await resp.json()
                flow = body.get("flow")
                if flow:
                    _skill_flow_cache[skill_id] = flow
                    return flow
                logger.warning(
                    "Skill %s has no flow (classification.type must be 'orchestrator')", skill_id
                )
            elif resp.status == 404:
                logger.warning("Skill %s not found in Agent Registry", skill_id)
            else:
                logger.warning("Agent Registry returned HTTP %d for skill=%s", resp.status, skill_id)
    except Exception as exc:
        logger.warning("Agent Registry unreachable (%s): %s", url, exc)

    return None


def _load_yaml_fallback(agent_type_id: str) -> dict | None:
    """
    Dev fallback: load SKILLS_DIR/{agent_type_id}.yaml when Agent Registry
    is unavailable or the skill is not yet registered.
    Returns None if the file doesn't exist.
    """
    path = Path(SKILLS_DIR) / f"{agent_type_id}.yaml"
    if not path.exists():
        return None
    try:
        with path.open() as f:
            flow = yaml.safe_load(f)
        logger.info("YAML fallback loaded: %s", path)
        return flow
    except Exception as exc:
        logger.error("Failed to parse YAML skill %s: %s", path, exc)
        return None


async def resolve_flow_for_agent(
    http: aiohttp.ClientSession,
    tenant_id: str,
    agent_type_id: str,
    skills: list[dict],
) -> tuple[str, dict] | None:
    """
    Given the skills[] list from an agent type, resolve the flow to execute.
    Returns (skill_id, flow_dict) or None if no executable flow is found.

    Resolution order:
      1. First skill in skills[] with a flow in Agent Registry
      2. YAML fallback in SKILLS_DIR
    """
    # Try each skill in declaration order
    for skill_ref in skills:
        skill_id = skill_ref.get("skill_id", "")
        if not skill_id:
            continue
        flow = await get_skill_flow(http, tenant_id, skill_id)
        if flow:
            return skill_id, flow

    # No skill had a flow in the registry — try YAML fallback
    flow = _load_yaml_fallback(agent_type_id)
    if flow:
        return agent_type_id, flow  # use agent_type_id as skill_id in fallback

    return None


# ── plughub-native activation: call skill-flow-service ───────────────────────

async def activate_native_agent(
    http: aiohttp.ClientSession,
    redis_client: aioredis.Redis,
    session_id: str,
    customer_id: str,
    agent_type_id: str,
    tenant_id: str,
    skills: list[dict],
    instance_id: str = "",
    conference_id: str = "",
    extra_context: dict | None = None,
    segment_id: str = "",
) -> dict:
    """
    Activate a plughub-native orchestrator agent by calling skill-flow-service.
    Returns the skill-flow-service response body (or {} on error).

    instance_id is passed to the engine so it is stored in the execution lock
    ({tenant_id}:pipeline:{session_id}:running = instance_id).  The crash
    detector checks this key before re-queueing a conversation: if the key
    exists the engine is still alive (no false-positive re-queue).

    conference_id, when non-empty, is included in session_context so the AI
    agent knows it is operating under human supervision in a conference.
    """
    resolved = await resolve_flow_for_agent(http, tenant_id, agent_type_id, skills)
    if resolved is None:
        logger.error(
            "No executable flow found for agent_type_id=%s (tenant=%s) — "
            "register a skill with classification.type='orchestrator' in the Agent Registry, "
            "or add %s/%s.yaml as a dev fallback",
            agent_type_id, tenant_id, SKILLS_DIR, agent_type_id,
        )
        return {}

    skill_id, flow = resolved

    # Enrich session_context from Redis (written by channel-gateway on connect)
    contact_id = customer_id
    channel    = "chat"
    try:
        raw = await redis_client.get(f"session:{session_id}:meta")
        if raw:
            meta       = json.loads(raw)
            contact_id = meta.get("contact_id", customer_id)
            channel    = meta.get("channel", "chat")
    except Exception:
        pass

    session_context: dict = {
        "contact_id":  contact_id,
        "channel":     channel,
        "tenant_id":   tenant_id,
        "agent_type":  agent_type_id,
        "session_id":  session_id,   # exposed so invoke step inputs can reference $.session.session_id
    }
    if conference_id:
        session_context["conference_id"]  = conference_id
        session_context["is_conference"]  = True
    if extra_context:
        session_context.update(extra_context)

    payload: dict = {
        "tenant_id":       tenant_id,
        "session_id":      session_id,
        "customer_id":     customer_id,
        "skill_id":        skill_id,
        "flow":            flow,
        "instance_id":     instance_id,   # stored in execution lock by the engine
        "session_context": session_context,
    }
    # segment_id for segment-scoped ContextStore writes (scope: segment in YAML).
    # Allows parallel agents (NPS + wrap-up) to isolate their data per participation.
    if segment_id:
        payload["segment_id"] = segment_id
    # Conference specialists (hook agents) share the same session_id for
    # message delivery, but each needs its own pipeline_state and execution
    # lock.  Use the agent's unique segment_id — each participant in the
    # session has a distinct segment, so two hook agents running in parallel
    # on the same session never collide.
    if segment_id:
        payload["pipeline_session_id"] = f"{session_id}--seg--{segment_id[:8]}"
    elif conference_id:
        payload["pipeline_session_id"] = f"{session_id}--conf--{conference_id[:8]}"

    url = f"{SKILL_FLOW_URL}/execute"
    try:
        # No HTTP timeout — the flow may contain menu steps with timeout_s = 0
        # (indefinite wait), so we must not impose an upper bound here.
        # The skill-flow-engine itself is responsible for unblocking via BLPOP
        # when the customer disconnects (session:closed LPUSH) or when a finite
        # timeout_s elapses. The execution lock on the skill-flow side prevents
        # two instances from advancing the pipeline_state simultaneously.
        async with http.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=None)) as resp:
            body = await resp.json()
            if resp.status == 200:
                logger.info(
                    "Native agent executed: session=%s skill=%s outcome=%s",
                    session_id, skill_id, body.get("outcome"),
                )
                return body
            elif resp.status == 412:
                logger.warning(
                    "Skill already running: session=%s active_job=%s",
                    session_id, body.get("active_job_id"),
                )
            else:
                logger.error(
                    "Skill execute failed: session=%s status=%d body=%s",
                    session_id, resp.status, body,
                )
    except Exception as exc:
        logger.error("HTTP error calling skill-flow-service: session=%s — %s", session_id, exc)
    return {}


# ── human activation: notify Agent Assist UI via Redis pub/sub ────────────────

async def activate_human_agent(
    redis_client: aioredis.Redis,
    session_id: str,
    pool_id: str,
    tenant_id: str,
    routing_result: dict,
) -> None:
    """
    Notify the human agent's Agent Assist UI that a conversation was assigned.

    Publishes conversation.assigned to pool:events:{pool_id} — the Agent Assist UI
    subscribes to this channel before knowing the session_id (it connects with only
    pool= in the URL). The MCP Server dynamically adds an agent:events:{session_id}
    subscription upon receiving this event, so subsequent customer messages also
    reach the agent WebSocket without a reconnect.

    Also marks the session as human-handled so incoming customer messages
    from conversations.inbound are forwarded to agent:events:{session_id}.

    Saves a snapshot of the agent instance state so it can be fully restored
    when contact_closed arrives (routing engine TTL is 30s; no heartbeat in demo).
    """
    instance_id = routing_result.get("instance_id", "")

    try:
        # ── Mark session as having at least one human agent (fast-lookup flag) ──
        await redis_client.setex(f"session:{session_id}:human_agent", 14400, "1")
        # ── Track this specific instance in a SET for conference support ────────
        # Allows multiple human agents to share the same session_id.
        # process_contact_event uses SREM to remove each agent on agent_done and
        # clears the human_agent flag only when the SET becomes empty.
        if instance_id:
            await redis_client.sadd(f"session:{session_id}:human_agents", instance_id)
            await redis_client.expire(f"session:{session_id}:human_agents", 14400)
    except Exception as exc:
        logger.error(
            "Failed to set human_agent flag — bidirectional messaging will not work: "
            "session=%s — %s", session_id, exc
        )

    # ── Save instance snapshot for restore on contact_closed ─────────────────
    # The routing engine marks the instance busy (TTL=30s). Without heartbeats
    # the key expires before the session ends. We snapshot now and restore later.
    # Snapshot is keyed by instance_id so conference does not overwrite each other.
    if instance_id and tenant_id:
        try:
            raw = await redis_client.get(f"{tenant_id}:instance:{instance_id}")
            if raw:
                await redis_client.setex(
                    f"session:{session_id}:routing:{instance_id}",
                    14400,
                    json.dumps({
                        "tenant_id":   tenant_id,
                        "instance_id": instance_id,
                        "pool_id":     pool_id,
                        "snapshot":    json.loads(raw),
                    }),
                )
                logger.debug(
                    "Instance snapshot saved: session=%s instance=%s", session_id, instance_id
                )
        except Exception as exc:
            logger.warning("Could not save instance snapshot: session=%s — %s", session_id, exc)

    # ── Store instance_id in session meta so REST /agent_done can include it ──
    # The Agent Assist UI does not pass instance_id in the agent_done request,
    # so the mcp-server reads it from here. In true conference the last-writer
    # wins — acceptable since the REST endpoint is only used by human agents.
    if instance_id:
        try:
            raw_meta = await redis_client.get(f"session:{session_id}:meta")
            if raw_meta:
                meta = json.loads(raw_meta)
                meta["instance_id"] = instance_id
                await redis_client.setex(f"session:{session_id}:meta", 14400, json.dumps(meta))
        except Exception as exc:
            logger.warning("Could not update session meta with instance_id: session=%s — %s", session_id, exc)

    event = {
        "type":          "conversation.assigned",
        "session_id":    session_id,
        "pool_id":       pool_id,
        "instance_id":   instance_id,
        "agent_type_id": routing_result.get("agent_type_id"),
        "assigned_at":   datetime.now(timezone.utc).isoformat(),
    }
    event_json = json.dumps(event)
    try:
        await redis_client.publish(f"pool:events:{pool_id}", event_json)
        logger.info("Human agent notified: session=%s pool=%s", session_id, pool_id)
    except Exception as exc:
        logger.error("Redis publish error: session=%s — %s", session_id, exc)

    # Also persist the assignment so agents that connect AFTER the pub/sub event
    # (e.g. after a server restart) can still receive it.  TTL=300s (5 minutes).
    # Cleared on contact_closed so agents reconnecting to a closed session don't
    # get a stale assignment.
    try:
        await redis_client.setex(
            f"pool:pending_assignment:{pool_id}",
            300,
            event_json,
        )
        logger.debug("Pending assignment stored: pool=%s session=%s", pool_id, session_id)
    except Exception as exc:
        logger.warning("Could not store pending assignment: pool=%s — %s", pool_id, exc)

    # ── Fase C: record join time + publish participant_joined to Kafka ─────────────
    # joined_at is stored in Redis so process_contact_event can compute duration.
    _joined_iso = datetime.now(timezone.utc).isoformat()
    # ── Arc 5: generate segment_id for this participation window ─────────────────
    _seg_id = str(uuid.uuid4())
    _seq_idx = 0
    if instance_id:
        try:
            await redis_client.setex(
                f"session:{session_id}:participant_joined_at:{instance_id}",
                14400,
                _joined_iso,
            )
        except Exception:
            pass
        try:
            # Increment sequence counter (primary segments only; 0-indexed)
            _seq_raw = await redis_client.incr(f"session:{session_id}:segment_seq")
            _seq_idx = int(_seq_raw) - 1
            await redis_client.expire(f"session:{session_id}:segment_seq", 14400)
            # Store segment_id keyed by instance_id for retrieval on participant_left
            await redis_client.setex(
                f"session:{session_id}:segment:{instance_id}",
                14400,
                _seg_id,
            )
            # Store as current primary segment for conference specialists
            await redis_client.setex(
                f"session:{session_id}:primary_segment",
                14400,
                _seg_id,
            )
        except Exception:
            pass
    asyncio.create_task(_publish_participant_event(
        session_id=session_id,
        tenant_id=tenant_id,
        participant_id=instance_id,
        pool_id=pool_id,
        agent_type_id=routing_result.get("agent_type_id") or "",
        event_type="participant_joined",
        agent_type="human",
        role="primary",
        segment_id=_seg_id,
        sequence_index=_seq_idx,
        joined_at=_joined_iso,
    ))


# ── Pre-hook ContextStore writes ──────────────────────────────────────────────

async def _write_pre_hook_context(
    redis_client: aioredis.Redis,
    tenant_id:    str,
    session_id:   str,
    close_origin: str,
    human_instance_id: str | None = None,
) -> None:
    """
    Escreve campos no ContextStore que os hook agents precisam ANTES de
    executar.  Chamado imediatamente antes de fire_pool_hooks("on_human_end").

    Campos escritos:
      session.close_origin              — "agent_closed" ou "client_disconnect"
      session.customer_participant_id   — lido de session:{id}:customer_participant_id
                                          (gerado pelo channel-gateway no handshake)
      session.human_agent_participant_id — instance_id do agente humano que saiu;
                                           usado pelo wrap-up para visibility array
    """
    ctx_key = f"{tenant_id}:ctx:{session_id}"
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        # 1. close_origin — permite que o agente NPS saiba se o cliente está ativo
        entry_origin = json.dumps({
            "value":      close_origin,
            "confidence": 1.0,
            "source":     "bridge:pre_hook",
            "visibility": "agents_only",
            "updated_at": now_iso,
        })
        await redis_client.hset(ctx_key, "session.close_origin", entry_origin)

        # 2. customer_participant_id — o agente NPS usa para montar o array de
        #    visibility [customer_participant_id] das suas mensagens.
        cust_pid = await redis_client.get(
            f"session:{session_id}:customer_participant_id"
        )
        if cust_pid:
            pid_str = cust_pid if isinstance(cust_pid, str) else cust_pid.decode()
            entry_pid = json.dumps({
                "value":      pid_str,
                "confidence": 1.0,
                "source":     "bridge:pre_hook",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })
            await redis_client.hset(ctx_key, "session.customer_participant_id", entry_pid)

        # 3. human_agent_participant_id — o agente de wrap-up usa para montar o
        #    array de visibility [human_instance_id] das suas mensagens, garantindo
        #    que apenas o agente humano que encerrou veja o wrap-up (e não
        #    supervisores ou outros participantes da conferência).
        if human_instance_id:
            entry_human = json.dumps({
                "value":      human_instance_id,
                "confidence": 1.0,
                "source":     "bridge:pre_hook",
                "visibility": "agents_only",
                "updated_at": now_iso,
            })
            await redis_client.hset(
                ctx_key, "session.human_agent_participant_id", entry_human,
            )

        await redis_client.expire(ctx_key, 14400)
        logger.info(
            "pre_hook context written: session=%s close_origin=%s cust_pid=%s human_pid=%s",
            session_id, close_origin, bool(cust_pid), human_instance_id or "none",
        )
    except Exception as exc:
        logger.warning(
            "pre_hook context write failed: session=%s — %s (non-fatal)", session_id, exc,
        )


# ── Pool lifecycle hooks ──────────────────────────────────────────────────────

async def fire_pool_hooks(
    http:         aiohttp.ClientSession,
    redis_client: aioredis.Redis,
    session_id:   str,
    pool_id:      str,
    tenant_id:    str,
    customer_id:  str,
    hook_type:    str,
) -> None:
    """
    Dispatch pool lifecycle hooks defined in pool.hooks[hook_type].

    For each entry { pool } in the hook list, publishes a synthetic
    ConversationInboundEvent to conversations.inbound with conference_id set.
    The routing engine treats this as a conference specialist invitation:
      → allocates an instance from the target pool
      → publishes conversations.routed with conference_id
      → bridge activates the specialist as a conference participant

    This reuses 100% of the existing @mention / conference routing path —
    no new routing logic is needed.

    Supported hook_type values:
      on_human_start  — wired (Fase A): fires after activate_human_agent()
      on_human_end    — ✅ Fase B: fires when last human calls agent_done
      post_human      — ✅ Fase C: fires after all on_human_end agents complete

    Errors are logged but never raised — hook failure never blocks the session.
    """
    global _kafka_producer
    if _kafka_producer is None:
        logger.warning(
            "fire_pool_hooks: Kafka producer not ready — skipping %s hooks for pool=%s session=%s",
            hook_type, pool_id, session_id,
        )
        return

    pool_config = await get_pool_config(http, tenant_id, pool_id)
    if not pool_config:
        return

    hooks      = pool_config.get("hooks") or {}
    hook_list  = hooks.get(hook_type, [])

    if not hook_list:
        logger.debug(
            "fire_pool_hooks: no %s hooks configured for pool=%s", hook_type, pool_id,
        )
        return

    # Resolve channel from session meta so the specialist matches the contact's channel.
    channel = "webchat"
    try:
        raw_meta = await redis_client.get(f"session:{session_id}:meta")
        if raw_meta:
            channel = json.loads(raw_meta).get("channel", "webchat") or "webchat"
    except Exception:
        pass

    # For on_human_end and post_human hooks: track completion so the bridge knows when ALL hook
    # agents have finished and can then trigger the full contact close.
    # Counter key: session:{id}:hook_pending:{hook_type}   (TTL 4h)
    # Per-conference key: session:{id}:hook_conf:{conference_id}  (TTL 4h)
    # When process_routed detects a conference agent completing that has a hook_conf
    # key, it decrements the counter. When it hits 0 → _trigger_contact_close() (for post_human)
    # or checks for post_human hooks (for on_human_end).
    if hook_type in ("on_human_end", "post_human") and hook_list:
        try:
            await redis_client.setex(
                f"session:{session_id}:hook_pending:{hook_type}",
                14400,
                str(len(hook_list)),
            )
        except Exception as exc:
            logger.warning(
                "fire_pool_hooks: could not set pending counter: session=%s — %s",
                session_id, exc,
            )

    for entry in hook_list:
        target_pool = entry.get("pool") if isinstance(entry, dict) else None
        if not target_pool:
            logger.warning(
                "fire_pool_hooks: hook entry missing 'pool' field — skipping: %s", entry,
            )
            continue

        conference_id = str(uuid.uuid4())

        # ConversationInboundEvent — routing engine picks up on pool_id + conference_id.
        # pool_id routes to the specialist pool; conference_id marks it as a specialist
        # invite so process_routed skips the dedup guard and activates as conference.
        _now_iso = datetime.now(timezone.utc).isoformat()
        event = {
            "event":         "conversations.inbound",
            "type":          "conversations.inbound",
            "session_id":    session_id,
            "contact_id":    customer_id,
            "customer_id":   customer_id,
            "tenant_id":     tenant_id,
            "channel":       channel,
            "pool_id":       target_pool,
            "conference_id": conference_id,
            # Required by ConversationInboundEvent (Pydantic model in routing-engine).
            # Without this field, the routing engine rejects the event as "unrecognised".
            "started_at":    _now_iso,
            # Metadata for observability — not processed by routing engine.
            "hook_type":     hook_type,
            "origin_pool":   pool_id,
            "timestamp":     _now_iso,
        }

        try:
            await _kafka_producer.send_and_wait(
                TOPIC_INBOUND,
                json.dumps(event).encode("utf-8"),
            )
            logger.info(
                "Pool hook fired: hook=%s origin_pool=%s → target_pool=%s "
                "session=%s conference=%s",
                hook_type, pool_id, target_pool, session_id, conference_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to fire pool hook: hook=%s target_pool=%s session=%s — %s",
                hook_type, target_pool, session_id, exc,
            )
            continue

        # Mark this conference_id as hook-spawned so process_routed can detect
        # when the hook agent completes and decrement the pending counter.
        if hook_type in ("on_human_end", "post_human"):
            try:
                await redis_client.setex(
                    f"session:{session_id}:hook_conf:{conference_id}",
                    14400,
                    f"{hook_type}:{target_pool}",
                )
            except Exception as exc:
                logger.warning(
                    "fire_pool_hooks: could not mark hook conference: session=%s conf=%s — %s",
                    session_id, conference_id, exc,
                )


# ── Hook timeout guard — safety net when hook agents never start/complete ────

_HOOK_TIMEOUT_S = 180  # seconds to wait before forcing contact close

async def _hook_timeout_guard(
    redis_client: aioredis.Redis,
    session_id:   str,
    hook_type:    str,
) -> None:
    """
    Safety net for on_human_end / post_human hook completion tracking.

    If the hook agents don't complete within _HOOK_TIMEOUT_S seconds (e.g. because
    the target pool has no running instances and the routing engine queues the request
    indefinitely), this guard force-closes the contact so the customer WebSocket is
    never left open permanently.

    Called via asyncio.create_task() immediately after fire_pool_hooks().
    """
    await asyncio.sleep(_HOOK_TIMEOUT_S)
    pending_key = f"session:{session_id}:hook_pending:{hook_type}"
    try:
        still_pending = await redis_client.exists(pending_key)
        if still_pending:
            logger.warning(
                "_hook_timeout_guard: %s hooks did not complete within %ds — "
                "force-closing contact: session=%s",
                hook_type, _HOOK_TIMEOUT_S, session_id,
            )
            # Delete the pending key to prevent double-close if a late hook completes.
            await redis_client.delete(pending_key)
            await _trigger_contact_close(redis_client, session_id)
        else:
            logger.debug(
                "_hook_timeout_guard: %s hooks completed normally before timeout: session=%s",
                hook_type, session_id,
            )
    except Exception as exc:
        logger.warning(
            "_hook_timeout_guard: error checking pending key: session=%s — %s",
            session_id, exc,
        )


# ── Contact close trigger — used by hook completion and no-hook fallback ─────

async def _trigger_contact_close(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> None:
    """
    Publish the two events that close a contact from the bridge side:

    1. conversations.outbound  session.closed  → channel-gateway closes customer WS
    2. conversations.events    contact_closed  → process_contact_event does full cleanup

    reason "agent_done" → customer_side=True in process_contact_event, which:
      - signals BLPOP / XADD session:closed
      - notifies any remaining human agents
      - restores all AI instances

    Called when either:
    - The last on_human_end hook agent completes (counter → 0)
    - A pool has no on_human_end hooks (immediate close)
    """
    global _kafka_producer
    if _kafka_producer is None:
        logger.warning(
            "_trigger_contact_close: Kafka producer not ready — session=%s", session_id,
        )
        return

    # Idempotency guard: only the first caller wins.  SET NX with TTL 7d.
    # This prevents double-close when both a hook completion and the timeout guard
    # race to call _trigger_contact_close for the same session.
    try:
        acquired = await redis_client.set(
            f"session:{session_id}:close_fired",
            "1",
            nx=True,
            ex=604800,
        )
        if not acquired:
            logger.debug(
                "_trigger_contact_close: close already fired for session=%s — skipping",
                session_id,
            )
            return
    except Exception as exc:
        logger.warning(
            "_trigger_contact_close: could not acquire close_fired guard: session=%s — %s "
            "(proceeding anyway)",
            session_id, exc,
        )
        # Non-fatal — proceed even if Redis SET failed; double-close is benign
        # (channel-gateway handles duplicate session.closed gracefully).

    # Resolve contact_id, channel, and tenant_id from session meta.
    contact_id = session_id
    channel    = "webchat"
    tenant_id  = ""
    try:
        raw_meta = await redis_client.get(f"session:{session_id}:meta")
        if raw_meta:
            meta       = json.loads(raw_meta)
            contact_id = meta.get("contact_id", session_id) or session_id
            channel    = meta.get("channel", "webchat") or "webchat"
            tenant_id  = meta.get("tenant_id", "") or meta.get("tenant", "")
    except Exception as exc:
        logger.warning(
            "_trigger_contact_close: could not read session meta: session=%s — %s",
            session_id, exc,
        )

    # 0. Notify the agent WebSocket that the session is now truly closed.
    #    This is deferred from agent_done (which publishes "session.agent_done")
    #    so hook agents (wrapup, NPS) can interact with the human agent first.
    try:
        await redis_client.publish(
            f"agent:events:{session_id}",
            json.dumps({
                "type":       "session.closed",
                "session_id": session_id,
                "reason":     "agent_done",
            }),
        )
        logger.info(
            "_trigger_contact_close: published session.closed to agent:events: session=%s",
            session_id,
        )
    except Exception as exc:
        logger.warning(
            "_trigger_contact_close: could not publish agent session.closed: session=%s — %s",
            session_id, exc,
        )

    try:
        # 1. Close the customer WebSocket.
        await _kafka_producer.send_and_wait(
            "conversations.outbound",
            json.dumps({
                "type":       "session.closed",
                "contact_id": contact_id,
                "session_id": session_id,
                "channel":    channel,
                "reason":     "flow_complete",
            }).encode("utf-8"),
        )
        logger.info(
            "_trigger_contact_close: published conversations.outbound session.closed: "
            "session=%s contact_id=%s channel=%s",
            session_id, contact_id, channel,
        )
    except Exception as exc:
        logger.error(
            "_trigger_contact_close: failed to publish outbound close: session=%s — %s",
            session_id, exc,
        )

    try:
        # 2. Trigger full bridge cleanup via existing process_contact_event path.
        # reason "agent_done" → customer_side=True → LPUSH/XADD + instance restore.
        await _kafka_producer.send_and_wait(
            TOPIC_EVENTS,
            json.dumps({
                "event_type": "contact_closed",
                "session_id": session_id,
                "tenant_id":  tenant_id,
                "reason":     "agent_done",
            }).encode("utf-8"),
        )
        logger.info(
            "_trigger_contact_close: published conversations.events contact_closed: session=%s",
            session_id,
        )
    except Exception as exc:
        logger.error(
            "_trigger_contact_close: failed to publish contact_closed: session=%s — %s",
            session_id, exc,
        )


# ── Participant event publishing — Fase C (analytics) ─────────────────────────

async def _publish_participant_event(
    session_id:     str,
    tenant_id:      str,
    participant_id: str,
    pool_id:        str,
    agent_type_id:  str,
    event_type:     str,        # "participant_joined" | "participant_left"
    agent_type:     str,        # "human" | "native" | "external"
    role:           str,        # "primary" | "specialist"
    segment_id:     str = "",   # Arc 5: ContactSegment UUID
    conference_id:  str = "",
    joined_at:      str = "",
    duration_ms:    int | None = None,
    sequence_index: int = 0,
    parent_segment_id: str = "",
    outcome:        str | None = None,
    close_reason:   str | None = None,
    handoff_reason: str | None = None,
    issue_status:   str | None = None,
) -> None:
    """
    Fire-and-forget publish to conversations.participants Kafka topic.
    Consumed by analytics-api → participation_intervals + segments ClickHouse tables.
    Never raises — failures are logged at DEBUG level only.

    Arc 5: segment_id, sequence_index, parent_segment_id added for ContactSegment model.
    """
    global _kafka_producer
    if _kafka_producer is None:
        return
    event: dict = {
        "event_id":       str(uuid.uuid4()),
        "type":           event_type,
        "session_id":     session_id,
        "tenant_id":      tenant_id,
        "segment_id":     segment_id or str(uuid.uuid4()),  # fallback if not provided
        "participant_id": participant_id,
        "pool_id":        pool_id,
        "agent_type_id":  agent_type_id,
        "role":           role,
        "agent_type":     agent_type,
        "sequence_index": sequence_index,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }
    if conference_id:
        event["conference_id"] = conference_id
    if parent_segment_id:
        event["parent_segment_id"] = parent_segment_id
    if joined_at:
        event["joined_at"] = joined_at
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    if outcome is not None:
        event["outcome"] = outcome
    if close_reason is not None:
        event["close_reason"] = close_reason
    if handoff_reason is not None:
        event["handoff_reason"] = handoff_reason
    if issue_status is not None:
        event["issue_status"] = issue_status
    try:
        await _kafka_producer.send_and_wait(
            TOPIC_PARTICIPANTS,
            json.dumps(event).encode("utf-8"),
        )
        logger.debug(
            "Participant event: %s session=%s participant=%s segment=%s",
            event_type, session_id, participant_id, event["segment_id"],
        )
    except Exception as exc:
        logger.debug("Could not publish participant event: %s — %s", event_type, exc)


# ── external-mcp activation: LPUSH context_package → agent:queue ─────────────

async def activate_external_mcp_agent(
    redis_client: aioredis.Redis,
    session_id: str,
    pool_id: str,
    tenant_id: str,
    customer_id: str,
    agent_type_id: str,
    routing_result: dict,
) -> None:
    """
    Activate an external-mcp agent by pushing a context_package to its queue.

    The external agent is already running and blocked in wait_for_assignment
    (BLPOP on agent:queue:{instance_id}).  This LPUSH unblocks it so it can
    start handling the conversation.

    The agent manages its own lifecycle and calls agent_done when finished.
    The bridge tracks the instance in session:{session_id}:ai_agents so that
    the existing contact_closed handler restores it when the contact ends.

    Conference fields (conference_id, channel_identity, participant_id, is_conference)
    are included when the routing was triggered by agent_join_conference.
    The agent uses is_conference to adapt its behaviour (specialist vs primary).

    Spec: 4.6k — external-mcp framework branch.
    """
    import uuid as _uuid

    instance_id      = routing_result.get("instance_id", "")
    conference_id    = routing_result.get("conference_id") or ""
    channel_identity = routing_result.get("channel_identity")  # dict | None
    queue_key        = f"{tenant_id}:agent:queue:{instance_id}"

    # Ler wait_key da instância — gravada pelo mcp-server no wait_for_assignment.
    # Incluir no context_package para que wait_for_assignment possa rejeitar itens
    # obsoletos (de reinícios anteriores) que têm wait_key diferente da atual.
    wait_key = ""
    if instance_id:
        try:
            raw_wait = await redis_client.hget(
                f"{tenant_id}:agent:instance:{instance_id}", "wait_key"
            )
            wait_key = raw_wait or ""
        except Exception:
            pass

    context_package: dict = {
        "session_id":    session_id,
        "contact_id":    customer_id,
        "customer_id":   customer_id,
        "tenant_id":     tenant_id,
        "agent_type_id": agent_type_id,
        "instance_id":   instance_id,
        "pool_id":       pool_id,
        "assigned_at":   datetime.now(timezone.utc).isoformat(),
        # wait_key: nonce de ciclo gerado pelo mcp-server no wait_for_assignment.
        # wait_for_assignment rejeita itens cujo wait_key != wait_key atual da
        # instância — elimina context_packages obsoletos de reinícios anteriores
        # sem depender de cleanup externo.
        "wait_key":      wait_key,
    }

    # ── Conferência — enriquecer context_package ──────────────────────────────
    # O agente externo usa is_conference para saber que está como especialista
    # convidado, não como atendente principal. conference_id é necessário para:
    #   - wait_for_message: offset 0 no consumer group (lê histórico desde o join)
    #   - send_message: inclui conference_id no evento Kafka (labeling + mirror)
    #   - agent_done: bridge publica conference.agent_completed sem fechar sessão
    if conference_id:
        participant_id = str(_uuid.uuid4())
        context_package.update({
            "is_conference":    True,
            "conference_id":    conference_id,
            "participant_id":   participant_id,
            "channel_identity": channel_identity or {"text": "Assistente"},
        })
        logger.info(
            "Conference context_package: session=%s conference=%s participant=%s identity=%s",
            session_id, conference_id, participant_id,
            (channel_identity or {}).get("text", "Assistente"),
        )

    context_package_json = json.dumps(context_package)
    try:
        await redis_client.lpush(queue_key, context_package_json)
        logger.info(
            "External-MCP agent notified: session=%s instance=%s queue=%s",
            session_id, instance_id, queue_key,
        )
    except Exception as exc:
        logger.error(
            "Failed to push context_package to external-mcp queue: session=%s — %s",
            session_id, exc,
        )
        return

    # Track instance in ai_agents SET so contact_closed restores it.
    # Uses the same cleanup path as plughub-native agents.
    if instance_id:
        try:
            await redis_client.sadd(f"session:{session_id}:ai_agents", instance_id)
            await redis_client.expire(f"session:{session_id}:ai_agents", 14400)

            # Persist routing snapshot for recovery on bridge restart.
            raw_inst = await redis_client.get(f"{tenant_id}:instance:{instance_id}")
            if raw_inst:
                await redis_client.setex(
                    f"session:{session_id}:routing:{instance_id}",
                    14400,
                    json.dumps({
                        "tenant_id":   tenant_id,
                        "instance_id": instance_id,
                        "pool_id":     pool_id,
                        "snapshot":    json.loads(raw_inst),
                    }),
                )

            # Guardar o JSON do context_package para permitir LREM na limpeza.
            # Se a sessão encerrar antes do agente consumir (ex: reinício do agente),
            # o contact_closed handler usa este valor para remover o item da fila via
            # LREM — evitando que o próximo agente consuma um context_package obsoleto.
            # Chave com TTL curto (10min): se o agente consumir normalmente, a chave
            # expira sozinha sem necessidade de limpeza ativa.
            await redis_client.setex(
                f"session:{session_id}:pending_queue:{instance_id}",
                600,   # 10min — suficiente para o agente consumir (BLPOP típico < 2min)
                context_package_json,
            )
        except Exception as exc:
            logger.warning(
                "Could not track external-mcp instance: session=%s instance=%s — %s",
                session_id, instance_id, exc,
            )


# ── Process conversations.routed ──────────────────────────────────────────────

async def process_routed(
    msg: dict,
    http: aiohttp.ClientSession,
    redis_client: aioredis.Redis,
) -> None:
    """
    Handle a ConversationRoutedEvent from the Routing Engine.

    Event structure (routing_engine/models.py → ConversationRoutedEvent):
      session_id, tenant_id, result.{allocated, instance_id, agent_type_id, pool_id, ...}
    """
    session_id    = msg.get("session_id", "")
    tenant_id     = msg.get("tenant_id", "")
    result        = msg.get("result", {})

    if not tenant_id:
        logger.error("Received routed event without tenant_id: session=%s — discarding", session_id)
        return

    if not result.get("allocated"):
        logger.debug("Routing queued (not allocated): session=%s", session_id)
        return

    # ── Dedup guard ───────────────────────────────────────────────────────────
    # The routing engine's periodic drain re-emits conversations.routed for
    # sessions that are already being served (skill flow still running, or human
    # agent active). Without this guard every drain tick generates a new
    # participant_joined event in the session stream → "Agente entrou no
    # atendimento" spam in the webchat client.
    #
    # IMPORTANT: conference invites (conference_id present) are EXEMPT from this
    # guard. A conference invite is by definition sent to a session where a human
    # agent is already active — blocking it would mean the specialist can never join.
    # The conference-specific dedup (session:{id}:conference:specialist:{pool_id})
    # lower in this function prevents double-activation of the same specialist.
    #
    # We check two independent locks:
    #   {tenant_id}:pipeline:{session_id}:running  — set by skill-flow-service
    #                                                 while a flow is executing
    #   session:{session_id}:human_agent           — set by activate_human_agent
    conference_id = result.get("conference_id") or ""
    if not conference_id:
        try:
            existing_lock  = await redis_client.get(f"{tenant_id}:pipeline:{session_id}:running")
            existing_human = await redis_client.get(f"session:{session_id}:human_agent")
            if existing_lock or existing_human:
                logger.info(
                    "Skipping duplicate routing for already-served session: "
                    "session=%s skill_running=%s human_active=%s",
                    session_id, bool(existing_lock), bool(existing_human),
                )
                return
        except Exception as exc:
            logger.warning(
                "Could not check session state for dedup: session=%s — %s", session_id, exc
            )
    # ─────────────────────────────────────────────────────────────────────────

    agent_type_id = result.get("agent_type_id", "")
    pool_id       = result.get("pool_id", "")

    # Prefer customer_id from Redis session meta (written by channel-gateway on connect)
    customer_id = result.get("session_id", session_id)
    try:
        raw = await redis_client.get(f"session:{session_id}:meta")
        if raw:
            customer_id = json.loads(raw).get("customer_id", customer_id)
    except Exception:
        pass

    # ── Resolve agent type from Agent Registry ────────────────────────────────
    agent_type = await get_agent_type(http, tenant_id, agent_type_id)

    if agent_type is None:
        # Registry unavailable or agent not registered.
        # Best-effort fallback 1: if a YAML skill exists, treat as plughub-native.
        flow = _load_yaml_fallback(agent_type_id)
        if flow:
            logger.warning(
                "Agent type %s not found in Agent Registry — activating via YAML fallback",
                agent_type_id,
            )
            # Mirror the plughub-native snapshot/restore logic so current_sessions is
            # decremented after the skill flow completes.  Without this, each session
            # leaks +1 on the AI instance; after max_concurrent runs the instance is
            # removed from the pool set and all new contacts go silently to queue.
            yaml_instance_id = result.get("instance_id", "")
            yaml_snapshot: dict | None = None
            if yaml_instance_id and tenant_id:
                try:
                    raw_inst = await redis_client.get(f"{tenant_id}:instance:{yaml_instance_id}")
                    if raw_inst:
                        yaml_snapshot = json.loads(raw_inst)
                except Exception:
                    pass
            if yaml_instance_id and yaml_snapshot and tenant_id:
                try:
                    await redis_client.setex(
                        f"session:{session_id}:routing:{yaml_instance_id}",
                        14400,
                        json.dumps({
                            "tenant_id":   tenant_id,
                            "instance_id": yaml_instance_id,
                            "pool_id":     pool_id,
                            "snapshot":    yaml_snapshot,
                        }),
                    )
                    await redis_client.sadd(f"session:{session_id}:ai_agents", yaml_instance_id)
                    await redis_client.expire(f"session:{session_id}:ai_agents", 14400)
                    logger.debug(
                        "YAML fallback: AI snapshot persisted: session=%s instance=%s",
                        session_id, yaml_instance_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "YAML fallback: could not persist AI snapshot: session=%s — %s",
                        session_id, exc,
                    )

            await activate_native_agent(
                http=http, redis_client=redis_client,
                session_id=session_id, customer_id=customer_id,
                agent_type_id=agent_type_id, tenant_id=tenant_id,
                skills=[],       # no skills list; resolve_flow_for_agent will use YAML directly
                instance_id=yaml_instance_id,  # pass actual id so engine lock includes it
            )

            # Restore instance after skill flow completes (mirrors plughub-native path).
            # process_contact_event may have already restored it via ai_agents SET on
            # customer disconnect — double restore is idempotent (max(0, …) guards against
            # going negative and status is idempotently set to "ready").
            if yaml_instance_id and yaml_snapshot:
                try:
                    yaml_snapshot["current_sessions"] = max(
                        0, int(yaml_snapshot.get("current_sessions", 1)) - 1
                    )
                    yaml_snapshot["status"] = "ready"
                    await redis_client.set(
                        f"{tenant_id}:instance:{yaml_instance_id}",
                        json.dumps(yaml_snapshot),
                        ex=3600,
                    )
                    if pool_id:
                        await redis_client.sadd(
                            f"{tenant_id}:pool:{pool_id}:instances", yaml_instance_id
                        )
                    logger.info(
                        "YAML fallback: AI instance restored: tenant=%s instance=%s pool=%s",
                        tenant_id, yaml_instance_id, pool_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "YAML fallback: could not restore AI instance: session=%s — %s",
                        session_id, exc,
                    )
            return

        # Best-effort fallback 2: check execution_model from Redis instance.
        # Human agents (execution_model=stateful) are never registered in the
        # Agent Registry in dev — activate them directly without a skill flow.
        instance_id_for_check = result.get("instance_id", "")
        execution_model = ""
        if instance_id_for_check:
            try:
                raw_inst = await redis_client.get(f"{tenant_id}:instance:{instance_id_for_check}")
                if raw_inst:
                    execution_model = json.loads(raw_inst).get("execution_model", "")
            except Exception:
                pass

        if execution_model == "stateful":
            logger.warning(
                "Agent type %s not in registry — activating as human agent (execution_model=stateful)",
                agent_type_id,
            )
            await activate_human_agent(
                redis_client=redis_client,
                session_id=session_id, pool_id=pool_id,
                tenant_id=tenant_id,
                routing_result=result,
            )
            asyncio.create_task(fire_pool_hooks(
                http=http, redis_client=redis_client,
                session_id=session_id, pool_id=pool_id,
                tenant_id=tenant_id, customer_id=customer_id,
                hook_type="on_human_start",
            ))
        else:
            logger.error(
                "Agent type %s not found in Agent Registry and no YAML fallback in %s — "
                "register the agent type or add the YAML skill file",
                agent_type_id, SKILLS_DIR,
            )
        return

    framework = agent_type.get("framework", "")
    skills    = agent_type.get("skills", [])

    logger.info(
        "Routing: session=%s agent=%s pool=%s framework=%s",
        session_id, agent_type_id, pool_id, framework,
    )

    if framework == "plughub-native":
        # Snapshot the instance BEFORE invoking the skill flow.
        # The routing engine resets the TTL to 30s on mark_busy; the key will
        # expire long before the next contact if we don't refresh it here.
        native_instance_id = result.get("instance_id", "")
        conference_id      = result.get("conference_id", "")
        native_snapshot: dict | None = None
        if native_instance_id:
            try:
                raw_inst = await redis_client.get(f"{tenant_id}:instance:{native_instance_id}")
                if raw_inst:
                    native_snapshot = json.loads(raw_inst)
            except Exception:
                pass

        # ── Conference dedup: skip if specialist from this pool already active ─
        # A repeat @mention while the specialist is already running would cause the
        # routing engine to generate another conversations.routed for the same pool.
        # Guard against that here so we don't double-activate the specialist.
        if conference_id and pool_id:
            try:
                existing_spec = await redis_client.get(
                    f"session:{session_id}:conference:specialist:{pool_id}"
                )
                if existing_spec:
                    logger.info(
                        "Skipping duplicate conference invite: specialist pool=%s already active "
                        "in session=%s — mention command dispatch will handle the command",
                        pool_id, session_id,
                    )
                    return
            except Exception as exc:
                logger.warning(
                    "Could not check existing specialist: session=%s pool=%s — %s",
                    session_id, pool_id, exc,
                )

        # ── Persist snapshot to Redis before blocking call ────────────────────
        # activate_native_agent blocks for the entire session duration (up to hours
        # for menus with timeout_s=0). If the bridge process is killed mid-session,
        # the in-memory snapshot is lost and the instance is never restored.
        # We persist the snapshot now so a restart can recover it.
        # Key: session:{session_id}:routing:{instance_id} (same pattern as human agents)
        if native_instance_id and native_snapshot and tenant_id:
            try:
                await redis_client.setex(
                    f"session:{session_id}:routing:{native_instance_id}",
                    14400,
                    json.dumps({
                        "tenant_id":   tenant_id,
                        "instance_id": native_instance_id,
                        "pool_id":     pool_id,
                        "snapshot":    native_snapshot,
                    }),
                )
                # Track instance_id in a SET so process_contact_event can restore
                # ALL AI instances on contact_closed (mirrors session:{id}:human_agents).
                await redis_client.sadd(
                    f"session:{session_id}:ai_agents", native_instance_id,
                )
                await redis_client.expire(f"session:{session_id}:ai_agents", 14400)
                logger.debug(
                    "AI instance snapshot persisted: session=%s instance=%s",
                    session_id, native_instance_id,
                )
            except Exception as exc:
                logger.warning(
                    "Could not persist AI instance snapshot: session=%s — %s",
                    session_id, exc,
                )

        # ── Store specialist info for @mention command dispatch ──────────────
        # When conference_id is present, this agent is a conference specialist
        # (e.g. agente_copilot_v1 in pool copilot_sac). The mention command dispatch
        # in process_mention_routing uses this key to find the active specialist's
        # skill_id so it can look up mention_commands from the YAML and push the
        # appropriate signal to menu:result:{session_id}.
        if conference_id and pool_id:
            # Resolve skill_id early (same logic used inside activate_native_agent)
            resolved_for_mention = await resolve_flow_for_agent(http, tenant_id, agent_type_id, skills)
            mention_skill_id = resolved_for_mention[0] if resolved_for_mention else agent_type_id
            try:
                await redis_client.setex(
                    f"session:{session_id}:conference:specialist:{pool_id}",
                    14400,
                    json.dumps({
                        "skill_id":      mention_skill_id,
                        "instance_id":   native_instance_id,
                        "agent_type_id": agent_type_id,
                    }),
                )
                logger.info(
                    "Specialist info stored for @mention dispatch: "
                    "session=%s pool=%s skill=%s instance=%s",
                    session_id, pool_id, mention_skill_id, native_instance_id,
                )
            except Exception as exc:
                logger.warning(
                    "Could not store specialist info: session=%s pool=%s — %s",
                    session_id, pool_id, exc,
                )

        # ── Fase C: participant_joined ─────────────────────────────────────────
        _part_joined_at  = datetime.now(timezone.utc)
        _part_joined_iso = _part_joined_at.isoformat()
        _part_role = "specialist" if conference_id else "primary"
        # ── Arc 5: generate segment_id + derive topology fields ───────────────
        _part_seg_id = str(uuid.uuid4())
        _part_seq_idx = 0
        _part_parent_seg = ""
        try:
            if conference_id:
                # Specialist in a conference: parent = current primary segment
                _raw_primary = await redis_client.get(
                    f"session:{session_id}:primary_segment"
                )
                if _raw_primary:
                    _part_parent_seg = (
                        _raw_primary if isinstance(_raw_primary, str)
                        else _raw_primary.decode()
                    )
            else:
                # Primary sequential agent: increment sequence counter
                _seq_raw = await redis_client.incr(f"session:{session_id}:segment_seq")
                _part_seq_idx = int(_seq_raw) - 1
                await redis_client.expire(f"session:{session_id}:segment_seq", 14400)
                # Publish as current primary segment for upcoming specialists
                await redis_client.setex(
                    f"session:{session_id}:primary_segment",
                    14400,
                    _part_seg_id,
                )
            # Store segment_id for retrieval on participant_left
            await redis_client.setex(
                f"session:{session_id}:segment:{native_instance_id}",
                14400,
                _part_seg_id,
            )
        except Exception:
            pass
        asyncio.create_task(_publish_participant_event(
            session_id=session_id,
            tenant_id=tenant_id,
            participant_id=native_instance_id,
            pool_id=pool_id,
            agent_type_id=agent_type_id,
            event_type="participant_joined",
            agent_type="native",
            role=_part_role,
            segment_id=_part_seg_id,
            sequence_index=_part_seq_idx,
            parent_segment_id=_part_parent_seg,
            conference_id=conference_id,
            joined_at=_part_joined_iso,
        ))

        agent_result = await activate_native_agent(
            http=http, redis_client=redis_client,
            session_id=session_id, customer_id=customer_id,
            agent_type_id=agent_type_id, tenant_id=tenant_id,
            skills=skills,
            instance_id=native_instance_id,
            conference_id=conference_id,
            segment_id=_part_seg_id,
        )

        # ── Conference: notify the human agent that the AI has completed ──────
        # Published to agent:events:{session_id} (Redis pub/sub) so the
        # Agent Assist UI can update its state immediately — the human can
        # resume full control knowing exactly what the AI resolved.
        if conference_id and agent_result.get("outcome"):
            try:
                await redis_client.publish(
                    f"agent:events:{session_id}",
                    json.dumps({
                        "type":          "conference.agent_completed",
                        "session_id":    session_id,
                        "conference_id": conference_id,
                        "agent_type_id": agent_type_id,
                        "outcome":       agent_result.get("outcome"),
                        "pipeline_state": agent_result.get("pipeline_state"),
                        "completed_at":  datetime.now(timezone.utc).isoformat(),
                    }),
                )
                logger.info(
                    "Conference AI completed: session=%s conference=%s outcome=%s",
                    session_id, conference_id, agent_result.get("outcome"),
                )
            except Exception as exc:
                logger.warning(
                    "Could not publish conference.agent_completed: session=%s — %s",
                    session_id, exc,
                )

        # Restore instance with a long TTL so the next contact can be routed.
        # Stateless AI agents are always available after serving a session.
        if native_instance_id and native_snapshot:
            try:
                native_snapshot["current_sessions"] = max(
                    0, int(native_snapshot.get("current_sessions", 1)) - 1
                )
                native_snapshot["status"] = "ready"
                await redis_client.set(
                    f"{tenant_id}:instance:{native_instance_id}",
                    json.dumps(native_snapshot),
                    ex=3600,
                )
                if pool_id:
                    await redis_client.sadd(
                        f"{tenant_id}:pool:{pool_id}:instances", native_instance_id
                    )
                logger.info(
                    "AI agent instance restored to ready: tenant=%s instance=%s pool=%s",
                    tenant_id, native_instance_id, pool_id,
                )
            except Exception as exc:
                logger.warning(
                    "Could not restore AI agent instance: tenant=%s instance=%s — %s",
                    tenant_id, native_instance_id, exc,
                )

        # ── Fase C: participant_left ───────────────────────────────────────────
        _part_duration_ms = int(
            (datetime.now(timezone.utc) - _part_joined_at).total_seconds() * 1000
        )
        # ── Arc 5: retrieve segment_id stored at participant_joined ───────────
        _left_seg_id = _part_seg_id   # already in scope; GETDEL for cleanup
        try:
            _raw_seg = await redis_client.getdel(
                f"session:{session_id}:segment:{native_instance_id}"
            )
            if _raw_seg:
                _left_seg_id = (
                    _raw_seg if isinstance(_raw_seg, str) else _raw_seg.decode()
                )
        except Exception:
            pass
        # Outcome from agent_result (populated by activate_native_agent)
        _part_outcome = agent_result.get("outcome") if agent_result else None
        asyncio.create_task(_publish_participant_event(
            session_id=session_id,
            tenant_id=tenant_id,
            participant_id=native_instance_id,
            pool_id=pool_id,
            agent_type_id=agent_type_id,
            event_type="participant_left",
            agent_type="native",
            role=_part_role,
            segment_id=_left_seg_id,
            sequence_index=_part_seq_idx,
            parent_segment_id=_part_parent_seg,
            conference_id=conference_id,
            joined_at=_part_joined_iso,
            duration_ms=_part_duration_ms,
            outcome=_part_outcome,
        ))

        # ── Primary AI agent complete: trigger contact close ──────────────────
        # Conference / hook agents are handled by the Fase B/C block below
        # (counter tracked via hook_conf keys).  Primary (non-conference) AI
        # agents own the session lifecycle directly, so we must trigger the
        # close here.  The idempotency guard (close_fired NX key) inside
        # _trigger_contact_close prevents double-close when the channel-gateway
        # already fired a close due to customer disconnect or session timeout.
        #
        # EXCEÇÃO: outcomes de escalação/transferência indicam que a sessão
        # continua com outro agente — NÃO fechar o WebSocket do cliente.
        # O conversation_escalate (BPM tool) já publicou conversations.inbound
        # para alocar o próximo agente; fechar aqui causaria race condition.
        _escalation_outcomes = ("escalated_human", "escalated_ai", "transferred")
        _ai_outcome = (agent_result or {}).get("outcome", "")
        if not conference_id and _ai_outcome not in _escalation_outcomes:
            asyncio.create_task(_trigger_contact_close(redis_client, session_id))

        # ── Fase B/C: hook completion detection ───────────────────────────────
        # hook_conf key stores "{hook_type}:{target_pool}" (e.g. "on_human_end:finalizacao_ia").
        # Parse hook_type to determine which counter to decrement and what to do next:
        #   on_human_end → when counter hits 0: check post_human hooks (Fase C) or close
        #   post_human   → when counter hits 0: always trigger contact close
        if conference_id:
            try:
                hook_label = await redis_client.getdel(
                    f"session:{session_id}:hook_conf:{conference_id}"
                )
                if hook_label:
                    _hl = hook_label if isinstance(hook_label, str) else hook_label.decode()
                    completed_hook_type = _hl.split(":")[0]   # "on_human_end" or "post_human"

                    remaining_hooks = await redis_client.decr(
                        f"session:{session_id}:hook_pending:{completed_hook_type}"
                    )
                    logger.info(
                        "Hook agent completed: session=%s conference=%s hook=%s remaining=%d",
                        session_id, conference_id, completed_hook_type, remaining_hooks,
                    )

                    if remaining_hooks <= 0:
                        if completed_hook_type == "on_human_end":
                            # ── Fase C: check for post_human hooks ────────────
                            # If post_human hooks are declared, dispatch them now.
                            # Otherwise go straight to contact close.
                            _ph_pool = _ph_tenant = _ph_customer = ""
                            try:
                                _ph_raw = await redis_client.get(f"session:{session_id}:meta")
                                if _ph_raw:
                                    _ph_meta    = json.loads(_ph_raw)
                                    _ph_pool    = _ph_meta.get("pool_id", "")
                                    _ph_tenant  = (
                                        _ph_meta.get("tenant_id", "")
                                        or _ph_meta.get("tenant", "")
                                    )
                                    _ph_customer = (
                                        _ph_meta.get("customer_id", session_id) or session_id
                                    )
                            except Exception as _ph_exc:
                                logger.debug(
                                    "Could not read meta for post_human check: "
                                    "session=%s — %s", session_id, _ph_exc,
                                )
                            _dispatched_post = False
                            if http and _ph_pool and _ph_tenant:
                                try:
                                    _ph_config = await get_pool_config(
                                        http, _ph_tenant, _ph_pool
                                    )
                                    _post_human_list = (
                                        ((_ph_config or {}).get("hooks") or {})
                                        .get("post_human", [])
                                    )
                                    if _post_human_list:
                                        asyncio.create_task(fire_pool_hooks(
                                            http=http,
                                            redis_client=redis_client,
                                            session_id=session_id,
                                            pool_id=_ph_pool,
                                            tenant_id=_ph_tenant,
                                            customer_id=_ph_customer,
                                            hook_type="post_human",
                                        ))
                                        # Safety net for post_human hooks too
                                        asyncio.create_task(_hook_timeout_guard(
                                            redis_client, session_id, "post_human",
                                        ))
                                        logger.info(
                                            "post_human hooks dispatched: session=%s pool=%s count=%d "
                                            "(timeout guard scheduled: %ds)",
                                            session_id, _ph_pool, len(_post_human_list),
                                            _HOOK_TIMEOUT_S,
                                        )
                                        _dispatched_post = True
                                except Exception as _ph_exc2:
                                    logger.warning(
                                        "Could not check post_human hooks: session=%s — %s",
                                        session_id, _ph_exc2,
                                    )
                            if not _dispatched_post:
                                asyncio.create_task(
                                    _trigger_contact_close(redis_client, session_id)
                                )
                        else:
                            # post_human complete → trigger contact close
                            asyncio.create_task(
                                _trigger_contact_close(redis_client, session_id)
                            )
            except Exception as exc:
                logger.warning(
                    "Hook completion detection error: session=%s conference=%s — %s",
                    session_id, conference_id, exc,
                )

    elif framework == "human":
        await activate_human_agent(
            redis_client=redis_client,
            session_id=session_id, pool_id=pool_id,
            tenant_id=tenant_id,
            routing_result=result,
        )
        # Fire on_human_start hooks non-blocking.
        # Each hook entry routes a specialist as conference participant via
        # conversations.inbound → routing engine → process_routed (conference path).
        asyncio.create_task(fire_pool_hooks(
            http=http, redis_client=redis_client,
            session_id=session_id, pool_id=pool_id,
            tenant_id=tenant_id, customer_id=customer_id,
            hook_type="on_human_start",
        ))

    elif framework == "external-mcp":
        # Agentes externos integrados via MCP (spec 4.6k).
        # O agente já está conectado ao mcp-server-plughub aguardando em
        # wait_for_assignment (BLPOP). O bridge faz LPUSH do context_package
        # e retorna imediatamente — o agente gerencia seu próprio ciclo de vida
        # e chama agent_done ao concluir.
        await activate_external_mcp_agent(
            redis_client=redis_client,
            session_id=session_id, pool_id=pool_id,
            tenant_id=tenant_id, customer_id=customer_id,
            agent_type_id=agent_type_id,
            routing_result=result,
        )

    else:
        # External AI frameworks (langgraph, crewai, anthropic_sdk, etc.)
        # These agents manage their own runtime — not activated by the bridge.
        # They connect to the platform via the plughub-sdk proxy sidecar.
        logger.warning(
            "External agent framework '%s' for agent=%s — "
            "activation is handled by the agent runtime, not the bridge",
            framework, agent_type_id,
        )


# ── Process conversations.queued — Queue Agent Pattern ────────────────────────

async def process_queued(
    msg: dict,
    http: aiohttp.ClientSession,
    redis_client: aioredis.Redis,
) -> None:
    """
    Handle a ConversationRoutedEvent where result.allocated=False (queued contact).

    Queue Agent Pattern (spec Queue Agent):
      If the pool has a queue_config, activate a native skill-flow agent that
      interacts with the customer while they wait.  When a human agent becomes
      available, the Routing Engine's kafka_listener sets a Redis marker and
      signals the queue agent via LPUSH '__agent_available__' to
      menu:result:{session_id}, which unblocks the menu step and causes the
      skill flow to execute an escalate step to the human pool.

    Redis marker set here:
      queue:agent_active:{session_id} → JSON  (TTL 4h)
      Checked by kafka_listener._drain_queue_for_agent() to decide whether to
      signal the queue agent (LPUSH) or re-publish to conversations.inbound.

    If queue_config is absent or the agent type cannot be resolved, the contact
    waits silently (original behaviour — routing engine drain still works).
    """
    session_id = msg.get("session_id", "")
    tenant_id  = msg.get("tenant_id", "")
    result     = msg.get("result", {})
    pool_id    = result.get("pool_id", "")

    if not session_id or not tenant_id or not pool_id:
        logger.warning("Queued event missing required fields: %s", msg)
        return

    # Fetch pool config to check for queue_config
    pool = await get_pool_config(http, tenant_id, pool_id)
    if not pool:
        logger.warning(
            "Could not fetch pool config for queue agent activation: pool=%s tenant=%s",
            pool_id, tenant_id,
        )
        return

    queue_cfg = pool.get("queue_config")
    if not queue_cfg:
        logger.debug("No queue_config for pool=%s — customer waits silently", pool_id)
        return

    agent_type_id = queue_cfg.get("agent_type_id", "")
    explicit_skill_id = queue_cfg.get("skill_id")   # optional — overrides agent's default skill
    if not agent_type_id:
        logger.warning("queue_config.agent_type_id is empty for pool=%s", pool_id)
        return

    # Resolve agent type metadata (framework, skills list)
    agent_type = await get_agent_type(http, tenant_id, agent_type_id)
    if agent_type is None:
        # YAML fallback (dev environment without Agent Registry)
        flow = _load_yaml_fallback(agent_type_id)
        if not flow:
            logger.error(
                "Queue agent %s not found in Agent Registry and no YAML fallback in %s",
                agent_type_id, SKILLS_DIR,
            )
            return
        skills: list[dict] = []
    else:
        skills = agent_type.get("skills", [])

    # Prepend explicit skill_id if given and not already in the list
    if explicit_skill_id and not any(s.get("skill_id") == explicit_skill_id for s in skills):
        skills = [{"skill_id": explicit_skill_id}] + skills

    # Resolve customer_id from session meta
    customer_id = session_id
    try:
        raw = await redis_client.get(f"session:{session_id}:meta")
        if raw:
            customer_id = json.loads(raw).get("customer_id", customer_id)
    except Exception:
        pass

    # Set Redis marker so kafka_listener knows to signal the queue agent
    # instead of re-publishing to conversations.inbound when an agent becomes ready.
    marker_value = json.dumps({
        "pool_id":       pool_id,
        "agent_type_id": agent_type_id,
        "activated_at":  datetime.now(timezone.utc).isoformat(),
    })
    try:
        await redis_client.set(
            f"queue:agent_active:{session_id}", marker_value, ex=14_400
        )
        logger.debug("Queue agent marker set: session=%s pool=%s", session_id, pool_id)
    except Exception as exc:
        logger.warning(
            "Could not set queue agent marker: session=%s — %s", session_id, exc
        )

    logger.info(
        "Activating queue agent: session=%s pool=%s agent=%s",
        session_id, pool_id, agent_type_id,
    )

    # Activate the queue agent — this call blocks for the entire wait duration
    # because the skill flow contains a menu step with timeout_s=0.
    # It returns only when the queue agent's skill flow completes (either via
    # '__agent_available__' signal or customer disconnect / max_wait_s timeout).
    # extra_context exposes pool_id and session_id so the YAML's invoke step can
    # dynamically call conversation_escalate with the correct target pool.
    await activate_native_agent(
        http=http, redis_client=redis_client,
        session_id=session_id, customer_id=customer_id,
        agent_type_id=agent_type_id, tenant_id=tenant_id,
        skills=skills,
        instance_id="",   # queue agents don't hold a routing slot
        extra_context={"pool_id": pool_id},
    )

    # Clean up marker after the queue agent completes
    try:
        await redis_client.delete(f"queue:agent_active:{session_id}")
    except Exception:
        pass

    logger.info("Queue agent completed: session=%s pool=%s", session_id, pool_id)


# ── Process conversations.events — notify human agent on contact_closed ───────

async def process_contact_event(
    msg: dict,
    redis_client: aioredis.Redis,
    http: aiohttp.ClientSession | None = None,
) -> None:
    """
    Handle lifecycle events from conversations.events.

    Currently handled:
      contact_closed — when the customer disconnects or times out, publish
                       session.closed to agent:events:{session_id} so the
                       Agent Assist UI updates its state immediately.

    The human-agent Redis flag (session:{session_id}:human_agent) is cleaned
    up here so subsequent messages for this session are no longer forwarded.
    """
    event_type = msg.get("event_type")

    # ── Conference specialist completed ───────────────────────────────────────
    # Published by runtime.ts agent_done when conference_id is present.
    # Notifies the human agent's Agent Assist UI that the AI specialist is done,
    # and removes the specialist instance from the ai_agents tracking SET.
    if event_type == "conference_agent_completed":
        session_id    = msg.get("session_id", "")
        conference_id = msg.get("conference_id", "")
        instance_id   = msg.get("instance_id", "")
        outcome       = msg.get("outcome", "")
        if session_id:
            # Notify human agent (if active) that the specialist finished
            try:
                await redis_client.publish(
                    f"agent:events:{session_id}",
                    json.dumps({
                        "type":          "conference.agent_completed",
                        "session_id":    session_id,
                        "conference_id": conference_id,
                        "instance_id":   instance_id,
                        "outcome":       outcome,
                        "completed_at":  msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    }),
                )
                logger.info(
                    "Conference specialist completed: session=%s conference=%s instance=%s outcome=%s",
                    session_id, conference_id, instance_id, outcome,
                )
            except Exception as exc:
                logger.warning(
                    "Could not publish conference.agent_completed: session=%s — %s",
                    session_id, exc,
                )
            # Remove specialist from ai_agents tracking SET
            if instance_id:
                try:
                    await redis_client.srem(f"session:{session_id}:ai_agents", instance_id)
                    # Restore specialist instance (routing snapshot cleanup)
                    await _restore_instance(redis_client, session_id, instance_id)
                except Exception as exc:
                    logger.warning(
                        "Could not clean up specialist instance: session=%s instance=%s — %s",
                        session_id, instance_id, exc,
                    )
            # Remove specialist conference key so a new @mention creates a fresh invite
            try:
                spec_keys = await redis_client.keys(
                    f"session:{session_id}:conference:specialist:*"
                )
                for k in spec_keys:
                    spec_raw = await redis_client.get(k)
                    if spec_raw:
                        spec = json.loads(spec_raw)
                        if spec.get("instance_id") == instance_id:
                            await redis_client.delete(k)
                            logger.info(
                                "Specialist conference key removed: session=%s key=%s",
                                session_id, k,
                            )
                            break
            except Exception as exc:
                logger.warning(
                    "Could not clean up specialist conference key: session=%s — %s",
                    session_id, exc,
                )
        return

    if event_type != "contact_closed":
        return

    session_id = msg.get("session_id")
    if not session_id:
        return

    reason      = msg.get("reason", "client_disconnect")
    instance_id = msg.get("instance_id", "")

    # ── Classify the close origin ─────────────────────────────────────────────
    #
    # customer_side=True  → customer disconnected or timed out, or the platform
    #                        closed the customer WebSocket after agent_done.
    #                        The entire conversation is over: signal session:closed
    #                        to unblock any menu BLPOP and notify all agents.
    #
    # customer_side=False → a single agent called agent_done while other agents
    #                        (and the customer) may still be active (conference).
    #                        Do NOT signal session:closed; only restore that
    #                        agent's instance and update the per-session tracking.
    #
    # Reason values:
    #   "client_disconnect" — customer closed WebSocket (channel-gateway)
    #   "timeout"           — customer connection timed out (channel-gateway)
    #   "agent_done"        — platform closed customer WebSocket after agent_done
    #                         (channel-gateway, triggered by conversations.outbound)
    #   "agent_closed"      — a human agent called REST /agent_done (mcp-server)

    customer_side = reason in ("client_disconnect", "timeout", "session_timeout", "agent_done")

    try:
        # ── Marcar sessão como encerrada ──────────────────────────────────────
        # Escrita para TODOS os motivos de encerramento (não só customer_side).
        # O Routing Engine lê este marcador em _drain_queue_for_agent para
        # descartar sessões que ainda estão na fila mas já foram encerradas,
        # evitando o "ghost contact" no Agent Assist ao reconectar.
        # TTL 7 dias: cobre sessões que ficam na fila por muito tempo.
        try:
            await redis_client.setex(f"session:{session_id}:closed", 604800, reason)
        except Exception as exc:
            logger.warning("Could not set session:closed marker: session=%s — %s", session_id, exc)

        if customer_side:

            # ── Signal session closed — dois mecanismos em paralelo ───────────
            #
            # 1. LPUSH session:closed:{session_id}   — desbloqueia BLPOP legado
            #    (Skill Flow menu step, wait_for_message de versões anteriores).
            #    TTL 300s — must survive long enough for hook-triggered flows
            #    (e.g. on_human_end finalizacao) whose menu steps only start
            #    5-15s later after Kafka routing.  BLPOP consumes the value
            #    immediately, so the list becomes empty; the TTL is just a
            #    guard against orphaned keys.
            #
            # 2. XADD session:{session_id}:stream  — desbloqueia XREADGROUP de
            #    agentes external-mcp usando wait_for_message com Streams.
            #    Item {type: session_closed} na mesma fila de mensagens garante
            #    que o sinal respeita a ordem de entrega — não chega antes de
            #    mensagens do cliente já enfileiradas.
            #    NOTA: usa :stream (não :messages) — :messages é uma List do canal-gateway.
            try:
                # Quando múltiplos agentes estão bloqueados em menu steps simultâneos
                # (ex: NPS + wrap-up), cada BLPOP consome UMA entrada do list.
                # LPUSH N cópias do sinal para garantir que todos os BLPOPs desbloqueiem.
                n_waiting = 1
                try:
                    _wh = await redis_client.hgetall(f"menu:waiting:{session_id}")
                    if _wh and len(_wh) > 1:
                        n_waiting = len(_wh)
                except Exception:
                    pass
                closed_key = f"session:closed:{session_id}"
                for _ in range(n_waiting):
                    await redis_client.lpush(closed_key, reason)
                await redis_client.expire(closed_key, 300)
            except Exception as exc:
                logger.warning("Could not push session:closed: session=%s — %s", session_id, exc)

            stream_key = f"session:{session_id}:stream"
            try:
                groups = await redis_client.xinfo_groups(stream_key)
                if groups:
                    await redis_client.xadd(
                        stream_key,
                        {"type": "session_closed", "reason": reason},
                    )
                    logger.info(
                        "XADD session_closed to stream: session=%s reason=%s groups=%d",
                        session_id, reason, len(groups),
                    )
            except Exception as exc:
                logger.warning(
                    "Could not XADD session_closed to stream: session=%s — %s", session_id, exc
                )

            # ── Clear pending pool assignment unconditionally ─────────────────
            # Must run regardless of whether a human agent was ever assigned.
            # A force-close before human assignment leaves pool:pending_assignment
            # in Redis, causing "ghost contact" on agent Ctrl+Shift+R reconnect.
            try:
                pool_id_for_cleanup = None
                meta_raw = await redis_client.get(f"session:{session_id}:meta")
                if meta_raw:
                    pool_id_for_cleanup = json.loads(meta_raw).get("pool_id")
                if pool_id_for_cleanup:
                    await redis_client.delete(f"pool:pending_assignment:{pool_id_for_cleanup}")
                    logger.debug(
                        "Pending assignment cleared: pool=%s session=%s",
                        pool_id_for_cleanup, session_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Could not clear pending assignment: session=%s — %s", session_id, exc
                )

            # ── Notify all active human agents that the session ended ─────────
            _hooks_pending = False  # set True when on_human_end hooks are dispatched
            is_human = await redis_client.get(f"session:{session_id}:human_agent")
            if is_human:
                closed_event = {
                    "type":       "session.closed",
                    "session_id": session_id,
                    "reason":     reason,
                }
                await redis_client.publish(f"agent:events:{session_id}", json.dumps(closed_event))
                logger.info("Human agent(s) notified: session=%s reason=%s", session_id, reason)

                # ── Restore all instances still tracked for this session ──────
                await _restore_all_instances(redis_client, session_id)

                # ── Publish participant_left for all tracked human agents ─────
                _human_members = await redis_client.smembers(
                    f"session:{session_id}:human_agents"
                )
                _last_human_instance_id: str | None = None
                for _hm_inst in (_human_members or []):
                    _hm_inst_str = (
                        _hm_inst if isinstance(_hm_inst, str) else _hm_inst.decode()
                    )
                    _last_human_instance_id = _hm_inst_str
                    _hm_joined_iso = ""
                    try:
                        _raw_hm_jat = await redis_client.getdel(
                            f"session:{session_id}:participant_joined_at:{_hm_inst_str}"
                        )
                        _hm_joined_iso = (
                            _raw_hm_jat if isinstance(_raw_hm_jat, str)
                            else (_raw_hm_jat.decode() if _raw_hm_jat else "")
                        )
                    except Exception:
                        pass
                    _hm_dur: int | None = None
                    if _hm_joined_iso:
                        try:
                            _hm_jdt = datetime.fromisoformat(_hm_joined_iso)
                            _hm_dur = int(
                                (datetime.now(timezone.utc) - _hm_jdt).total_seconds() * 1000
                            )
                        except Exception:
                            pass
                    _hm_seg_id = ""
                    try:
                        _raw_hm_seg = await redis_client.getdel(
                            f"session:{session_id}:segment:{_hm_inst_str}"
                        )
                        if _raw_hm_seg:
                            _hm_seg_id = (
                                _raw_hm_seg if isinstance(_raw_hm_seg, str)
                                else _raw_hm_seg.decode()
                            )
                    except Exception:
                        pass
                    _hm_pool = _hm_at = _hm_ten = ""
                    try:
                        _hm_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                        if _hm_raw_meta:
                            _hm_m = json.loads(_hm_raw_meta)
                            _hm_pool = _hm_m.get("pool_id", "")
                            _hm_at   = _hm_m.get("agent_type_id", "")
                            _hm_ten  = _hm_m.get("tenant_id", "") or _hm_m.get("tenant", "")
                    except Exception:
                        pass
                    asyncio.create_task(_publish_participant_event(
                        session_id=session_id,
                        tenant_id=_hm_ten,
                        participant_id=_hm_inst_str,
                        pool_id=_hm_pool,
                        agent_type_id=_hm_at,
                        event_type="participant_left",
                        agent_type="human",
                        role="primary",
                        segment_id=_hm_seg_id,
                        joined_at=_hm_joined_iso,
                        duration_ms=_hm_dur,
                    ))

                # ── Clean up all human-agent tracking for this session ────────
                await redis_client.delete(f"session:{session_id}:human_agent")
                await redis_client.delete(f"session:{session_id}:human_agents")

                # ── Check for on_human_end hooks (wrap-up agent) ─────────────
                # Even when the *client* disconnected, we still fire on_human_end
                # hooks so that wrap-up agents (NPS, encerramento) can execute.
                # The customer WS is already closed, so the wrap-up agent operates
                # in "post-session" mode — its messages go to the stream but the
                # client won't see them.  The hooks guarantee that the session is
                # properly closed on the platform side.
                _cs_pool_id    = ""
                _cs_tenant_id  = ""
                _cs_customer_id = ""
                try:
                    _cs_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                    if _cs_raw_meta:
                        _cs_meta        = json.loads(_cs_raw_meta)
                        _cs_pool_id     = _cs_meta.get("pool_id", "")
                        _cs_tenant_id   = (
                            _cs_meta.get("tenant_id", "")
                            or _cs_meta.get("tenant", "")
                        )
                        _cs_customer_id = (
                            _cs_meta.get("customer_id", session_id) or session_id
                        )
                except Exception as _exc:
                    logger.warning(
                        "customer_disconnect: could not read session meta for hooks: "
                        "session=%s — %s", session_id, _exc,
                    )

                _cs_hooks_fired = False
                if http and _cs_pool_id and _cs_tenant_id:
                    _cs_pool_cfg = await get_pool_config(
                        http, _cs_tenant_id, _cs_pool_id
                    )
                    _cs_on_human_end = (
                        ((_cs_pool_cfg or {}).get("hooks") or {})
                        .get("on_human_end", [])
                    )
                    if _cs_on_human_end:
                        _cs_hooks_fired = True
                        _hooks_pending = True
                        # Escreve close_origin + customer/human participant_id no
                        # ContextStore ANTES de disparar os hooks.
                        await _write_pre_hook_context(
                            redis_client, _cs_tenant_id, session_id,
                            close_origin="client_disconnect",
                            human_instance_id=_last_human_instance_id,
                        )
                        asyncio.create_task(fire_pool_hooks(
                            http=http, redis_client=redis_client,
                            session_id=session_id,
                            pool_id=_cs_pool_id,
                            tenant_id=_cs_tenant_id,
                            customer_id=_cs_customer_id,
                            hook_type="on_human_end",
                        ))
                        asyncio.create_task(_hook_timeout_guard(
                            redis_client, session_id, "on_human_end",
                        ))
                        logger.info(
                            "on_human_end hooks dispatched (client disconnect): "
                            "session=%s pool=%s count=%d (timeout guard: %ds)",
                            session_id, _cs_pool_id, len(_cs_on_human_end),
                            _HOOK_TIMEOUT_S,
                        )

                if not _cs_hooks_fired:
                    # No hooks or meta unavailable — close the contact immediately
                    asyncio.create_task(
                        _trigger_contact_close(redis_client, session_id)
                    )
                    logger.info(
                        "No on_human_end hooks — closing contact immediately: session=%s",
                        session_id,
                    )

            else:
                # No human agent was active — close the contact immediately
                asyncio.create_task(
                    _trigger_contact_close(redis_client, session_id)
                )

            # ── Clear conversation data (only when no hooks are pending) ──────
            # When on_human_end hooks were dispatched, the stream must survive
            # until the wrap-up agent completes.  Stream/messages are cleaned
            # naturally by TTL (4h) or by the re-entry after hooks complete.
            if not _hooks_pending:
                try:
                    await redis_client.delete(
                        f"session:{session_id}:messages",
                        f"session:{session_id}:stream",
                    )
                    logger.debug("Message data cleared: session=%s", session_id)
                except Exception as exc:
                    logger.warning(
                        "Could not delete message data: session=%s — %s", session_id, exc
                    )

            # ── Restore all AI agent instances for this session ───────────────
            # AI agents are tracked in session:{session_id}:ai_agents SET,
            # persisted in process_routed before the blocking activate_native_agent call.
            # This ensures instances are freed even if the bridge was restarted mid-session.
            ai_members = await redis_client.smembers(f"session:{session_id}:ai_agents")
            if ai_members:
                for ai_inst_id in ai_members:
                    await _restore_instance(redis_client, session_id, ai_inst_id)
                await redis_client.delete(f"session:{session_id}:ai_agents")
                logger.info(
                    "AI instance(s) restored on contact_closed: session=%s count=%d",
                    session_id, len(ai_members),
                )

            # ── Remover context_packages pendentes de agentes external-mcp ─────
            # Quando a sessão encerra antes do agente consumir o context_package
            # (ex: agente reiniciado entre LPUSH e BLPOP), o item fica obsoleto
            # na fila. Usamos o JSON guardado em pending_queue para LREM exato.
            # Sem isso, o próximo ciclo do agente consumiria um context_package
            # de sessão inexistente. (Defesa adicional: wait_for_assignment valida
            # session:meta antes de retornar — belt-and-suspenders.)
            try:
                pending_keys = await redis_client.keys(f"session:{session_id}:pending_queue:*")
                for pk in pending_keys:
                    inst_id = pk.split(":")[-1]
                    pending_json = await redis_client.get(pk)
                    if pending_json:
                        tenant = "default"   # extrair do JSON para suporte multi-tenant
                        try:
                            pkg = json.loads(pending_json)
                            tenant = pkg.get("tenant_id", tenant)
                        except Exception:
                            pass
                        queue_key = f"{tenant}:agent:queue:{inst_id}"
                        removed = await redis_client.lrem(queue_key, 0, pending_json)
                        if removed:
                            logger.info(
                                "Removed stale context_package from queue: "
                                "session=%s instance=%s removed=%d",
                                session_id, inst_id, removed,
                            )
                    await redis_client.delete(pk)
            except Exception as exc:
                logger.warning(
                    "Could not clean pending_queue on contact_closed: session=%s — %s",
                    session_id, exc,
                )

        else:
            # reason == "agent_closed": one specific agent ended their session.
            # Other agents + customer may still be active — do not disturb them.
            is_human = await redis_client.get(f"session:{session_id}:human_agent")
            if not is_human:
                return  # not a human session — nothing to do

            # ── Restore this specific agent's instance ────────────────────────
            await _restore_instance(redis_client, session_id, instance_id)

            # ── Remove this agent from the active-agents SET ─────────────────
            if instance_id:
                # ── Fase C: participant_left for human agent ────────────────
                _ha_joined_iso = ""
                try:
                    _raw_jat = await redis_client.getdel(
                        f"session:{session_id}:participant_joined_at:{instance_id}"
                    )
                    _ha_joined_iso = (
                        _raw_jat if isinstance(_raw_jat, str)
                        else (_raw_jat.decode() if _raw_jat else "")
                    )
                except Exception:
                    pass
                _ha_duration_ms: int | None = None
                if _ha_joined_iso:
                    try:
                        _ha_jdt = datetime.fromisoformat(_ha_joined_iso)
                        _ha_duration_ms = int(
                            (datetime.now(timezone.utc) - _ha_jdt).total_seconds() * 1000
                        )
                    except Exception:
                        pass
                _ha_pool = _ha_agent_type_id = _ha_tenant = ""
                try:
                    _ha_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                    if _ha_raw_meta:
                        _ha_m = json.loads(_ha_raw_meta)
                        _ha_pool          = _ha_m.get("pool_id", "")
                        _ha_agent_type_id = _ha_m.get("agent_type_id", "")
                        _ha_tenant        = (
                            _ha_m.get("tenant_id", "") or _ha_m.get("tenant", "")
                        )
                except Exception:
                    pass
                # ── Arc 5: retrieve segment_id stored at activate_human_agent ────
                _ha_seg_id = ""
                _ha_seq_idx = 0
                try:
                    _raw_ha_seg = await redis_client.getdel(
                        f"session:{session_id}:segment:{instance_id}"
                    )
                    if _raw_ha_seg:
                        _ha_seg_id = (
                            _raw_ha_seg if isinstance(_raw_ha_seg, str)
                            else _raw_ha_seg.decode()
                        )
                except Exception:
                    pass
                asyncio.create_task(_publish_participant_event(
                    session_id=session_id,
                    tenant_id=_ha_tenant,
                    participant_id=instance_id,
                    pool_id=_ha_pool,
                    agent_type_id=_ha_agent_type_id,
                    event_type="participant_left",
                    agent_type="human",
                    role="primary",
                    segment_id=_ha_seg_id,
                    sequence_index=_ha_seq_idx,
                    joined_at=_ha_joined_iso,
                    duration_ms=_ha_duration_ms,
                ))

                await redis_client.srem(f"session:{session_id}:human_agents", instance_id)
                remaining = await redis_client.scard(f"session:{session_id}:human_agents")
                if remaining <= 0:
                    # Last human agent dropped — clear the fast-lookup flag
                    await redis_client.delete(f"session:{session_id}:human_agent")
                    await redis_client.delete(f"session:{session_id}:human_agents")
                    logger.info("Last human agent dropped: session=%s", session_id)

                    # ── Fase B: fire on_human_end hooks or trigger contact close ─
                    # Read pool_id, tenant_id, customer_id from session meta.
                    # If the pool declares on_human_end hooks, dispatch them now and
                    # let hook completion tracking call _trigger_contact_close.
                    # If not (or if meta is missing), close the contact immediately.
                    _pool_id_hooks    = ""
                    _tenant_id_hooks  = ""
                    _customer_id_hooks = ""
                    try:
                        _raw_meta_hooks = await redis_client.get(f"session:{session_id}:meta")
                        if _raw_meta_hooks:
                            _meta_hooks        = json.loads(_raw_meta_hooks)
                            _pool_id_hooks     = _meta_hooks.get("pool_id", "")
                            _tenant_id_hooks   = (
                                _meta_hooks.get("tenant_id", "")
                                or _meta_hooks.get("tenant", "")
                            )
                            _customer_id_hooks = (
                                _meta_hooks.get("customer_id", session_id) or session_id
                            )
                    except Exception as _exc:
                        logger.warning(
                            "agent_closed: could not read session meta for hooks: "
                            "session=%s — %s", session_id, _exc,
                        )

                    if http and _pool_id_hooks and _tenant_id_hooks:
                        _pool_cfg_hooks = await get_pool_config(
                            http, _tenant_id_hooks, _pool_id_hooks
                        )
                        _on_human_end = (
                            ((_pool_cfg_hooks or {}).get("hooks") or {})
                            .get("on_human_end", [])
                        )
                        if _on_human_end:
                            # NÃO envia "atendimento encerrado" ao cliente aqui —
                            # os hook agents (NPS, wrap-up) ainda vão interagir.
                            # A mensagem de encerramento é enviada somente quando
                            # _trigger_contact_close() executa (após todos os hooks).

                            # Escreve close_origin + customer/human participant_id no
                            # ContextStore ANTES de disparar os hooks.
                            await _write_pre_hook_context(
                                redis_client, _tenant_id_hooks, session_id,
                                close_origin="agent_closed",
                                human_instance_id=instance_id,
                            )
                            # Pool has on_human_end hooks — dispatch them.
                            # _trigger_contact_close fires when all agents complete
                            # (hook_pending counter reaches 0 in process_routed).
                            asyncio.create_task(fire_pool_hooks(
                                http=http, redis_client=redis_client,
                                session_id=session_id,
                                pool_id=_pool_id_hooks,
                                tenant_id=_tenant_id_hooks,
                                customer_id=_customer_id_hooks,
                                hook_type="on_human_end",
                            ))
                            # Safety net: if hook agents never start or complete
                            # (e.g. pool has no running instances), force-close
                            # the contact after _HOOK_TIMEOUT_S seconds so the
                            # customer WebSocket is never left open indefinitely.
                            asyncio.create_task(_hook_timeout_guard(
                                redis_client, session_id, "on_human_end",
                            ))
                            logger.info(
                                "on_human_end hooks dispatched: session=%s pool=%s count=%d "
                                "(timeout guard scheduled: %ds)",
                                session_id, _pool_id_hooks, len(_on_human_end),
                                _HOOK_TIMEOUT_S,
                            )
                        else:
                            # No hooks — close the contact immediately
                            asyncio.create_task(
                                _trigger_contact_close(redis_client, session_id)
                            )
                    else:
                        # Meta not available — fall back to immediate close
                        asyncio.create_task(
                            _trigger_contact_close(redis_client, session_id)
                        )
                else:
                    logger.info(
                        "Agent dropped, %d agent(s) still active: session=%s instance=%s",
                        remaining, session_id, instance_id,
                    )
            else:
                # instance_id not in event (legacy path) — fall back to clearing everything
                logger.warning(
                    "agent_closed without instance_id — clearing all human tracking: session=%s",
                    session_id,
                )
                await _restore_all_instances(redis_client, session_id)
                await redis_client.delete(f"session:{session_id}:human_agent")
                await redis_client.delete(f"session:{session_id}:human_agents")
                # Trigger contact close (no instance_id means we can't check hooks;
                # fall back to immediate close so the customer WS is never left open).
                asyncio.create_task(_trigger_contact_close(redis_client, session_id))

    except Exception as exc:
        logger.error("Error processing contact_closed: session=%s — %s", session_id, exc)


# ── @mention command dispatch ─────────────────────────────────────────────────

def _load_mention_commands(skill_id: str) -> dict | None:
    """
    Load mention_commands from a skill YAML (SKILLS_DIR/{skill_id}.yaml).
    Returns the dict or None if not found / not declared.
    """
    flow = _load_yaml_fallback(skill_id)
    if flow is None:
        return None
    mention_commands = flow.get("mention_commands")
    if not isinstance(mention_commands, dict):
        return None
    return mention_commands


async def dispatch_mention_command(
    redis_client:  aioredis.Redis,
    session_id:    str,
    tenant_id:     str,
    command_name:  str,
    command_def:   dict,
) -> None:
    """
    Execute a mention_command action for an already-active specialist.

    Actions (exactly one per command, Zod union):
      trigger_step: <step_id>    → LPUSH { _mention_trigger_step: step_id }
                                    to menu:result:{session_id} so the specialist's
                                    blocked menu BLPOP wakes up and jumps to step_id.
      terminate_self: true       → LPUSH { _mention_terminate: true } so the
                                    specialist's menu step returns on_failure.
      set_context: { key: val }  → HSET {tenant}:ctx:{session_id} with ContextEntry
                                    (source="mention_command", confidence=1.0).

    If acknowledge: true, also publishes mention_command.ack to agent:events:{session_id}
    so the Agent Assist UI can display a confirmation badge.
    """
    action   = command_def.get("action", {})
    ack      = command_def.get("acknowledge", False)

    trigger_step = action.get("trigger_step")
    terminate    = action.get("terminate_self", False)
    set_ctx      = action.get("set_context", {})

    if trigger_step:
        payload = json.dumps({"_mention_trigger_step": trigger_step})
        try:
            await redis_client.lpush(f"menu:result:{session_id}", payload)
            logger.info(
                "mention_command dispatch: trigger_step=%s session=%s",
                trigger_step, session_id,
            )
        except Exception as exc:
            logger.error(
                "mention_command: failed to push trigger_step=%s session=%s — %s",
                trigger_step, session_id, exc,
            )

    elif terminate:
        payload = json.dumps({"_mention_terminate": True})
        try:
            await redis_client.lpush(f"menu:result:{session_id}", payload)
            logger.info(
                "mention_command dispatch: terminate session=%s", session_id,
            )
        except Exception as exc:
            logger.error(
                "mention_command: failed to push terminate session=%s — %s", session_id, exc,
            )

    elif set_ctx:
        ctx_key = f"{tenant_id}:ctx:{session_id}"
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            for field, value in set_ctx.items():
                entry = json.dumps({
                    "value":      value,
                    "confidence": 1.0,
                    "source":     "mention_command",
                    "visibility": "agents_only",
                    "updated_at": now_iso,
                })
                await redis_client.hset(ctx_key, field, entry)
            await redis_client.expire(ctx_key, 14400)
            logger.info(
                "mention_command dispatch: set_context fields=%s session=%s",
                list(set_ctx.keys()), session_id,
            )
        except Exception as exc:
            logger.error(
                "mention_command: failed to set_context session=%s — %s", session_id, exc,
            )
    else:
        logger.warning(
            "mention_command: unknown action keys=%s session=%s — ignoring",
            list(action.keys()), session_id,
        )

    if ack:
        try:
            await redis_client.publish(
                f"agent:events:{session_id}",
                json.dumps({
                    "type":            "mention_command.ack",
                    "session_id":      session_id,
                    "command":         command_name,
                    "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                }),
            )
            logger.info(
                "mention_command ack published: session=%s command=%s", session_id, command_name,
            )
        except Exception as exc:
            logger.warning(
                "mention_command: failed to publish ack session=%s — %s", session_id, exc,
            )


async def process_mention_routing(
    msg:          dict,
    redis_client: aioredis.Redis,
) -> None:
    """
    Handle a mention_routing event from conversations.inbound.

    These events are published by routeMentions() in mcp-server-plughub/session.ts
    when a human agent sends a @alias command. They carry:
      mention_routing: true
      session_id, tenant_id, pool_id (target pool), mention_text, from_pool_id

    If the target specialist is already active in conference, dispatch the command
    to their running skill flow via menu:result interrupt.

    If the specialist is NOT yet active, do nothing — the Routing Engine will route
    the event as a new conference invite (conversations.inbound → conversations.routed
    → process_routed → activate_native_agent with conference_id).

    Specialist presence is tracked by process_routed at:
      session:{session_id}:conference:specialist:{pool_id}
        → { skill_id, instance_id, agent_type_id }  (TTL 4h)
    """
    session_id   = msg.get("session_id", "")
    pool_id      = msg.get("pool_id", "")
    mention_text = msg.get("mention_text", "")
    tenant_id    = msg.get("tenant_id", "")

    if not session_id or not pool_id:
        logger.warning("mention_routing: missing session_id or pool_id: %s", msg)
        return

    # Resolve tenant_id from session meta if absent from event
    if not tenant_id:
        try:
            raw = await redis_client.get(f"session:{session_id}:meta")
            if raw:
                tenant_id = json.loads(raw).get("tenant_id", "") or json.loads(raw).get("tenant", "")
        except Exception:
            pass

    # Check if the specialist is already active in this session
    specialist_key = f"session:{session_id}:conference:specialist:{pool_id}"
    try:
        raw_specialist = await redis_client.get(specialist_key)
    except Exception as exc:
        logger.warning(
            "mention_routing: could not read specialist key session=%s pool=%s — %s",
            session_id, pool_id, exc,
        )
        return

    if not raw_specialist:
        # Not active yet — routing engine will handle this as a new conference invite
        logger.info(
            "mention_routing: specialist pool=%s not active in session=%s — "
            "routing engine handles as new invite",
            pool_id, session_id,
        )
        return

    try:
        specialist = json.loads(raw_specialist)
    except Exception:
        logger.warning(
            "mention_routing: corrupt specialist info session=%s pool=%s", session_id, pool_id,
        )
        return

    skill_id      = specialist.get("skill_id", "")
    agent_type_id = specialist.get("agent_type_id", "")

    # Parse command name: first token of mention_text (e.g. "ativa", "pausa cliente=123")
    command_name = mention_text.strip().split()[0] if mention_text.strip() else ""
    if not command_name:
        logger.info(
            "mention_routing: bare mention (no command) session=%s pool=%s — ignoring",
            session_id, pool_id,
        )
        return

    # Load mention_commands from skill YAML.
    # Resolution order:
    #   1. skill_id   → SKILLS_DIR/skill_copilot_sac_v1.yaml  (populated if file was named after skill_id)
    #   2. agent_type_id → SKILLS_DIR/agente_copilot_v1.yaml  (the actual filename convention)
    # YAML files are named after agent_type_id (e.g. agente_copilot_v1.yaml), not skill_id
    # (skill_copilot_sac_v1), so the agent_type_id fallback is usually the one that resolves.
    mention_commands: dict | None = None
    lookup_id = ""
    for candidate in filter(None, [skill_id, agent_type_id]):
        mention_commands = _load_mention_commands(candidate)
        if mention_commands is not None:
            lookup_id = candidate
            break

    if mention_commands is None:
        logger.warning(
            "mention_routing: no mention_commands found for skill=%r agent_type=%r session=%s — ignoring",
            skill_id, agent_type_id, session_id,
        )
        return

    command_def = mention_commands.get(command_name)
    if command_def is None:
        logger.warning(
            "mention_routing: unknown command=%r skill=%s session=%s — ignoring",
            command_name, lookup_id, session_id,
        )
        return

    logger.info(
        "mention_routing: dispatching command=%r to specialist skill=%s session=%s",
        command_name, lookup_id, session_id,
    )

    await dispatch_mention_command(
        redis_client=redis_client,
        session_id=session_id,
        tenant_id=tenant_id,
        command_name=command_name,
        command_def=command_def,
    )


# ── Process conversations.inbound — forward customer messages to human agent ──

async def process_inbound(
    msg: dict,
    redis_client: aioredis.Redis,
) -> None:
    """
    Three event types share conversations.inbound:
      1. NormalizedInboundEvent (from channel-gateway) — has "author" field
      2. ConversationInboundEvent (from conversation_escalate) — no "author" field,
         consumed by the Routing Engine; nothing to do here.
      3. mention_routing event (from routeMentions in mcp-server-plughub) — has
         mention_routing=True and no "author" field; dispatched to active specialists.
    """
    if msg.get("mention_routing"):
        await process_mention_routing(msg, redis_client)
        return

    if "author" not in msg:
        return

    session_id = msg.get("session_id")
    contact_id = msg.get("contact_id")
    content    = msg.get("content", {})
    author     = msg.get("author", {})

    if not session_id or author.get("type") != "customer":
        return

    logger.info(
        "Inbound customer message: session=%s content_type=%s",
        session_id, content.get("type"),
    )

    try:
        msg_type = content.get("type")

        # Normalise payload to text regardless of channel interaction type
        if msg_type == "text":
            reply_text = content.get("text", "")
        elif msg_type == "menu_result":
            result_value = content.get("payload", {}).get("result", "")
            # For button/list results (plain string) use the raw value — json.dumps
            # would wrap it in extra quotes ("especialista" → '"especialista"'),
            # causing the choice step's strict === comparison to always fail.
            # For checklist (list) or form (dict) results, JSON-encode so the
            # BLPOP consumer receives a parseable representation.
            if isinstance(result_value, str):
                reply_text = result_value
            else:
                reply_text = json.dumps(result_value)
        else:
            logger.warning(
                "Unknown content type in inbound message: session=%s type=%s",
                session_id, msg_type,
            )
            return  # unknown content type — ignore

        # ── Check which agent types are active for this session ──────────────
        # In a conference, multiple agent types can be active simultaneously.
        # Deliver to each channel independently — do not short-circuit.
        #
        # Three delivery channels, checked independently:
        #   1. Human agent   → Redis pub/sub  agent:events:{session_id}
        #   2. Native AI     → Redis LPUSH    menu:result:{session_id}    (Skill Flow menu step)
        #   3. External-MCP  → Redis Streams  session:{session_id}:stream   (XADD, fan-out)

        is_human     = await redis_client.get(f"session:{session_id}:human_agent")

        # ── menu:waiting é agora um HASH com metadados por agente ────────────
        # Cada campo é um instanceId, valor é JSON({visibility, masked}).
        # Permite roteamento preciso: customer → agente com visibility que
        # inclui o customer; agent → agente com visibility agents_only.
        waiting_hash: dict[str, str] = {}
        try:
            raw_hash = await redis_client.hgetall(f"menu:waiting:{session_id}")
            if raw_hash:
                # redis-py pode retornar bytes ou str dependendo de decode_responses
                waiting_hash = {
                    (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                    for k, v in raw_hash.items()
                }
        except Exception:
            pass  # treat as no waiting agents
        menu_waiting = bool(waiting_hash)

        # Detect external-mcp agents: stream exists and has at least one consumer group.
        # XINFO GROUPS returns [] when the stream doesn't exist or has no groups.
        # NOTE: session:{id}:stream (not :messages) — :messages is a List used by
        # the channel-gateway for conversation history; using the same key for a Stream
        # would cause WRONGTYPE errors.
        stream_key = f"session:{session_id}:stream"
        has_stream_consumers = False
        try:
            groups = await redis_client.xinfo_groups(stream_key)
            has_stream_consumers = len(groups) > 0
        except Exception:
            pass  # stream may not exist yet — treat as no consumers

        # Legacy retry window: only for native AI agents waiting in menu step.
        # External-MCP agents don't need it — XADD persists even before XREADGROUP.
        if not menu_waiting and not is_human and not has_stream_consumers:
            for _ in range(15):   # 15 × 200ms = 3s window
                await asyncio.sleep(0.2)
                try:
                    raw_hash = await redis_client.hgetall(f"menu:waiting:{session_id}")
                    if raw_hash:
                        waiting_hash = {
                            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                            for k, v in raw_hash.items()
                        }
                        menu_waiting = True
                except Exception:
                    pass
                is_human             = await redis_client.get(f"session:{session_id}:human_agent")
                try:
                    groups               = await redis_client.xinfo_groups(stream_key)
                    has_stream_consumers = len(groups) > 0
                except Exception:
                    pass
                if menu_waiting or is_human or has_stream_consumers:
                    logger.info(
                        "Agent appeared after retry: session=%s menu=%s human=%s stream=%s",
                        session_id, bool(menu_waiting), bool(is_human), has_stream_consumers,
                    )
                    break

        logger.info(
            "Inbound routing: session=%s menu_waiting=%s(%d) is_human=%s stream_consumers=%s",
            session_id, bool(menu_waiting), len(waiting_hash), bool(is_human), has_stream_consumers,
        )

        delivered = False

        # ── Determinar mascaramento a partir do hash (com fallback legado) ──
        any_masked = False
        if waiting_hash:
            for _meta_json in waiting_hash.values():
                try:
                    _meta = json.loads(_meta_json)
                    if _meta.get("masked"):
                        any_masked = True
                        break
                except Exception:
                    pass
        if not any_masked:
            # Fallback legado: key separada menu:masked:{session_id}
            try:
                legacy_masked = await redis_client.get(f"menu:masked:{session_id}")
                if legacy_masked:
                    any_masked = True
            except Exception:
                pass

        if is_human:
            # ── Human agent: forward to Agent Assist UI via Redis pub/sub ────
            # Check if the active menu step is masked — if so, suppress the raw
            # value and show a placeholder instead. This prevents PIN / passwords
            # from ever reaching the agent's chat UI, which is the invariant
            # stated in docs/guias/masked-input.md (maskedScope is memory-only).
            if any_masked:
                display_text = "[entrada mascarada — conteúdo não disponível]"
                visibility   = "agents_only"
                logger.info(
                    "Masked menu reply suppressed for human agent: session=%s", session_id,
                )
            else:
                display_text = reply_text if msg_type == "text" else f"[Seleção: {reply_text}]"
                visibility   = "all"
            event = {
                "type":       "message.text",
                "message_id": msg.get("message_id", str(uuid.uuid4())),
                "author":     author,
                "text":       display_text,
                "timestamp":  msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "session_id": session_id,
                "contact_id": contact_id,
                "visibility": visibility,
            }
            await redis_client.publish(f"agent:events:{session_id}", json.dumps(event))
            logger.info("Forwarded %s to human agent: session=%s masked=%s",
                        msg_type, session_id, bool(any_masked))

            # Write to canonical stream so supervision SSE and analytics can see the message
            try:
                stream_key_human = f"session:{session_id}:stream"
                await redis_client.xadd(
                    stream_key_human,
                    {
                        "event_id":    event.get("message_id", str(uuid.uuid4())),
                        "type":        "message",
                        "timestamp":   event.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        "author_id":   author.get("id") or contact_id or "customer",
                        "author_role": "customer",
                        "visibility":  visibility,
                        "content":     json.dumps({"text": display_text}),
                    },
                )
                await redis_client.expire(stream_key_human, 14400)  # 4h TTL
            except Exception as _xadd_exc:
                logger.warning(
                    "Could not XADD customer message to stream: session=%s — %s",
                    session_id, _xadd_exc,
                )

            delivered = True

        if waiting_hash:
            # ── Native AI agents in Skill Flow menu step: route by visibility ──
            # Customer messages go to agents whose visibility includes the customer
            # (visibility "all" or array containing customer's participant_id).
            # Each agent has its own isolated BLPOP key: menu:result:{session_id}:{instanceId}
            customer_pid = contact_id or "customer"
            for agent_key, meta_json in waiting_hash.items():
                try:
                    meta = json.loads(meta_json)
                except Exception:
                    meta = {"visibility": "all"}
                vis = meta.get("visibility", "all")

                # Determine if this agent is waiting for customer input:
                # - "all": always receives customer messages
                # - array: receives if customer_pid is in the array
                # - "agents_only": does NOT receive customer messages
                is_customer_facing = False
                if vis == "all":
                    is_customer_facing = True
                elif isinstance(vis, list):
                    # Array of participant IDs — customer is in the audience
                    is_customer_facing = True  # customer sent a message, and they can see it
                # "agents_only" → skip — customer messages are not for this agent

                if is_customer_facing:
                    result_key = (
                        f"menu:result:{session_id}:{agent_key}"
                        if agent_key != "_default_"
                        else f"menu:result:{session_id}"
                    )
                    await redis_client.lpush(result_key, reply_text)
                    logger.info(
                        "Pushed menu reply to AI agent: session=%s agent=%s key=%s text=%r",
                        session_id, agent_key, result_key, reply_text[:80],
                    )
            delivered = True

        if has_stream_consumers:
            # ── External-MCP agents: XADD to session stream ───────────────────
            # Fan-out nativo: cada consumer group (um por instance_id) recebe
            # uma cópia independente da mensagem via XREADGROUP no wait_for_message.
            # Não é necessário conhecer quais instâncias estão esperando — o stream
            # persiste e cada agente consome no seu próprio ritmo.
            try:
                await redis_client.xadd(
                    stream_key,
                    {
                        "type":       "message.text",
                        "text":       reply_text,
                        "author":     json.dumps(author),
                        "message_id": msg.get("message_id", str(uuid.uuid4())),
                        "timestamp":  msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        "contact_id": contact_id or "",
                    },
                    maxlen=500,   # descarta itens mais antigos se stream crescer demais
                )
                await redis_client.expire(stream_key, 14400)  # renova TTL 4h a cada mensagem
                logger.info(
                    "XADD to session stream: session=%s groups=%d text=%r",
                    session_id, len(groups), reply_text[:80],
                )
                delivered = True
            except Exception as exc:
                logger.error(
                    "Failed to XADD to session stream: session=%s — %s", session_id, exc
                )

        if not delivered:
            # No active agent recognised for this session — message dropped.
            # This is normal when the AI is between steps (not in menu).
            logger.warning(
                "No active agent for inbound message (dropped): session=%s msg_type=%s",
                session_id, msg_type,
            )

    except Exception as exc:
        logger.error("Error forwarding inbound message: session=%s — %s", session_id, exc)


# ── Instance restore helpers ──────────────────────────────────────────────────

async def _restore_instance(
    redis_client: aioredis.Redis,
    session_id:   str,
    instance_id:  str,
) -> None:
    """
    Restore a single agent instance to ready state using its per-instance
    routing snapshot (session:{session_id}:routing:{instance_id}).
    No-op if the snapshot doesn't exist.
    """
    if not instance_id:
        return
    try:
        raw = await redis_client.get(f"session:{session_id}:routing:{instance_id}")
        if not raw:
            return
        info     = json.loads(raw)
        tenant   = info.get("tenant_id", "")
        inst     = info.get("instance_id", instance_id)
        pool     = info.get("pool_id", "")
        snapshot = info.get("snapshot", {})
        if tenant and inst and snapshot:
            snapshot["current_sessions"] = max(0, int(snapshot.get("current_sessions", 1)) - 1)
            snapshot["status"] = "ready"
            # Use 24h TTL — matches seed-demo.sh so the instance survives across sessions.
            # Previous code used 1h; after restoration the key expired, routing couldn't find
            # the instance (key gone but ID still in pool instances set), causing contacts to queue.
            await redis_client.set(f"{tenant}:instance:{inst}", json.dumps(snapshot), ex=86400)
            if pool:
                await redis_client.sadd(f"{tenant}:pool:{pool}:instances", inst)
            logger.info("Instance restored: tenant=%s instance=%s pool=%s", tenant, inst, pool)
        await redis_client.delete(f"session:{session_id}:routing:{instance_id}")
    except Exception as exc:
        logger.warning("Could not restore instance %s: session=%s — %s", instance_id, session_id, exc)


async def _restore_all_instances(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> None:
    """
    Restore every instance tracked in session:{session_id}:human_agents SET.
    Used when the customer disconnects (all agents in the session should be freed).
    """
    try:
        members = await redis_client.smembers(f"session:{session_id}:human_agents")
        for inst_id in members:
            await _restore_instance(redis_client, session_id, inst_id)
    except Exception as exc:
        logger.warning("Could not restore all instances: session=%s — %s", session_id, exc)


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run() -> None:
    global _kafka_producer
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    consumer = AIOKafkaConsumer(
        TOPIC_ROUTED,
        TOPIC_QUEUED,
        TOPIC_INBOUND,
        TOPIC_EVENTS,
        TOPIC_REGISTRY_CHANGED,
        TOPIC_CONFIG_CHANGED,
        bootstrap_servers=KAFKA_BROKERS,
        group_id=GROUP_ID,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="latest",
    )
    await consumer.start()

    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BROKERS)
    await producer.start()
    _kafka_producer = producer
    logger.info("Kafka producer started (pool hooks)")

    logger.info(
        "✅ Orchestrator Bridge started — topics: %s, %s, %s, %s, %s, %s",
        TOPIC_ROUTED, TOPIC_QUEUED, TOPIC_INBOUND, TOPIC_EVENTS,
        TOPIC_REGISTRY_CHANGED, TOPIC_CONFIG_CHANGED,
    )
    logger.info("   skill-flow-service: %s", SKILL_FLOW_URL)
    logger.info("   agent-registry:     %s", AGENT_REGISTRY_URL)
    logger.info("   YAML fallback dir:  %s", SKILLS_DIR)
    logger.info("   bootstrap tenants:  %s", BOOTSTRAP_TENANT_IDS)

    # ── Instance Bootstrap (created before the http session, used inside it) ──
    bootstrap = InstanceBootstrap(
        redis=redis_client,
        registry_url=AGENT_REGISTRY_URL,
        tenant_ids=BOOTSTRAP_TENANT_IDS,
    )

    # ── Registry Sync ─────────────────────────────────────────────────────────
    # Reads declarative YAML config and upserts pools + agent types into the
    # Agent Registry (PostgreSQL). Runs before InstanceBootstrap so that the
    # registry is always consistent with the declared configuration, even on a
    # completely fresh environment. Idempotent — safe to run on every startup.
    syncer = RegistrySyncer(
        registry_url=AGENT_REGISTRY_URL,
        config_path=REGISTRY_CONFIG_DIR or None,
        skills_dir=SKILLS_DIR or None,
    )

    async with aiohttp.ClientSession() as http:
        # 1. Sync registry first (upsert pools + agent types from YAML)
        sync_reports = await syncer.sync(http)
        if sync_reports:
            logger.info("Registry sync complete (%d tenant(s))", len(sync_reports))
        elif REGISTRY_CONFIG_DIR:
            logger.warning(
                "Registry sync: REGISTRY_CONFIG_DIR=%r but no configs loaded — "
                "check path and YAML format", REGISTRY_CONFIG_DIR
            )

        # 2. Reconcile Redis instances from the (now up-to-date) registry.
        # Initial reconciliation — compares Registry vs Redis and applies the diff.
        # Idempotent: safe to re-run; only applies what has actually changed.
        reports = await bootstrap.reconcile(http)
        for r in reports:
            level = logging.WARNING if r.errors else logging.INFO
            logger.log(level, "Startup reconciliation: %s", r.summary())

        # Write readiness signal to Redis so E2E tests and health probes can
        # detect that the initial reconciliation completed without polling logs.
        # Key: {tenant}:bootstrap:ready  TTL: 60s (renewed by heartbeat)
        for tenant_id in BOOTSTRAP_TENANT_IDS:
            await redis_client.set(
                f"{tenant_id}:bootstrap:ready",
                "1",
                ex=60,
            )
        logger.info("Bootstrap readiness signal written for tenants: %s", BOOTSTRAP_TENANT_IDS)

        # Heartbeat loop runs as a background task — renews instance TTLs every 15s,
        # applies pending updates, and runs a full reconciliation every 5 min or
        # immediately when registry.changed signals a config update.
        heartbeat_task = asyncio.create_task(bootstrap.heartbeat_loop())

        try:
            async for msg in consumer:
                asyncio.create_task(_dispatch(msg.value, msg.topic, http, redis_client, bootstrap))
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await consumer.stop()
            await producer.stop()
            _kafka_producer = None
            await redis_client.aclose()


async def _dispatch(
    payload:      dict,
    topic:        str,
    http:         aiohttp.ClientSession,
    redis_client: aioredis.Redis,
    bootstrap:    InstanceBootstrap,
) -> None:
    try:
        if topic == TOPIC_ROUTED:
            await process_routed(payload, http, redis_client)
        elif topic == TOPIC_QUEUED:
            await process_queued(payload, http, redis_client)
        elif topic == TOPIC_INBOUND:
            await process_inbound(payload, redis_client)
        elif topic == TOPIC_EVENTS:
            await process_contact_event(payload, redis_client, http)
        elif topic == TOPIC_REGISTRY_CHANGED:
            # Agent Registry published a structural change (AgentType/Pool/Skill CRUD).
            entity_type = payload.get("entity_type", "?")
            entity_id   = payload.get("entity_id",   "?")
            logger.info(
                "registry.changed received: entity=%s id=%s — scheduling instance re-bootstrap",
                entity_type, entity_id,
            )
            # Skill update: invalidate the in-memory flow cache so the next agent
            # activation fetches the updated flow from the Agent Registry.
            # Using entity_id directly covers both:
            #   - Registry path: skill_id == entity_id (e.g. "skill_copilot_sac_v1")
            # Skills loaded via YAML fallback are never cached, so they reload
            # from disk on every activation — no cache entry to invalidate.
            if entity_type == "skill":
                if entity_id in _skill_flow_cache:
                    del _skill_flow_cache[entity_id]
                    logger.info("Skill flow cache invalidated: skill_id=%s", entity_id)
                else:
                    logger.debug("Skill flow cache miss on invalidation (not cached): %s", entity_id)
            bootstrap.request_refresh()
        elif topic == TOPIC_CONFIG_CHANGED:
            await _handle_config_changed(payload, bootstrap)
    except Exception as exc:
        logger.error("Dispatch error topic=%s: %s", topic, exc)


async def _handle_config_changed(
    payload:   dict,
    bootstrap: InstanceBootstrap,
) -> None:
    """
    Reacts to a config.changed event published by the Config API.

    Routing:
      namespace=quota      → bootstrap.request_refresh()
                             (max_concurrent_sessions or quota limits changed;
                              reconciliation may need to create or remove instances)

      namespace=routing /
               session /
               masking /
               webchat /
               sentiment /
               consumer /
               dashboard   → no bootstrap action needed.
                             These values are read at runtime via ConfigStore
                             (60s cache); the cache will naturally pick up the
                             new values on next access.

    If a future namespace requires a bootstrap trigger, add it to
    _BOOTSTRAP_NAMESPACES in the constants section.
    """
    namespace  = payload.get("namespace", "")
    key        = payload.get("key", "")
    tenant_id  = payload.get("tenant_id", "__global__")
    operation  = payload.get("operation", "set")

    if namespace in _BOOTSTRAP_NAMESPACES:
        logger.info(
            "config.changed [%s] tenant=%s %s.%s — scheduling instance re-bootstrap",
            operation, tenant_id, namespace, key,
        )
        bootstrap.request_refresh()
    else:
        logger.info(
            "config.changed [%s] tenant=%s %s.%s — runtime config, no bootstrap needed",
            operation, tenant_id, namespace, key,
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
