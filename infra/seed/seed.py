#!/usr/bin/env python3
"""
seed.py — Docker seed for the plughub-full integration environment.

Creates:
  1. Pools      via agent-registry HTTP API (idempotent: 409 → OK)
  2. Agent types via agent-registry HTTP API (idempotent: 409 → OK)
  3. Redis data (pool configs, global pools set, pool rosters)

NOTE — Agent instance registration in Redis is NO LONGER done here.
The orchestrator-bridge InstanceBootstrap module reads all active AgentTypes
from the Agent Registry at startup and registers the configured number of
instances (max_concurrent_sessions slots) in Redis automatically.
Billing is per configured instance → Agent Registry is the source of truth.

Environment variables (all have defaults):
  AGENT_REGISTRY_URL  — http://agent-registry:3300
  REDIS_URL           — redis://redis:6379
  TENANT_ID           — tenant_demo
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
REGISTRY_URL = os.environ.get("AGENT_REGISTRY_URL", "http://agent-registry:3300")
TENANT_ID    = os.environ.get("TENANT_ID",           "tenant_demo")
MAX_WAIT_S   = int(os.environ.get("SEED_MAX_WAIT",   "120"))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[seed]  {msg}", flush=True)

def ok(msg: str) -> None:
    print(f"[ok]    {msg}", flush=True)

def warn(msg: str) -> None:
    print(f"[warn]  {msg}", flush=True)

def die(msg: str) -> None:
    print(f"[error] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def _parse_body(raw: bytes) -> dict:
    """Parse response body as JSON; return {} on empty or non-JSON body."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw.decode(errors="replace")}


