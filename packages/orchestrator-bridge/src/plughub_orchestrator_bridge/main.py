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
from aiokafka import AIOKafkaConsumer

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

TOPIC_ROUTED  = "conversations.routed"
TOPIC_QUEUED  = "conversations.queued"
TOPIC_INBOUND = "conversations.inbound"
TOPIC_EVENTS  = "conversations.events"
GROUP_ID      = "orchestrator-bridge"


# ── Agent type resolution ─────────────────────────────────────────────────────
# Cached in memory to avoid repeated Registry calls for the same agent type.
# Cache is not invalidated during the process lifetime — acceptable because
# agent type registrations are immutable (new version = new agent_type_id).

_agent_type_cache: dict[str, dict] = {}   # agent_type_id → agent type response body
_skill_flow_cache: dict[str, dict] = {}   # skill_id → flow dict


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

    payload = {
        "tenant_id":       tenant_id,
        "session_id":      session_id,
        "customer_id":     customer_id,
        "skill_id":        skill_id,
        "flow":            flow,
        "instance_id":     instance_id,   # stored in execution lock by the engine
        "session_context": session_context,
    }

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
    try:
        await redis_client.publish(f"pool:events:{pool_id}", json.dumps(event))
        logger.info("Human agent notified: session=%s pool=%s", session_id, pool_id)
    except Exception as exc:
        logger.error("Redis publish error: session=%s — %s", session_id, exc)


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

        agent_result = await activate_native_agent(
            http=http, redis_client=redis_client,
            session_id=session_id, customer_id=customer_id,
            agent_type_id=agent_type_id, tenant_id=tenant_id,
            skills=skills,
            instance_id=native_instance_id,
            conference_id=conference_id,
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

    elif framework == "human":
        await activate_human_agent(
            redis_client=redis_client,
            session_id=session_id, pool_id=pool_id,
            tenant_id=tenant_id,
            routing_result=result,
        )

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

    customer_side = reason in ("client_disconnect", "timeout", "agent_done")

    try:
        if customer_side:
            # ── Marcar sessão como encerrada (para rejeitar context_packages obsoletos) ─
            # wait_for_assignment verifica a AUSÊNCIA desta chave antes de retornar
            # um context_package ao agente externo. Usando marcador de fechamento
            # (em vez de checar presença de session:meta, que tem TTL de 4h) porque
            # meta persiste muito tempo após o encerramento e causaria falso-positivo.
            # TTL 4h: cobre a janela onde itens obsoletos podem estar na fila.
            try:
                await redis_client.setex(f"session:{session_id}:closed", 14400, reason)
            except Exception as exc:
                logger.warning("Could not set session:closed marker: session=%s — %s", session_id, exc)

            # ── Signal session closed — dois mecanismos em paralelo ───────────
            #
            # 1. LPUSH session:closed:{session_id}   — desbloqueia BLPOP legado
            #    (Skill Flow menu step, wait_for_message de versões anteriores).
            #    TTL 10s — consumo imediato esperado.
            #
            # 2. XADD session:{session_id}:stream  — desbloqueia XREADGROUP de
            #    agentes external-mcp usando wait_for_message com Streams.
            #    Item {type: session_closed} na mesma fila de mensagens garante
            #    que o sinal respeita a ordem de entrega — não chega antes de
            #    mensagens do cliente já enfileiradas.
            #    NOTA: usa :stream (não :messages) — :messages é uma List do canal-gateway.
            try:
                await redis_client.lpush(f"session:closed:{session_id}", reason)
                await redis_client.expire(f"session:closed:{session_id}", 10)
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

            # ── Notify all active human agents that the session ended ─────────
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

                # ── Clean up all human-agent tracking for this session ────────
                await redis_client.delete(f"session:{session_id}:human_agent")
                await redis_client.delete(f"session:{session_id}:human_agents")

            # ── Clear conversation data so next agent doesn't see stale data ───
            # session:{id}:messages — List (channel-gateway conversation history)
            # session:{id}:stream   — Redis Stream (external-mcp wait_for_message)
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
                await redis_client.srem(f"session:{session_id}:human_agents", instance_id)
                remaining = await redis_client.scard(f"session:{session_id}:human_agents")
                if remaining <= 0:
                    # Last human agent dropped — clear the fast-lookup flag
                    await redis_client.delete(f"session:{session_id}:human_agent")
                    await redis_client.delete(f"session:{session_id}:human_agents")
                    logger.info("Last human agent dropped: session=%s", session_id)
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

    except Exception as exc:
        logger.error("Error processing contact_closed: session=%s — %s", session_id, exc)


# ── Process conversations.inbound — forward customer messages to human agent ──

async def process_inbound(
    msg: dict,
    redis_client: aioredis.Redis,
) -> None:
    """
    Two event types share conversations.inbound:
      1. NormalizedInboundEvent (from channel-gateway) — has "author" field
      2. ConversationInboundEvent (from conversation_escalate) — no "author" field,
         consumed by the Routing Engine; nothing to do here.
    """
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
        menu_waiting = await redis_client.get(f"menu:waiting:{session_id}")

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
                menu_waiting         = await redis_client.get(f"menu:waiting:{session_id}")
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
            "Inbound routing: session=%s menu_waiting=%s is_human=%s stream_consumers=%s",
            session_id, bool(menu_waiting), bool(is_human), has_stream_consumers,
        )

        delivered = False

        if is_human:
            # ── Human agent: forward to Agent Assist UI via Redis pub/sub ────
            event = {
                "type":       "message.text",
                "message_id": msg.get("message_id", str(uuid.uuid4())),
                "author":     author,
                "text":       reply_text if msg_type == "text" else f"[Seleção: {reply_text}]",
                "timestamp":  msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "session_id": session_id,
                "contact_id": contact_id,
            }
            await redis_client.publish(f"agent:events:{session_id}", json.dumps(event))
            logger.info("Forwarded %s to human agent: session=%s", msg_type, session_id)
            delivered = True

        if menu_waiting:
            # ── Native AI agent in Skill Flow menu step: unblock BLPOP ───────
            await redis_client.lpush(f"menu:result:{session_id}", reply_text)
            logger.info(
                "Pushed menu reply to native AI session: session=%s text=%r",
                session_id, reply_text[:80],
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
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    consumer = AIOKafkaConsumer(
        TOPIC_ROUTED,
        TOPIC_QUEUED,
        TOPIC_INBOUND,
        TOPIC_EVENTS,
        bootstrap_servers=KAFKA_BROKERS,
        group_id=GROUP_ID,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info(
        "✅ Orchestrator Bridge started — topics: %s, %s, %s, %s",
        TOPIC_ROUTED, TOPIC_QUEUED, TOPIC_INBOUND, TOPIC_EVENTS,
    )
    logger.info("   skill-flow-service: %s", SKILL_FLOW_URL)
    logger.info("   agent-registry:     %s", AGENT_REGISTRY_URL)
    logger.info("   YAML fallback dir:  %s", SKILLS_DIR)

    async with aiohttp.ClientSession() as http:
        try:
            async for msg in consumer:
                asyncio.create_task(_dispatch(msg.value, msg.topic, http, redis_client))
        finally:
            await consumer.stop()
            await redis_client.aclose()


async def _dispatch(
    payload:      dict,
    topic:        str,
    http:         aiohttp.ClientSession,
    redis_client: aioredis.Redis,
) -> None:
    try:
        if topic == TOPIC_ROUTED:
            await process_routed(payload, http, redis_client)
        elif topic == TOPIC_QUEUED:
            await process_queued(payload, http, redis_client)
        elif topic == TOPIC_INBOUND:
            await process_inbound(payload, redis_client)
        elif topic == TOPIC_EVENTS:
            await process_contact_event(payload, redis_client)
    except Exception as exc:
        logger.error("Dispatch error topic=%s: %s", topic, exc)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
