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
REDIS_URL    = os.environ.get("REDIS_URL",           "redis://redis:6379")
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
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


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
# Redis helper  (pure Python, no redis-py dependency)
# ─────────────────────────────────────────────────────────────────────────────

class RedisConn:
    """Minimal synchronous Redis client via raw TCP (RESP protocol)."""

    def __init__(self, url: str) -> None:
        import socket
        url   = url.replace("redis://", "")
        host, _, port_s = url.partition(":")
        port  = int(port_s) if port_s else 6379
        self._s = socket.create_connection((host, port), timeout=10)
        self._f = self._s.makefile("rb")

    def _send(self, *args: str | bytes) -> None:
        parts = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = a.encode() if isinstance(a, str) else a
            parts.append(f"${len(b)}\r\n".encode())
            parts.append(b + b"\r\n")
        self._s.sendall(b"".join(parts))

    def _read(self):  # noqa: ANN001
        line = self._f.readline().decode().rstrip("\r\n")
        t, data = line[0], line[1:]
        if t == "+":
            return data
        if t == "-":
            raise RuntimeError(data)
        if t == ":":
            return int(data)
        if t == "$":
            n = int(data)
            if n == -1:
                return None
            blob = self._f.read(n + 2)
            return blob[:-2].decode()
        if t == "*":
            n = int(data)
            return [self._read() for _ in range(n)]
        raise RuntimeError(f"Unknown RESP type {t!r}")

    def set(self, key: str, value: str) -> str:
        self._send("SET", key, value)
        return self._read()

    def sadd(self, key: str, *members: str) -> int:
        self._send("SADD", key, *members)
        return self._read()

    def close(self) -> None:
        self._s.close()


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
    },
]

# Pools that will be sent to the registry API (routing_expression + is_human_pool
# are Routing Engine Redis fields, not registry fields — strip them for the POST body).
REGISTRY_POOL_FIELDS = {"pool_id", "description", "channel_types", "sla_target_ms"}

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
        "agent_type_id":           "agente_avaliacao_v1",
        "framework":               "plughub-native",
        "execution_model":         "stateless",
        "role":                    "executor",
        "max_concurrent_sessions": 20,
        "pools":                   ["demo_ia"],
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
            warn(f"Pool {pool['pool_id']} já existia — ignorado")
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
            warn(f"AgentType {at['agent_type_id']} já existia — ignorado")
        else:
            die(f"Erro ao criar agent type {at['agent_type_id']}: HTTP {status} — {resp}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: write Redis keys (pool configs, instances, rosters, pool sets)
# ─────────────────────────────────────────────────────────────────────────────

def seed_redis() -> None:
    """
    Writes pool configuration data to Redis.

    Agent instance registration (instance keys, pool:*:instances sets) is now
    handled by the orchestrator-bridge InstanceBootstrap module, which derives
    instances from Agent Registry configuration at startup and keeps them alive
    via a 15-second heartbeat loop.

    This function only writes:
      - pool_config:{pool_id}  — routing scores and SLA config (no TTL)
      - {tenant}:pools         — global set of known pool IDs
    """
    log(f"Conectando ao Redis em {REDIS_URL} …")
    try:
        r = RedisConn(REDIS_URL)
    except Exception as e:
        die(f"Não foi possível conectar ao Redis: {e}")

    # ── Pool configs (TTL-free — routing engine reads these) ─────────────────
    log("Gravando pool configs no Redis …")
    for pool in POOLS:
        key   = f"{TENANT_ID}:pool_config:{pool['pool_id']}"
        value = json.dumps({
            "pool_id":             pool["pool_id"],
            "tenant_id":           TENANT_ID,
            "channel_types":       pool["channel_types"],
            "sla_target_ms":       pool["sla_target_ms"],
            "routing_expression":  pool["routing_expression"],
            "competency_weights":  {},
            "aging_factor":        0.4,
            "breach_factor":       0.8,
            "remote_sites":        [],
            "is_human_pool":       pool.get("is_human_pool", False),
        })
        r.set(key, value)
        ok(f"pool_config:{pool['pool_id']}")

    # ── Global pools set ─────────────────────────────────────────────────────
    pool_ids = [p["pool_id"] for p in POOLS]
    r.sadd(f"{TENANT_ID}:pools", *pool_ids)
    ok(f"pools set: {', '.join(pool_ids)}")

    r.close()


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
    log(f"REDIS_URL          : {REDIS_URL}")
    log(f"TENANT_ID          : {TENANT_ID}")
    print()

    wait_for_registry()
    print()

    seed_pools()
    print()

    seed_agent_types()
    print()

    seed_redis()
    print()

    print("━" * 57)
    print("  Seed concluído com sucesso!")
    print()
    print("  Pools:        demo_ia · sac_ia · fila_humano · retencao_humano")
    print("  Agent types:  agente_demo_ia_v1 · agente_sac_ia_v1")
    print("                agente_fila_v1 · agente_retencao_humano_v1")
    print("                agente_avaliacao_v1")
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