def http_post(path: str, payload: dict) -> tuple[int, dict]:
    url  = f"{REGISTRY_URL}{path}"
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type":  "application/json",
            "x-tenant-id":   TENANT_ID,
            "x-user-id":     "seed",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _parse_body(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_body(e.read())


def http_patch(path: str, payload: dict) -> tuple[int, dict]:
    url  = f"{REGISTRY_URL}{path}"
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        method="PATCH",
        headers={
            "Content-Type":  "application/json",
            "x-tenant-id":   TENANT_ID,
            "x-user-id":     "seed",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _parse_body(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_body(e.read())


def http_put(path: str, payload: dict) -> tuple[int, dict]:
    url  = f"{REGISTRY_URL}{path}"
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Content-Type":  "application/json",
            "x-tenant-id":   TENANT_ID,
            "x-user-id":     "seed",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _parse_body(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_body(e.read())


def wait_for_registry() -> None:
    log(f"Aguardando agent-registry em {REGISTRY_URL}/v1/health …")
    deadline = time.time() + MAX_WAIT_S
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{REGISTRY_URL}/v1/health", timeout=5) as r:
                if r.status == 200:
                    ok("agent-registry disponível")
                    return
        except Exception:
            pass
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(3)
    print()
    die(f"agent-registry não respondeu em {MAX_WAIT_S}s")


# ─────────────────────────────────────────────────────────────────────────────
# (F1.1a, 2026-06-11) Redis helper REMOVIDO — o seed não escreve mais Redis.
# pool_config:{id} e {tenant}:pools são populados pelo routing-engine a partir de
# registry.changed (kafka_listener._handle_pool_event → save_pool_config). Ver
# docs/arcos/config-consolidation.md (invariante "provisioning only via API").
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Data definitions
# ─────────────────────────────────────────────────────────────────────────────

ROUTING_EXPR = {
    "weight_sla":      0.4,
    "weight_wait":     0.2,
    "weight_tier":     0.2,
    "weight_churn":    0.1,
    "weight_business": 0.1,
}

POOLS = [
    {
        "pool_id":       "demo_ia",
        "description":   "Pool IA — entry point do fluxo de demo IVR com botões",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 480_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": False,
    },
    {
        "pool_id":       "sac_ia",
        "description":   "Pool IA — atendimento SAC via LLM (Claude)",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 480_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": False,
    },
    {
        "pool_id":       "fila_humano",
        "description":   "Pool intermediário — fila para agente humano de retenção",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 300_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": False,
    },
    {
        "pool_id":       "retencao_humano",
        "description":   "Pool de agentes humanos — retenção e suporte especializado",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 300_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": True,
        "hooks": {
            "on_human_start": [],
            "on_human_end": [
                {"pool": "wrapup_ia"},
                {"pool": "nps_ia"},
            ],
            "post_human": [],
        },
    },
    {
        "pool_id":       "wrapup_ia",
        "description":   "Pool do agente de wrap-up — notas internas pós-atendimento (agents_only)",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 180_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": False,
    },
    {
        "pool_id":       "nps_ia",
        "description":   "Pool do agente NPS — pesquisa isolada com o cliente (visibility array)",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 120_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": False,
    },
    {
        "pool_id":       "avaliacao_ia",
        "description":   "Pool exclusivo para agentes de avaliação pós-sessão — NÃO recebe tráfego ao vivo",
        "channel_types": ["webchat", "whatsapp"],
        "sla_target_ms": 600_000,
        "routing_expression": ROUTING_EXPR,
        "is_human_pool": False,
    },
]

# Pools that will be sent to the registry API (routing_expression + is_human_pool
# are Routing Engine Redis fields, not registry fields — strip them for the POST body).
REGISTRY_POOL_FIELDS = {"pool_id", "description", "channel_types", "sla_target_ms", "hooks"}

# ─── Channel Endpoints — demo entries ────────────────────────────────────────
# Visible in Platform UI → Configuration → Channels
CHANNEL_ENDPOINTS = [
    # WebChat endpoints — slug-based routing
    {"channel": "webchat", "identifier": "demo",  "pool_id": "demo_ia",          "label": "WebChat Demo IA"},
    {"channel": "webchat", "identifier": "sac",   "pool_id": "sac_ia",           "label": "WebChat SAC IA"},
    # Webhook endpoint — path-based: {host}/channel/webhook/crm-callback
    {"channel": "webhook", "identifier": "crm-callback", "pool_id": "retencao_humano", "label": "CRM Callback"},
]

AGENT_TYPES = [
    {
        "agent_type_id":           "agente_demo_ia_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "executor",
        "max_concurrent_sessions": 10,
        "pools":                   ["demo_ia"],
        "skills":                  [],
        "permissions": [
            "mcp-server-plughub:agent_heartbeat",
            "mcp-server-plughub:notification_send",
            "mcp-server-plughub:conversation_escalate",
            "mcp-server-plughub:interaction_request",
        ],
        "capabilities": {"channels": "webchat,whatsapp"},
    },
    {
        "agent_type_id":           "agente_sac_ia_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "executor",
        "max_concurrent_sessions": 10,
        "pools":                   ["sac_ia"],
        "skills":                  [],
        "permissions": [
            "mcp-server-plughub:agent_heartbeat",
            "mcp-server-plughub:notification_send",
            "mcp-server-plughub:conversation_escalate",
            "mcp-server-plughub:interaction_request",
        ],
        "capabilities": {"channels": "webchat,whatsapp", "llm": "true"},
    },
    {
        "agent_type_id":           "agente_fila_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "executor",
        "max_concurrent_sessions": 50,
        "pools":                   ["fila_humano"],
        "skills":                  [],
        "permissions": [
            "mcp-server-plughub:agent_heartbeat",
            "mcp-server-plughub:notification_send",
            "mcp-server-plughub:conversation_escalate",
        ],
        "capabilities": {"channels": "webchat,whatsapp"},
    },
    {
        "agent_type_id":           "agente_retencao_humano_v1",
        "framework":               "human",
        "execution_model":         "stateful",
        "role":                    "executor",
        "max_concurrent_sessions": 3,
        "pools":                   ["retencao_humano"],
        "skills":                  [],
        "permissions":             [],
        "capabilities": {"channels": "webchat,whatsapp"},
    },
    {
        "agent_type_id":           "agente_nps_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "executor",
        "max_concurrent_sessions": 20,
        "pools":                   ["nps_ia"],
        "skills":                  [{"skill_id": "skill_nps_v1"}],
        "permissions": [
            "mcp-server-plughub:agent_heartbeat",
            "mcp-server-plughub:notification_send",
            "mcp-server-plughub:interaction_request",
        ],
        "capabilities": {"channels": "webchat,whatsapp"},
    },
    {
        "agent_type_id":           "agente_wrapup_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "executor",
        "max_concurrent_sessions": 20,
        "pools":                   ["wrapup_ia"],
        "skills":                  [{"skill_id": "skill_wrapup_v1"}],
        "permissions": [
            "mcp-server-plughub:agent_heartbeat",
            "mcp-server-plughub:notification_send",
            "mcp-server-plughub:interaction_request",
        ],
        "capabilities": {"channels": "webchat,whatsapp", "finalization": "true"},
    },
    {
        "agent_type_id":           "agente_avaliacao_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "evaluator",
        "max_concurrent_sessions": 20,
        # IMPORTANT: must be in its own pool — NOT in demo_ia or any live-routing pool.
        # The evaluator only handles post-session quality assessment (evaluation_context_get
        # + evaluation_submit). Routing a live conversation to it causes an immediate
        # skill-flow failure because no live-conversation tools are available.
        "pools":                   ["avaliacao_ia"],
        "skills":                  [],
        "permissions": [
            "mcp-server-plughub:evaluation_context_get",
            "mcp-server-plughub:evaluation_submit",
        ],
        "capabilities": {"evaluation": "true"},
    },
]

# NOTE: Redis instance registration is handled by the orchestrator-bridge
# InstanceBootstrap module — no INSTANCES list needed here.


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: register pools via agent-registry
# ─────────────────────────────────────────────────────────────────────────────

def seed_pools() -> None:
    log("Registrando pools no agent-registry …")
    for pool in POOLS:
        body   = {k: v for k, v in pool.items() if k in REGISTRY_POOL_FIELDS}
        status, resp = http_post("/v1/pools", body)
        if status == 201:
            ok(f"Pool {pool['pool_id']} criado")
        elif status == 409:
            # Already exists — PUT to apply config changes (e.g. hooks added).
            put_body = {k: v for k, v in body.items() if k != "pool_id"}
            put_status, put_resp = http_put(
                f"/v1/pools/{pool['pool_id']}", put_body
            )
            if put_status in (200, 204):
                ok(f"Pool {pool['pool_id']} atualizado (PUT {put_status})")
            else:
                warn(
                    f"Pool {pool['pool_id']} já existia; PUT retornou {put_status} "
                    f"— {put_resp}. Configuração pode estar desatualizada."
                )
        else:
            die(f"Erro ao criar pool {pool['pool_id']}: HTTP {status} — {resp}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: register agent types via agent-registry
# ─────────────────────────────────────────────────────────────────────────────

def seed_agent_types() -> None:
    log("Registrando agent types no agent-registry …")
    for at in AGENT_TYPES:
        body   = {k: v for k, v in at.items()}
        status, resp = http_post("/v1/agent-types", body)
        if status == 201:
            ok(f"AgentType {at['agent_type_id']} criado")
        elif status == 409:
            # Already exists — PATCH to apply any config changes (e.g. pool reassignment).
            # The PATCH endpoint replaces pool associations and updates mutable fields.
            # This ensures existing environments converge to the current seed definition.
            patch_body   = {k: v for k, v in at.items() if k != "agent_type_id"}
            patch_status, patch_resp = http_patch(
                f"/v1/agent-types/{at['agent_type_id']}", patch_body
            )
            if patch_status in (200, 204):
                ok(f"AgentType {at['agent_type_id']} atualizado (PATCH {patch_status})")
            else:
                warn(
                    f"AgentType {at['agent_type_id']} já existia; PATCH retornou {patch_status} "
                    f"— {patch_resp}. Configuração pode estar desatualizada."
                )
        else:
            die(f"Erro ao criar agent type {at['agent_type_id']}: HTTP {status} — {resp}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: seed channel endpoints via agent-registry
# ─────────────────────────────────────────────────────────────────────────────

def seed_channel_endpoints() -> None:
    log("Registrando channel endpoints no agent-registry …")
    for ep in CHANNEL_ENDPOINTS:
        body = {
            "channel":    ep["channel"],
            "identifier": ep["identifier"],
            "pool_id":    ep["pool_id"],
            "label":      ep.get("label", ep["identifier"]),
            "active":     True,
        }
        status, resp = http_post("/v1/channel-endpoints", body)
        if status == 201:
            ok(f"ChannelEndpoint {ep['channel']}/{ep['identifier']} → {ep['pool_id']}")
        elif status == 409:
            # Already exists — idempotent, skip
            ok(f"ChannelEndpoint {ep['channel']}/{ep['identifier']} já existe (skip)")
        else:
            warn(f"ChannelEndpoint {ep['channel']}/{ep['identifier']}: HTTP {status} — {resp}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("━" * 57)
    print("  PlugHub — seed.py  (ambiente full / integração)")
    print("━" * 57)
    print()
    log(f"AGENT_REGISTRY_URL : {REGISTRY_URL}")
    log(f"TENANT_ID          : {TENANT_ID}")
    print()

    wait_for_registry()
    print()

    seed_pools()
    print()

    seed_agent_types()
    print()

    seed_channel_endpoints()
    print()

    print("━" * 57)
    print("  Seed concluído com sucesso!")
    print()
    print("  Pools:        demo_ia · sac_ia · fila_humano · retencao_humano · avaliacao_ia")
    print("  Agent types:  agente_demo_ia_v1 · agente_sac_ia_v1")
    print("                agente_fila_v1 · agente_retencao_humano_v1")
    print("                agente_avaliacao_v1  (pool: avaliacao_ia — pós-sessão apenas)")
    print()
    print("  Redis instances: registradas pelo orchestrator-bridge InstanceBootstrap")
    print("  (billing por instância configurada → Agent Registry = source of truth)")
    print()
    print("  WebChat demo_ia  → http://localhost:8010  (canal: demo_ia)")
    print("  WebChat sac_ia   → http://localhost:8010  (canal: sac_ia)")
    print("  Agent Assist UI  → http://localhost:5173  (pool: retencao_humano)")
    print("  Platform UI      → http://localhost:5174")
    print("━" * 57)
    print()


if __name__ == "__main__":
    main()
