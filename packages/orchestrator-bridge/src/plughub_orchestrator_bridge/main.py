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
from .session_config import session_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("plughub.orchestrator-bridge")


# ── Config ────────────────────────────────────────────────────────────────────

KAFKA_BROKERS       = os.getenv("KAFKA_BROKERS",       "localhost:9092")
KAFKA_DLQ_TOPIC     = os.getenv("KAFKA_DLQ_TOPIC",     "events.dead_letter")
REDIS_URL           = os.getenv("REDIS_URL",            "redis://localhost:6379")
SKILL_FLOW_URL      = os.getenv("SKILL_FLOW_URL",       "http://localhost:3400")
AGENT_REGISTRY_URL  = os.getenv("AGENT_REGISTRY_URL",  "http://localhost:3300")
CONFIG_API_URL      = os.getenv("CONFIG_API_URL",       "http://localhost:3500")

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
TOPIC_LIFECYCLE         = "agent.lifecycle"
GROUP_ID                = "orchestrator-bridge"

# ── Reliability ───────────────────────────────────────────────────────────────
_MAX_DISPATCH_ATTEMPTS    = 3
_DISPATCH_BACKOFF_BASE_MS = 500   # 500ms → 1 000ms between retries

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
_skill_version_cache: dict[str, str] = {} # skill_id → version (R9 — deploy_version do segmento)
# Skill Versioning Fase B/P1: produção = snapshot do slot `current` do POOL (não o
# skill.flow vivo). pool_id → (skill_id, flow). Invalidado no registry.changed(pool).
_pool_flow_cache: dict[str, tuple[str, dict]] = {}
# Skill Versioning Fase C: identidade da VERSÃO = `set_at` do slot `current` (momento
# do promote). pool_id → set_at (ISO). Carimbado em segments.deploy_version.
_pool_deploy_version_cache: dict[str, str] = {}
# Config params por deploy (fatia 1): config_json do slot `current` do pool → injetado
# no launch como `$.config.*`. pool_id → config_json (dict). Populado por
# get_pool_current_flow (mesma leitura do slot) e invalidado junto com _pool_flow_cache
# no registry.changed(pool). Entrada presente ⟺ o pool rodou pelo slot (path de produção).
_pool_config_cache: dict[str, dict] = {}


def _stl() -> int:
    """Returns the session Redis TTL (seconds) from Config API, with hardcoded fallback."""
    return int(session_config.get("orchestrator_session_ttl_s", 14_400))


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
                # Fase 3a — deploy-driven: the agent_type_id may actually be a
                # deployed skill_id (instances provisioned from PoolSkillSlot carry
                # agent_type_id = skill_id). If a skill with that id has a flow,
                # synthesize a native agent_type so EVERY activation path (routed,
                # conference specialist, queue agent, restore) resolves it uniformly
                # without depending on a registered agent_type.
                synth = await _synthesize_agent_type_from_skill(http, tenant_id, agent_type_id)
                if synth is not None:
                    _agent_type_cache[cache_key] = synth
                    return synth
            else:
                logger.warning(
                    "Agent Registry returned HTTP %d for agent-type=%s", resp.status, agent_type_id
                )
    except Exception as exc:
        logger.warning("Agent Registry unreachable (%s): %s", url, exc)

    return None


async def _synthesize_agent_type_from_skill(
    http: aiohttp.ClientSession,
    tenant_id: str,
    skill_id: str,
) -> dict | None:
    """
    Fase 3a — build a native agent_type dict from a deployed skill, used when no
    agent_type is registered for the given id but a skill with that id has a
    flow. Returns None when the skill has no flow (so callers fall through to
    their existing fallbacks).
    """
    flow = await get_skill_flow(http, tenant_id, skill_id)
    if not flow:
        return None
    logger.info(
        "Deploy-driven: synthesized native agent_type from skill_id=%s "
        "(no registered agent_type)",
        skill_id,
    )
    synth: dict = {
        "agent_type_id":          skill_id,
        "framework":              "plughub-native",
        "execution_model":        "stateless",
        # role is not consumed at runtime (no reads in routing-engine or the
        # bridge); evaluator-pool isolation comes from routing topology, not
        # this field. Defaulted to "executor" only for cosmetic completeness.
        "role":                   "executor",
        "skills":                 [{"skill_id": skill_id}],
        "media_capabilities":     [],
        "_synthesized_from_skill": True,
    }
    # mention_commands rides inside the flow JSON (see RegistrySyncer._sync_skills).
    # Carry it onto the synthesized agent_type so @mention specialists work under
    # deploy-driven provisioning, where there is no registered agent_type to read.
    mention_commands = flow.get("mention_commands")
    if isinstance(mention_commands, dict):
        synth["mention_commands"] = mention_commands
    return synth


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


async def _write_routing_assigned_to_stream(
    redis_client: aioredis.Redis,
    session_id:   str,
    agent_type:   dict,
    pool_config:  dict,
    segment_id:   str,
    instance_id:  str,
) -> None:
    """
    Write a routing.assigned entry to the session stream.

    The WebRTC adapter (_stream_watcher) watches for this event to negotiate
    the media medium (video / voice / text), create the LiveKit room, and send
    webrtc.ready to the customer browser.  Non-WebRTC sessions ignore the event.

    Written on every agent activation (native, human, external-mcp) so that
    the WebRTC adapter can react regardless of framework.  Fire-and-forget safe
    — a failure here never blocks the main routing path.

    Arc 15 Phase B.
    """
    try:
        await redis_client.xadd(
            f"session:{session_id}:stream",
            {
                "type":        "routing.assigned",
                "agent_type":  json.dumps({
                    "media_capabilities": agent_type.get("media_capabilities", []),
                }),
                "pool":        json.dumps(pool_config),
                "segment_id":  segment_id,
                "instance_id": instance_id,
            },
            maxlen=500,
        )
        logger.debug(
            "routing.assigned written: session=%s instance=%s caps=%s",
            session_id, instance_id, agent_type.get("media_capabilities", []),
        )
    except Exception as exc:
        logger.warning(
            "Could not write routing.assigned to stream: session=%s — %s",
            session_id, exc,
        )


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
                # R9 — cacheia a versão do skill p/ carimbar deploy_version no segmento
                # (resolvida no início, do corpo do skill = versão corrente = a que rodou).
                _skill_version_cache[skill_id] = str(body.get("version") or "")
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


async def get_pool_current_flow(
    http: aiohttp.ClientSession,
    tenant_id: str,
    pool_id: str,
) -> tuple[str, dict] | None:
    """
    Skill Versioning Fase B/P1 — PRODUÇÃO = snapshot do slot `current` do POOL.

    Resolve o flow que o pool de fato deve executar lendo o slot `current`
    (`GET /v1/pools/{pool}/slots`), em vez do `skill.flow` vivo. Garante que
    edições no editor (que escrevem `flow_draft`) NÃO vazem para produção — só o
    deploy (set-next → promote) preenche o `current`. Retorna `(skill_id, flow)`
    ou None quando o pool não tem `current` configurado (→ caller faz fallback p/
    o caminho por skill_id). Cacheia por pool_id; invalidado no registry.changed(pool).
    """
    if not pool_id:
        return None
    if pool_id in _pool_flow_cache:
        return _pool_flow_cache[pool_id]

    url = f"{AGENT_REGISTRY_URL}/v1/pools/{pool_id}/slots"
    headers = {"x-tenant-id": tenant_id}
    try:
        async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                return None
            body    = await resp.json()
            current = (body.get("slots") or {}).get("current") or {}
            if not current.get("set"):
                return None
            skill_id = str(current.get("skill_id") or "")
            flow     = current.get("yaml_snapshot")
            if skill_id and isinstance(flow, dict) and flow:
                # Fase C: identidade da versão = `set_at` do slot (momento do promote).
                # Cacheado por pool e carimbado em segments.deploy_version (em vez de
                # skill.version). Casa com SkillDeployment.deployed_at no epoch.
                set_at = str(current.get("set_at") or "")
                if set_at:
                    _pool_deploy_version_cache[pool_id] = set_at
                # Fatia 1: config params do deploy — o config_json do MESMO slot vira
                # `$.config.*` no launch (injetado em activate_native_agent). Presença
                # da entrada ⟺ o pool rodou pelo slot; ausente = fallback sem config.
                cfg = current.get("config_json")
                _pool_config_cache[pool_id] = cfg if isinstance(cfg, dict) else {}
                _pool_flow_cache[pool_id] = (skill_id, flow)
                return skill_id, flow
            return None
    except Exception as exc:
        logger.warning("Pool slots unreachable (%s): %s", url, exc)
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
    pool_id: str = "",
) -> tuple[str, dict] | None:
    """
    Resolve the flow to execute. Returns (skill_id, flow_dict) or None.

    Resolution order (Skill Versioning Fase B/P1):
      0. POOL: snapshot do slot `current` do pool (PRODUÇÃO real — o deploy é por pool).
         É a fonte autoritativa; só cai p/ os passos abaixo se o pool não tiver `current`
         (retrocompat: pools ainda não migrados ao slot rodam o skill.flow publicado).
      1. First skill in skills[] with a flow in Agent Registry (skill.flow publicado)
      2. YAML fallback in SKILLS_DIR
    """
    # 0. Pool current slot (produção por-pool) — preferencial.
    pool_flow = await get_pool_current_flow(http, tenant_id, pool_id)
    if pool_flow:
        return pool_flow

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
    webhook_pool: bool = False,
    resume_context: dict | None = None,
    pool_id: str = "",
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
    resolved = await resolve_flow_for_agent(http, tenant_id, agent_type_id, skills, pool_id)
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
    # Fatia 1: config params por deploy — o config_json do slot `current` do pool
    # (populado no cache por resolve_flow_for_agent → get_pool_current_flow, acima)
    # é injetado como `config` → resolvido no engine como `$.config.*`. Só quando o
    # flow veio do slot (path de produção) e há config; fallback (skill.flow/YAML) = sem config.
    pool_config = _pool_config_cache.get(pool_id, {}) if pool_id else {}
    if pool_config:
        payload["config"] = pool_config
    # segment_id for segment-scoped ContextStore writes (scope: segment in YAML).
    # Allows parallel agents (NPS + wrap-up) to isolate their data per participation.
    if segment_id:
        payload["segment_id"] = segment_id
    # Arc 19: webhook pool sessions wire persistSuspendWebhook in skill-flow-service.
    if webhook_pool:
        payload["webhook_pool"] = True
    # Arc 19: resume context — when set, the engine picks up from the suspended step.
    if resume_context:
        payload["resume_context"] = resume_context
    # Conference agents (hook agents AND task-step specialists) share the same
    # session_id for message delivery, but each needs its own pipeline_state and
    # execution lock so two of them running in parallel on the same session never
    # collide on the lock (G7 Camada 3 — fan-out de wrap-up concorrente).
    # Isolate by the agent's unique segment_id; fall back to instance_id, then a
    # uuid — NEVER the raw session_id, which the primary agent legitimately uses
    # for its own pipeline.  (Before: the segment-less branch keyed by
    # conference_id, which collided when two agents shared a conference_id; and
    # the YAML-fallback path passed neither, silently keying on session_id puro.)
    #
    # Arc 19: webhook sessions are PRIMARY (non-conference) agents — they must
    # NOT use a suffixed pipeline_session_id.  The analytics-api
    # GET /sessions/{id}/pipeline-state reads from {tenant}:pipeline:{session_id}
    # directly; a suffix would make the pipeline inaccessible to analytics.
    # Only apply the suffix when conference_id is set (conference isolation).
    if conference_id:
        iso = segment_id or instance_id or uuid.uuid4().hex
        payload["pipeline_session_id"] = f"{session_id}--seg--{iso[:8]}"
    # else: primary agents (including webhook) use session_id directly — no suffix

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
        await redis_client.setex(f"session:{session_id}:human_agent", _stl(), "1")
        # ── Track this specific instance in a SET for conference support ────────
        # Allows multiple human agents to share the same session_id.
        # process_contact_event uses SREM to remove each agent on agent_done and
        # clears the human_agent flag only when the SET becomes empty.
        if instance_id:
            await redis_client.sadd(f"session:{session_id}:human_agents", instance_id)
            await redis_client.expire(f"session:{session_id}:human_agents", _stl())
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

    # ── Store instance_id and pool_id in session meta ─────────────────────────
    # The Agent Assist UI does not pass instance_id in the agent_done request,
    # so the mcp-server reads it from here. In true conference the last-writer
    # wins — acceptable since the REST endpoint is only used by human agents.
    #
    # pool_id is critical for hook dispatch: when the human agent calls
    # agent_done, process_contact_event reads pool_id from meta to find the
    # pool_config and its on_human_end hooks. Without pool_id, no hooks fire
    # and the session closes immediately without NPS/wrap-up.
    # C1 — human identity: read the login (email) off the instance so it can be
    # denormalized onto the segment via the participant events (join + left).
    _human_user_login = ""
    if instance_id:
        try:
            _raw_inst = await redis_client.get(f"{tenant_id}:instance:{instance_id}")
            if _raw_inst:
                _human_user_login = (json.loads(_raw_inst) or {}).get("user_login", "") or ""
        except Exception:
            pass
        try:
            raw_meta = await redis_client.get(f"session:{session_id}:meta")
            if raw_meta:
                meta = json.loads(raw_meta)
                meta["instance_id"]   = instance_id
                meta["pool_id"]       = pool_id
                meta["agent_type_id"] = routing_result.get("agent_type_id", "")
                meta["user_login"]    = _human_user_login
                await redis_client.setex(f"session:{session_id}:meta", _stl(), json.dumps(meta))
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
            await redis_client.expire(f"session:{session_id}:segment_seq", _stl())
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
        # G7 Slice 1b: per-instance participant meta (pool/agent_type/tenant/login)
        # keyed by instance_id — fonte por-participante para o path de close, evitando
        # o meta.pool_id de SESSÃO (last-writer, errado em multi-humano). Ver g7 §11.
        try:
            await redis_client.setex(
                f"session:{session_id}:participant_meta:{instance_id}",
                14400,
                json.dumps({
                    "pool_id":       pool_id,
                    "agent_type_id": routing_result.get("agent_type_id", "") or "",
                    "tenant_id":     tenant_id,
                    "user_login":    _human_user_login,
                }),
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
        user_login=_human_user_login,
    ))


# ── Pre-hook ContextStore writes ──────────────────────────────────────────────

async def _write_pre_hook_context(
    redis_client: aioredis.Redis,
    tenant_id:    str,
    session_id:   str,
    close_origin: str,
    human_instance_id: str | None = None,
    customer_participant_id: str | None = None,
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
        # Fallback: if dedicated key expired/missing, use value from session meta
        if not cust_pid and customer_participant_id:
            cust_pid = customer_participant_id
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

        await redis_client.expire(ctx_key, _stl())
        logger.info(
            "pre_hook context written: session=%s close_origin=%s cust_pid=%s human_pid=%s",
            session_id, close_origin, bool(cust_pid), human_instance_id or "none",
        )
    except Exception as exc:
        logger.warning(
            "pre_hook context write failed: session=%s — %s (non-fatal)", session_id, exc,
        )


# ── receive:waiting router ────────────────────────────────────────────────────

async def _route_to_receive_waiting(
    redis_client:         aioredis.Redis,
    session_id:           str,
    event_type:           str,
    author_id:            str,
    author_role:          str,
    visibility:           str,
    content:              str,
    instance_id_of_author: str | None = None,
) -> int:
    """
    Route a stream event to any receive:waiting agents whose filter matches.

    Called from:
      - conversations.inbound handler (customer messages)
      - notification_send path (agent messages, via Kafka conversations.events)

    Filter semantics (all fields AND):
      event_types   — event must be in the list (default: ["message_sent"])
      author_role   — event author_role must be in the list (null = any)
      visibility    — event visibility must match (null = any)

    Echo suppression: the instance that authored the event never receives it in
    its own receive:result queue (prevents the evaluator from analysing its own
    output and entering an infinite loop).

    Returns the number of instances notified.
    """
    waiting: dict[str, str] = {}
    try:
        raw = await redis_client.hgetall(f"receive:waiting:{session_id}")
        if raw:
            waiting = {
                (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in raw.items()
            }
    except Exception:
        return 0

    if not waiting:
        return 0

    notified = 0
    now_iso  = datetime.now(timezone.utc).isoformat()
    payload  = json.dumps({
        "event_type":  event_type,
        "author_id":   author_id,
        "author_role": author_role,
        "content":     content,
        "received_at": now_iso,
    })

    for inst_id, filt_json in waiting.items():
        # Echo suppression: never route an event back to the instance that authored it
        if instance_id_of_author and inst_id == instance_id_of_author:
            continue

        try:
            filt = json.loads(filt_json)
        except Exception:
            filt = {}

        # Check event_type filter
        allowed_event_types = filt.get("event_types") or ["message_sent"]
        if event_type not in allowed_event_types:
            continue

        # Check author_role filter (null = accept any role)
        allowed_roles = filt.get("author_role")
        if allowed_roles is not None and author_role not in allowed_roles:
            continue

        # Check visibility filter (null = accept any visibility)
        allowed_visibility = filt.get("visibility")
        if allowed_visibility is not None and visibility != allowed_visibility:
            continue

        # All filters passed — push to this instance's receive:result queue
        try:
            await redis_client.lpush(f"receive:result:{session_id}:{inst_id}", payload)
            notified += 1
            logger.debug(
                "receive:result pushed: session=%s instance=%s event=%s author=%s",
                session_id, inst_id, event_type, author_role,
            )
        except Exception as exc:
            logger.warning(
                "receive:result LPUSH failed: session=%s instance=%s — %s",
                session_id, inst_id, exc,
            )

    return notified


# ── Pool lifecycle hooks ──────────────────────────────────────────────────────

async def fire_pool_hooks(
    http:         aiohttp.ClientSession,
    redis_client: aioredis.Redis,
    session_id:   str,
    pool_id:      str,
    tenant_id:    str,
    customer_id:  str,
    hook_type:    str,
    human_instance_id: str = "",
    arm_contact_close: bool = False,
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
    # G7 Slice B: segment_wrapup reusa a lista on_human_end do pool (o loop abaixo
    # filtra para side=agent). Pools não declaram uma lista "segment_wrapup".
    _hook_list_key = "on_human_end" if hook_type == "segment_wrapup" else hook_type
    hook_list  = hooks.get(_hook_list_key, [])

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

    # ── close_origin (lido UMA vez) ──────────────────────────────────────────
    # Necessário tanto para dimensionar o contador de conclusão (hooks de cliente
    # pulados NÃO podem inflá-lo — senão o contato fica preso em `active` até o
    # safety net de 180s force-close) quanto para o skip por-entrada abaixo.
    _close_origin_val = ""
    try:
        _co_raw0 = await redis_client.hget(
            f"{tenant_id}:ctx:{session_id}", "session.close_origin"
        )
        if _co_raw0:
            _co0 = json.loads(_co_raw0 if isinstance(_co_raw0, str) else _co_raw0.decode())
            _close_origin_val = str((_co0 or {}).get("value", "") or "")
    except Exception:
        pass

    def _entry_will_dispatch(_e) -> bool:
        """Reproduz os predicados de skip do loop abaixo para dimensionar o
        contador. Uma entrada NÃO é disparada quando falta 'pool' ou quando é
        um hook de cliente com nps_on_disconnect=skip numa queda do cliente."""
        if not isinstance(_e, dict) or not _e.get("pool"):
            return False
        _s   = _e.get("side", "agent") or "agent"
        _nod = _e.get("nps_on_disconnect", "timeout") or "timeout"
        if _s == "customer" and _nod == "skip" and _close_origin_val == "customer_disconnect":
            return False
        return True

    # For on_human_end and post_human hooks: track completion so the bridge knows when ALL hook
    # agents have finished and can then trigger the full contact close.
    # Counter key: session:{id}:hook_pending:{hook_type}   (TTL 4h)
    # Per-conference key: session:{id}:hook_conf:{conference_id}  (TTL 4h)
    # When process_routed detects a conference agent completing that has a hook_conf
    # key, it decrements the counter. When it hits 0 → _trigger_contact_close() (for post_human)
    # or checks for post_human hooks (for on_human_end).
    # Bugfix: o contador é dimensionado pelas entradas que REALMENTE serão
    # disparadas (não por len(hook_list)); entradas puladas (nps_on_disconnect=
    # skip em customer_disconnect) deixariam um contador órfão que só zerava no
    # force-close de 180s, mantendo a sessão `active` nesse intervalo.
    if hook_type in ("on_human_end", "post_human", "on_contact_end") and hook_list:
        _dispatch_n = sum(1 for _e in hook_list if _entry_will_dispatch(_e))
        if _dispatch_n > 0:
            try:
                await redis_client.setex(
                    f"session:{session_id}:hook_pending:{hook_type}",
                    14400,
                    str(_dispatch_n),
                )
            except Exception as exc:
                logger.warning(
                    "fire_pool_hooks: could not set pending counter: session=%s — %s",
                    session_id, exc,
                )

    # ── F5 (grão segmento): segmento humano que ESTE on_human_end serve ───────
    # pool_id é o pool do humano que encerrou. Lê o registro human_seg:{pool}
    # (gravado no participant_left) + deriva o close_reason da iniciativa
    # (session.close_origin), semeia o acumulador do segmento e guarda o
    # segment_id para carimbar no hook_conf de cada hook desta leva.
    _hook_human_seg_id = ""
    _hook_human_instance_id = ""   # G7: pid do humano DESTE segmento (não o global)
    # G7 Slice B: segment_wrapup também serve um segmento humano específico (o que
    # transferiu) — mesma leitura de human_seg + served_human stash que on_human_end.
    # G7 Fase 3b: on_contact_end (NPS) também precisa do stash — o agente de NPS lê
    # session.surveyed_segment_id/surveyed_agent_key para gravar survey_record(grain=segment)
    # atribuído ao segmento do humano dono do contato.
    if hook_type in ("on_human_end", "segment_wrapup", "on_contact_end"):
        try:
            # G7 Item1 Fatia 1: prefere a chave por-INSTÂNCIA (human_seg:{instance_id})
            # quando o caller a fornece — desambigua 2 humanos no MESMO pool (a chave
            # por-pool colapsava, limitação da Mudança 14/Slice 2′). Fallback na chave
            # por-pool (legado/single-humano/sessões in-flight durante deploy).
            _hs_key = (
                f"session:{session_id}:human_seg:{human_instance_id}"
                if human_instance_id
                else f"session:{session_id}:human_seg:{pool_id}"
            )
            _raw_hs = await redis_client.get(_hs_key)
            _hs_via_fallback = False
            if not _raw_hs and human_instance_id:
                _raw_hs = await redis_client.get(
                    f"session:{session_id}:human_seg:{pool_id}"
                )
                _hs_via_fallback = bool(_raw_hs)
            if _raw_hs:
                _hs_rec = json.loads(_raw_hs if isinstance(_raw_hs, str) else _raw_hs.decode())
                _hook_human_seg_id = _hs_rec.get("segment_id", "") or ""
                _hook_human_instance_id = _hs_rec.get("instance_id", "") or ""
                logger.debug(
                    "G7 Item1 human_seg READ: session=%s hook=%s key=%s fallback=%s "
                    "→ seg=%s human_inst=%s pool=%s",
                    session_id, hook_type, _hs_key, _hs_via_fallback,
                    _hook_human_seg_id, _hook_human_instance_id, pool_id,
                )
                if _hook_human_seg_id:
                    _hs_transport = ""
                    try:
                        _raw_org = await redis_client.hget(
                            f"{tenant_id}:ctx:{session_id}", "session.close_origin")
                        if _raw_org:
                            _org = json.loads(_raw_org if isinstance(_raw_org, str) else _raw_org.decode())
                            _hs_transport = str((_org or {}).get("value", "") or "")
                    except Exception:
                        pass
                    _hs_close_reason = _TRANSPORT_TO_CLOSE_REASON.get(_hs_transport, "agent_hangup")
                    await _seed_segment_signal(redis_client, session_id, _hs_rec, _hs_close_reason)
                    # F10.3b (cutover unificado): expõe a atribuição do segmento humano
                    # ao agente de NPS (on_human_end side=customer) via @ctx, para ele
                    # gravar via survey_record(grain=segment) — mesmo fluxo dos demais
                    # grãos. agent_key = user_id (deriva de instance_id 'human-{userId}').
                    try:
                        _inst = _hs_rec.get("instance_id", "") or ""
                        _surveyed_agent_key = (
                            _inst[len("human-"):] if _inst.startswith("human-")
                            else (_hs_rec.get("user_login", "") or _inst)
                        )
                        _sv_now = datetime.now(timezone.utc).isoformat()
                        await redis_client.hset(f"{tenant_id}:ctx:{session_id}", mapping={
                            "session.surveyed_segment_id": json.dumps({
                                "value": _hook_human_seg_id, "confidence": 1.0,
                                "source": "on_human_end_hook", "visibility": "agents_only",
                                "updated_at": _sv_now,
                            }),
                            "session.surveyed_agent_key": json.dumps({
                                "value": _surveyed_agent_key, "confidence": 1.0,
                                "source": "on_human_end_hook", "visibility": "agents_only",
                                "updated_at": _sv_now,
                            }),
                        })
                    except Exception as _sv_exc:
                        logger.debug("F10.3b: surveyed_* ctx write failed: session=%s — %s",
                                     session_id, _sv_exc)
        except Exception as _hs_exc:
            logger.debug("F5: could not load human_seg for hooks: session=%s — %s", session_id, _hs_exc)

    # ── Break cross-pool GETSET chain before dispatching parallel hooks ──────
    # routing-engine mark_busy() uses {tenant}:session:pool:{session_id} (GETSET)
    # to detect sequential agent transfers and DECR the previous pool.  Hook agents
    # (NPS + wrapup) are dispatched in PARALLEL — whichever routes SECOND would
    # find the first hook's pool_id and DECR it while it is still running.
    # Deleting the key here makes each hook INCR its own pool independently.
    # retencao_humano is DECR'd by the human's own remove_conversation (agent_done).
    #
    # G7 Slice B (nota): no segment_wrapup o contato CONTINUA (destino ativo). A chave
    # session:pool é um slot único e não representa destino + wrap-up concorrentes —
    # contabilidade de pool nesse cenário é ponto de validação E2E (pior caso: +1 no
    # contador do destino). Mantido o delete (comportamento provado do on_human_end).
    if tenant_id and session_id:
        try:
            await redis_client.delete(f"{tenant_id}:session:pool:{session_id}")
        except Exception as _e:
            logger.warning(
                "fire_pool_hooks: could not clear session serving key: session=%s — %s",
                session_id, _e,
            )

    for entry in hook_list:
        target_pool = entry.get("pool") if isinstance(entry, dict) else None
        if not target_pool:
            logger.warning(
                "fire_pool_hooks: hook entry missing 'pool' field — skipping: %s", entry,
            )
            continue

        # Arc 14: read `side` from hook YAML entry (default "agent" — backward compat).
        # "agent"    → segment interacts with the human agent (wrap-up, resumo).
        # "customer" → segment interacts with the customer (NPS, satisfaction survey).
        hook_side = "agent"
        nps_on_disconnect = "timeout"
        if isinstance(entry, dict):
            hook_side          = entry.get("side", "agent") or "agent"
            nps_on_disconnect  = entry.get("nps_on_disconnect", "timeout") or "timeout"

        # G7 Slice B: segment_wrapup dispara SÓ o wrap-up (side=agent) — sem NPS.
        # O NPS é hook de fim-de-CONTATO (Fase 3), não de fim-de-segmento.
        if hook_type == "segment_wrapup" and hook_side != "agent":
            continue

        # Arc 14 Fase D: skip customer-side hooks when nps_on_disconnect="skip"
        # and the client already disconnected. close_origin foi lido uma vez acima
        # (_close_origin_val) e é o MESMO valor usado para dimensionar o contador —
        # mantém skip e contagem coerentes (sem contador órfão).
        if (
            hook_side == "customer"
            and nps_on_disconnect == "skip"
            and _close_origin_val == "customer_disconnect"
        ):
            logger.info(
                "fire_pool_hooks: NPS hook skipped (nps_on_disconnect=skip, "
                "customer_disconnect): session=%s pool=%s",
                session_id, target_pool,
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
                "Pool hook fired: hook=%s side=%s origin_pool=%s → target_pool=%s "
                "session=%s conference=%s",
                hook_type, hook_side, pool_id, target_pool, session_id, conference_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to fire pool hook: hook=%s target_pool=%s session=%s — %s",
                hook_type, target_pool, session_id, exc,
            )
            continue

        # Mark this conference_id as hook-spawned so process_routed can detect
        # when the hook agent completes and decrement the pending counter.
        # Also covers on_human_start so those agents are excluded from the
        # active_ai_specialists set (G2 guard) and never block on_human_end hooks.
        #
        # Arc 14: hook_conf value format: "{hook_type}:{target_pool}:{side}:{origin_pool}"
        # where origin_pool is the pool the human agent belongs to (used for wrap_up_pending
        # cleanup in process_routed so it can derive human-{origin_pool} instance_id).
        # Backward compat: parse uses split(":", 3) and defaults missing parts gracefully.
        if hook_type in ("on_human_end", "post_human", "on_human_start", "segment_wrapup", "on_contact_end"):
            try:
                # F5: 5º campo = segment_id do humano que este on_human_end serve
                # (vazio p/ post_human/on_human_start). Parse com split(":", 4).
                # G7 Slice B: segment_wrapup carrega o mesmo 5º campo (segmento da origem).
                await redis_client.setex(
                    f"session:{session_id}:hook_conf:{conference_id}",
                    14400,
                    f"{hook_type}:{target_pool}:{hook_side}:{pool_id}:{_hook_human_seg_id}",
                )
            except Exception as exc:
                logger.warning(
                    "fire_pool_hooks: could not mark hook conference: session=%s conf=%s — %s",
                    session_id, conference_id, exc,
                )

        # Arc 14: INCR posatt:active counter for this hook segment.
        # Decremented in process_routed when the hook agent completes.
        # When the counter reaches 0, _destroy_conference() is called.
        # Only tracked for on_human_end and post_human (not on_human_start).
        # G7 Slice B: segment_wrapup entra neste bloco para registrar o participants
        # SET + wrap_up_pending, MAS nunca faz INCR posatt:active — é fim-de-segmento,
        # não pode gatilhar _close_contact_layer/_destroy_conference (o contato segue).
        # G7 Fase 3b: on_contact_end (NPS, side=customer) arma posatt:active +
        # posatt:customer_active — fim-de-CONTATO: gatilha _close_contact_layer (WS) e
        # participa do _destroy_conference.
        if hook_type in ("on_human_end", "post_human", "segment_wrapup", "on_contact_end"):
            if hook_type in ("on_human_end", "post_human", "on_contact_end"):
                try:
                    await redis_client.incr(f"session:{session_id}:posatt:active")
                    await redis_client.expire(f"session:{session_id}:posatt:active", 14400)
                    # Arc 14 Fase E: track customer-side hooks separately.
                    # _close_contact_layer() fires only after posatt:customer_active hits 0,
                    # keeping the customer WebSocket open while NPS is running.
                    if hook_side == "customer":
                        await redis_client.incr(f"session:{session_id}:posatt:customer_active")
                        await redis_client.expire(f"session:{session_id}:posatt:customer_active", 14400)
                    logger.debug(
                        "fire_pool_hooks: posatt:active INCR — session=%s hook=%s side=%s conf=%s",
                        session_id, hook_type, hook_side, conference_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "fire_pool_hooks: could not INCR posatt:active: session=%s — %s",
                        session_id, exc,
                    )

            # G7 Fatia 2b/3 — fan-out do customer-disconnect: cada segment_wrapup de
            # peer arma contact_close_pending (segment_wrapup NÃO toca posatt:active).
            # O teardown (_close_contact_layer/_destroy_conference) espera o contador
            # zerar, garantindo que TODOS os humanos recebam wrap-up antes do contato
            # fechar. Marcador por-conferência → DECR idempotente em process_routed.
            if arm_contact_close and hook_type == "segment_wrapup":
                try:
                    await redis_client.incr(f"session:{session_id}:contact_close_pending")
                    await redis_client.expire(
                        f"session:{session_id}:contact_close_pending", 14400
                    )
                    await redis_client.setex(
                        f"session:{session_id}:close_arming:{conference_id}", 14400, "1",
                    )
                    logger.debug(
                        "fire_pool_hooks: contact_close_pending armed — session=%s conf=%s",
                        session_id, conference_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "fire_pool_hooks: could not arm contact_close_pending: session=%s — %s",
                        session_id, exc,
                    )

            # Arc 14 Fase B: register the fixed-side participant in the posatt
            # participants SET so process_routed can publish a targeted session.closed
            # when this segment completes.
            #   side=customer → fixed participant = customer_participant_id
            #   side=agent    → fixed participant = human_agent_participant_id
            # The hook agent itself is added in process_routed when it joins.
            try:
                _fixed_pid: str | None = None
                if hook_side == "customer":
                    _cpid_raw = await redis_client.get(
                        f"session:{session_id}:customer_participant_id"
                    )
                    if _cpid_raw:
                        _fixed_pid = (
                            _cpid_raw if isinstance(_cpid_raw, str) else _cpid_raw.decode()
                        )
                else:
                    # G7/multi-humano: preferir o instance_id do humano DESTE segmento
                    # (human_seg:{pool}) ao campo de SESSÃO global
                    # human_agent_participant_id — assim cada wrap-up endereça o seu
                    # humano. Fallback no global p/ post_human / registro ausente.
                    if _hook_human_instance_id:
                        _fixed_pid = _hook_human_instance_id
                    else:
                        _ha_raw = await redis_client.hget(
                            f"{tenant_id}:ctx:{session_id}",
                            "session.human_agent_participant_id",
                        )
                        if _ha_raw:
                            _ha_entry = json.loads(
                                _ha_raw if isinstance(_ha_raw, str) else _ha_raw.decode()
                            )
                            _fixed_pid = _ha_entry.get("value")
                if _fixed_pid:
                    _pset_key = f"session:{session_id}:posatt:{conference_id}:participants"
                    await redis_client.sadd(_pset_key, _fixed_pid)
                    await redis_client.expire(_pset_key, 14400)
                    # G7: guarda o pid do humano servido por ESTE hook (keyed por
                    # conference_id) para o join do wrap-up (process_routed) gravar
                    # segment.{wrapupSegId}.served_human_participant_id — fonte da
                    # visibility por-segmento (substitui o campo global na YAML).
                    if hook_side == "agent":
                        try:
                            await redis_client.setex(
                                f"session:{session_id}:hook_served_human:{conference_id}",
                                14400, _fixed_pid,
                            )
                        except Exception:
                            pass
                    logger.debug(
                        "fire_pool_hooks: posatt participants fixed-side registered: "
                        "session=%s conf=%s side=%s pid=%s",
                        session_id, conference_id, hook_side, _fixed_pid,
                    )

                    # Arc 14 Fase C: wrap_up_pending flag — block the human agent from
                    # receiving a new contact until the wrap-up segment completes.
                    # Checked by routing-engine get_ready_instances() to skip the instance.
                    # Deleted in process_routed() when the agent-side hook segment concludes.
                    # TTL = hook timeout + 5 min safety margin so it auto-expires even
                    # if the cleanup path is never reached (e.g. bridge crash).
                    # Key uses human-{pool_id} — same format routing-engine uses to index
                    # pool instances — NOT participant_id from ContextStore (unreliable).
                    if hook_side == "agent" and hook_type in ("on_human_end", "segment_wrapup"):
                        try:
                            _human_iid = f"human-{pool_id}"
                            _wp_key = (
                                f"{tenant_id}:instance:{_human_iid}:wrap_up_pending"
                            )
                            await redis_client.setex(
                                _wp_key, _HOOK_TIMEOUT_S + 300, session_id
                            )
                            logger.info(
                                "fire_pool_hooks: wrap_up_pending set — session=%s "
                                "instance=%s key=%s",
                                session_id, _human_iid, _wp_key,
                            )
                        except Exception as _wp_exc:
                            logger.warning(
                                "fire_pool_hooks: could not set wrap_up_pending: "
                                "session=%s pool=%s — %s",
                                session_id, pool_id, _wp_exc,
                            )
                else:
                    logger.debug(
                        "fire_pool_hooks: no fixed-side participant found: "
                        "session=%s conf=%s side=%s",
                        session_id, conference_id, hook_side,
                    )
            except Exception as exc:
                logger.warning(
                    "fire_pool_hooks: could not register posatt participants: session=%s — %s",
                    session_id, exc,
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
        # Check the counter VALUE, not just key existence.
        # After all hooks complete, decr leaves the key with value 0 (key still exists).
        # We only force-close if the value is > 0 (hooks genuinely didn't finish).
        raw_val = await redis_client.get(pending_key)
        remaining = 0
        if raw_val is not None:
            try:
                remaining = int(raw_val if isinstance(raw_val, str) else raw_val.decode())
            except (ValueError, AttributeError):
                remaining = 0
        if remaining > 0:
            logger.warning(
                "_hook_timeout_guard: %s hooks did not complete within %ds — "
                "remaining=%d, force-closing contact: session=%s",
                hook_type, _HOOK_TIMEOUT_S, remaining, session_id,
            )
            # Delete the pending key to prevent double-close if a late hook completes.
            await redis_client.delete(pending_key)
            await _trigger_contact_close(redis_client, session_id)
        else:
            logger.debug(
                "_hook_timeout_guard: %s hooks completed normally before timeout "
                "(remaining=%d): session=%s",
                hook_type, remaining, session_id,
            )
    except Exception as exc:
        logger.warning(
            "_hook_timeout_guard: error checking pending key: session=%s — %s",
            session_id, exc,
        )


async def _contact_close_timeout_guard(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> None:
    """G7 Fatia 2b/3 — safety net do fan-out de customer-disconnect.

    Os wrap-ups de peer usam segment_wrapup (NÃO usam hook_pending), então o
    _hook_timeout_guard não os cobre. Se algum peer não completar em _HOOK_TIMEOUT_S
    (pool sem instância, etc.), este guard força o fechamento do contato para não
    deixar a conferência presa em contact_close_pending > 0.

    Idempotente: limpa o contador antes de fechar (close layers têm guards NX).
    """
    await asyncio.sleep(_HOOK_TIMEOUT_S)
    key = f"session:{session_id}:contact_close_pending"
    try:
        raw = await redis_client.get(key)
        remaining = 0
        if raw is not None:
            try:
                remaining = int(raw if isinstance(raw, str) else raw.decode())
            except (ValueError, AttributeError):
                remaining = 0
        if remaining > 0:
            logger.warning(
                "_contact_close_timeout_guard: %d peer wrap-up(s) did not complete "
                "within %ds — force-closing contact: session=%s",
                remaining, _HOOK_TIMEOUT_S, session_id,
            )
            await redis_client.delete(key)
            await _close_contact_layer(redis_client, session_id)
            await _destroy_conference(redis_client, session_id)
    except Exception as exc:
        logger.warning(
            "_contact_close_timeout_guard: error: session=%s — %s", session_id, exc,
        )


# ── Contact close trigger — used by hook completion and no-hook fallback ─────

async def _mark_contact_ended(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> str:
    """
    Records the true contact end time — the moment the customer left or the
    primary interaction ended.  Uses SET NX so the first call wins; subsequent
    calls from hook completion / cleanup paths are silently ignored.

    Returns the stored ISO-8601 timestamp (useful for immediate use at the
    call site).  _trigger_contact_close() reads this key to populate ended_at
    in the contact_closed analytics event, decoupling AHT from wrap-up time.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        await redis_client.set(
            f"session:{session_id}:contact_ended_at",
            now,
            nx=True,
            ex=604800,  # 7d — same as close_fired guard
        )
    except Exception:
        pass
    return now


async def _resolve_close_customer_id(
    redis_client: aioredis.Redis,
    tenant_id:    str,
    session_id:   str,
    fallback:     str,
) -> str:
    """
    Identity Resolver Slice 4 — resolve the customer_id to stamp on the sessions
    close row. Prefers the native `caller.customer_id` from the ContextStore
    (written by the intake/resolve step once identity is resolved); falls back to
    `fallback` (the ephemeral contact_id from session meta) when absent.

    The ctx field is a ContextEntry JSON blob ({value, confidence, ...}); we read
    only its `value`. Best-effort: any error → fallback (never blocks close).
    """
    if not tenant_id or not session_id:
        return fallback
    try:
        raw = await redis_client.hget(f"{tenant_id}:ctx:{session_id}", "caller.customer_id")
        if not raw:
            return fallback
        entry = json.loads(raw if isinstance(raw, str) else raw.decode())
        value = entry.get("value") if isinstance(entry, dict) else entry
        value = str(value).strip() if value is not None else ""
        return value or fallback
    except Exception as exc:
        logger.debug(
            "_resolve_close_customer_id: fallback for session=%s — %s", session_id, exc,
        )
        return fallback


async def _close_contact_layer(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> None:
    """
    Arc 14 — Layer 1 close: close the customer-facing channel immediately.

    Publishes:
      1. conversations.outbound  session.closed  → channel-gateway closes customer WS
      2. conversations.events    contact_closed  → analytics (AHT uses contact_ended_at,
                                                   not the time this fires)

    Guarded by session:{id}:contact_close_fired (NX, TTL 7d) — idempotent.
    May be called concurrently with posatt hooks still running; those hooks
    will discover the customer is gone and handle it via their own skill-flow
    branches (Arc 14 decision D2).

    Called immediately when the contact ends — before posatt hooks fire.
    In the no-hook path _trigger_contact_close() calls this first.
    """
    global _kafka_producer
    if _kafka_producer is None:
        logger.warning(
            "_close_contact_layer: Kafka producer not ready — session=%s", session_id,
        )
        return

    # Idempotency guard — separate from _destroy_conference's close_fired key
    # so each function can be called independently without blocking the other.
    try:
        acquired = await redis_client.set(
            f"session:{session_id}:contact_close_fired",
            "1",
            nx=True,
            ex=604800,
        )
        if not acquired:
            logger.debug(
                "_close_contact_layer: already fired for session=%s — skipping",
                session_id,
            )
            return
    except Exception as exc:
        logger.warning(
            "_close_contact_layer: could not acquire guard: session=%s — %s (proceeding)",
            session_id, exc,
        )

    # Resolve session meta (contact_id, channel, tenant_id, pool_id, started_at).
    contact_id  = session_id
    channel     = "webchat"
    tenant_id   = ""
    meta: dict  = {}
    try:
        raw_meta = await redis_client.get(f"session:{session_id}:meta")
        if raw_meta:
            meta        = json.loads(raw_meta)
            contact_id  = meta.get("contact_id", session_id) or session_id
            channel     = meta.get("channel", "webchat") or "webchat"
            tenant_id   = meta.get("tenant_id", "") or meta.get("tenant", "")
    except Exception as exc:
        logger.warning(
            "_close_contact_layer: could not read session meta: session=%s — %s",
            session_id, exc,
        )

    # 1. Close the customer WebSocket.
    try:
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
            "_close_contact_layer: published conversations.outbound session.closed: "
            "session=%s contact_id=%s channel=%s",
            session_id, contact_id, channel,
        )
    except Exception as exc:
        logger.error(
            "_close_contact_layer: failed to publish outbound close: session=%s — %s",
            session_id, exc,
        )

    # 2. Publish contact_closed analytics event.
    # reason "agent_done" → customer_side=True in process_contact_event.
    # Include full session data so analytics-api builds a complete sessions row.
    try:
        _pool_id_close     = meta.get("pool_id", "") if meta else ""
        _started_at_close  = meta.get("started_at", "") if meta else ""
        _customer_id_close = meta.get("customer_id", "") if meta else ""
        _channel_close     = meta.get("channel", "webchat") if meta else "webchat"

        # Identity Resolver Fase A · Slice 4 — stamp the resolved NATIVE customer_id
        # onto the authoritative sessions-close row. Until identity is resolved,
        # customer_id is the ephemeral contact_id (channel handle) set at session
        # open; once the intake/resolve step writes caller.customer_id (native),
        # past closed contacts must unify under it so history/search key on the
        # real customer. Overwrite here (ReplacingMergeTree close row wins);
        # fallback to the meta contact_id when unresolved.
        _customer_id_close = await _resolve_close_customer_id(
            redis_client, tenant_id, session_id, _customer_id_close,
        )

        # SLA do pool no fechamento: a linha de close é a que sobrevive no
        # ReplacingMergeTree do analytics — repetir o sla_target_ms aqui evita
        # que o valor do parse_routed seja substituído por NULL.
        _sla_close = None
        try:
            if tenant_id and _pool_id_close:
                _raw_pc = await redis_client.get(
                    f"{tenant_id}:pool_config:{_pool_id_close}"
                )
                if _raw_pc:
                    _sla_close = (json.loads(_raw_pc) or {}).get("sla_target_ms")
        except Exception:
            pass

        # G1 fix: read the true contact end time recorded by _mark_contact_ended().
        # Falls back to now() only when the key is absent (crash recovery path).
        _ended_at_close = ""
        try:
            _raw_ended = await redis_client.get(f"session:{session_id}:contact_ended_at")
            _ended_at_close = (
                _raw_ended if isinstance(_raw_ended, str)
                else (_raw_ended.decode() if _raw_ended else "")
            )
        except Exception:
            pass
        _ended_at_close = _ended_at_close or datetime.now(timezone.utc).isoformat()

        # ── Fase A (queue-attended-model): derive business close_reason + outcome ──
        # Transport reasons (client_disconnect/timeout/agent_done/agent_closed) stay
        # on the wire untouched; the ANALYTICS event maps them to the business
        # close_reason domain. outcome = last primary segment outcome (marker written
        # by process_routed for AI and by the contact_closed handler for humans).
        _transport_reason = "agent_done"
        try:
            _raw_closed = await redis_client.get(f"session:{session_id}:closed")
            if _raw_closed:
                _transport_reason = (
                    _raw_closed if isinstance(_raw_closed, str) else _raw_closed.decode()
                ) or "agent_done"
        except Exception:
            pass

        # F5 (grão segmento): a disposição do wrap-up e o NPS são atribuídos ao
        # segmento humano correto na CONCLUSÃO de cada hook (process_routed), via
        # _apply_wrapup_to_segment / _apply_nps_to_segment — keyed pelo segmento
        # que o pool daquele on_human_end serviu. O last_outcome de sessão é
        # atualizado lá (último primary humano) e lido logo abaixo.
        _last_outcome_val = ""
        _last_agent_kind  = ""
        try:
            _raw_lo = await redis_client.get(f"session:{session_id}:last_outcome")
            if _raw_lo:
                _lo = json.loads(_raw_lo if isinstance(_raw_lo, str) else _raw_lo.decode())
                _last_outcome_val = _lo.get("outcome", "") or ""
                _last_agent_kind  = _lo.get("agent_kind", "") or ""
        except Exception:
            pass

        if _transport_reason == "client_disconnect":
            # Served at least once → customer_disconnect; never served → customer_abandon
            _close_reason_biz = (
                "customer_disconnect" if _last_outcome_val else "customer_abandon"
            )
            _last_outcome_val = _last_outcome_val or "abandoned"
        elif _transport_reason in ("timeout", "session_timeout"):
            _close_reason_biz = "session_timeout"
            _last_outcome_val = _last_outcome_val or "abandoned"
        elif _transport_reason == "max_wait_exceeded":
            # Fase E (queue-attended-model): retention bound. Normally the
            # routing engine is the authoritative writer (contact_close_fired
            # set before this runs) — defensive mapping for marker-write races.
            _close_reason_biz = "max_wait_exceeded"
            _last_outcome_val = _last_outcome_val or "abandoned"
        elif _transport_reason == "no_resource":
            _close_reason_biz = "no_resource"
            _last_outcome_val = _last_outcome_val or "failed"
        elif _transport_reason == "agent_closed":
            _close_reason_biz = "agent_hangup"
        else:  # "agent_done" — platform closed after agent completion
            _close_reason_biz = (
                "agent_hangup" if _last_agent_kind == "human" else "flow_complete"
            )

        await _kafka_producer.send_and_wait(
            TOPIC_EVENTS,
            json.dumps({
                "event_type":   "contact_closed",
                "session_id":   session_id,
                "tenant_id":    tenant_id,
                # "reason" stays hard-coded: this event is re-consumed by the bridge
                # itself (process_contact_event) and customer_side classification +
                # the Arc 14 re-entry guard depend on "agent_done". Wire untouched.
                "reason":       "agent_done",
                "close_reason": _close_reason_biz,      # business domain (analytics)
                "outcome":      _last_outcome_val or None,
                "pool_id":      _pool_id_close,
                "started_at":   _started_at_close,
                "ended_at":     _ended_at_close,
                "customer_id":  _customer_id_close,
                "channel":      _channel_close,
                "sla_target_ms": _sla_close,
            }).encode("utf-8"),
        )
        logger.info(
            "_close_contact_layer: published conversations.events contact_closed: "
            "session=%s pool=%s ended_at=%s",
            session_id, _pool_id_close, _ended_at_close,
        )

        # ── F2 (bancada de agentes): trigger do pipeline de avaliação ─────────
        # conversations.session_closed NUNCA teve produtor no codebase (a doc o
        # atribuía ao "Core", que não existe como serviço no demo). O Persister
        # do session-replayer consome este tópico, persiste o stream e publica
        # evaluation.requested — sem este publish, o pipeline de avaliação
        # (Arc 3/6) fica dormente e evaluation_results permanece vazio.
        # Payload = SessionClosedEvent (session-replayer/models.py).
        try:
            await _kafka_producer.send_and_wait(
                "conversations.session_closed",
                json.dumps({
                    "session_id":   session_id,
                    "tenant_id":    tenant_id,
                    "outcome":      _last_outcome_val or None,
                    "close_reason": _close_reason_biz,
                    "closed_at":    _ended_at_close,
                    # S2.1 (avaliação campaign-driven): campos que a amostragem por
                    # campanha precisa (should_sample filtra por pool/canal/duração).
                    # O bridge já os tem do contact_closed acima.
                    "pool_id":      _pool_id_close,
                    "channel":      _channel_close,
                    "started_at":   _started_at_close,
                }).encode("utf-8"),
            )
            logger.info(
                "_close_contact_layer: published conversations.session_closed: session=%s",
                session_id,
            )
        except Exception as _sc_exc:
            logger.warning(
                "_close_contact_layer: failed to publish session_closed: session=%s — %s",
                session_id, _sc_exc,
            )
    except Exception as exc:
        logger.error(
            "_close_contact_layer: failed to publish contact_closed: session=%s — %s",
            session_id, exc,
        )


async def _destroy_conference(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> None:
    """
    Arc 14 — Layer 3 destroy: clean up conference infrastructure once all
    posatt segments have finished.

    Actions:
      - Deletes human_agent tracking keys (they MUST stay alive during posatt
        so that hook agent messages are forwarded to the Console correctly).
      - Publishes session.closed to agent:events:{session_id} (broadcast) so
        all Console connections for this session close gracefully.
        (Arc 14 Fase B will make this targeted per-segment instead.)

    Guarded by session:{id}:close_fired (NX, TTL 7d) — idempotent.
    Called by the last posatt segment to complete (posatt:active hits 0),
    or by _trigger_contact_close in the no-hook path.
    """
    # Arc 14 — posatt guard: if any posatt hook segments are still running,
    # do NOT destroy the conference yet.  The last posatt segment to complete
    # will call _destroy_conference() again (via process_routed decrement path)
    # once posatt:active reaches 0.
    try:
        posatt_raw = await redis_client.get(f"session:{session_id}:posatt:active")
        if posatt_raw:
            remaining = int(posatt_raw if isinstance(posatt_raw, str) else posatt_raw.decode())
            if remaining > 0:
                logger.info(
                    "_destroy_conference: posatt:active=%d — deferring destroy (hooks still running): session=%s",
                    remaining, session_id,
                )
                return
    except Exception as exc:
        logger.warning(
            "_destroy_conference: could not read posatt:active: session=%s — %s (proceeding)",
            session_id, exc,
        )

    # G7 Fatia 2b/3 — guarda do fan-out de customer-disconnect: enquanto houver
    # wrap-ups de peer pendentes (contact_close_pending > 0), NÃO destrói a
    # conferência. O último segment_wrapup a completar (process_routed) zera o
    # contador e dispara o teardown. Espelha o guard de posatt:active acima.
    try:
        _ccp_raw = await redis_client.get(f"session:{session_id}:contact_close_pending")
        if _ccp_raw:
            _ccp = int(_ccp_raw if isinstance(_ccp_raw, str) else _ccp_raw.decode())
            if _ccp > 0:
                logger.info(
                    "_destroy_conference: contact_close_pending=%d — deferring destroy "
                    "(peer wrap-ups still running): session=%s", _ccp, session_id,
                )
                return
    except Exception as exc:
        logger.warning(
            "_destroy_conference: could not read contact_close_pending: session=%s — %s (proceeding)",
            session_id, exc,
        )

    # Idempotency guard — same key as the old _trigger_contact_close used,
    # preserved for backward compat with existing watchdog/crash recovery logic.
    try:
        acquired = await redis_client.set(
            f"session:{session_id}:close_fired",
            "1",
            nx=True,
            ex=604800,
        )
        if not acquired:
            logger.debug(
                "_destroy_conference: already fired for session=%s — skipping",
                session_id,
            )
            return
    except Exception as exc:
        logger.warning(
            "_destroy_conference: could not acquire close_fired guard: session=%s — %s "
            "(proceeding anyway)",
            session_id, exc,
        )

    # Delete human-agent tracking keys.
    # These MUST stay alive during posatt hooks so that wrap-up/NPS messages
    # are routed correctly to the Console (human_agent key is read by the
    # notify/menu delivery path).  Now that all posatt segments are done, it
    # is safe to remove them.
    try:
        await redis_client.delete(
            f"session:{session_id}:human_agent",
            f"session:{session_id}:human_agents",
        )
    except Exception as exc:
        logger.warning(
            "_destroy_conference: could not delete human_agent keys: session=%s — %s",
            session_id, exc,
        )

    # Broadcast session.closed to all Console connections for this session.
    # (Arc 14 Fase B: replace broadcast with targeted recipients per segment.)
    try:
        await redis_client.publish(
            f"agent:events:{session_id}",
            json.dumps({
                "type":       "session.closed",
                "session_id": session_id,
                "reason":     "conference_destroyed",
            }),
        )
        logger.info(
            "_destroy_conference: published session.closed to agent:events: session=%s",
            session_id,
        )
    except Exception as exc:
        logger.warning(
            "_destroy_conference: could not publish session.closed: session=%s — %s",
            session_id, exc,
        )


async def _trigger_contact_close(
    redis_client: aioredis.Redis,
    session_id:   str,
) -> None:
    """
    Backward-compatible wrapper — closes both layers in sequence.

    Used by:
    - No-hook path (pool has no on_human_end hooks): Layer 1 + Layer 3 fire immediately.
    - Crash/watchdog/timeout paths that may not distinguish between the two layers.
    - AI primary agent completion (no hooks involved).

    In the hook path (Arc 14), the bridge calls _close_contact_layer() immediately
    on contact end and _destroy_conference() when the last posatt segment finishes,
    so _trigger_contact_close() is NOT called in that path.
    """
    await _close_contact_layer(redis_client, session_id)
    await _destroy_conference(redis_client, session_id)


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
    flow_id:        str = "",   # skill-flow deployado que o agente executou (avaliação IA)
    deploy_version: str = "",   # R9: versão do deploy; se vazio, resolve do cache via flow_id
    channel:        str = "",   # R9: canal da sessão, carimbado no segmento
    user_login:     str = "",   # C1: login (email) do agente humano — identidade no relatório
    conference_id:  str = "",
    joined_at:      str = "",
    duration_ms:    int | None = None,
    sequence_index: int = 0,
    parent_segment_id: str = "",
    outcome:        str | None = None,
    close_reason:   str | None = None,
    handoff_reason: str | None = None,
    issue_status:   str | None = None,
    escalation_reason: str | None = None,
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
    if flow_id:
        event["flow_id"] = flow_id
        # deploy_version (Fase C): identidade = `set_at` do slot `current` do pool
        # (momento do promote), cacheado por pool em get_pool_current_flow. Prioridade:
        # explícito → set_at do pool (novo, casa com SkillDeployment.deployed_at) →
        # skill.version (fallback legado, pools não migrados ao slot). "" → omitido.
        _dv = (deploy_version
               or _pool_deploy_version_cache.get(pool_id, "")
               or _skill_version_cache.get(flow_id, ""))
        if _dv:
            event["deploy_version"] = _dv
    if channel:
        event["channel"] = channel
    # C1 — human identity by user_id. The human participant_id is `human-{userId}`
    # (instance key written by registerHumanAgent), so the login user_id is derived
    # by stripping the prefix. AI segments use flow_id instead; user_id stays "".
    if agent_type == "human" and participant_id.startswith("human-"):
        derived_user_id = participant_id[len("human-"):]
        if derived_user_id:
            event["user_id"] = derived_user_id
    if user_login:
        event["user_login"] = user_login
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
    if escalation_reason is not None:
        event["escalation_reason"] = escalation_reason
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


# ── F1.4 (bancada de agentes): outcome real do humano via wrap-up ─────────────
# Mapa de normalização da taxonomia CRUA do wrap-up (pool-scoped) para o domínio
# canônico SegmentOutcomeSchema. Decisões §13.2 do analytics-agents-workbench.md:
# pending ≡ suspended, transfer ≡ escalate. Identidade defensiva para pools cujo
# YAML já use valores normalizados. Valor desconhecido → sem re-publish (mantém
# o placeholder; nunca inventa "resolved").
_WRAPUP_OUTCOME_MAP: dict[str, str] = {
    "resolvido": "resolved",
    "pendente":  "suspended",
    "escalado":  "escalated",
    "cancelado": "abandoned",
    "resolved":  "resolved",
    "suspended": "suspended",
    "escalated": "escalated",
    "abandoned": "abandoned",
}


_TRANSPORT_TO_CLOSE_REASON = {
    "agent_closed":      "agent_hangup",
    "client_disconnect": "customer_disconnect",
    "timeout":           "session_timeout",
    "session_timeout":   "session_timeout",
    "max_wait_exceeded": "max_wait_exceeded",
    "no_resource":       "no_resource",
}


# ── F5 (grão segmento): acumulador de sinais por segmento humano ──────────────
# Cada on_human_end serve um SEGMENTO humano específico (o do pool que disparou
# o hook). Os sinais (disposição do wrap-up, NPS) são acumulados num hash Redis
# por segment_id e o segmento é re-publicado com o estado COMPLETO — wrap-up e
# NPS completam em momentos diferentes, e como analytics.segments é
# ReplacingMergeTree(ingested_at), um re-publish parcial (só nps, sem outcome)
# sobrescreveria o anterior. O acumulador garante que cada re-publish carrega
# todos os campos conhecidos. Suporta N humanos/pools por contato.

def _seg_signal_key(session_id: str, segment_id: str) -> str:
    return f"session:{session_id}:seg_signal:{segment_id}"


async def _seed_segment_signal(
    redis_client: aioredis.Redis,
    session_id:   str,
    record:       dict,
    close_reason: str | None,
) -> None:
    """Semeia o acumulador com os campos ESTÁTICOS do segmento humano (do
    registro human_seg:{pool}) + close_reason. Chamado por fire_pool_hooks. NX
    por campo — não sobrescreve sinais já acumulados se re-semeado."""
    seg_id = record.get("segment_id") or ""
    if not seg_id:
        return
    key = _seg_signal_key(session_id, seg_id)
    try:
        mapping = {
            "segment_id":     seg_id,
            "instance_id":    record.get("instance_id", "") or "",
            "pool_id":        record.get("pool_id", "") or "",
            "agent_type_id":  record.get("agent_type_id", "") or "",
            "user_login":     record.get("user_login", "") or "",
            "joined_at":      record.get("joined_at", "") or "",
            "sequence_index": str(record.get("sequence_index", 0) or 0),
            "tenant_id":      record.get("tenant_id", "") or "",
        }
        if record.get("duration_ms") is not None:
            mapping["duration_ms"] = str(record["duration_ms"])
        if close_reason:
            mapping["close_reason"] = close_reason
        await redis_client.hset(key, mapping=mapping)
        await redis_client.expire(key, 604800)
    except Exception as exc:
        logger.debug("F5: seed_segment_signal failed: session=%s — %s", session_id, exc)


async def _republish_segment_from_signal(
    redis_client: aioredis.Redis,
    session_id:   str,
    segment_id:   str,
) -> None:
    """Re-publica o segmento humano lendo o acumulador (estado completo).
    Idempotente por construção (ReplacingMergeTree dedup). Sem segment_id real,
    no-op (não cria segmento novo)."""
    try:
        h = await redis_client.hgetall(_seg_signal_key(session_id, segment_id))
        if not h:
            return
        g = (lambda k: (h.get(k) if isinstance(h.get(k), str)
                        else (h.get(k).decode() if h.get(k) else None)))
        seg_id = g("segment_id")
        if not seg_id:
            return
        dur_raw = g("duration_ms")
        await _publish_participant_event(
            session_id=session_id,
            tenant_id=g("tenant_id") or "",
            participant_id=g("instance_id") or "",
            pool_id=g("pool_id") or "",
            agent_type_id=g("agent_type_id") or "",
            event_type="participant_left",
            agent_type="human",
            role="primary",
            segment_id=seg_id,
            sequence_index=int(g("sequence_index") or 0),
            joined_at=g("joined_at") or "",
            duration_ms=int(dur_raw) if dur_raw not in (None, "") else None,
            user_login=g("user_login") or "",
            outcome=g("outcome"),
            issue_status=g("issue_status"),
            handoff_reason=g("handoff_reason"),
            close_reason=g("close_reason"),
            escalation_reason=g("escalation_reason"),
        )
        logger.info(
            "F5: segment re-published from signal: session=%s segment=%s outcome=%s",
            session_id, seg_id, g("outcome"),
        )
    except Exception as exc:
        logger.warning("F5: republish_segment_from_signal failed: session=%s — %s", session_id, exc)


async def _apply_wrapup_to_segment(
    redis_client: aioredis.Redis,
    session_id:   str,
    segment_id:   str,
    wrapup_raw:   str,
    wrapup_resumo: str,
    escalation_reason: str = "",
) -> None:
    """Conclusão do hook wrap-up (on_human_end side=agent): normaliza a
    disposição CRUA → outcome, acumula no segmento e re-publica. Atualiza também
    o last_outcome de sessão (último primary humano). F7: grava o
    escalation_reason normalizado quando o outcome é da família escalate."""
    outcome = _WRAPUP_OUTCOME_MAP.get(wrapup_raw, "")
    if not outcome:
        logger.warning(
            "F5: unknown wrap-up classification %r — keeping placeholder: session=%s seg=%s",
            wrapup_raw, session_id, segment_id,
        )
        return
    try:
        key = _seg_signal_key(session_id, segment_id)
        mapping = {"outcome": outcome, "issue_status": wrapup_raw}
        if outcome != "resolved" and wrapup_resumo:
            mapping["handoff_reason"] = wrapup_resumo
        # F7: só faz sentido para escalações (escalado → outcome escalated).
        if outcome == "escalated" and escalation_reason:
            mapping["escalation_reason"] = escalation_reason
        await redis_client.hset(key, mapping=mapping)
        await redis_client.expire(key, 604800)
        await redis_client.setex(
            f"session:{session_id}:last_outcome",
            604800,
            json.dumps({"outcome": outcome, "agent_kind": "human"}),
        )
    except Exception as exc:
        logger.debug("F5: apply_wrapup hset failed: session=%s — %s", session_id, exc)
    await _republish_segment_from_signal(redis_client, session_id, segment_id)


# _apply_nps_to_segment — REMOVIDO (F10.3b cutover). O NPS de segmento é gravado
# pelo agente de NPS via survey_record(grain=segment) → session_signal. A lente
# `nps` lê de session_signal; segments.nps_score não é mais escrito nem lido.


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
            await redis_client.expire(f"session:{session_id}:ai_agents", _stl())

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


# ── Native instance mirror release (gap-A fix) ────────────────────────────────

async def _release_native_instance_snapshot(
    redis_client: aioredis.Redis,
    tenant_id: str,
    instance_id: str,
    pool_id: str,
    agent_type_id: str,
    native_snapshot: dict | None,
) -> None:
    """Land the bridge's instance mirror at status=ready after a native agent
    releases a session (natural completion, escalation, OR suspend).

    Gap-A fix (delegate→suspend instance leak): the previous inline restore was
    gated on `native_snapshot` being present. The snapshot key
    ({tenant}:instance:{iid}) is refreshed at routing time with a 30s TTL; when a
    flow runs long (e.g. delegate→OTP→suspend) the key expires, so the read at
    activation time returned None and the restore was skipped — while agent_done
    still fired (SREM → SCARD 0), leaving the mirror stuck at busy/1 (the ghost
    the bootstrap then re-creates on restart). We now ALWAYS write a ready
    snapshot, reconstructing a minimal one when the prior snapshot is gone, so the
    mirror can never be left busy after the vaga was released. SCARD (the
    routing-engine occupancy SET) remains the source of truth; the bootstrap
    self-heal reconciles any residual drift against it.
    """
    if not instance_id:
        return
    try:
        snap = dict(native_snapshot) if native_snapshot else {}
        prev = int(snap.get("current_sessions", 1) or 1)
        snap["current_sessions"] = max(0, prev - 1)
        snap["status"] = "ready"
        snap.setdefault("state", "ready")
        if agent_type_id:
            snap.setdefault("agent_type_id", agent_type_id)
        if pool_id:
            snap["pools"] = snap.get("pools") or [pool_id]
        snap.setdefault("execution_model", "stateless")
        snap.setdefault(
            "max_concurrent_sessions", snap.get("max_concurrent", 1) or 1
        )
        await redis_client.set(
            f"{tenant_id}:instance:{instance_id}",
            json.dumps(snap),
            ex=3600,
        )
        if pool_id:
            await redis_client.sadd(
                f"{tenant_id}:pool:{pool_id}:instances", instance_id
            )
        logger.info(
            "AI instance mirror released to ready: tenant=%s instance=%s pool=%s "
            "(had_snapshot=%s)",
            tenant_id, instance_id, pool_id, bool(native_snapshot),
        )
    except Exception as exc:
        logger.warning(
            "Could not release AI instance mirror: tenant=%s instance=%s — %s",
            tenant_id, instance_id, exc,
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
    # get_agent_type also synthesizes a native agent_type from a deployed skill
    # when agent_type_id is actually a skill_id (Fase 3a deploy-driven).
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
                    await redis_client.expire(f"session:{session_id}:ai_agents", _stl())
                    # Arc 11: store participant metadata for supervisor_state (F1)
                    await redis_client.setex(
                        f"session:{session_id}:ai_participant:{yaml_instance_id}",
                        14400,
                        json.dumps({
                            "role":          "primary",
                            "agent_type_id": agent_type_id,
                            "pool_id":       pool_id,
                            "segment_id":    "",
                            "joined_at":     datetime.now(timezone.utc).isoformat(),
                        }),
                    )
                    # Guard against premature restore: process_contact_event checks
                    # this key before calling _restore_instance on contact_closed.
                    # Cleared when activate_native_agent returns (natural path).
                    # TTL=4h matches routing snapshot; crash recovery falls to bootstrap.
                    await redis_client.setex(
                        f"session:{session_id}:ai_completing:{yaml_instance_id}",
                        14400,
                        "1",
                    )
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
                # G7 Camada 3: conference hook agents reaching the YAML-fallback path
                # (registry 404/unavailable) must isolate their pipeline too. Passing
                # conference_id makes activate_native_agent suffix pipeline_session_id
                # (by instance_id, since no segment_id is generated here) instead of
                # silently keying on session_id puro and colliding with a peer hook.
                conference_id=conference_id,
            )

            # Skill flow ended naturally — clear the completing marker so that any
            # concurrent contact_closed event knows NOT to skip this instance.
            # (If contact_closed already ran and saw the key, it skipped restore;
            #  this path is now the sole restorer.)
            if yaml_instance_id:
                try:
                    await redis_client.delete(
                        f"session:{session_id}:ai_completing:{yaml_instance_id}"
                    )
                except Exception:
                    pass

            # Restore instance after skill flow completes (mirrors plughub-native path).
            # process_contact_event skips immediate restore for completing instances;
            # this is now the authoritative restore path.
            # Gap-A fix: always land the mirror at ready (incl. when yaml_snapshot is
            # None), so a delegate→suspend that expired the snapshot cannot leave the
            # mirror busy after agent_done drops the SCARD.
            await _release_native_instance_snapshot(
                redis_client, tenant_id, yaml_instance_id, pool_id,
                agent_type_id, yaml_snapshot,
            )

            # Notify routing-engine to decrement the pool's busy counter.
            # For YAML-fallback agents the bridge manages lifecycle via direct Redis
            # writes; mcp-server never publishes agent_done, so remove_conversation()
            # is never called and _pool_active_count_key stays elevated. We publish
            # agent_done here immediately after the instance is restored.
            if _kafka_producer and yaml_instance_id:
                _yaml_pools = list((yaml_snapshot or {}).get("pools") or ([pool_id] if pool_id else []))
                asyncio.create_task(_kafka_producer.send(
                    TOPIC_LIFECYCLE,
                    json.dumps({
                        "event":           "agent_done",
                        "tenant_id":       tenant_id,
                        "instance_id":     yaml_instance_id,
                        "agent_type_id":   agent_type_id,
                        "pools":           _yaml_pools,   # required by remove_conversation()
                        "conversation_id": session_id,
                        "timestamp":       datetime.now(timezone.utc).isoformat(),
                    }).encode("utf-8"),
                ))

            # Signal to process_contact_event that this instance was naturally
            # restored — no emergency restore needed on future contact_closed events.
            if yaml_instance_id:
                try:
                    await redis_client.srem(
                        f"session:{session_id}:ai_agents", yaml_instance_id
                    )
                except Exception:
                    pass
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

        # ── Populate routing-engine instance meta (bootstrap gap) ─────────────
        # instance_bootstrap.py creates AI instances directly in Redis without
        # publishing agent_ready to Kafka.  Routing engine's remove_conversation()
        # calls get_instance_meta() → reads this hash → if empty, DECR is skipped
        # silently → active_count accumulates on every session.
        # Writing the hash here ensures the routing engine can DECR correctly.
        if native_instance_id and pool_id and tenant_id:
            try:
                await redis_client.hset(
                    f"{tenant_id}:routing:instance:{native_instance_id}:meta",
                    mapping={"pools": json.dumps([pool_id]), "agent_type_id": agent_type_id},
                )
            except Exception as _meta_exc:
                logger.warning(
                    "Could not write instance meta: instance=%s — %s",
                    native_instance_id, _meta_exc,
                )

        # ── Conference dedup: skip if specialist from this pool already active ─
        # A repeat @mention while the specialist is already running would cause the
        # routing engine to generate another conversations.routed for the same pool.
        # Guard against that here so we don't double-activate the specialist.
        #
        # G7 fan-out EXEMPTION: hook agents (wrap-up / NPS) are NOT subject to this
        # pool-level dedup. In a multi-human customer-disconnect the anchor's
        # on_human_end AND each peer's segment_wrapup both target wrapup_ia — two
        # legitimate agents of the SAME pool serving different humans/segments.
        # They are identifiable by hook_conf (set by fire_pool_hooks, unique per
        # conference_id) and their lifecycle is governed by posatt/hook_pending,
        # not by this guard. Without the exemption the second hook to reach here
        # races on conference:specialist:{pool_id} (written further below) and is
        # silently skipped, so one human never gets a wrap-up — intermittent,
        # depending on the dispatch ordering of the two hooks.
        _is_hook_conf = False
        if conference_id:
            try:
                _is_hook_conf = bool(await redis_client.exists(
                    f"session:{session_id}:hook_conf:{conference_id}"
                ))
            except Exception:
                _is_hook_conf = False
        if conference_id and pool_id and not _is_hook_conf:
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
                await redis_client.expire(f"session:{session_id}:ai_agents", _stl())
                # Guard against premature restore: process_contact_event checks
                # this key before calling _restore_instance on contact_closed.
                # Cleared when activate_native_agent returns (natural path).
                # TTL=4h matches routing snapshot; crash recovery falls to bootstrap.
                await redis_client.setex(
                    f"session:{session_id}:ai_completing:{native_instance_id}",
                    14400,
                    "1",
                )
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

        # ── G2 fix: track non-hook AI conference specialists ──────────────────
        # Hook agents (NPS, wrap-up) already have session:{id}:hook_conf:{conference_id}
        # set by fire_pool_hooks() before this point.  Task-step specialists do not.
        # We track task-step specialists in a separate SET so the agent_closed path
        # can defer on_human_end hooks until they finish, avoiding message interleaving.
        _is_hook_agent = False  # default; set inside the block below when conference_id is set
        if conference_id and native_instance_id:
            try:
                _is_hook_agent = await redis_client.exists(
                    f"session:{session_id}:hook_conf:{conference_id}"
                )
                if not _is_hook_agent:
                    await redis_client.sadd(
                        f"session:{session_id}:active_ai_specialists", native_instance_id,
                    )
                    await redis_client.expire(
                        f"session:{session_id}:active_ai_specialists", 14400,
                    )
                    logger.debug(
                        "AI task-specialist tracked: session=%s instance=%s conference=%s",
                        session_id, native_instance_id, conference_id,
                    )
                else:
                    # Arc 14 Fase B: hook agent joining — add its instance_id to the
                    # posatt participants SET so the targeted session.closed includes it.
                    try:
                        _pset_key = (
                            f"session:{session_id}:posatt:{conference_id}:participants"
                        )
                        await redis_client.sadd(_pset_key, native_instance_id)
                        # TTL was already set by fire_pool_hooks; extend as safety.
                        await redis_client.expire(_pset_key, 14400)
                        logger.debug(
                            "posatt hook agent registered: session=%s conf=%s instance=%s",
                            session_id, conference_id, native_instance_id,
                        )
                    except Exception as _pset_exc:
                        logger.warning(
                            "Could not add hook agent to posatt participants: "
                            "session=%s — %s", session_id, _pset_exc,
                        )
            except Exception as exc:
                logger.warning(
                    "Could not track AI specialist: session=%s — %s", session_id, exc,
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
                await redis_client.expire(f"session:{session_id}:segment_seq", _stl())
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

            # Arc 11 — Store AI participant metadata for supervisor_state (F1)
            # Read by supervisor_state tool to build the participants[] array.
            # Key: session:{id}:ai_participant:{instance_id}  TTL: session TTL
            await redis_client.setex(
                f"session:{session_id}:ai_participant:{native_instance_id}",
                14400,
                json.dumps({
                    "role":          _part_role,
                    "agent_type_id": agent_type_id,
                    "pool_id":       pool_id,
                    "segment_id":    _part_seg_id,
                    "joined_at":     _part_joined_iso,
                }),
            )
        except Exception:
            pass

        # ── Write inviter_participant_id to ContextStore (conference only) ─────
        # When this activation is a conference specialist invite (e.g. Supervisor
        # Evaluator), write the inviting agent's instance_id into the specialist's
        # segment-scoped ContextStore namespace.  This allows the specialist's
        # notify steps to target ONLY the inviter via:
        #   visibility: ["@ctx.segment.inviter_participant_id"]
        # which resolves to the segment-prefixed tag at runtime:
        #   @ctx.segment.{_part_seg_id}.inviter_participant_id
        #
        # The inviter is identified as the current primary instance stored in
        # session:{session_id}:meta.instance_id — written by activate_human_agent
        # (human) or left intact from the previous primary native agent.
        if conference_id and _part_seg_id and tenant_id:
            try:
                _inviter_id  = ""
                _raw_meta    = await redis_client.get(f"session:{session_id}:meta")
                if _raw_meta:
                    _parsed_meta = json.loads(_raw_meta)
                    _inviter_id  = _parsed_meta.get("instance_id", "")
                if _inviter_id:
                    _ctx_key   = f"{tenant_id}:ctx:{session_id}"
                    _ctx_now   = datetime.now(timezone.utc).isoformat()
                    _ctx_field = f"segment.{_part_seg_id}.inviter_participant_id"
                    _ctx_entry = json.dumps({
                        "value":      _inviter_id,
                        "confidence": 1.0,
                        "source":     "bridge:conference",
                        "visibility": "agents_only",
                        "updated_at": _ctx_now,
                    })
                    await redis_client.hset(_ctx_key, _ctx_field, _ctx_entry)
                    await redis_client.expire(_ctx_key, _stl())
                    logger.debug(
                        "inviter_participant_id written: session=%s seg=%s inviter=%s",
                        session_id, _part_seg_id, _inviter_id,
                    )
            except Exception as _inviter_exc:
                logger.warning(
                    "Could not write inviter_participant_id: session=%s — %s",
                    session_id, _inviter_exc,
                )

            # ── G7: served_human_participant_id (wrap-up hook, por-segmento) ───────
            # Quando este join é um hook side=agent (wrap-up), fire_pool_hooks deixou
            # session:{id}:hook_served_human:{conference_id} = pid do humano DESTE
            # segmento. Grava no namespace segment-scoped do próprio wrap-up para que
            # suas mensagens usem visibility ["@ctx.segment.served_human_participant_id"]
            # — isolando o wrap-up ao humano certo mesmo com 2+ humanos na conferência
            # (substitui o campo de SESSÃO global session.human_agent_participant_id).
            try:
                _served_raw = await redis_client.get(
                    f"session:{session_id}:hook_served_human:{conference_id}"
                )
                if _served_raw:
                    _served_pid = (
                        _served_raw if isinstance(_served_raw, str) else _served_raw.decode()
                    )
                    if _served_pid:
                        _ctx_key   = f"{tenant_id}:ctx:{session_id}"
                        _ctx_now   = datetime.now(timezone.utc).isoformat()
                        _ctx_field = f"segment.{_part_seg_id}.served_human_participant_id"
                        await redis_client.hset(_ctx_key, _ctx_field, json.dumps({
                            "value":      _served_pid,
                            "confidence": 1.0,
                            "source":     "bridge:wrapup_hook",
                            "visibility": "agents_only",
                            "updated_at": _ctx_now,
                        }))
                        await redis_client.expire(_ctx_key, _stl())
                        logger.debug(
                            "served_human_participant_id written: session=%s seg=%s human=%s",
                            session_id, _part_seg_id, _served_pid,
                        )
            except Exception as _served_exc:
                logger.warning(
                    "Could not write served_human_participant_id: session=%s — %s",
                    session_id, _served_exc,
                )

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

        # ── Arc 15 Phase B: signal WebRTC adapter for medium negotiation ─────────
        # Publishes routing.assigned to the session stream so the WebRTC
        # _stream_watcher can negotiate media (video/voice/text) and send
        # webrtc.ready before the skill flow starts.  Non-WebRTC sessions
        # ignore this event.
        await _write_routing_assigned_to_stream(
            redis_client=redis_client,
            session_id=session_id,
            agent_type=agent_type,
            pool_config={"pool_id": pool_id},
            segment_id=_part_seg_id,
            instance_id=native_instance_id,
        )

        # ── Arc 19 Fase C: detect webhook pool + write session meta ─────────────
        # For webhook pool sessions the bridge writes session meta (NX) so that
        # _handle_webhook_session_resumed can look up agent_type_id and pool_id
        # when a resume_token arrives later on conversations.inbound.
        _is_webhook_pool = False
        try:
            _raw_pool_cfg = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
            if _raw_pool_cfg:
                _pool_cfg = json.loads(_raw_pool_cfg)
                _is_webhook_pool = "webhook" in (_pool_cfg.get("channel_types") or [])
        except Exception as _pool_exc:
            logger.debug(
                "Could not read pool config for webhook check: pool=%s — %s",
                pool_id, _pool_exc,
            )

        if _is_webhook_pool and native_instance_id:
            try:
                _wh_meta = json.dumps({
                    "contact_id":    customer_id,
                    "channel":       "webhook",
                    "agent_type_id": agent_type_id,
                    "pool_id":       pool_id,
                    "tenant_id":     tenant_id,
                    "customer_id":   customer_id,
                    "instance_id":   native_instance_id,
                })
                # NX: do not overwrite if already present (e.g. on a resume re-allocation)
                await redis_client.set(
                    f"session:{session_id}:meta",
                    _wh_meta,
                    nx=True,
                    ex=_stl(),
                )
                logger.debug(
                    "Webhook session meta written (NX): session=%s pool=%s instance=%s",
                    session_id, pool_id, native_instance_id,
                )
            except Exception as _wh_exc:
                logger.warning(
                    "Could not write webhook session meta: session=%s — %s",
                    session_id, _wh_exc,
                )

        # ── Arc 19 v2: ensure PRIMARY agent_type_id is in session meta ─────────
        # The webhook resume path (_handle_webhook_session_resumed) reads
        # agent_type_id from session:{id}:meta to re-allocate the agent when a
        # delegate/suspend step is resumed. Webchat sessions (e.g. Session A-new's
        # intake) have meta written by the WebchatAdapter WITHOUT agent_type_id, so
        # a resume failed with "agent_type_id not in meta — cannot resume".
        # Merge it in here (preserving existing meta fields) for the primary agent.
        if not conference_id and native_instance_id:
            try:
                _raw_meta_m = await redis_client.get(f"session:{session_id}:meta")
                _meta_m = json.loads(_raw_meta_m) if _raw_meta_m else {}
                _meta_m["agent_type_id"] = agent_type_id
                _meta_m["instance_id"]   = native_instance_id
                if not _meta_m.get("pool_id"):
                    _meta_m["pool_id"] = pool_id
                if not _meta_m.get("tenant_id"):
                    _meta_m["tenant_id"] = tenant_id
                await redis_client.setex(
                    f"session:{session_id}:meta", _stl(), json.dumps(_meta_m),
                )
            except Exception as _mm_exc:
                logger.warning(
                    "Could not merge agent_type_id into session meta: session=%s — %s",
                    session_id, _mm_exc,
                )

        agent_result = await activate_native_agent(
            http=http, redis_client=redis_client,
            session_id=session_id, customer_id=customer_id,
            agent_type_id=agent_type_id, tenant_id=tenant_id,
            skills=skills,
            instance_id=native_instance_id,
            conference_id=conference_id,
            segment_id=_part_seg_id,
            webhook_pool=_is_webhook_pool,
            pool_id=pool_id,
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

        # Skill flow ended naturally — clear the completing marker so that any
        # concurrent contact_closed event knows NOT to skip this instance.
        if native_instance_id:
            try:
                await redis_client.delete(
                    f"session:{session_id}:ai_completing:{native_instance_id}"
                )
            except Exception:
                pass

        # Restore instance with a long TTL so the next contact can be routed.
        # Stateless AI agents are always available after serving a session.
        # Gap-A fix: always land the mirror at ready — even when native_snapshot
        # is None (snapshot key expired mid-flow, e.g. delegate→OTP→suspend) — so
        # the mirror is never left busy after agent_done drops the SCARD.
        await _release_native_instance_snapshot(
            redis_client, tenant_id, native_instance_id, pool_id,
            agent_type_id, native_snapshot,
        )

        # Notify routing-engine of the instance transition:
        #   1. agent_ready  — triggers _drain_queue_for_agent() so queued contacts
        #                     are offered to this instance immediately. Also triggers
        #                     _refresh_pool_snapshots() so Monitor reflects the restored
        #                     capacity without waiting for the next routing event.
        #                     max_concurrent_sessions read from the instance snapshot so
        #                     the routing-engine models capacity correctly (#276).
        #   2. agent_done   — triggers remove_conversation() → DECR pool active_count.
        #                     Must fire AFTER agent_ready so the drain sees correct counts.
        if _kafka_producer and native_instance_id:
            _snap_max_concurrent = int(
                (native_snapshot or {}).get("max_concurrent_sessions")
                or (native_snapshot or {}).get("max_concurrent")
                or 1
            )
            _snap_pools = list((native_snapshot or {}).get("pools") or ([pool_id] if pool_id else []))
            asyncio.create_task(_kafka_producer.send(
                TOPIC_LIFECYCLE,
                json.dumps({
                    "event":                   "agent_ready",
                    "tenant_id":               tenant_id,
                    "instance_id":             native_instance_id,
                    "agent_type_id":           agent_type_id,
                    "status":                  "ready",
                    "execution_model":         (native_snapshot or {}).get("execution_model", "stateless"),
                    "current_sessions":        0,
                    "max_concurrent_sessions": _snap_max_concurrent,
                    "pools":                   _snap_pools,
                    "timestamp":               datetime.now(timezone.utc).isoformat(),
                }).encode("utf-8"),
            ))

        # Notify routing-engine to decrement the pool's busy counter.
        # For plughub-native agents the bridge manages lifecycle via direct Redis
        # writes; mcp-server never publishes agent_done, so remove_conversation()
        # is never called and _pool_active_count_key stays elevated. We publish
        # agent_done here immediately after the instance is restored.
        if _kafka_producer and native_instance_id:
            asyncio.create_task(_kafka_producer.send(
                TOPIC_LIFECYCLE,
                json.dumps({
                    "event":           "agent_done",
                    "tenant_id":       tenant_id,
                    "instance_id":     native_instance_id,
                    "agent_type_id":   agent_type_id,
                    "pools":           _snap_pools,   # required by remove_conversation()
                    "conversation_id": session_id,
                    "timestamp":       datetime.now(timezone.utc).isoformat(),
                }).encode("utf-8"),
            ))

        # Signal to process_contact_event that this instance was naturally
        # restored — no emergency restore needed on future contact_closed events.
        if native_instance_id:
            try:
                await redis_client.srem(
                    f"session:{session_id}:ai_agents", native_instance_id
                )
            except Exception:
                pass

        # ── Clear specialist conference key ───────────────────────────────────
        # For plughub-native agents, runtime.ts agent_done is never called so
        # conference_agent_completed is never published → the Kafka handler that
        # normally cleans up this key never runs.  Without this delete the key
        # persists with its 4h TTL and the dedup guard blocks re-invocation of
        # the same pool within the same session (e.g. calling @auth_form twice).
        if conference_id and pool_id:
            try:
                await redis_client.delete(
                    f"session:{session_id}:conference:specialist:{pool_id}"
                )
                logger.info(
                    "Specialist conference key cleared: session=%s pool=%s",
                    session_id, pool_id,
                )
            except Exception:
                pass

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
        # flow_id = skill-flow deployado que o agente executou (avaliação IA por skill)
        _part_flow_id = (((agent_result or {}).get("pipeline_state")) or {}).get("flow_id", "") or ""
        # F7: motivo de escalação normalizado declarado pelo escalate step (IA),
        # persistido em pipeline_state.results.escalation_reason via output_as.
        _part_results = (((agent_result or {}).get("pipeline_state")) or {}).get("results") or {}
        _part_esc = str(_part_results.get("escalation_reason", "") or "") or None
        # ── Fase A (queue-attended-model): record last primary outcome ────────
        # Single source of truth for outcome is the segment; the session-level
        # outcome in contact_closed is DERIVED from the last primary segment.
        # _close_contact_layer() reads this marker to populate the analytics event.
        if _part_role == "primary" and _part_outcome:
            try:
                await redis_client.setex(
                    f"session:{session_id}:last_outcome",
                    604800,
                    json.dumps({"outcome": _part_outcome, "agent_kind": "ai"}),
                )
            except Exception:
                pass
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
            flow_id=_part_flow_id,
            escalation_reason=_part_esc,
        ))
        # G5 dedup guard: conference_agent_completed checks this key before emitting
        # participant_left for external conference specialists.  Native bridge agents
        # always go through process_routed (here), so the guard is set and the Kafka
        # handler correctly skips emission.  External agents never reach this branch,
        # so the guard is absent and conference_agent_completed emits for them.
        if native_instance_id:
            try:
                await redis_client.set(
                    f"session:{session_id}:participant_left:{native_instance_id}",
                    "1",
                    nx=True,
                    ex=86400,
                )
            except Exception:
                pass

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
        # Arc 19: "suspended" is added so webhook sessions are NOT closed when
        # the engine returns outcome: "suspended" — the session persists in Redis
        # (TTL extended by persistSuspendWebhook) awaiting a resume signal.
        _escalation_outcomes = ("escalated_human", "escalated_ai", "transferred", "suspended")
        _ai_outcome = (agent_result or {}).get("outcome", "")
        if not conference_id and _ai_outcome not in _escalation_outcomes:
            # G1 fix: freeze AHT at primary AI completion, before any hook agents run.
            await _mark_contact_ended(redis_client, session_id)
            # ── on_contact_end no fim de contato de primário IA (completude do hook) ──
            # O hook on_contact_end (fim-de-CONTATO) é o mecanismo GENÉRICO de
            # fim-de-contato: segura a sessão do cliente (posatt:customer_active) e roda
            # o skill do pool configurado NA conferência. Até aqui só era disparado no
            # caminho com humano (process_contact_event); um contato resolvido SÓ por IA
            # fechava direto sem dar a chance ao hook. Aqui completamos: quando o
            # contato encerra com o primário IA e o pool declara hooks.on_contact_end,
            # disparamos o hook em vez de fechar direto. NÃO é lógica de survey — o que o
            # hook faz (NPS in-conference, outbound, skip) é decisão do SKILL configurado.
            # Gate em outcome=resolved: é o sinal "cliente presente no fim" do fluxo só-IA
            # (em failed/abandoned/timeout o cliente já saiu — fecha direto, como antes).
            _contact_end_hooks: list = []
            if _part_role == "primary" and _ai_outcome == "resolved" and http and pool_id and tenant_id:
                try:
                    _ce_cfg = await get_pool_config(http, tenant_id, pool_id)
                    _contact_end_hooks = (
                        ((_ce_cfg or {}).get("hooks") or {}).get("on_contact_end", []) or []
                    )
                except Exception as _ce_exc:
                    logger.warning(
                        "on_contact_end lookup failed: session=%s pool=%s — %s",
                        session_id, pool_id, _ce_exc,
                    )
            if _contact_end_hooks:
                _ce_customer = session_id
                try:
                    _ce_meta = await redis_client.get(f"session:{session_id}:meta")
                    if _ce_meta:
                        _ce_customer = (json.loads(
                            _ce_meta if isinstance(_ce_meta, str) else _ce_meta.decode()
                        ).get("customer_id") or session_id)
                except Exception:
                    pass
                # close_origin=flow_complete: cliente presente (resolveu o fluxo). O skill
                # de NPS e nps_on_disconnect=skip cuidam do caso de o cliente cair durante.
                await _write_pre_hook_context(
                    redis_client, tenant_id, session_id,
                    close_origin="flow_complete",
                )
                asyncio.create_task(fire_pool_hooks(
                    http=http, redis_client=redis_client,
                    session_id=session_id, pool_id=pool_id, tenant_id=tenant_id,
                    customer_id=_ce_customer, hook_type="on_contact_end",
                    human_instance_id="",
                ))
                asyncio.create_task(_hook_timeout_guard(
                    redis_client, session_id, "on_contact_end",
                ))
                logger.info(
                    "on_contact_end hook dispatched (AI primary contact end): "
                    "session=%s pool=%s n=%d", session_id, pool_id, len(_contact_end_hooks),
                )
            else:
                asyncio.create_task(_trigger_contact_close(redis_client, session_id))

        # ── Arc 19 Fase B: publish session_suspended to canonical stream ──────
        # Fired for webhook pool sessions only (channel_type: webhook).
        # Non-webhook sessions never return outcome: "suspended" because the
        # engine guard (Arc 19 step profile enforcement) blocks the suspend step.
        if _ai_outcome == "suspended" and not conference_id:
            try:
                # Extract resume_token and expires_at from pipeline_state.results.
                # Keys: {step_id}:__resume_token__ and {step_id}:__expires_at__
                _ps_results = (
                    ((agent_result or {}).get("pipeline_state") or {}).get("results") or {}
                )
                _susp_token    = ""
                _susp_expires  = ""
                _susp_step_id  = ""
                for _k, _v in _ps_results.items():
                    if isinstance(_k, str) and _k.endswith(":__resume_token__"):
                        _susp_token   = str(_v or "")
                        _susp_step_id = _k[: -len(":__resume_token__")]
                    elif isinstance(_k, str) and _k.endswith(":__expires_at__"):
                        _susp_expires = str(_v or "")

                await redis_client.xadd(
                    f"session:{session_id}:stream",
                    {
                        "event_id":    str(uuid.uuid4()),
                        "type":        "session_suspended",
                        "timestamp":   datetime.now(timezone.utc).isoformat(),
                        "author_id":   native_instance_id or agent_type_id,
                        "author_role": "ai",
                        "visibility":  json.dumps("agents_only"),
                        "segment_id":  _left_seg_id or "",
                        "payload":     json.dumps({
                            "step_id":           _susp_step_id,
                            "resume_token":      _susp_token,
                            "resume_expires_at": _susp_expires,
                        }),
                    },
                    maxlen=500,
                )

                # Update the status key so WebhookAdapter.get_status() returns "suspended".
                # TTL: use the session TTL as a safe floor; the actual Redis key TTLs on
                # the session stream were extended by persistSuspendWebhook (Fase C).
                if tenant_id:
                    await redis_client.setex(
                        f"{tenant_id}:session:{session_id}:status",
                        _stl(),
                        "suspended",
                    )

                # Arc 19 Fase E: publish session_suspended to Kafka conversations.events
                # so analytics-api can write status='suspended' to ClickHouse sessions table.
                if _kafka_producer is not None and tenant_id:
                    try:
                        _susp_now = datetime.now(timezone.utc).isoformat()
                        await _kafka_producer.send_and_wait(
                            TOPIC_EVENTS,
                            json.dumps({
                                "event_type": "session_suspended",
                                "session_id": session_id,
                                "tenant_id":  tenant_id,
                                "step_id":    _susp_step_id,
                                "timestamp":  _susp_now,
                            }).encode("utf-8"),
                        )
                    except Exception as _ke:
                        logger.warning(
                            "Could not publish session_suspended to Kafka: session=%s — %s",
                            session_id, _ke,
                        )

                logger.info(
                    "session_suspended published to stream: session=%s step=%s token=%s expires=%s",
                    session_id, _susp_step_id, _susp_token, _susp_expires,
                )
            except Exception as _susp_exc:
                logger.warning(
                    "Could not publish session_suspended to stream: session=%s — %s",
                    session_id, _susp_exc,
                )

        # ── Arc 14 / Fase B/C: hook completion detection ─────────────────────
        # hook_conf key stores "{hook_type}:{target_pool}:{side}" (Arc 14 extended
        # from the old "{hook_type}:{target_pool}" format — backward compat: missing
        # side part defaults to "agent").
        #
        # Algorithm (Arc 14 Fase A):
        #   1. DECR hook_pending:{hook_type} (tracks completion per hook_type)
        #   2. If last on_human_end: dispatch post_human BEFORE DECRing posatt:active
        #      (so post_human INCRs are already in place before our DECR)
        #   3. DECR posatt:active (one per hook segment completing, regardless of type)
        #   4. If posatt:active == 0 and no new segments dispatched → _destroy_conference()
        #      (Layer 1 — customer WS — was already closed by _close_contact_layer())
        if conference_id:
            try:
                hook_label = await redis_client.getdel(
                    f"session:{session_id}:hook_conf:{conference_id}"
                )
                if hook_label:
                    _hl = hook_label if isinstance(hook_label, str) else hook_label.decode()
                    # F5: 5º campo = human_segment_id. "{hook}:{target}:{side}:{origin}:{seg}"
                    _hl_parts           = _hl.split(":", 4)
                    completed_hook_type = _hl_parts[0]
                    _hook_target_pool   = _hl_parts[1] if len(_hl_parts) > 1 else ""
                    _hook_side          = _hl_parts[2] if len(_hl_parts) > 2 else "agent"
                    _hook_origin_pool   = _hl_parts[3] if len(_hl_parts) > 3 else ""  # Arc 14 Fase C
                    _hook_human_seg     = _hl_parts[4] if len(_hl_parts) > 4 else ""   # F5

                    # G7 Slice B: segment_wrapup é fim-de-SEGMENTO (transfer) — NÃO
                    # arma contadores de close. Não DECR hook_pending/posatt:active,
                    # não dispara _close_contact_layer/_destroy_conference. Só aplica a
                    # disposição ao segmento, limpa wrap_up_pending e fecha o painel
                    # de wrap-up da origem (posatt_segment_complete). O contato segue
                    # pelo destino (re-rota já em voo).
                    _is_segment_wrapup = (completed_hook_type == "segment_wrapup")

                    if _is_segment_wrapup:
                        remaining_hooks = 0
                    else:
                        remaining_hooks = await redis_client.decr(
                            f"session:{session_id}:hook_pending:{completed_hook_type}"
                        )
                    logger.info(
                        "Hook agent completed: session=%s conference=%s hook=%s pool=%s side=%s remaining=%d",
                        session_id, conference_id, completed_hook_type, _hook_target_pool,
                        _hook_side, remaining_hooks,
                    )

                    # Arc 14 Fase B: publish targeted session.closed for this segment.
                    # Read the participant SET registered by fire_pool_hooks (fixed-side)
                    # and by process_routed on hook-agent join.
                    # recipients=[...] → only those agents tear down their session view.
                    # The broadcast session.closed (reason=conference_destroyed) from
                    # _destroy_conference() still fires as the global cleanup signal.
                    try:
                        _pset_key = (
                            f"session:{session_id}:posatt:{conference_id}:participants"
                        )
                        _raw_pids = await redis_client.smembers(_pset_key)
                        _recipients = [
                            (p.decode() if isinstance(p, bytes) else p)
                            for p in (_raw_pids or [])
                        ]
                        if _recipients:
                            await redis_client.publish(
                                f"agent:events:{session_id}",
                                json.dumps({
                                    "type":       "session.closed",
                                    "session_id": session_id,
                                    "reason":     "posatt_segment_complete",
                                    "recipients": _recipients,
                                }),
                            )
                            await redis_client.delete(_pset_key)
                            logger.info(
                                "posatt segment closed: session=%s conf=%s hook=%s "
                                "recipients=%s",
                                session_id, conference_id, completed_hook_type, _recipients,
                            )
                        else:
                            logger.debug(
                                "posatt segment closed: no participants SET found — "
                                "session=%s conf=%s", session_id, conference_id,
                            )
                    except Exception as _tgt_exc:
                        logger.warning(
                            "Could not publish posatt targeted session.closed: "
                            "session=%s — %s", session_id, _tgt_exc,
                        )

                    # Arc 14 Fase C: wrap_up_pending cleanup.
                    # When the agent-side (wrap-up) segment completes, delete the flag
                    # so the routing-engine can allocate new contacts to this agent.
                    # Use origin_pool from hook_conf (4th field) to derive instance_id:
                    # human-{origin_pool} — same key written by fire_pool_hooks.
                    if _hook_side == "agent" and completed_hook_type in ("on_human_end", "segment_wrapup"):
                        try:
                            if _hook_origin_pool and tenant_id:
                                _wup_iid = f"human-{_hook_origin_pool}"
                                _wp_key = (
                                    f"{tenant_id}:instance:{_wup_iid}:wrap_up_pending"
                                )
                                await redis_client.delete(_wp_key)
                                logger.info(
                                    "wrap_up_pending cleared: session=%s instance=%s",
                                    session_id, _wup_iid,
                                )
                            else:
                                logger.warning(
                                    "wrap_up_pending: no origin_pool in hook_conf — "
                                    "key not deleted: session=%s", session_id,
                                )
                        except Exception as _wup_exc:
                            logger.warning(
                                "Could not clear wrap_up_pending: session=%s — %s",
                                session_id, _wup_exc,
                            )

                        # ── F5 (grão segmento): wrap-up completou ──────────────
                        # A disposição coletada está no pipeline_state do PRÓPRIO
                        # agente que completou (results.wrapup_classificacao/resumo)
                        # — sem depender de ContextStore. Atribui ao segmento humano
                        # que ESTE on_human_end serviu (_hook_human_seg do hook_conf).
                        if _hook_human_seg:
                            _wp_results = (((agent_result or {}).get("pipeline_state")) or {}).get("results") or {}
                            _wp_cls = str(_wp_results.get("wrapup_classificacao", "") or "")
                            _wp_res = str(_wp_results.get("wrapup_resumo", "") or "")
                            # F7: motivo de escalação normalizado (só presente quando escalado).
                            _wp_esc = str(_wp_results.get("wrapup_escalation_reason", "") or "")
                            if _wp_cls:
                                asyncio.create_task(_apply_wrapup_to_segment(
                                    redis_client, session_id, _hook_human_seg, _wp_cls, _wp_res, _wp_esc,
                                ))

                    # Arc 14 Fase E: when a customer-side hook (NPS) completes,
                    # DECR posatt:customer_active and close the customer WS when
                    # the counter reaches 0 (all NPS/survey segments finished).
                    # In the no-customer-hook path, _close_contact_layer() fires
                    # immediately in the agent_done handler above.
                    if _hook_side == "customer":
                        # F10.3b: o NPS de segmento é gravado pelo PRÓPRIO agente de
                        # NPS via survey_record(grain=segment) — caminho unificado em
                        # session_signal. O bridge não deriva mais nps_score aqui
                        # (legado _apply_nps_to_segment/segments.nps_score removido).
                        try:
                            _cust_remaining = await redis_client.decr(
                                f"session:{session_id}:posatt:customer_active"
                            )
                            logger.info(
                                "posatt:customer_active DECR: session=%s conf=%s "
                                "hook=%s remaining=%d",
                                session_id, conference_id, completed_hook_type,
                                _cust_remaining,
                            )
                            if _cust_remaining <= 0:
                                asyncio.create_task(
                                    _close_contact_layer(redis_client, session_id)
                                )
                        except Exception as _ca_exc:
                            logger.warning(
                                "Could not DECR posatt:customer_active: session=%s — %s",
                                session_id, _ca_exc,
                            )

                    # Arc 14: dispatch post_human BEFORE DECRing posatt:active so that
                    # fire_pool_hooks() INCRs posatt:active for each new segment FIRST.
                    _dispatched_post = False
                    if remaining_hooks <= 0 and completed_hook_type == "on_human_end":
                        _ph_pool = _ph_tenant = _ph_customer = ""
                        try:
                            _ph_raw = await redis_client.get(f"session:{session_id}:meta")
                            if _ph_raw:
                                _ph_meta     = json.loads(_ph_raw)
                                _ph_pool     = _ph_meta.get("pool_id", "")
                                _ph_tenant   = (
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
                                    # fire_pool_hooks INCRs posatt:active for each post_human hook
                                    asyncio.create_task(fire_pool_hooks(
                                        http=http,
                                        redis_client=redis_client,
                                        session_id=session_id,
                                        pool_id=_ph_pool,
                                        tenant_id=_ph_tenant,
                                        customer_id=_ph_customer,
                                        hook_type="post_human",
                                    ))
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

                    # Arc 14: DECR posatt:active for this completing hook segment.
                    # Done AFTER dispatching post_human so INCRs precede this DECR.
                    # G7 Slice B: segment_wrapup nunca fez INCR posatt:active e NÃO pode
                    # fechar o contato (segue pelo destino) — pula DECR + _destroy.
                    _posatt_remaining = -1
                    if not _is_segment_wrapup:
                        try:
                            _posatt_remaining = await redis_client.decr(
                                f"session:{session_id}:posatt:active"
                            )
                            logger.info(
                                "posatt:active DECR: session=%s conference=%s hook=%s remaining=%d",
                                session_id, conference_id, completed_hook_type, _posatt_remaining,
                            )
                        except Exception as _pa_exc:
                            logger.warning(
                                "Could not DECR posatt:active: session=%s — %s",
                                session_id, _pa_exc,
                            )

                        # Destroy conference when all posatt segments finished AND no new
                        # segments were just dispatched (post_human dispatch adds more INCRs).
                        # _close_contact_layer() already closed the customer WS immediately.
                        if _posatt_remaining <= 0 and not _dispatched_post:
                            asyncio.create_task(
                                _destroy_conference(redis_client, session_id)
                            )

                    # G7 Fatia 2b/3 — conclusão de um segment_wrapup do fan-out de
                    # customer-disconnect: DECR contact_close_pending (via marcador
                    # close_arming por-conferência, idempotente). Quando zera E não há
                    # posatt:active pendente → fecha o contato (deferido até aqui para
                    # que TODOS os humanos recebessem wrap-up). segment_wrapup de
                    # transfer/peer-continuação não tem o marcador → no-op.
                    if _is_segment_wrapup:
                        try:
                            _arming = await redis_client.getdel(
                                f"session:{session_id}:close_arming:{conference_id}"
                            )
                            if _arming:
                                _ccp_rem = await redis_client.decr(
                                    f"session:{session_id}:contact_close_pending"
                                )
                                logger.info(
                                    "contact_close_pending DECR: session=%s conf=%s remaining=%d",
                                    session_id, conference_id, _ccp_rem,
                                )
                                if _ccp_rem <= 0:
                                    # Todos os wrap-ups de peer terminaram. _close_contact_layer
                                    # é idempotente (contact_close_fired NX) e _destroy_conference
                                    # se auto-guarda em posatt:active — então, na ordem âncora-por-
                                    # último, o _destroy aqui adia e a conclusão da âncora
                                    # (posatt→0) o re-dispara. Robusto em qualquer ordem.
                                    logger.info(
                                        "Fan-out complete (contact_close_pending=0) — closing "
                                        "contact: session=%s", session_id,
                                    )
                                    asyncio.create_task(
                                        _close_contact_layer(redis_client, session_id)
                                    )
                                    asyncio.create_task(
                                        _destroy_conference(redis_client, session_id)
                                    )
                        except Exception as _ccp_exc:
                            logger.warning(
                                "Could not process contact_close_pending: session=%s — %s",
                                session_id, _ccp_exc,
                            )

            except Exception as exc:
                logger.warning(
                    "Hook completion detection error: session=%s conference=%s — %s",
                    session_id, conference_id, exc,
                )

            # ── G2 fix (native inline): SREM + deferred on_human_end dispatch ──────
            # For plughub-native specialists the skill flow runs inline inside
            # activate_native_agent — runtime.ts agent_done is never called, so
            # conference_agent_completed is never published to Kafka and the Kafka
            # handler's G2 block (line ~2500) never executes.
            # We must do the SREM + deferred hook check here, synchronously, after
            # the skill flow returns.
            if native_instance_id and not _is_hook_agent:
                try:
                    await redis_client.srem(
                        f"session:{session_id}:active_ai_specialists", native_instance_id,
                    )
                    _native_rem_specs = await redis_client.scard(
                        f"session:{session_id}:active_ai_specialists"
                    )
                    if _native_rem_specs == 0:
                        _native_pend_raw = await redis_client.getdel(
                            f"session:{session_id}:pending_on_human_end"
                        )
                        if _native_pend_raw:
                            _npd = json.loads(
                                _native_pend_raw if isinstance(_native_pend_raw, str)
                                else _native_pend_raw.decode()
                            )
                            _npd_pool     = _npd.get("pool_id", "")
                            _npd_tenant   = _npd.get("tenant_id", "")
                            _npd_customer = _npd.get("customer_id", session_id)
                            _npd_h_inst   = _npd.get("human_instance_id")
                            _npd_cust_pid = _npd.get("customer_participant_id")
                            logger.info(
                                "All native specialists done — dispatching deferred on_human_end: "
                                "session=%s pool=%s", session_id, _npd_pool,
                            )
                            if http and _npd_pool and _npd_tenant:
                                _npd_pool_cfg = await get_pool_config(
                                    http, _npd_tenant, _npd_pool
                                )
                                _npd_hooks_cfg = (_npd_pool_cfg or {}).get("hooks") or {}
                                _npd_hooks      = _npd_hooks_cfg.get("on_human_end", [])
                                # G7 Fase 3b: NPS migrou para on_contact_end.
                                _npd_contact    = _npd_hooks_cfg.get("on_contact_end", [])
                                if _npd_hooks or _npd_contact:
                                    await _write_pre_hook_context(
                                        redis_client, _npd_tenant, session_id,
                                        close_origin="agent_closed",
                                        human_instance_id=_npd_h_inst,
                                        customer_participant_id=_npd_cust_pid,
                                    )
                                    if _npd_hooks:
                                        asyncio.create_task(fire_pool_hooks(
                                            http=http, redis_client=redis_client,
                                            session_id=session_id,
                                            pool_id=_npd_pool,
                                            tenant_id=_npd_tenant,
                                            customer_id=_npd_customer,
                                            hook_type="on_human_end",
                                            human_instance_id=_npd_h_inst or "",
                                        ))
                                        asyncio.create_task(_hook_timeout_guard(
                                            redis_client, session_id, "on_human_end",
                                        ))
                                    if _npd_contact:
                                        asyncio.create_task(fire_pool_hooks(
                                            http=http, redis_client=redis_client,
                                            session_id=session_id,
                                            pool_id=_npd_pool,
                                            tenant_id=_npd_tenant,
                                            customer_id=_npd_customer,
                                            hook_type="on_contact_end",
                                            human_instance_id=_npd_h_inst or "",
                                        ))
                                        asyncio.create_task(_hook_timeout_guard(
                                            redis_client, session_id, "on_contact_end",
                                        ))
                                    logger.info(
                                        "contact-end hooks dispatched (native deferred): "
                                        "session=%s pool=%s on_human_end=%d on_contact_end=%d",
                                        session_id, _npd_pool, len(_npd_hooks), len(_npd_contact),
                                    )
                                else:
                                    asyncio.create_task(
                                        _trigger_contact_close(redis_client, session_id)
                                    )
                            else:
                                asyncio.create_task(
                                    _trigger_contact_close(redis_client, session_id)
                                )
                except Exception as _g2n_exc:
                    logger.warning(
                        "G2 native: could not process deferred on_human_end: "
                        "session=%s — %s", session_id, _g2n_exc,
                    )

    elif framework == "human":
        # ── Arc 15 Phase B: signal WebRTC adapter before activating human agent ──
        await _write_routing_assigned_to_stream(
            redis_client=redis_client,
            session_id=session_id,
            agent_type=agent_type,
            pool_config={"pool_id": pool_id},
            segment_id="",   # segment_id assigned inside activate_human_agent
            instance_id=result.get("instance_id", ""),
        )
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
        # ── Arc 15 Phase B: signal WebRTC adapter ────────────────────────────
        await _write_routing_assigned_to_stream(
            redis_client=redis_client,
            session_id=session_id,
            agent_type=agent_type,
            pool_config={"pool_id": pool_id},
            segment_id="",
            instance_id=result.get("instance_id", ""),
        )
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
        # Fase C (queue-attended-model): tenant-wide default queue agent.
        # Pool-level queue_config wins; the Config API session namespace
        # provides the fallback (queue_default_agent_type_id / _skill_id).
        # Empty default = original behaviour (customer waits silently).
        _default_agent = session_config.get("queue_default_agent_type_id", "") or ""
        if _default_agent:
            queue_cfg = {"agent_type_id": _default_agent}
            _default_skill = session_config.get("queue_default_skill_id", "") or ""
            if _default_skill:
                queue_cfg["skill_id"] = _default_skill
            logger.info(
                "No queue_config for pool=%s — using tenant default queue agent %s",
                pool_id, _default_agent,
            )
        else:
            logger.debug("No queue_config for pool=%s — customer waits silently", pool_id)
            return

    agent_type_id = queue_cfg.get("agent_type_id", "") or ""
    explicit_skill_id = queue_cfg.get("skill_id")   # optional — overrides agent's default skill
    if not agent_type_id:
        # Fase E (skill-first): agent_types aposentados — queue_config pode
        # vir só com skill_id (UI do pool). A skill é a identidade do agente
        # de fila (segmento role=queue) e resolve o flow direto no registry.
        if explicit_skill_id:
            agent_type_id = explicit_skill_id
        else:
            logger.warning(
                "queue_config has neither agent_type_id nor skill_id for pool=%s",
                pool_id,
            )
            return

    # Resolve agent type metadata (framework, skills list)
    agent_type = await get_agent_type(http, tenant_id, agent_type_id)
    if agent_type is None:
        # YAML fallback (dev environment without Agent Registry)
        flow = _load_yaml_fallback(agent_type_id)
        if not flow and not explicit_skill_id:
            logger.error(
                "Queue agent %s not found in Agent Registry and no YAML fallback in %s",
                agent_type_id, SKILLS_DIR,
            )
            return
        # explicit_skill_id present: resolve_flow_for_agent fetches the flow
        # from the registry by skill_id — no agent type nor YAML required.
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

    # ── Fase C (queue-attended-model): queue segment — own ledger entry ───────
    # role='queue' marks the wait window in analytics.segments. pool_id stays =
    # the TARGET pool (the Fila/SLA reporting dimension — "where did the contact
    # wait"). Queue segments never touch segment_seq nor primary_segment: the
    # analytic invariant is "atendido" = first `primary` segment of the session,
    # and agent metrics (primary/specialist filters) exclude queue by construction.
    _q_seg_id      = str(uuid.uuid4())
    _q_joined_at   = datetime.now(timezone.utc)
    _q_joined_iso  = _q_joined_at.isoformat()
    _q_participant = f"queue-{session_id}"   # instance_id="" — synthetic identity
    asyncio.create_task(_publish_participant_event(
        session_id=session_id,
        tenant_id=tenant_id,
        participant_id=_q_participant,
        pool_id=pool_id,
        agent_type_id=agent_type_id,
        event_type="participant_joined",
        agent_type="native",
        role="queue",
        segment_id=_q_seg_id,
        joined_at=_q_joined_iso,
    ))

    # Activate the queue agent — this call blocks for the entire wait duration
    # because the skill flow contains a menu step with timeout_s=0.
    # It returns only when the queue agent's skill flow completes (either via
    # '__agent_available__' signal or customer disconnect / max_wait_s timeout).
    # extra_context exposes pool_id and session_id so the YAML's invoke step can
    # dynamically call conversation_escalate with the correct target pool.
    agent_result = await activate_native_agent(
        http=http, redis_client=redis_client,
        session_id=session_id, customer_id=customer_id,
        agent_type_id=agent_type_id, tenant_id=tenant_id,
        skills=skills,
        instance_id="",   # queue agents don't hold a routing slot
        extra_context={"pool_id": pool_id},
        segment_id=_q_seg_id,
        pool_id=pool_id,   # fatia 1: allow $.config from the queue pool's slot
    )

    # ── Fase C: close the queue segment (wait window) ─────────────────────────
    # outcome from the queue skill-flow: escalated_human (handoff to target).
    # ABANDONO é detectado pela plataforma, nunca declarado pelo flow (contrato
    # Fase A): se a sessão fechou enquanto a fila rodava (session:{id}:closed),
    # o flow saiu via on_disconnect — mas seu complete step ainda reporta
    # escalated_human. Override aqui: segmento de fila vira "abandoned".
    # Deliberately NOT written to session:{id}:last_outcome — only primary
    # segments drive session outcome.
    _q_duration_ms = int(
        (datetime.now(timezone.utc) - _q_joined_at).total_seconds() * 1000
    )
    _q_outcome = (agent_result or {}).get("outcome") or None
    try:
        if await redis_client.exists(f"session:{session_id}:closed"):
            _q_outcome = "abandoned"
    except Exception:
        pass
    _q_flow_id = (((agent_result or {}).get("pipeline_state")) or {}).get("flow_id", "") or ""
    asyncio.create_task(_publish_participant_event(
        session_id=session_id,
        tenant_id=tenant_id,
        participant_id=_q_participant,
        pool_id=pool_id,
        agent_type_id=agent_type_id,
        event_type="participant_left",
        agent_type="native",
        role="queue",
        segment_id=_q_seg_id,
        joined_at=_q_joined_iso,
        duration_ms=_q_duration_ms,
        outcome=_q_outcome,
        flow_id=_q_flow_id,
    ))

    # Clean up marker after the queue agent completes
    try:
        await redis_client.delete(f"queue:agent_active:{session_id}")
    except Exception:
        pass

    logger.info(
        "Queue agent completed: session=%s pool=%s outcome=%s wait_ms=%d",
        session_id, pool_id, _q_outcome, _q_duration_ms,
    )


# ── G7 — ponto único de verdade: este fim de segmento é também fim de contato? ──

async def _has_continuation(
    redis_client: aioredis.Redis,
    session_id: str,
    reason: str,
    remaining: int,
) -> tuple[bool, str]:
    """G7: classifica se o fim de um segmento humano é uma CONTINUAÇÃO (o contato
    segue) ou um fim de contato. Read-only — não muda estado.

    Continuação quando:
      - reason == "agent_transfer"  → re-rota em voo para outro pool;
      - remaining > 0               → ainda há outro humano primário ativo;
      - active_ai_specialists > 0   → specialist IA ainda respondendo.
    Caso contrário → fim de contato (no_continuation).

    Retorna (is_continuation, motivo). Nas fases ≥3 do G7 esta decisão passa a
    governar contact-close + disparo do NPS. Ver docs/arcos/g7-segment-contact-decoupling.md.
    """
    if reason == "agent_transfer":
        return True, "transfer"
    if remaining > 0:
        return True, "other_human_active"
    try:
        _spec = await redis_client.scard(f"session:{session_id}:active_ai_specialists")
        if _spec and int(_spec) > 0:
            return True, "specialist_active"
    except Exception:
        pass
    return False, "no_continuation"


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
            # ── G5 fix: participant_left for external conference specialists ──────
            # Native bridge agents emit participant_left in process_routed when
            # activate_native_agent returns — they set a dedup guard key.
            # External agents (external-mcp SDK) never go through activate_native_agent,
            # so this Kafka handler is their only chance to emit the event.
            # We check the guard; if absent, we won and emit it here.
            if instance_id:
                try:
                    _g5_guard = await redis_client.set(
                        f"session:{session_id}:participant_left:{instance_id}",
                        "1",
                        nx=True,
                        ex=86400,
                    )
                    if _g5_guard:
                        # Guard not previously set → external agent → emit participant_left
                        _g5_joined_iso = ""
                        try:
                            _raw_g5_jat = await redis_client.getdel(
                                f"session:{session_id}:participant_joined_at:{instance_id}"
                            )
                            _g5_joined_iso = (
                                _raw_g5_jat if isinstance(_raw_g5_jat, str)
                                else (_raw_g5_jat.decode() if _raw_g5_jat else "")
                            )
                        except Exception:
                            pass
                        _g5_dur: int | None = None
                        if _g5_joined_iso:
                            try:
                                _g5_jdt = datetime.fromisoformat(_g5_joined_iso)
                                _g5_dur = int(
                                    (datetime.now(timezone.utc) - _g5_jdt).total_seconds() * 1000
                                )
                            except Exception:
                                pass
                        _g5_seg_id = ""
                        try:
                            _raw_g5_seg = await redis_client.getdel(
                                f"session:{session_id}:segment:{instance_id}"
                            )
                            if _raw_g5_seg:
                                _g5_seg_id = (
                                    _raw_g5_seg if isinstance(_raw_g5_seg, str)
                                    else _raw_g5_seg.decode()
                                )
                        except Exception:
                            pass
                        # pool_id from specialist key; agent_type_id from same
                        _g5_pool = _g5_agent_type = _g5_tenant = ""
                        try:
                            _g5_spec_keys = await redis_client.keys(
                                f"session:{session_id}:conference:specialist:*"
                            )
                            for _g5_sk in _g5_spec_keys:
                                _g5_sr = await redis_client.get(_g5_sk)
                                if _g5_sr:
                                    _g5_so = json.loads(
                                        _g5_sr if isinstance(_g5_sr, str)
                                        else _g5_sr.decode()
                                    )
                                    if _g5_so.get("instance_id") == instance_id:
                                        _g5_agent_type = _g5_so.get("agent_type_id", "")
                                        _g5_sk_str = (
                                            _g5_sk if isinstance(_g5_sk, str)
                                            else _g5_sk.decode()
                                        )
                                        _g5_pool = _g5_sk_str.split(":")[-1]
                                        break
                        except Exception:
                            pass
                        try:
                            _g5_meta_raw = await redis_client.get(
                                f"session:{session_id}:meta"
                            )
                            if _g5_meta_raw:
                                _g5_meta = json.loads(_g5_meta_raw)
                                _g5_tenant = (
                                    _g5_meta.get("tenant_id", "")
                                    or _g5_meta.get("tenant", "")
                                )
                        except Exception:
                            pass
                        asyncio.create_task(_publish_participant_event(
                            session_id=session_id,
                            tenant_id=_g5_tenant,
                            participant_id=instance_id,
                            pool_id=_g5_pool,
                            agent_type_id=_g5_agent_type,
                            event_type="participant_left",
                            agent_type="native",
                            role="specialist",
                            segment_id=_g5_seg_id,
                            joined_at=_g5_joined_iso,
                            duration_ms=_g5_dur,
                            outcome=outcome,
                        ))
                        logger.info(
                            "G5: participant_left emitted for external conference specialist: "
                            "session=%s instance=%s pool=%s",
                            session_id, instance_id, _g5_pool,
                        )
                except Exception as exc:
                    logger.warning(
                        "G5: could not emit participant_left for specialist: session=%s — %s",
                        session_id, exc,
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

            # ── G2 fix: SREM from active_ai_specialists; dispatch deferred hooks ─
            # If agent_closed deferred on_human_end because this specialist was still
            # active, and this was the last one, fire the hooks now.
            if instance_id:
                try:
                    await redis_client.srem(
                        f"session:{session_id}:active_ai_specialists", instance_id,
                    )
                    _rem_specs = await redis_client.scard(
                        f"session:{session_id}:active_ai_specialists"
                    )
                    if _rem_specs == 0:
                        _pend_raw = await redis_client.getdel(
                            f"session:{session_id}:pending_on_human_end"
                        )
                        if _pend_raw:
                            _pd = json.loads(
                                _pend_raw if isinstance(_pend_raw, str)
                                else _pend_raw.decode()
                            )
                            _pd_pool     = _pd.get("pool_id", "")
                            _pd_tenant   = _pd.get("tenant_id", "")
                            _pd_customer = _pd.get("customer_id", session_id)
                            _pd_h_inst   = _pd.get("human_instance_id")
                            _pd_cust_pid = _pd.get("customer_participant_id")
                            logger.info(
                                "All specialists done — dispatching deferred on_human_end: "
                                "session=%s pool=%s", session_id, _pd_pool,
                            )
                            if http and _pd_pool and _pd_tenant:
                                _pd_pool_cfg = await get_pool_config(
                                    http, _pd_tenant, _pd_pool
                                )
                                _pd_hooks_cfg = (_pd_pool_cfg or {}).get("hooks") or {}
                                _pd_hooks      = _pd_hooks_cfg.get("on_human_end", [])
                                # G7 Fase 3b: NPS migrou para on_contact_end.
                                _pd_contact    = _pd_hooks_cfg.get("on_contact_end", [])
                                if _pd_hooks or _pd_contact:
                                    await _write_pre_hook_context(
                                        redis_client, _pd_tenant, session_id,
                                        close_origin="agent_closed",
                                        human_instance_id=_pd_h_inst,
                                        customer_participant_id=_pd_cust_pid,
                                    )
                                    if _pd_hooks:
                                        asyncio.create_task(fire_pool_hooks(
                                            http=http, redis_client=redis_client,
                                            session_id=session_id,
                                            pool_id=_pd_pool,
                                            tenant_id=_pd_tenant,
                                            customer_id=_pd_customer,
                                            hook_type="on_human_end",
                                            human_instance_id=_pd_h_inst or "",
                                        ))
                                        asyncio.create_task(_hook_timeout_guard(
                                            redis_client, session_id, "on_human_end",
                                        ))
                                    if _pd_contact:
                                        asyncio.create_task(fire_pool_hooks(
                                            http=http, redis_client=redis_client,
                                            session_id=session_id,
                                            pool_id=_pd_pool,
                                            tenant_id=_pd_tenant,
                                            customer_id=_pd_customer,
                                            hook_type="on_contact_end",
                                            human_instance_id=_pd_h_inst or "",
                                        ))
                                        asyncio.create_task(_hook_timeout_guard(
                                            redis_client, session_id, "on_contact_end",
                                        ))
                                else:
                                    asyncio.create_task(
                                        _trigger_contact_close(redis_client, session_id)
                                    )
                            else:
                                asyncio.create_task(
                                    _trigger_contact_close(redis_client, session_id)
                                )
                except Exception as exc:
                    logger.warning(
                        "G2: could not process deferred on_human_end: session=%s — %s",
                        session_id, exc,
                    )
        return

    # ── Agent message_sent → route to receive:waiting instances ──────────────
    # Published by message_send tool (human agent) and by notification_send
    # (AI agent, visibility=all only).  Enables the receive step to see
    # messages from role=primary and role=specialist in addition to the
    # customer inbound path already handled in process_inbound.
    #
    # Echo suppression: pass author_id as instance_id_of_author so an AI agent
    # does not receive its own notification_send messages in its receive queue.
    # For human agents author_id is participant_id (not an instance_id), so the
    # suppression check in _route_to_receive_waiting will never match — correct.
    if event_type == "message_sent":
        _ms_session_id   = msg.get("session_id", "")
        _ms_author_id    = msg.get("author_id", "")
        _ms_author_role  = msg.get("author_role", "primary")
        _ms_visibility   = msg.get("visibility", "all")
        _ms_content      = msg.get("content", "")
        # Customer messages are already routed to receive:waiting by process_inbound
        # (lines ~3731-3740). Routing them again here would cause double BLPOP wakes,
        # producing a duplicate "aguardando" re-arm bubble for every customer message.
        if _ms_author_role == "customer":
            return
        if _ms_session_id:
            try:
                _n = await _route_to_receive_waiting(
                    redis_client          = redis_client,
                    session_id            = _ms_session_id,
                    event_type            = "message_sent",
                    author_id             = _ms_author_id,
                    author_role           = _ms_author_role,
                    visibility            = _ms_visibility,
                    content               = _ms_content,
                    instance_id_of_author = _ms_author_id,
                )
                if _n:
                    logger.debug(
                        "receive:waiting routed agent message: session=%s author=%s role=%s notified=%d",
                        _ms_session_id, _ms_author_id, _ms_author_role, _n,
                    )
            except Exception as _exc:
                logger.warning(
                    "receive:waiting routing failed for agent message: session=%s — %s",
                    _ms_session_id, _exc,
                )
        return

    if event_type != "contact_closed":
        return

    session_id = msg.get("session_id")
    if not session_id:
        return

    reason      = msg.get("reason", "client_disconnect")
    instance_id = msg.get("instance_id", "")

    # ── Fase A (queue-attended-model): record last primary outcome (human) ────
    # The Console sends outcome via /api/agent_done → mcp-server now includes it
    # in this event. Stored so _close_contact_layer() can derive the session-level
    # outcome from the last primary segment (single source of truth = segment).
    _done_outcome = msg.get("outcome", "") or ""
    if _done_outcome:
        try:
            await redis_client.setex(
                f"session:{session_id}:last_outcome",
                604800,
                json.dumps({"outcome": _done_outcome, "agent_kind": "human"}),
            )
        except Exception:
            pass

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
        # O Routing Engine lê este marcador em _drain_queue_for_agent / is_closing
        # para descartar sessões que ainda estão na fila mas já foram encerradas,
        # evitando o "ghost contact" no Agent Assist ao reconectar. TTL 7 dias.
        #
        # G7 Fase 3a — marcador condicionado à NÃO-continuação:
        #   • customer_side (cliente saiu/timeout/agent_done) → sempre fim-de-contato:
        #     escreve aqui.
        #   • agent_closed → ADIADO para o path remaining<=0/no_continuation (abaixo),
        #     após conhecer `remaining`. Antes era escrito incondicionalmente (exceto
        #     agent_transfer), o que VAZAVA o marcador em other_human_active (multi-humano)
        #     fazendo o Routing Engine descartar re-rotas legítimas. transfer continua sem
        #     marcador (re-rota em voo). Ver docs/arcos/g7-segment-contact-decoupling.md §10.
        if customer_side:
            try:
                await redis_client.setex(f"session:{session_id}:closed", 604800, reason)
            except Exception as exc:
                logger.warning("Could not set session:closed marker: session=%s — %s", session_id, exc)

        if customer_side:

            # ── Record true contact end time (G1 fix) ─────────────────────────
            # Freeze AHT timestamp the moment the customer leaves, before any
            # hook agents (NPS, wrap-up) are dispatched.  SET NX ensures that
            # the first call wins — re-entries from hook completion are no-ops.
            await _mark_contact_ended(redis_client, session_id)

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
                # Quando múltiplos agentes estão bloqueados em menu steps simultâneos,
                # cada BLPOP consome UMA entrada do list.
                # IMPORTANTE: só enviar session:closed para agentes *customer-facing*
                # (visibility == "all").  Agentes de hook como wrapup e NPS usam
                # visibility de participante específico (["human_pid"] / ["cust_pid"]) —
                # se receberem o sinal, sairão via on_disconnect e pularão os steps de
                # texto antes de o agente humano ter chance de responder.
                n_waiting = 0
                _no_menu_entries = True
                try:
                    _wh = await redis_client.hgetall(f"menu:waiting:{session_id}")
                    if _wh:
                        _no_menu_entries = False
                        # Read tenant_id for activity-key checks.
                        # menu.ts sets {tenant}:session:{sid}:active_instance:{instanceId}
                        # while the BLPOP is running and deletes it in the finally block
                        # (on timeout, response, or disconnect).  If the key is absent the
                        # agent's BLPOP already exited — a session:closed push would be
                        # consumed by a hook agent (wrapup/NPS) instead, causing it to
                        # exit prematurely via on_disconnect before collecting human input.
                        _fix_a_tenant: str | None = None
                        try:
                            _fa_meta_raw = await redis_client.get(
                                f"session:{session_id}:meta"
                            )
                            if _fa_meta_raw:
                                _fa_meta_s = (
                                    _fa_meta_raw if isinstance(_fa_meta_raw, str)
                                    else _fa_meta_raw.decode()
                                )
                                _fix_a_tenant = json.loads(_fa_meta_s).get("tenant_id")
                        except Exception:
                            pass
                        for _fa_field, _meta_json in _wh.items():
                            try:
                                _fa_field_s = (
                                    _fa_field if isinstance(_fa_field, str)
                                    else _fa_field.decode()
                                )
                                _fa_meta_s2 = (
                                    _meta_json if isinstance(_meta_json, str)
                                    else _meta_json.decode()
                                )
                                _meta_vis = json.loads(_fa_meta_s2).get("visibility")
                                # Somente agentes com visibility "all" (ou null/legacy)
                                # esperam input do cliente — esses precisam do sinal.
                                if _meta_vis == "all" or _meta_vis is None:
                                    if _fix_a_tenant:
                                        # Check activity key: only count agents whose
                                        # BLPOP is still active (key set by menu.ts).
                                        _akey = (
                                            f"{_fix_a_tenant}:session:{session_id}"
                                            f":active_instance:{_fa_field_s}"
                                        )
                                        if await redis_client.exists(_akey):
                                            n_waiting += 1
                                        # else: agent already exited BLPOP — skip
                                    else:
                                        # tenant_id unavailable — fall back to old
                                        # behaviour (count all customer-facing entries)
                                        n_waiting += 1
                            except Exception:
                                # Malformed entry — include for safety
                                n_waiting += 1
                except Exception:
                    pass
                # Note: if menu:waiting has entries but ALL customer-facing agents
                # already exited their BLPOPs, n_waiting stays 0.  Do NOT push —
                # any push would be consumed by a hook agent starting later.
                #
                # Fase C (queue-attended-model): the queue agent runs with
                # instance_id="" → menu.ts never sets its activity key, so the
                # counting above always skips it and its BLPOP would hang forever
                # on customer disconnect (queue segment never closed). If the
                # queue-agent marker is present, add one push for it.
                try:
                    if await redis_client.exists(f"queue:agent_active:{session_id}"):
                        n_waiting += 1
                        logger.info(
                            "Queue agent abort signal queued on disconnect: session=%s",
                            session_id,
                        )
                except Exception:
                    pass
                if n_waiting > 0:
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

            # Arc 14 Fase E guard: if _close_contact_layer() already fired (e.g.
            # the server closed the customer WS after NPS completed), the channel-
            # gateway sends a secondary contact_closed(reason="agent_done") that
            # reaches this handler.  Treating it as a real customer close would:
            #   1. Broadcast session.closed (no recipients) → Console tears down wrap-up
            #   2. Re-dispatch on_human_end hooks → posatt:active inflated
            # When contact_close_fired is set the hook path is already managing
            # the close lifecycle; skip both actions.
            _ccf_already = False
            try:
                _ccf_raw = await redis_client.get(
                    f"session:{session_id}:contact_close_fired"
                )
                _ccf_already = bool(_ccf_raw)
            except Exception:
                pass
            if _ccf_already:
                # Prevent the session-key cleanup block below (if not _hooks_pending)
                # from deleting keys that posatt hooks still need.
                _hooks_pending = True
                logger.debug(
                    "customer_side close: contact_close_fired set — skipping agent "
                    "broadcast and hook re-dispatch (hook path owns this close): "
                    "session=%s reason=%s", session_id, reason,
                )

            is_human = await redis_client.get(f"session:{session_id}:human_agent")
            if is_human and not _ccf_already:
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
                logger.info(
                    "customer_side close: session=%s human_members=%s",
                    session_id,
                    [m.decode() if isinstance(m, bytes) else m for m in (_human_members or [])],
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
                    # G7 Slice 1b: pool/agent_type/tenant por-instance (participant_meta),
                    # não do meta de SESSÃO (last-writer) — cada humano da conferência
                    # sai com o SEU pool. Fallback no meta p/ compat. Ver g7 §11.
                    _hm_pool = _hm_at = _hm_ten = ""
                    try:
                        _hm_pm_raw = await redis_client.get(
                            f"session:{session_id}:participant_meta:{_hm_inst_str}"
                        )
                        if _hm_pm_raw:
                            _hm_pm = json.loads(_hm_pm_raw)
                            _hm_pool = _hm_pm.get("pool_id", "") or ""
                            _hm_at   = _hm_pm.get("agent_type_id", "") or ""
                            _hm_ten  = _hm_pm.get("tenant_id", "") or ""
                    except Exception:
                        pass
                    _hm_login = ""
                    if not _hm_pool or not _hm_ten:
                        try:
                            _hm_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                            if _hm_raw_meta:
                                _hm_m = json.loads(_hm_raw_meta)
                                _hm_pool = _hm_pool or _hm_m.get("pool_id", "")
                                _hm_at   = _hm_at   or _hm_m.get("agent_type_id", "")
                                _hm_ten  = _hm_ten  or (_hm_m.get("tenant_id", "") or _hm_m.get("tenant", ""))
                        except Exception:
                            pass
                    # ── G7 Fatia 3 — human_seg por humano TAMBÉM no customer_side ──
                    # O branch agent_closed escreve human_seg no agent_done; o
                    # customer-disconnect usa ESTE loop, que não escrevia → o fan-out
                    # (fire_pool_hooks) não achava human_seg:{inst} e _fixed_pid caía no
                    # global (last-writer) → TODOS os wrap-ups iam pro mesmo humano
                    # (operator recebia 2, admin 0). Escrevemos aqui (dual-write) para
                    # cada humano antes do fan-out rodar. Ver g7 §11 / Mudança 17.
                    if _hm_seg_id and _hm_pool:
                        _hm_hs_record = {
                            "segment_id":     _hm_seg_id,
                            "instance_id":    _hm_inst_str,
                            "pool_id":        _hm_pool,
                            "agent_type_id":  _hm_at,
                            "user_login":     _hm_login,
                            "joined_at":      _hm_joined_iso,
                            "duration_ms":    _hm_dur,
                            "sequence_index": 0,
                            "tenant_id":      _hm_ten,
                        }
                        try:
                            await redis_client.setex(
                                f"session:{session_id}:human_seg:{_hm_inst_str}",
                                604800, json.dumps(_hm_hs_record),
                            )
                            await redis_client.setex(
                                f"session:{session_id}:human_seg:{_hm_pool}",
                                604800, json.dumps(_hm_hs_record),
                            )
                            logger.debug(
                                "G7 Item1 human_seg WRITE (customer_side): session=%s "
                                "instance=%s pool=%s seg=%s",
                                session_id, _hm_inst_str, _hm_pool, _hm_seg_id,
                            )
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
                        # Fase A: customer left while human agent active → abandoned
                        outcome=(
                            "abandoned"
                            if reason in ("client_disconnect", "timeout", "session_timeout")
                            else None
                        ),
                    ))

                    # Fase A: keep the last_outcome marker consistent with the
                    # segment ledger — the human segment is the LAST primary one,
                    # so an earlier AI outcome (e.g. escalated_human) must not
                    # leak into the session-level outcome derived at close.
                    if reason in ("client_disconnect", "timeout", "session_timeout"):
                        try:
                            await redis_client.setex(
                                f"session:{session_id}:last_outcome",
                                604800,
                                json.dumps(
                                    {"outcome": "abandoned", "agent_kind": "human"}
                                ),
                            )
                        except Exception:
                            pass

                    # ── Decrement pool active_count via routing engine ─────────
                    # The customer_side path calls _restore_all_instances() to
                    # reset agent state in Redis, but does NOT publish agent_done
                    # to agent.lifecycle — so the routing engine's
                    # remove_conversation() never fires and pool:active_count
                    # stays incremented indefinitely (phantom "Ocupados").
                    #
                    # Publishing agent_done here triggers remove_conversation()
                    # in the routing engine's LifecycleEventHandler, which DECRs
                    # pool:active_count and patches the pool snapshot in-place.
                    # Same pattern as the agent_closed path at line ~3265.
                    logger.info(
                        "customer_side agent_done check: session=%s instance=%s "
                        "pool=%s tenant=%s has_producer=%s",
                        session_id, _hm_inst_str, _hm_pool, _hm_ten,
                        _kafka_producer is not None,
                    )
                    if _kafka_producer and _hm_ten:
                        asyncio.create_task(_kafka_producer.send(
                            TOPIC_LIFECYCLE,
                            json.dumps({
                                "event":           "agent_done",
                                "tenant_id":       _hm_ten,
                                "instance_id":     _hm_inst_str,
                                "agent_type_id":   _hm_at,
                                "pools":           [_hm_pool] if _hm_pool else [],
                                "conversation_id": session_id,
                                "timestamp":       datetime.now(timezone.utc).isoformat(),
                            }).encode("utf-8"),
                        ))
                        logger.info(
                            "agent_done published to lifecycle: "
                            "session=%s instance=%s pool=%s (customer_side)",
                            session_id, _hm_inst_str, _hm_pool,
                        )

                # ── Check for on_human_end hooks (wrap-up agent) ─────────────
                # Even when the *client* disconnected, we still fire on_human_end
                # hooks so that wrap-up agents (NPS, encerramento) can execute.
                # The customer WS is already closed, so the wrap-up agent operates
                # in "post-session" mode — its messages go to the stream but the
                # client won't see them.  The hooks guarantee that the session is
                # properly closed on the platform side.
                #
                # IMPORTANT: human_agent / human_agents tracking keys are deleted
                # AFTER this hook check, not before.  The delivery path for hook
                # agent messages (notify_send, menu steps) checks
                # session:{id}:human_agent to know whether to publish to
                # agent:events:{session_id}.  Deleting it before hooks fire causes
                # wrap-up messages to be silently dropped (is_human=None).
                # When hooks are dispatched → defer deletion to _trigger_contact_close().
                # When no hooks → delete immediately below.
                _cs_pool_id    = ""
                _cs_tenant_id  = ""
                _cs_customer_id = ""
                _cs_meta: dict = {}
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

                # G7 (hook-pool por segmento): a âncora (último a se desligar) dispara
                # on_human_end/on_contact_end com o SEU pool (participant_meta do
                # _last_human_instance_id), não o do session:meta (last-writer = último
                # humano ATIVADO). Alinha a âncora aos peers (que já resolvem por
                # participant_meta). Fallback ao meta preserva paridade se faltar.
                if _last_human_instance_id:
                    try:
                        _anchor_pm_raw = await redis_client.get(
                            f"session:{session_id}:participant_meta:{_last_human_instance_id}"
                        )
                        if _anchor_pm_raw:
                            _anchor_pool = json.loads(_anchor_pm_raw).get("pool_id", "") or ""
                            if _anchor_pool:
                                _cs_pool_id = _anchor_pool
                    except Exception:
                        pass

                _cs_hooks_fired = False
                if http and _cs_pool_id and _cs_tenant_id:
                    _cs_pool_cfg = await get_pool_config(
                        http, _cs_tenant_id, _cs_pool_id
                    )
                    _cs_hooks_cfg      = (_cs_pool_cfg or {}).get("hooks") or {}
                    _cs_on_human_end   = _cs_hooks_cfg.get("on_human_end", [])
                    # G7 Fase 3b: NPS migrou para on_contact_end (fim-de-CONTATO).
                    _cs_on_contact_end = _cs_hooks_cfg.get("on_contact_end", [])
                    # G7 Fatia 2b/3 — peers (humanos ≠ âncora) que também precisam de
                    # wrap-up no customer-disconnect. A âncora (_last_human_instance_id)
                    # segue o caminho atual (on_human_end arma posatt; on_contact_end NPS);
                    # cada peer dispara segment_wrapup do SEU pool (arm_contact_close).
                    # Single-humano → _cs_peers vazio → byte-parity (nenhum fan-out).
                    _cs_peers = [
                        (m.decode() if isinstance(m, bytes) else m)
                        for m in (_human_members or [])
                        if (m.decode() if isinstance(m, bytes) else m) != _last_human_instance_id
                    ]
                    if _cs_on_human_end or _cs_on_contact_end or _cs_peers:
                        _cs_hooks_fired = True
                        _hooks_pending = True
                        # Escreve close_origin + customer/human participant_id no
                        # ContextStore ANTES de disparar os hooks.
                        await _write_pre_hook_context(
                            redis_client, _cs_tenant_id, session_id,
                            close_origin="customer_disconnect",
                            human_instance_id=_last_human_instance_id,
                            customer_participant_id=_cs_meta.get("customer_participant_id") if _cs_meta else None,
                        )
                        # ── Fechamento determinístico da camada de contato ───────
                        # Decisão de TRANSPORTE (não de negócio): nesta queda do
                        # cliente, algum hook de cliente vai REALMENTE rodar? Entries
                        # side=customer com nps_on_disconnect=skip são puladas — se
                        # NENHUM hook de cliente roda, ninguém dispara
                        # _close_contact_layer() (posatt:customer_active nunca zera) e
                        # a sessão fica presa em `active` até o safety net de 180s.
                        # Espelha o guard do caminho agent_done: fecha a camada de
                        # contato já. _close_contact_layer é idempotente (NX).
                        _cs_customer_will_run = any(
                            isinstance(e, dict)
                            and (e.get("side", "agent") or "agent") == "customer"
                            and (e.get("nps_on_disconnect", "timeout") or "timeout") != "skip"
                            for e in (list(_cs_on_contact_end) + list(_cs_on_human_end))
                        )
                        if not _cs_customer_will_run:
                            # Se algum segmento agent-side ainda vai rodar (wrap-up
                            # on_human_end ou peers segment_wrapup), fecha SÓ a camada
                            # de contato agora; o _destroy_conference vem quando o
                            # último posatt/peer conclui (paridade com agent_done).
                            # Se NENHUM segmento agent-side roda, ninguém destruiria a
                            # conferência → usa o teardown completo (close + destroy).
                            _cs_agent_side_will_run = bool(_cs_on_human_end) or bool(_cs_peers)
                            if _cs_agent_side_will_run:
                                asyncio.create_task(
                                    _close_contact_layer(redis_client, session_id)
                                )
                            else:
                                asyncio.create_task(
                                    _trigger_contact_close(redis_client, session_id)
                                )
                            logger.info(
                                "customer_disconnect: no customer-side hook will run "
                                "(all skipped/absent) — closing contact now "
                                "(agent_side_segments=%s): session=%s pool=%s",
                                _cs_agent_side_will_run, session_id, _cs_pool_id,
                            )
                        if _cs_on_human_end:
                            asyncio.create_task(fire_pool_hooks(
                                http=http, redis_client=redis_client,
                                session_id=session_id,
                                pool_id=_cs_pool_id,
                                tenant_id=_cs_tenant_id,
                                customer_id=_cs_customer_id,
                                hook_type="on_human_end",
                                human_instance_id=_last_human_instance_id or "",
                            ))
                            asyncio.create_task(_hook_timeout_guard(
                                redis_client, session_id, "on_human_end",
                            ))
                        # NPS de fim-de-contato. Só dispara se ao menos uma entrada
                        # de cliente for realmente rodar nesta queda (_cs_customer_
                        # will_run); quando todas são skip, não há o que disparar e o
                        # fechamento já foi feito acima — evita contador/guard órfãos.
                        if _cs_on_contact_end and _cs_customer_will_run:
                            asyncio.create_task(fire_pool_hooks(
                                http=http, redis_client=redis_client,
                                session_id=session_id,
                                pool_id=_cs_pool_id,
                                tenant_id=_cs_tenant_id,
                                customer_id=_cs_customer_id,
                                hook_type="on_contact_end",
                                human_instance_id=_last_human_instance_id or "",
                            ))
                            asyncio.create_task(_hook_timeout_guard(
                                redis_client, session_id, "on_contact_end",
                            ))
                        # ── G7 Fatia 2b/3 — fan-out dos peers ────────────────────
                        # Cada humano ≠ âncora recebe segment_wrapup do SEU pool
                        # (resolvido por participant_meta), armando contact_close_pending.
                        # O contato só fecha quando todos completarem (guard em
                        # _destroy_conference + teardown na conclusão em process_routed).
                        _cs_peers_fired = 0
                        for _peer_inst in _cs_peers:
                            _peer_pool = ""
                            try:
                                _peer_pm_raw = await redis_client.get(
                                    f"session:{session_id}:participant_meta:{_peer_inst}"
                                )
                                if _peer_pm_raw:
                                    _peer_pm = json.loads(_peer_pm_raw)
                                    _peer_pool = _peer_pm.get("pool_id", "") or ""
                            except Exception:
                                pass
                            if not _peer_pool:
                                logger.warning(
                                    "customer_disconnect fan-out: no pool for peer — "
                                    "skipping wrap-up: session=%s instance=%s",
                                    session_id, _peer_inst,
                                )
                                continue
                            asyncio.create_task(fire_pool_hooks(
                                http=http, redis_client=redis_client,
                                session_id=session_id,
                                pool_id=_peer_pool,
                                tenant_id=_cs_tenant_id,
                                customer_id=_cs_customer_id,
                                hook_type="segment_wrapup",
                                human_instance_id=_peer_inst,
                                arm_contact_close=True,
                            ))
                            _cs_peers_fired += 1
                            logger.info(
                                "Peer wrap-up (segment_wrapup, customer_disconnect fan-out) "
                                "dispatched: session=%s pool=%s instance=%s",
                                session_id, _peer_pool, _peer_inst,
                            )
                        if _cs_peers_fired:
                            # Timeout-guard do contador (segment_wrapup não usa
                            # hook_pending → o _hook_timeout_guard não o cobre).
                            asyncio.create_task(_contact_close_timeout_guard(
                                redis_client, session_id,
                            ))
                        logger.info(
                            "contact-end hooks dispatched (client disconnect): "
                            "session=%s pool=%s on_human_end=%d on_contact_end=%d peers=%d (timeout guard: %ds)",
                            session_id, _cs_pool_id, len(_cs_on_human_end),
                            len(_cs_on_contact_end), _cs_peers_fired, _HOOK_TIMEOUT_S,
                        )

                if not _cs_hooks_fired:
                    # No hooks or meta unavailable — clean up and close immediately.
                    # When hooks ARE fired, these keys must survive until all hook
                    # agents complete so that their messages are forwarded to the
                    # Console (is_human check in notify/menu delivery path).
                    # _trigger_contact_close() handles deletion in the hook case.
                    await redis_client.delete(f"session:{session_id}:human_agent")
                    await redis_client.delete(f"session:{session_id}:human_agents")
                    asyncio.create_task(
                        _trigger_contact_close(redis_client, session_id)
                    )
                    logger.info(
                        "No on_human_end hooks — closing contact immediately: session=%s",
                        session_id,
                    )

            elif not _ccf_already:
                # No human agent was active — close the contact immediately.
                # Arc 14 Fase E: when contact_close_fired is already set it means
                # _close_contact_layer() fired (hook path is managing the close).
                # In that case the else branch must be skipped — calling
                # _trigger_contact_close() here would fire _destroy_conference()
                # while posatt hooks (wrapup) are still running.
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
            # AI agents are tracked in session:{session_id}:ai_agents SET.
            # If an instance is still actively running a skill flow (ai_completing
            # key present), skip immediate restore — process_routed will restore it
            # naturally when activate_native_agent returns and publish agent_done.
            # Emergency restore (key absent) covers crash-recovery and instances
            # that completed before contact_closed was processed.
            ai_members = await redis_client.smembers(f"session:{session_id}:ai_agents")
            if ai_members:
                restored_count = 0
                skipped_count  = 0
                for ai_inst_id in ai_members:
                    inst_str = (
                        ai_inst_id if isinstance(ai_inst_id, str)
                        else ai_inst_id.decode()
                    )
                    completing = await redis_client.get(
                        f"session:{session_id}:ai_completing:{inst_str}"
                    )
                    if completing:
                        # Still running — natural path will restore + publish agent_done
                        skipped_count += 1
                    else:
                        # Not running (or crash recovery) — restore immediately.
                        # Read routing snapshot BEFORE _restore_instance() deletes it,
                        # then publish agent_done so the routing engine DECR's the counter.
                        # This handles the case where the bridge restarted AFTER the 4h
                        # ai_completing TTL expired but contact_closed was never re-processed.
                        _csnap_raw = await redis_client.get(
                            f"session:{session_id}:routing:{inst_str}"
                        )
                        await _restore_instance(redis_client, session_id, ai_inst_id)
                        restored_count += 1
                        if _csnap_raw and _kafka_producer:
                            try:
                                _csnap  = json.loads(_csnap_raw)
                                _c_ten  = _csnap.get("tenant_id", "")
                                _c_pool = _csnap.get("pool_id", "")
                                _c_inst = _csnap.get("instance_id", inst_str)
                                _c_at   = (_csnap.get("snapshot") or {}).get("agent_type_id", "")
                                if _c_ten and _c_inst:
                                    asyncio.create_task(_kafka_producer.send(
                                        TOPIC_LIFECYCLE,
                                        json.dumps({
                                            "event":           "agent_done",
                                            "tenant_id":       _c_ten,
                                            "instance_id":     _c_inst,
                                            "agent_type_id":   _c_at,
                                            "conversation_id": session_id,
                                            "pools":           [_c_pool] if _c_pool else [],
                                            "timestamp":       datetime.now(timezone.utc).isoformat(),
                                        }).encode("utf-8"),
                                    ))
                                    logger.info(
                                        "crash-recovery agent_done: session=%s inst=%s pool=%s",
                                        session_id, inst_str, _c_pool,
                                    )
                            except Exception as _cad_exc:
                                logger.warning(
                                    "crash-recovery agent_done: session=%s inst=%s — %s",
                                    session_id, inst_str, _cad_exc,
                                )
                await redis_client.delete(f"session:{session_id}:ai_agents")
                logger.info(
                    "AI instance(s) on contact_closed: session=%s restored=%d skipped_completing=%d",
                    session_id, restored_count, skipped_count,
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

            # ── Fatia 2a — gate de idempotência (double-processing guard) ─────
            # agent_done/contact_closed pode chegar 2× (double-submit do Console
            # ou redelivery do Kafka): o 2º passe recriava o segmento (joined_at já
            # getdel'd → duração 0) e re-disparava segment_wrapup (conferência
            # redundante). SREM é ATÔMICO e retorna quantos removeu: se 0, este
            # instance já saiu (duplicado, OU já encerrado por outro caminho —
            # heartbeat drop) → no-op. O duplicado do ÚLTIMO humano já era pego
            # pelo is_human (flag deletada no remaining<=0); este gate cobre o
            # NÃO-último (flag segue viva sob o outro humano). A remoção aqui
            # substitui o SREM redundante mais abaixo. Ver g7 §11 + Mudança 17.
            if instance_id:
                _close_removed = await redis_client.srem(
                    f"session:{session_id}:human_agents", instance_id
                )
                if _close_removed == 0:
                    logger.info(
                        "Duplicate/late agent close ignored (instance not in "
                        "human_agents): session=%s instance=%s reason=%s",
                        session_id, instance_id, reason,
                    )
                    return

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
                _ha_pool = _ha_agent_type_id = _ha_tenant = _ha_user_login = ""
                # G7 Slice 1b: pool/agent_type/tenant/login DESTE humano vêm do registro
                # por-instance (participant_meta:{instance_id}), não do meta de SESSÃO
                # (pool_id é last-writer-wins → em multi-humano o close do não-último
                # era atribuído ao pool do último). Fallback no meta p/ compat. Ver g7 §11.
                try:
                    _ha_pm_raw = await redis_client.get(
                        f"session:{session_id}:participant_meta:{instance_id}"
                    )
                    if _ha_pm_raw:
                        _ha_pm = json.loads(_ha_pm_raw)
                        _ha_pool          = _ha_pm.get("pool_id", "") or ""
                        _ha_agent_type_id = _ha_pm.get("agent_type_id", "") or ""
                        _ha_user_login    = _ha_pm.get("user_login", "") or ""
                        _ha_tenant        = _ha_pm.get("tenant_id", "") or ""
                except Exception:
                    pass
                if not _ha_pool or not _ha_tenant:
                    try:
                        _ha_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                        if _ha_raw_meta:
                            _ha_m = json.loads(_ha_raw_meta)
                            _ha_pool          = _ha_pool          or _ha_m.get("pool_id", "")
                            _ha_agent_type_id = _ha_agent_type_id or _ha_m.get("agent_type_id", "")
                            _ha_user_login    = _ha_user_login    or (_ha_m.get("user_login", "") or "")
                            _ha_tenant        = _ha_tenant        or (
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
                # ── F5 (grão segmento): registro do segmento humano keyed por POOL ──
                # O participant_left abaixo sai com outcome placeholder (a Console
                # hardcoda resolved/abandoned). fire_pool_hooks lê human_seg:{pool}
                # e carimba este segmento no hook_conf; na conclusão de cada hook
                # on_human_end (process_routed) a disposição/NPS são atribuídos a
                # ESTE segmento. Keyed por pool → suporta N humanos/pools por contato
                # (o "último primário único" da F1.4 era simplificação de demo).
                if _ha_seg_id and _ha_pool:
                    _hs_record = {
                        "segment_id":     _ha_seg_id,
                        "instance_id":    instance_id,
                        "pool_id":        _ha_pool,
                        "agent_type_id":  _ha_agent_type_id,
                        "user_login":     _ha_user_login,
                        "joined_at":      _ha_joined_iso,
                        "duration_ms":    _ha_duration_ms,
                        "sequence_index": _ha_seq_idx,
                        "tenant_id":      _ha_tenant,
                    }
                    try:
                        # G7 Item1 Fatia 1: chave canônica por-INSTÂNCIA (resolve a
                        # colisão de 2 humanos no mesmo pool). Espelho por-pool mantido
                        # como fallback de back-compat enquanto callers não migrados
                        # existirem (remover no cleanup da Fatia 4).
                        await redis_client.setex(
                            f"session:{session_id}:human_seg:{instance_id}",
                            604800, json.dumps(_hs_record),
                        )
                        await redis_client.setex(
                            f"session:{session_id}:human_seg:{_ha_pool}",
                            604800, json.dumps(_hs_record),
                        )
                        logger.debug(
                            "G7 Item1 human_seg WRITE: session=%s instance=%s pool=%s seg=%s "
                            "(dual-write: human_seg:{inst} + human_seg:{pool})",
                            session_id, instance_id, _ha_pool, _ha_seg_id,
                        )
                        # Semeia o acumulador com o registro + outcome PLACEHOLDER
                        # (_done_outcome): garante que um re-publish de NPS-só (pool
                        # sem wrap-up) não anule o outcome. O wrap-up sobrescreve
                        # com a disposição real na conclusão do hook.
                        await _seed_segment_signal(redis_client, session_id, _hs_record, None)
                        if _done_outcome:
                            await redis_client.hset(
                                _seg_signal_key(session_id, _ha_seg_id),
                                mapping={"outcome": _done_outcome},
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
                    user_login=_ha_user_login,
                    # Fase A: human outcome from /api/agent_done (Console)
                    outcome=_done_outcome or None,
                ))

                # ── Decrement human pool active_count via routing engine ──────
                # mcp-server's /api/agent_done publishes contact_closed to
                # conversations.events but never agent_done to agent.lifecycle —
                # so routing engine's remove_conversation() is never triggered
                # for human agents, causing active_count to accumulate
                # indefinitely.  We publish agent_done here (same pattern as the
                # AI-agent fix at the top of this file) so the routing engine
                # calls remove_conversation() and DECRs the counter.
                if _kafka_producer and _ha_tenant:
                    asyncio.create_task(_kafka_producer.send(
                        TOPIC_LIFECYCLE,
                        json.dumps({
                            "event":           "agent_done",
                            "tenant_id":       _ha_tenant,
                            "instance_id":     instance_id,
                            "agent_type_id":   _ha_agent_type_id,
                            "pools":           [_ha_pool] if _ha_pool else [],
                            "conversation_id": session_id,
                            "timestamp":       datetime.now(timezone.utc).isoformat(),
                        }).encode("utf-8"),
                    ))
                    logger.info(
                        "agent_done published to lifecycle (human agent): "
                        "session=%s instance=%s pool=%s tenant=%s",
                        session_id, instance_id, _ha_pool, _ha_tenant,
                    )
                else:
                    logger.warning(
                        "agent_done NOT published (human agent): session=%s "
                        "has_producer=%s tenant=%r pool=%r",
                        session_id, _kafka_producer is not None, _ha_tenant, _ha_pool,
                    )

                # Fatia 2a: SREM já feito atomicamente no gate de idempotência acima
                # (este SREM redundante foi removido). Apenas lê o remaining resultante.
                remaining = await redis_client.scard(f"session:{session_id}:human_agents")
                # ── G7 Fase 3a — classificador de continuação GOVERNA o close ──────────
                # _has_continuation decide se este fim de segmento humano é também fim de
                # contato. Fase 3a AGE sobre a decisão: o motivo `transfer` dispara
                # segment_wrapup sem fechar (re-rota); no_continuation — e o defer por
                # specialist ainda ativo — escreve o marcador session:closed e segue para
                # o close. Default por `reason` para preservar o transfer mesmo se o
                # classificador falhar. Ver docs/arcos/g7-segment-contact-decoupling.md §10.
                _g7_cont, _g7_motive = (reason == "agent_transfer"), (
                    "transfer" if reason == "agent_transfer" else "no_continuation"
                )
                try:
                    _g7_cont, _g7_motive = await _has_continuation(
                        redis_client, session_id, reason, remaining
                    )
                    logger.info(
                        "G7-decision: session=%s instance=%s reason=%s remaining=%d "
                        "→ continuation=%s (%s)",
                        session_id, instance_id, reason, remaining, _g7_cont, _g7_motive,
                    )
                except Exception as _g7_exc:
                    logger.debug(
                        "G7-decision: classifier failed session=%s — %s", session_id, _g7_exc
                    )
                if remaining <= 0:
                    # Last human agent dropped — clear the fast-lookup flag
                    await redis_client.delete(f"session:{session_id}:human_agent")
                    await redis_client.delete(f"session:{session_id}:human_agents")
                    logger.info("Last human agent dropped: session=%s", session_id)

                    # ── G7 — Transfer: origin segment ended, CONTACT continues ────────
                    # The cleanup above (restore, participant_left outcome=transferred,
                    # agent_done lifecycle DECR, SREM human_agents) already removed the
                    # origin so the in-flight re-route (conversations.inbound to the target
                    # pool) can activate the target agent (the human_active guard now
                    # passes). A transfer is a mid-contact handoff: do NOT freeze AHT and
                    # do NOT close the contact.
                    # G7 Slice B: fire a SEGMENT wrap-up (side=agent only) for the origin
                    # segment — fim-de-segmento, sem armar contadores de close, sem NPS.
                    # A disposição (motivo da escalação/transfer) é coletada do humano que
                    # transferiu e atribuída ao seu segmento (seg_signal → re-publish).
                    # O contato segue pela re-rota; o wrap-up roda em paralelo, isolado
                    # pela identidade por-segmento da Slice A.
                    if _g7_cont and _g7_motive == "transfer":
                        logger.info(
                            "Transfer: origin segment ended, contact continues "
                            "(re-routed) — session=%s instance=%s",
                            session_id, instance_id,
                        )
                        if http and _ha_pool and _ha_tenant:
                            _tr_customer = session_id
                            try:
                                _tr_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                                if _tr_raw_meta:
                                    _tr_meta = json.loads(_tr_raw_meta)
                                    _tr_customer = (
                                        _tr_meta.get("customer_id")
                                        or _tr_meta.get("contact_id")
                                        or session_id
                                    )
                            except Exception:
                                pass
                            # Sem _hook_timeout_guard: segment_wrapup não segura o
                            # contato (não há hook_pending/posatt). wrap_up_pending tem
                            # TTL próprio; hook_conf/participants expiram sozinhos.
                            asyncio.create_task(fire_pool_hooks(
                                http=http,
                                redis_client=redis_client,
                                session_id=session_id,
                                pool_id=_ha_pool,
                                tenant_id=_ha_tenant,
                                customer_id=_tr_customer,
                                hook_type="segment_wrapup",
                                human_instance_id=instance_id,
                            ))
                            logger.info(
                                "Transfer wrap-up (segment_wrapup) dispatched: "
                                "session=%s origin_pool=%s", session_id, _ha_pool,
                            )
                        return

                    # ── G7 heartbeat Slice 1 — agent_disconnect do ÚLTIMO humano ──────
                    # O humano caiu (WS drop, não agent_done) e não há outro agente
                    # customer-facing (remaining<=0). Se o cliente tivesse saído, viria por
                    # customer_side — estar aqui implica cliente presente. Re-rota ao pool
                    # do dono (re-estabelece a posse por ALOCAÇÃO, não promoção) em vez de
                    # fechar → mantém o cliente atendido. Sem wrap-up/NPS (o humano sumiu).
                    # Não escreve session:closed (contato continua) — o mcp-server tb não
                    # setou (só /api/agent_done seta). Ver g7 §11 / heartbeat.
                    if reason == "agent_disconnect":
                        _rr_customer = session_id
                        _rr_channel  = "webchat"
                        try:
                            _rr_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                            if _rr_raw_meta:
                                _rr_meta = json.loads(_rr_raw_meta)
                                _rr_customer = (
                                    _rr_meta.get("customer_id")
                                    or _rr_meta.get("contact_id")
                                    or session_id
                                )
                                _rr_ch = _rr_meta.get("channel", "webchat") or "webchat"
                                _rr_channel = "webchat" if _rr_ch == "chat" else _rr_ch
                        except Exception:
                            pass
                        if _kafka_producer and _ha_pool and _ha_tenant:
                            try:
                                await _kafka_producer.send_and_wait(
                                    TOPIC_INBOUND,
                                    json.dumps({
                                        "session_id":  session_id,
                                        "tenant_id":   _ha_tenant,
                                        "customer_id": _rr_customer,
                                        "channel":     _rr_channel,
                                        "pool_id":     _ha_pool,
                                        "started_at":  datetime.now(timezone.utc).isoformat(),
                                    }).encode("utf-8"),
                                )
                                logger.info(
                                    "agent_disconnect: last human dropped — re-routing to "
                                    "pool=%s (contact kept alive): session=%s instance=%s",
                                    _ha_pool, session_id, instance_id,
                                )
                            except Exception as _rr_exc:
                                logger.error(
                                    "agent_disconnect re-route failed — closing: session=%s — %s",
                                    session_id, _rr_exc,
                                )
                                asyncio.create_task(
                                    _trigger_contact_close(redis_client, session_id)
                                )
                        else:
                            logger.warning(
                                "agent_disconnect: cannot re-route (producer/pool/tenant "
                                "missing) — closing: session=%s", session_id,
                            )
                            asyncio.create_task(
                                _trigger_contact_close(redis_client, session_id)
                            )
                        return

                    # ── G7 Fase 3a — marcador session:closed (no_continuation) ────
                    # Chegou aqui ⇒ fim-de-contato (no_continuation) OU defer por
                    # specialist ainda ativo: este fim-de-segmento fecha (ou vai
                    # fechar) o contato. O transfer já retornou acima; o branch
                    # remaining>0 (other_human_active) NÃO escreve (contato continua).
                    # Antes este marcador era escrito incondicionalmente no topo do
                    # handler — agora é condicional. Ver g7 §10.
                    try:
                        await redis_client.setex(
                            f"session:{session_id}:closed", 604800, reason
                        )
                    except Exception as _mk_exc:
                        logger.warning(
                            "Could not set session:closed marker (no_continuation): "
                            "session=%s — %s", session_id, _mk_exc,
                        )

                    # ── Record true contact end time (G1 fix) ─────────────────
                    # The human agent is the last active participant — freeze AHT
                    # now, before on_human_end hooks (NPS, wrap-up) run.
                    await _mark_contact_ended(redis_client, session_id)

                    # ── Fase B: fire on_human_end hooks or trigger contact close ─
                    # Read pool_id, tenant_id, customer_id from session meta.
                    # If the pool declares on_human_end hooks, dispatch them now and
                    # let hook completion tracking call _trigger_contact_close.
                    # If not (or if meta is missing), close the contact immediately.
                    _pool_id_hooks    = ""
                    _tenant_id_hooks  = ""
                    _customer_id_hooks = ""
                    _meta_hooks: dict = {}
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

                    # G7 (hook-pool por segmento): os hooks de fim-de-segmento/contato
                    # usam o pool do segmento que FECHA (participant_meta:{instance_id}),
                    # não o do session:meta (last-writer = último humano ATIVADO). Também
                    # corrige o disparo deferred (o stash pending_on_human_end copia
                    # _pool_id_hooks). Fallback ao meta preserva paridade se faltar.
                    try:
                        _pm_raw_hooks = await redis_client.get(
                            f"session:{session_id}:participant_meta:{instance_id}"
                        )
                        if _pm_raw_hooks:
                            _pm_pool_hooks = json.loads(_pm_raw_hooks).get("pool_id", "") or ""
                            if _pm_pool_hooks:
                                _pool_id_hooks = _pm_pool_hooks
                    except Exception:
                        pass

                    # ── G2 fix: defer on_human_end if AI specialists still active ─
                    # A task-step specialist (e.g. assist mode) may still be replying
                    # to the customer's last message when the human decides to end.
                    # Dispatching NPS/wrap-up hooks now would interleave their messages.
                    # We defer by storing the hook config in pending_on_human_end; the
                    # conference_agent_completed handler picks it up when the last
                    # specialist finishes.
                    _active_spec_count = 0
                    try:
                        _active_spec_count = await redis_client.scard(
                            f"session:{session_id}:active_ai_specialists"
                        )
                    except Exception:
                        pass

                    if _active_spec_count > 0:
                        # Defer hooks — specialists still active.
                        # Store the hook config so conference_agent_completed can pick
                        # it up when the last specialist finishes.
                        try:
                            await redis_client.setex(
                                f"session:{session_id}:pending_on_human_end",
                                300,   # 5 min guard — TTL prevents permanent hang
                                json.dumps({
                                    "pool_id":               _pool_id_hooks,
                                    "tenant_id":             _tenant_id_hooks,
                                    "customer_id":           _customer_id_hooks,
                                    "human_instance_id":     instance_id,
                                    "customer_participant_id": (
                                        _meta_hooks.get("customer_participant_id")
                                        if _meta_hooks else None
                                    ),
                                }),
                            )
                            logger.info(
                                "on_human_end deferred — AI specialist(s) still active: "
                                "session=%s active_specialists=%d",
                                session_id, _active_spec_count,
                            )
                        except Exception as _exc:
                            logger.warning(
                                "Could not store pending_on_human_end, falling back to immediate: "
                                "session=%s — %s", session_id, _exc,
                            )
                            # Fallthrough to immediate dispatch if we can't defer safely
                            _active_spec_count = 0

                        # Signal all waiting specialists to abort immediately.
                        # Specialists in a `menu` or `receive` step are blocked on
                        # BLPOP(menu:result:{sid}, session:closed:{sid}).  Pushing to
                        # session:closed:{sid} once per active specialist unblocks each
                        # BLPOP right away — they exit cleanly via on_disconnect, which
                        # triggers conference_agent_completed and fires the deferred hooks
                        # without waiting for the full form-collection timeout.
                        if _active_spec_count > 0:
                            try:
                                closed_key = f"session:closed:{session_id}"
                                # One push per specialist — each BLPOP consumes one item.
                                for _ in range(_active_spec_count):
                                    await redis_client.lpush(closed_key, "agent_hangup")
                                # Short TTL: the values are consumed by BLPOP; the key
                                # disappears naturally, but 60 s guards against leaks.
                                await redis_client.expire(closed_key, 60)
                                logger.info(
                                    "Sent abort signal to %d specialist(s): session=%s",
                                    _active_spec_count, session_id,
                                )
                            except Exception as _exc:
                                logger.warning(
                                    "Could not send specialist abort signal: session=%s — %s",
                                    session_id, _exc,
                                )

                    if _active_spec_count == 0:
                        if http and _pool_id_hooks and _tenant_id_hooks:
                            _pool_cfg_hooks = await get_pool_config(
                                http, _tenant_id_hooks, _pool_id_hooks
                            )
                            _hooks_cfg = (_pool_cfg_hooks or {}).get("hooks") or {}
                            _on_human_end   = _hooks_cfg.get("on_human_end", [])
                            # G7 Fase 3b: NPS migrou para on_contact_end (fim-de-CONTATO).
                            _on_contact_end = _hooks_cfg.get("on_contact_end", [])
                            if _on_human_end or _on_contact_end:
                                # Escreve close_origin + customer/human participant_id no
                                # ContextStore ANTES de disparar os hooks.
                                await _write_pre_hook_context(
                                    redis_client, _tenant_id_hooks, session_id,
                                    close_origin="agent_closed",
                                    human_instance_id=instance_id,
                                    customer_participant_id=_meta_hooks.get("customer_participant_id") if _meta_hooks else None,
                                )

                                # Arc 14 Fase E / G7 Fase 3b: só fecha o WS do cliente já
                                # se NÃO houver pesquisa ao cliente. NPS vive em
                                # on_contact_end; a checagem de entries side=customer em
                                # on_human_end fica como defesa para pools ainda não
                                # migrados ao cutover. _close_contact_layer() dispara de
                                # process_routed quando posatt:customer_active chega a 0.
                                _has_customer_hooks = bool(_on_contact_end) or any(
                                    (
                                        e.get("side", "agent")
                                        if isinstance(e, dict) else "agent"
                                    ) == "customer"
                                    for e in _on_human_end
                                )
                                if not _has_customer_hooks:
                                    asyncio.create_task(
                                        _close_contact_layer(redis_client, session_id)
                                    )

                                # Wrap-up de fim-de-segmento (side=agent).
                                if _on_human_end:
                                    asyncio.create_task(fire_pool_hooks(
                                        http=http, redis_client=redis_client,
                                        session_id=session_id,
                                        pool_id=_pool_id_hooks,
                                        tenant_id=_tenant_id_hooks,
                                        customer_id=_customer_id_hooks,
                                        hook_type="on_human_end",
                                        human_instance_id=instance_id,
                                    ))
                                    asyncio.create_task(_hook_timeout_guard(
                                        redis_client, session_id, "on_human_end",
                                    ))
                                # NPS de fim-de-contato (side=customer, 1ª classe).
                                if _on_contact_end:
                                    asyncio.create_task(fire_pool_hooks(
                                        http=http, redis_client=redis_client,
                                        session_id=session_id,
                                        pool_id=_pool_id_hooks,
                                        tenant_id=_tenant_id_hooks,
                                        customer_id=_customer_id_hooks,
                                        hook_type="on_contact_end",
                                        human_instance_id=instance_id,
                                    ))
                                    asyncio.create_task(_hook_timeout_guard(
                                        redis_client, session_id, "on_contact_end",
                                    ))
                                logger.info(
                                    "contact-end hooks dispatched: session=%s pool=%s "
                                    "on_human_end=%d on_contact_end=%d (timeout guard: %ds)",
                                    session_id, _pool_id_hooks, len(_on_human_end),
                                    len(_on_contact_end), _HOOK_TIMEOUT_S,
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
                    # G7 Fase 3a — other_human_active: o contato CONTINUA com outro agente.
                    # NÃO escrevemos session:closed (antes era escrito incondicionalmente
                    # no topo, descartando re-rotas legítimas — §4).
                    # G7 Slice 4′ Item 1 — o mcp-server seta session:closed de forma
                    # INCONDICIONAL no /api/agent_done (server.ts ~1475, p/ ganhar a corrida
                    # com pending_assignment no reconnect single-humano). Em continuação
                    # (outro agente ativo) esse marcador vaza → o Routing Engine descartaria
                    # re-rotas/reconexões da sessão ainda viva. Desfazemos aqui. Ver §4 / g7 §11.
                    try:
                        await redis_client.delete(f"session:{session_id}:closed")
                    except Exception as _mk_exc:
                        logger.warning(
                            "Could not undo session:closed marker (other_human_active): "
                            "session=%s — %s", session_id, _mk_exc,
                        )
                    logger.info(
                        "Agent dropped, %d agent(s) still active (session:closed undone — "
                        "contact continues): session=%s instance=%s",
                        remaining, session_id, instance_id,
                    )
                    # G7 Slice 2′ — wrap-up por peer: o humano que sai (não-último) faz o
                    # wrap-up do SEU segmento, igual ao transfer. segment_wrapup dispara só
                    # side=agent e NÃO arma posatt/hook_pending (o contato segue sob os
                    # outros). _ha_pool/_ha_tenant são por-instance (Slice 1b) e
                    # human_seg:{_ha_pool} (escrito acima) atribui ao segmento deste humano.
                    # Ver g7 §11.
                    # G7 heartbeat: num agent_disconnect o humano SUMIU — não pode preencher
                    # o menu de wrap-up → pula o segment_wrapup (só encerra o segmento acima).
                    if reason != "agent_disconnect" and http and _ha_pool and _ha_tenant:
                        _oha_customer = session_id
                        try:
                            _oha_raw_meta = await redis_client.get(f"session:{session_id}:meta")
                            if _oha_raw_meta:
                                _oha_meta = json.loads(_oha_raw_meta)
                                _oha_customer = (
                                    _oha_meta.get("customer_id")
                                    or _oha_meta.get("contact_id")
                                    or session_id
                                )
                        except Exception:
                            pass
                        asyncio.create_task(fire_pool_hooks(
                            http=http,
                            redis_client=redis_client,
                            session_id=session_id,
                            pool_id=_ha_pool,
                            tenant_id=_ha_tenant,
                            customer_id=_oha_customer,
                            hook_type="segment_wrapup",
                            human_instance_id=instance_id,
                        ))
                        logger.info(
                            "Peer wrap-up (segment_wrapup) dispatched for non-last human: "
                            "session=%s pool=%s instance=%s",
                            session_id, _ha_pool, instance_id,
                        )
            else:
                # instance_id not in event (legacy path) — fall back to clearing everything.
                # Root cause of "Ocupados never decrements": instance_id is empty when the
                # webchat human agent calls /api/agent_done because session:meta doesn't carry
                # instance_id.  We must publish agent_done to agent.lifecycle WITH the pool
                # info BEFORE deleting the human tracking keys, so the routing engine can
                # call remove_conversation() and DECR pool:active_count.
                logger.warning(
                    "agent_closed without instance_id — clearing all human tracking: session=%s",
                    session_id,
                )
                # 1. Read members and pool/tenant BEFORE deleting tracking keys.
                _leg_members: set = set()
                _leg_pool = _leg_ten = _leg_at = ""
                try:
                    _leg_members = await redis_client.smembers(
                        f"session:{session_id}:human_agents"
                    )
                except Exception:
                    pass
                try:
                    _leg_meta_raw = await redis_client.get(f"session:{session_id}:meta")
                    if _leg_meta_raw:
                        _leg_m = json.loads(_leg_meta_raw)
                        _leg_pool = _leg_m.get("pool_id", "")
                        _leg_ten  = _leg_m.get("tenant_id", "") or _leg_m.get("tenant", "")
                        _leg_at   = _leg_m.get("agent_type_id", "")
                except Exception:
                    pass
                # 2. Restore instances (resets routing state).
                await _restore_all_instances(redis_client, session_id)
                # 3. Publish agent_done for each human member so routing engine DECRs
                #    pool:active_count.  Uses fallback_pools (not instance_meta) since
                #    human agents in demo mode never wrote instance_meta via agent_ready.
                if _kafka_producer and _leg_ten:
                    for _leg_inst in _leg_members:
                        _leg_inst_str = (
                            _leg_inst if isinstance(_leg_inst, str) else _leg_inst.decode()
                        )
                        asyncio.create_task(_kafka_producer.send(
                            TOPIC_LIFECYCLE,
                            json.dumps({
                                "event":           "agent_done",
                                "tenant_id":       _leg_ten,
                                "instance_id":     _leg_inst_str,
                                "agent_type_id":   _leg_at,
                                "pools":           [_leg_pool] if _leg_pool else [],
                                "conversation_id": session_id,
                                "timestamp":       datetime.now(timezone.utc).isoformat(),
                            }).encode("utf-8"),
                        ))
                        logger.info(
                            "agent_done published to lifecycle (legacy path): "
                            "session=%s instance=%s pool=%s",
                            session_id, _leg_inst_str, _leg_pool,
                        )
                else:
                    logger.warning(
                        "agent_done NOT published (legacy): session=%s "
                        "has_producer=%s tenant=%r members=%d",
                        session_id, _kafka_producer is not None, _leg_ten, len(_leg_members),
                    )
                # 4. Delete tracking keys AFTER publishing agent_done.
                await redis_client.delete(f"session:{session_id}:human_agent")
                await redis_client.delete(f"session:{session_id}:human_agents")
                # 5. Trigger contact close (no instance_id means we can't check hooks;
                #    fall back to immediate close so the customer WS is never left open).
                asyncio.create_task(_trigger_contact_close(redis_client, session_id))

    except Exception as exc:
        logger.error("Error processing contact_closed: session=%s — %s", session_id, exc)


# ── @mention command dispatch ─────────────────────────────────────────────────

def _load_mention_commands(skill_id: str) -> dict | None:
    """
    Dev fallback: load mention_commands from a skill YAML on disk
    (SKILLS_DIR/{skill_id}.yaml). Returns the dict or None if not found.
    """
    flow = _load_yaml_fallback(skill_id)
    if flow is None:
        return None
    mention_commands = flow.get("mention_commands")
    if not isinstance(mention_commands, dict):
        return None
    return mention_commands


def _mention_commands_from_cached_flow(*candidates: str) -> dict | None:
    """
    Read mention_commands from an already-cached skill flow. The flow is
    populated in _skill_flow_cache when the specialist is activated, and
    (deploy-driven) carries mention_commands nested inside it. Tries each
    candidate id (skill_id, then agent_type_id) against the cache.
    """
    for candidate in filter(None, candidates):
        flow = _skill_flow_cache.get(candidate)
        if isinstance(flow, dict):
            mc = flow.get("mention_commands")
            if isinstance(mc, dict):
                return mc
    return None


async def _resolve_mention_commands(
    tenant_id: str,
    skill_id: str,
    agent_type_id: str,
) -> tuple[dict | None, str]:
    """
    Resolve a specialist's mention_commands under deploy-driven provisioning.

    Resolution order (registry as source of truth, disk only as dev fallback):
      1. _skill_flow_cache — flow already fetched on specialist activation,
         carrying mention_commands nested inside (RegistrySyncer._sync_skills).
      2. Agent Registry fetch via get_skill_flow (cache cold) for each id.
      3. Disk YAML by filename (_load_mention_commands) — legacy/dev fallback.

    Returns (mention_commands | None, lookup_id).
    """
    candidates = list(filter(None, [skill_id, agent_type_id]))

    cached = _mention_commands_from_cached_flow(*candidates)
    if cached is not None:
        return cached, (skill_id or agent_type_id)

    async with aiohttp.ClientSession() as http:
        for candidate in candidates:
            flow = await get_skill_flow(http, tenant_id, candidate)
            if isinstance(flow, dict):
                mc = flow.get("mention_commands")
                if isinstance(mc, dict):
                    return mc, candidate

    for candidate in candidates:
        mc = _load_mention_commands(candidate)
        if mc is not None:
            return mc, candidate

    return None, ""


async def dispatch_mention_command(
    redis_client:  aioredis.Redis,
    session_id:    str,
    tenant_id:     str,
    command_name:  str,
    command_def:   dict,
    instance_id:   str = "",
) -> None:
    """
    Execute a mention_command action for an already-active specialist.

    Actions (exactly one per command, Zod union):
      trigger_step: <step_id>    → LPUSH { _mention_trigger_step: step_id }
                                    to the specialist's BLPOP key so the blocked
                                    menu wakes up and jumps to step_id.
      terminate_self: true       → LPUSH { _mention_terminate: true } so the
                                    specialist's menu step returns on_failure.
      set_context: { key: val }  → HSET {tenant}:ctx:{session_id} with ContextEntry
                                    (source="mention_command", confidence=1.0).

    instance_id: o menu step BLPOPa em menu:result:{sid}:{iid} quando o agente
    roda com instance_id (specialists SEMPRE têm) — o interrupt DEVE mirar a
    chave instance-scoped, senão nunca chega (bug do standby do copilot).
    Fallback session-scoped só para agentes legados sem instance_id.

    If acknowledge: true, also publishes mention_command.ack to agent:events:{session_id}
    so the Agent Assist UI can display a confirmation badge.
    """
    action   = command_def.get("action", {})
    ack      = command_def.get("acknowledge", False)

    trigger_step = action.get("trigger_step")
    terminate    = action.get("terminate_self", False)
    set_ctx      = action.get("set_context", {})

    result_key = (
        f"menu:result:{session_id}:{instance_id}" if instance_id
        else f"menu:result:{session_id}"
    )

    if trigger_step:
        payload = json.dumps({"_mention_trigger_step": trigger_step})
        try:
            await redis_client.lpush(result_key, payload)
            logger.info(
                "mention_command dispatch: trigger_step=%s session=%s key=%s",
                trigger_step, session_id, result_key,
            )
        except Exception as exc:
            logger.error(
                "mention_command: failed to push trigger_step=%s session=%s — %s",
                trigger_step, session_id, exc,
            )

    elif terminate:
        payload = json.dumps({"_mention_terminate": True})
        try:
            await redis_client.lpush(result_key, payload)
            logger.info(
                "mention_command dispatch: terminate session=%s key=%s",
                session_id, result_key,
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
            await redis_client.expire(ctx_key, _stl())
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

    # Resolve mention_commands. Deploy-driven: the specialist carries
    # skill_id (== synthesized agent_type_id), and mention_commands rides
    # inside the skill flow (RegistrySyncer._sync_skills), round-tripped via
    # agent-registry — no dependency on a disk filename matching the id.
    # Disk YAML remains a dev fallback inside _resolve_mention_commands.
    mention_commands, lookup_id = await _resolve_mention_commands(
        tenant_id, skill_id, agent_type_id,
    )

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
        # Specialist BLPOPa em menu:result:{sid}:{iid} — interrupt mira a
        # chave instance-scoped (fix do standby do copilot).
        instance_id=specialist.get("instance_id", "") or "",
    )


# ── Arc 19 Fase C: resume a suspended webhook session ─────────────────────────

async def _handle_webhook_session_resumed(
    event: dict,
    redis_client: aioredis.Redis,
    http: aiohttp.ClientSession,
) -> None:
    """
    Arc 19 Fase C — Resume a suspended webhook session.

    Triggered by event_type='session_resumed' on conversations.inbound (published
    by WebhookAdapter.handle_resume()).  Bypasses the routing engine — directly
    re-activates the skill-flow with the resume context so the suspended step
    can follow its on_resume / on_reject / on_timeout path.

    Session meta written by process_routed (NX) on first activation provides the
    agent_type_id, pool_id, and instance_id needed for lifecycle management.
    """
    session_id = event.get("session_id", "")
    tenant_id  = event.get("tenant_id", "")
    step_id    = event.get("step_id", "")
    payload    = event.get("payload") or {}

    if not session_id or not tenant_id:
        logger.warning("session_resumed: missing session_id or tenant_id — skipping")
        return

    # Read session meta written by process_routed (NX) on first activation
    raw_meta = None
    try:
        raw_meta = await redis_client.get(f"session:{session_id}:meta")
    except Exception as exc:
        logger.error(
            "session_resumed: could not read session meta: session=%s — %s",
            session_id, exc,
        )
        return

    if not raw_meta:
        logger.error(
            "session_resumed: no session meta found for session=%s "
            "(was the session created via a webhook pool?)",
            session_id,
        )
        return

    meta          = json.loads(raw_meta)
    agent_type_id = meta.get("agent_type_id", "")
    pool_id       = meta.get("pool_id", "")
    customer_id   = meta.get("customer_id", "")
    instance_id   = meta.get("instance_id", "")

    if not agent_type_id:
        logger.error(
            "session_resumed: agent_type_id not in meta for session=%s — cannot resume",
            session_id,
        )
        return

    # Extract decision from payload (WebhookAdapter embeds it as payload["decision"])
    decision = payload.get("decision", "input")

    logger.info(
        "Resuming webhook session: session=%s agent=%s step=%s decision=%s",
        session_id, agent_type_id, step_id, decision,
    )

    # Arc 19: clear watchdog/crash-recovery flags that may have been set while
    # the session was legitimately suspended. A valid resume proves the session
    # is alive — stale close flags must be removed so that _close_contact_layer()
    # and _destroy_conference() can fire correctly when the workflow completes.
    # Two separate NX guard keys must be cleared:
    #   contact_close_fired — used by _close_contact_layer (analytics publish)
    #   close_fired         — used by _destroy_conference  (agent:events publish)
    try:
        await redis_client.delete(
            f"session:{session_id}:contact_close_fired",
            f"session:{session_id}:close_fired",
            f"session:{session_id}:closed",
        )
    except Exception as _exc:
        logger.debug("session_resumed: could not clear close flags: session=%s — %s", session_id, _exc)

    # Read instance snapshot for lifecycle restore after execution
    native_snapshot: dict | None = None
    if instance_id:
        try:
            raw_snap = await redis_client.get(f"{tenant_id}:instance:{instance_id}")
            if raw_snap:
                native_snapshot = json.loads(raw_snap)
        except Exception:
            pass

    # Resolve skills from Agent Registry (activate_native_agent calls resolve_flow_for_agent
    # which will perform its own registry lookup if skills is empty — safe fallback).
    skills: list[dict] = []
    try:
        agent_type = await get_agent_type(http, tenant_id, agent_type_id)
        if agent_type:
            skills = agent_type.get("skills", [])
    except Exception as _skill_exc:
        logger.debug("session_resumed: could not fetch agent type skills: %s", _skill_exc)

    # Build resume_context for skill-flow-service
    resume_context = {
        "step_id":   step_id,
        "decision":  decision,
        "payload":   payload,
    }

    # ── Arc 19 v2: emit a participation segment for THIS resume window ─────────
    # The resume re-executes the flow from the suspended step. That window must
    # surface as its own segment, so the trace reads e.g.
    #   intake (suspended) → confirmação specialist (resolved) → intake-resumed (resolved)
    # Without this the only primary segment is the pre-suspend window (outcome=
    # suspended), which é enganoso mesmo quando a sessão resolve. Espelha o padrão
    # de process_routed (sequence_index, primary_segment, joined/left).
    _resume_participant = instance_id or agent_type_id
    _resume_joined_at   = datetime.now(timezone.utc)
    _resume_joined_iso  = _resume_joined_at.isoformat()
    _resume_seg_id      = str(uuid.uuid4())
    _resume_seq_idx     = 0
    try:
        _seq_raw = await redis_client.incr(f"session:{session_id}:segment_seq")
        _resume_seq_idx = int(_seq_raw) - 1
        await redis_client.expire(f"session:{session_id}:segment_seq", _stl())
        await redis_client.setex(f"session:{session_id}:primary_segment", 14400, _resume_seg_id)
    except Exception:
        pass
    asyncio.create_task(_publish_participant_event(
        session_id=session_id, tenant_id=tenant_id,
        participant_id=_resume_participant, pool_id=pool_id,
        agent_type_id=agent_type_id, event_type="participant_joined",
        agent_type="native", role="primary",
        segment_id=_resume_seg_id, sequence_index=_resume_seq_idx,
        joined_at=_resume_joined_iso,
    ))

    # Re-activate skill flow with resume context (webhook_pool=True wires
    # persistSuspendWebhook in skill-flow-service for any subsequent suspend steps)
    agent_result = await activate_native_agent(
        http=http,
        redis_client=redis_client,
        session_id=session_id,
        customer_id=customer_id,
        agent_type_id=agent_type_id,
        tenant_id=tenant_id,
        skills=skills,
        instance_id=instance_id,
        webhook_pool=True,
        resume_context=resume_context,
        pool_id=pool_id,   # fatia 1: $.config persists across suspend/resume for webhook skills
    )

    _ai_outcome = (agent_result or {}).get("outcome", "")

    # ── Close the resume segment (outcome = whatever this window resolved to) ──
    _resume_duration_ms = int(
        (datetime.now(timezone.utc) - _resume_joined_at).total_seconds() * 1000
    )
    asyncio.create_task(_publish_participant_event(
        session_id=session_id, tenant_id=tenant_id,
        participant_id=_resume_participant, pool_id=pool_id,
        agent_type_id=agent_type_id, event_type="participant_left",
        agent_type="native", role="primary",
        segment_id=_resume_seg_id, sequence_index=_resume_seq_idx,
        joined_at=_resume_joined_iso, duration_ms=_resume_duration_ms,
        outcome=_ai_outcome or None,
        flow_id=(((agent_result or {}).get("pipeline_state")) or {}).get("flow_id", "") or "",
    ))

    # "suspended" = flow hit another suspend step; session persists in Redis.
    # Any other terminal outcome closes the session.
    if _ai_outcome != "suspended":
        await _mark_contact_ended(redis_client, session_id)
        asyncio.create_task(_trigger_contact_close(redis_client, session_id))

    # Restore instance to pool (mirrors process_routed post-activation lifecycle).
    # Gap-A fix: always land the mirror at ready, even when native_snapshot is None
    # (the resume window can itself hit another suspend and the snapshot may have
    # expired), so the mirror is never left busy after agent_done drops the SCARD.
    # Runs unconditionally (incl. re-suspend) because agent_done is published
    # unconditionally just below — the vaga is released either way (Arc 19).
    await _release_native_instance_snapshot(
        redis_client, tenant_id, instance_id, pool_id,
        agent_type_id, native_snapshot,
    )

    # Publish agent_ready + agent_done for routing-engine capacity tracking
    if _kafka_producer and instance_id:
        _snap_pools = list(
            (native_snapshot or {}).get("pools") or ([pool_id] if pool_id else [])
        )
        _snap_max = int(
            (native_snapshot or {}).get("max_concurrent_sessions")
            or (native_snapshot or {}).get("max_concurrent")
            or 1
        )
        asyncio.create_task(_kafka_producer.send(
            TOPIC_LIFECYCLE,
            json.dumps({
                "event":                   "agent_ready",
                "tenant_id":               tenant_id,
                "instance_id":             instance_id,
                "agent_type_id":           agent_type_id,
                "status":                  "ready",
                "execution_model":         (native_snapshot or {}).get("execution_model", "stateless"),
                "current_sessions":        0,
                "max_concurrent_sessions": _snap_max,
                "pools":                   _snap_pools,
                "timestamp":               datetime.now(timezone.utc).isoformat(),
            }).encode("utf-8"),
        ))
        asyncio.create_task(_kafka_producer.send(
            TOPIC_LIFECYCLE,
            json.dumps({
                "event":           "agent_done",
                "tenant_id":       tenant_id,
                "instance_id":     instance_id,
                "agent_type_id":   agent_type_id,
                "pools":           _snap_pools,
                "conversation_id": session_id,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
            }).encode("utf-8"),
        ))


# ── Process conversations.inbound — forward customer messages to human agent ──

async def process_inbound(
    msg: dict,
    redis_client: aioredis.Redis,
    http: aiohttp.ClientSession | None = None,
) -> None:
    """
    Four event types share conversations.inbound:
      1. NormalizedInboundEvent (from channel-gateway) — has "author" field
      2. ConversationInboundEvent (from conversation_escalate) — no "author" field,
         consumed by the Routing Engine; nothing to do here.
      3. mention_routing event (from routeMentions in mcp-server-plughub) — has
         mention_routing=True and no "author" field; dispatched to active specialists.
      4. session_resumed event (from WebhookAdapter.handle_resume) — has
         event_type="session_resumed"; re-activates the skill-flow instance directly.
    """
    if msg.get("event_type") == "session_resumed":
        if http is not None:
            await _handle_webhook_session_resumed(msg, redis_client, http)
        else:
            logger.warning(
                "session_resumed received but http session not available — skipping resume"
                " session_id=%s",
                msg.get("session_id", ""),
            )
        return

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
        # any_masked: step-level flag (entire submission suppressed)
        # all_masked_fields: union of per-field masked_fields across all waiting entries
        any_masked        = False
        all_masked_fields: set[str] = set()
        if waiting_hash:
            for _meta_json in waiting_hash.values():
                try:
                    _meta = json.loads(_meta_json)
                    if _meta.get("masked"):
                        any_masked = True
                    # Collect per-field masked IDs (written by skill-flow-engine menu.ts)
                    for _fid in (_meta.get("masked_fields") or []):
                        if isinstance(_fid, str):
                            all_masked_fields.add(_fid)
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
            elif all_masked_fields and msg_type == "menu_result":
                # Field-level masking: some form fields are sensitive, others are not.
                # Redact individual masked field values instead of suppressing entirely.
                try:
                    result_obj = json.loads(reply_text) if isinstance(reply_text, str) else reply_text
                    if isinstance(result_obj, dict):
                        redacted = {
                            k: ("••••••" if k in all_masked_fields else v)
                            for k, v in result_obj.items()
                        }
                        display_text = f"[Formulário: {json.dumps(redacted, ensure_ascii=False)}]"
                    else:
                        display_text = f"[Seleção: {reply_text}]"
                except Exception:
                    display_text = f"[Seleção: {reply_text}]"
                visibility = "all"
                logger.info(
                    "Field-level masked form reply redacted for human agent: session=%s fields=%s",
                    session_id, list(all_masked_fields),
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
                await redis_client.expire(stream_key_human, _stl())  # 4h TTL
            except Exception as _xadd_exc:
                logger.warning(
                    "Could not XADD customer message to stream: session=%s — %s",
                    session_id, _xadd_exc,
                )

            delivered = True

        # ── Publish customer message to analytics (ClickHouse persistence) ──
        # This must happen for ALL customer messages regardless of delivery target,
        # so messages survive Redis stream TTL expiration.
        try:
            _tenant_for_analytics = await redis_client.get(f"session:{session_id}:tenant_id")
            if _tenant_for_analytics and isinstance(_tenant_for_analytics, bytes):
                _tenant_for_analytics = _tenant_for_analytics.decode()
            _tenant_for_analytics = _tenant_for_analytics or os.environ.get("PLUGHUB_TENANT_ID", "tenant_demo")

            _analytics_event = {
                "event_type":   "message_sent",
                "message_id":   msg.get("message_id", str(uuid.uuid4())),
                "session_id":   session_id,
                "tenant_id":    _tenant_for_analytics,
                "author_id":    author.get("id") or contact_id or "customer",
                "author_role":  "customer",
                "content_type": "text",
                "content":      reply_text if not any_masked else "[entrada mascarada]",
                "visibility":   "all",
                "timestamp":    msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
            }
            if _kafka_producer:
                await _kafka_producer.send_and_wait(
                    "conversations.events",
                    json.dumps(_analytics_event).encode("utf-8"),
                )
                logger.debug("Published customer message to conversations.events: session=%s", session_id)
        except Exception as _analytics_exc:
            logger.warning("Failed to publish customer analytics event: session=%s — %s", session_id, _analytics_exc)

        if waiting_hash:
            # ── Native AI agents in Skill Flow menu step: route by visibility ──
            # Customer messages go to agents whose visibility includes the customer
            # (visibility "all" or array containing customer's participant_id).
            # Each agent has its own isolated BLPOP key: menu:result:{session_id}:{instanceId}
            #
            # customer_participant_id (cust_{hex12}) is distinct from contact_id (JWT sub).
            # The visibility array uses the resolved participant_id, so we must look it up.
            customer_pid = contact_id or "customer"
            try:
                _cust_pid_raw = await redis_client.get(
                    f"session:{session_id}:customer_participant_id"
                )
                if _cust_pid_raw:
                    customer_pid = (
                        _cust_pid_raw if isinstance(_cust_pid_raw, str)
                        else _cust_pid_raw.decode()
                    )
            except Exception:
                pass  # fallback to contact_id — best effort

            for agent_key, meta_json in waiting_hash.items():
                try:
                    meta = json.loads(meta_json)
                except Exception:
                    meta = {"visibility": "all"}
                vis = meta.get("visibility", "all")

                # Determine if this agent is waiting for customer input:
                # - "all": always receives customer messages
                # - array: receives only if customer_pid is in the array
                # - "agents_only": does NOT receive customer messages
                is_customer_facing = False
                if vis == "all":
                    is_customer_facing = True
                elif isinstance(vis, list):
                    is_customer_facing = customer_pid in vis
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

            # ── Write customer response to canonical stream for AI-agent sessions ──
            # Mirrors the is_human branch's stream write (above) so NPS scores,
            # form submissions, and other menu responses appear in the
            # Analytics/Sessions transcript even when no human agent is present.
            # Skipped when is_human — the human branch already wrote it.
            if not is_human:
                if any_masked:
                    _ai_stream_display = "[entrada mascarada]"
                    _ai_stream_vis     = "agents_only"
                elif all_masked_fields and msg_type == "menu_result":
                    try:
                        _result_obj = (
                            json.loads(reply_text)
                            if isinstance(reply_text, str) else reply_text
                        )
                        if isinstance(_result_obj, dict):
                            _redacted = {
                                k: ("••••••" if k in all_masked_fields else v)
                                for k, v in _result_obj.items()
                            }
                            _ai_stream_display = f"[Formulário: {json.dumps(_redacted, ensure_ascii=False)}]"
                        else:
                            _ai_stream_display = f"[Seleção: {reply_text}]"
                    except Exception:
                        _ai_stream_display = f"[Seleção: {reply_text}]"
                    _ai_stream_vis = "all"
                else:
                    _ai_stream_display = (
                        reply_text if msg_type == "text"
                        else f"[Seleção: {reply_text}]"
                    )
                    _ai_stream_vis = "all"

                try:
                    await redis_client.xadd(
                        stream_key,
                        {
                            "event_id":    msg.get("message_id", str(uuid.uuid4())),
                            "type":        "message",
                            "timestamp":   msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                            "author_id":   author.get("id") or contact_id or "customer",
                            "author_role": "customer",
                            "visibility":  _ai_stream_vis,
                            "content":     json.dumps({"text": _ai_stream_display}),
                        },
                    )
                    await redis_client.expire(stream_key, _stl())
                    logger.debug(
                        "XADD customer AI-menu reply to stream: session=%s masked=%s",
                        session_id, bool(any_masked),
                    )
                except Exception as _xadd_ai_exc:
                    logger.warning(
                        "Could not XADD customer AI-menu reply to stream: session=%s — %s",
                        session_id, _xadd_ai_exc,
                    )

        # ── Native AI agents in receive step: route by filter ─────────────────
        # receive:waiting agents listen to any participant's messages (not just the
        # customer) — but customer inbound messages are routed here too.
        # Each agent declares its filter (author_role, visibility, event_types) and
        # only receives events that pass ALL filter predicates.
        # Echo suppression: author instance_id is unknown for customer messages
        # (customers are not instances), so instance_id_of_author=None is correct.
        try:
            n_receive = await _route_to_receive_waiting(
                redis_client          = redis_client,
                session_id            = session_id,
                event_type            = "message_sent",
                author_id             = contact_id or "customer",
                author_role           = "customer",
                visibility            = "all",
                content               = reply_text if not any_masked else "[entrada mascarada]",
                instance_id_of_author = None,  # customers are not instances
            )
            if n_receive:
                logger.info(
                    "receive:waiting notified %d agent(s) of customer message: session=%s",
                    n_receive, session_id,
                )
                delivered = True
        except Exception as _rx_exc:
            logger.warning(
                "receive:waiting routing error: session=%s — %s", session_id, _rx_exc,
            )

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
                await redis_client.expire(stream_key, _stl())  # renova TTL 4h a cada mensagem
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


# ── Startup stale ai_completing cleanup ───────────────────────────────────────

async def _cleanup_stale_completing_at_startup(redis_client: aioredis.Redis) -> None:
    """
    At startup, every ai_completing key left in Redis is stale: the coroutine
    that set it was killed when the previous bridge process exited.

    For each stale key we:
      1. Read the routing snapshot (session:{sid}:routing:{inst}) BEFORE
         _restore_instance() deletes it.
      2. Restore the instance to ready state.
      3. Publish agent_done to Kafka so the routing engine DECR's the busy counter.

    This fixes the "ghost busy counter" bug that occurs when the bridge is
    restarted while a posatt AI skill flow (e.g. wrapup_ia) is running:
    the process dies, ai_completing survives in Redis for up to 4h, and
    contact_closed's skip-branch never publishes agent_done.
    """
    try:
        keys = await redis_client.keys("session:*:ai_completing:*")
        if not keys:
            return
        logger.info(
            "Startup: found %d stale ai_completing key(s) — will restore + publish agent_done",
            len(keys),
        )
        for key in keys:
            # Key format: session:{session_id}:ai_completing:{instance_id}
            # session_id and instance_id never contain ":" so a fixed split works.
            parts = key.split(":", 3)
            if len(parts) != 4 or parts[0] != "session" or parts[2] != "ai_completing":
                logger.warning("Startup cleanup: unexpected key format %r — skipping", key)
                continue
            session_id  = parts[1]
            instance_id = parts[3]
            try:
                # Read snapshot before _restore_instance() deletes it.
                snap_raw = await redis_client.get(
                    f"session:{session_id}:routing:{instance_id}"
                )
                await _restore_instance(redis_client, session_id, instance_id)
                await redis_client.delete(key)   # clear the stale completing flag
                if snap_raw and _kafka_producer:
                    snap_info  = json.loads(snap_raw)
                    tenant_id  = snap_info.get("tenant_id", "")
                    pool_id    = snap_info.get("pool_id", "")
                    inst_id    = snap_info.get("instance_id", instance_id)
                    agent_type = (snap_info.get("snapshot") or {}).get("agent_type_id", "")
                    if tenant_id and inst_id:
                        # Use await here (startup, no event-loop contention)
                        await _kafka_producer.send(
                            TOPIC_LIFECYCLE,
                            json.dumps({
                                "event":           "agent_done",
                                "tenant_id":       tenant_id,
                                "instance_id":     inst_id,
                                "agent_type_id":   agent_type,
                                "conversation_id": session_id,
                                "pools":           [pool_id] if pool_id else [],
                                "timestamp":       datetime.now(timezone.utc).isoformat(),
                            }).encode("utf-8"),
                        )
                        logger.info(
                            "Startup cleanup: agent_done published — "
                            "session=%s inst=%s pool=%s",
                            session_id, inst_id, pool_id,
                        )
                elif not snap_raw:
                    logger.warning(
                        "Startup cleanup: no routing snapshot for session=%s inst=%s — "
                        "counter not decremented (manual redis reset may be needed)",
                        session_id, instance_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Startup cleanup: error processing key=%r — %s", key, exc
                )
    except Exception as exc:
        logger.warning("Startup ai_completing cleanup failed: %s", exc)


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


# ── Session watchdog ──────────────────────────────────────────────────────────

async def _sweep_orphaned_sessions(redis_client: aioredis.Redis) -> None:
    """
    Scans all session:*:meta keys and fires _trigger_contact_close() for every
    session that has no active WebSocket keepalive key and has not yet been
    marked as closed.

    Detection criteria (all three must be true):
      1. session:{id}:meta       exists  — a channel-gateway session was opened
      2. session:{id}:ws_alive   missing — WebSocket keepalive has expired or
                                           was never set (pre-watchdog sessions)
      3. session:{id}:closed     missing — bridge has not yet processed close
      4. session:{id}:close_fired missing — _trigger_contact_close not already
                                             in flight for this session

    Uses SCAN in batches of 100 to avoid blocking Redis.
    """
    closed_count = 0
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(
            cursor, match="session:*:meta", count=100
        )
        for meta_key in keys:
            # Extract session_id from "session:{id}:meta"
            parts = meta_key.split(":")
            if len(parts) != 3:
                continue
            session_id = parts[1]

            # Skip if already closed or close already in flight
            already_closed = await redis_client.exists(
                f"session:{session_id}:closed",
                f"session:{session_id}:close_fired",
            )
            if already_closed:
                continue

            # Skip if WebSocket keepalive is still present
            ws_alive = await redis_client.exists(f"session:{session_id}:ws_alive")
            if ws_alive:
                continue

            # Arc 19: skip ALL webhook channel sessions — they never have a
            # WebSocket keepalive (ws_alive) because they are background workflow
            # executions. The suspend/resume/complete lifecycle manages their
            # closure. The crash detector + instance bootstrap handle stuck
            # active webhook executions via the routing engine, not the watchdog.
            try:
                meta_raw = await redis_client.get(f"session:{session_id}:meta")
                if meta_raw:
                    _meta = json.loads(meta_raw)
                    if _meta.get("channel_type") == "webhook" or _meta.get("channel") == "webhook":
                        continue  # webhook sessions are never WebSocket orphans
            except Exception:
                pass

            logger.warning(
                "session_watchdog: orphaned session detected "
                "(no ws_alive, not closed) — session=%s",
                session_id,
            )
            await _trigger_contact_close(redis_client, session_id)
            closed_count += 1

        if cursor == 0:
            break

    if closed_count:
        logger.info("session_watchdog: recovered %d orphaned session(s)", closed_count)


async def _session_watchdog(redis_client: aioredis.Redis) -> None:
    """
    Background task that periodically calls _sweep_orphaned_sessions().

    WebSocket sessions that die without publishing a clean ContactClosedEvent
    (crash, network drop, pre-fix bug) leave pool:active_count incremented
    indefinitely, making the Monitor show phantom "Ocupado" slots.

    The channel-gateway now writes session:{id}:ws_alive (TTL = idle_timeout +
    120s) and refreshes it on every received frame.  When that key expires the
    watchdog considers the session orphaned and fires _trigger_contact_close(),
    which decodes session:meta, publishes conversations.events contact_closed,
    and lets the existing process_contact_event path restore agent instances
    and decrement pool counters.

    Interval is configurable via SESSION_WATCHDOG_INTERVAL_S (default 120s).
    """
    interval_s = int(os.getenv("SESSION_WATCHDOG_INTERVAL_S", "120"))
    logger.info("session_watchdog started (interval=%ds)", interval_s)

    while True:
        await asyncio.sleep(interval_s)
        try:
            await _sweep_orphaned_sessions(redis_client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("session_watchdog sweep error: %s", exc, exc_info=True)


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
        # Low-latency tuning: reduce broker wait time before returning data.
        # Default fetch_max_wait_ms=500 adds up to 500ms per poll cycle.
        # With fetch_min_bytes=1, the broker returns as soon as any data arrives.
        fetch_max_wait_ms=100,
        fetch_min_bytes=1,
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
        # 0. Load session TTLs from Config API — replaces hardcoded 14400 literals.
        #    Falls back silently to defaults (14400s) if Config API is unreachable.
        await session_config.reload(CONFIG_API_URL, http)

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

        # 2b. Clean up any stale ai_completing keys left from a previous bridge run.
        #     Must run AFTER the Kafka producer is ready (above) and AFTER reconcile()
        #     has restored instance Redis state, so agent_done can be published correctly.
        await _cleanup_stale_completing_at_startup(redis_client)

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

        # Session watchdog — sweeps for orphaned WebSocket sessions that died
        # without publishing a ContactClosedEvent and repairs pool counters.
        watchdog_task = asyncio.create_task(_session_watchdog(redis_client))

        try:
            async for msg in consumer:
                asyncio.create_task(_dispatch(msg.value, msg.topic, http, redis_client, bootstrap))
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
            await consumer.stop()
            await producer.stop()
            _kafka_producer = None
            await redis_client.aclose()


async def _publish_dlq_bridge(payload: dict, topic: str, error: str) -> None:
    """Publish a failed dispatch event to the dead-letter topic."""
    if _kafka_producer is None:
        logger.error("[dlq] Producer not available — cannot publish to DLQ topic=%s", topic)
        return
    dlq_payload = {
        "event_id":       str(uuid.uuid4()),
        "source_topic":   topic,
        "consumer_group": GROUP_ID,
        "service":        "orchestrator-bridge",
        "error":          error,
        "attempt_count":  _MAX_DISPATCH_ATTEMPTS,
        "payload_raw":    json.dumps(payload),
        "failed_at":      datetime.now(timezone.utc).isoformat(),
    }
    try:
        await _kafka_producer.send_and_wait(
            KAFKA_DLQ_TOPIC,
            json.dumps(dlq_payload).encode("utf-8"),
        )
    except Exception as dlq_err:
        logger.error("[dlq] Failed to publish to DLQ topic=%s: %s", topic, dlq_err)


async def _dispatch(
    payload:      dict,
    topic:        str,
    http:         aiohttp.ClientSession,
    redis_client: aioredis.Redis,
    bootstrap:    InstanceBootstrap,
) -> None:
    """
    Dispatches a Kafka message to the appropriate handler with retry + DLQ.

    Reliability contract:
      • Each handler is retried up to _MAX_DISPATCH_ATTEMPTS times with
        exponential backoff before the event is published to the dead-letter topic.
      • This task runs fire-and-forget (asyncio.create_task) so the Kafka
        consumer loop is never stalled waiting for long-running handlers.
    """
    last_error: BaseException | None = None

    for attempt in range(1, _MAX_DISPATCH_ATTEMPTS + 1):
        try:
            await _dispatch_once(payload, topic, http, redis_client, bootstrap)
            return  # success
        except Exception as exc:
            last_error = exc
            if attempt < _MAX_DISPATCH_ATTEMPTS:
                delay_ms = _DISPATCH_BACKOFF_BASE_MS * (2 ** (attempt - 1))
                logger.warning(
                    "[retry %d/%d] topic=%s error=%s delay=%dms",
                    attempt, _MAX_DISPATCH_ATTEMPTS, topic, exc, delay_ms,
                )
                await asyncio.sleep(delay_ms / 1000)

    err_str = str(last_error)
    logger.error(
        "[dlq] All %d attempts failed topic=%s error=%s",
        _MAX_DISPATCH_ATTEMPTS, topic, err_str,
    )
    await _publish_dlq_bridge(payload, topic, err_str)


async def _dispatch_once(
    payload:      dict,
    topic:        str,
    http:         aiohttp.ClientSession,
    redis_client: aioredis.Redis,
    bootstrap:    InstanceBootstrap,
) -> None:
    if topic == TOPIC_ROUTED:
        await process_routed(payload, http, redis_client)
    elif topic == TOPIC_QUEUED:
        await process_queued(payload, http, redis_client)
    elif topic == TOPIC_INBOUND:
        await process_inbound(payload, redis_client, http)
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
        elif entity_type == "pool":
            # Skill Versioning P1/C: promote/rollback publica registry.changed(pool) →
            # invalida o snapshot do slot `current` E a identidade de versão (set_at)
            # p/ a próxima ativação pegar o novo deploy.
            if entity_id in _pool_flow_cache:
                del _pool_flow_cache[entity_id]
                logger.info("Pool flow cache invalidated: pool_id=%s", entity_id)
            _pool_deploy_version_cache.pop(entity_id, None)
            _pool_config_cache.pop(entity_id, None)  # fatia 1: config_json do slot segue o flow
        bootstrap.request_refresh()
    elif topic == TOPIC_CONFIG_CHANGED:
        await _handle_config_changed(payload, bootstrap, http)


async def _handle_config_changed(
    payload:   dict,
    bootstrap: InstanceBootstrap,
    http:      aiohttp.ClientSession,
) -> None:
    """
    Reacts to a config.changed event published by the Config API.

    Routing:
      namespace=quota      → bootstrap.request_refresh()
                             (max_concurrent_sessions or quota limits changed;
                              reconciliation may need to create or remove instances)

      namespace=session    → session_config.invalidate() + background reload.
                             Applies new TTL values immediately to all subsequent
                             Redis calls (setex/expire) in the bridge.

      namespace=routing /
               masking /
               webchat /
               sentiment /
               consumer /
               dashboard   → no bootstrap action needed.
                             These values are read at runtime via their own caches;
                             the cache TTL (60s) handles propagation naturally.

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
    elif namespace == "session":
        logger.info(
            "config.changed [%s] tenant=%s %s.%s — reloading session TTL config",
            operation, tenant_id, namespace, key,
        )
        session_config.invalidate()
        asyncio.create_task(session_config.reload(CONFIG_API_URL, http))
    else:
        logger.info(
            "config.changed [%s] tenant=%s %s.%s — runtime config, no bootstrap needed",
            operation, tenant_id, namespace, key,
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
