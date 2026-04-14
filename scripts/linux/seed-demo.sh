#!/usr/bin/env bash
# seed-demo.sh — Seed do Demo 2 (fluxo LLM com sentimento em tempo real)
#
# O que faz:
#   1. Aguarda o agent-registry estar saudável (até 60s)
#   2. Executa packages/e2e-tests/fixtures/seed_demo.ts via ts-node
#      → cria pools demo_ia e suporte_humano
#      → registra skill_demo_chat_v1 (skill flow com reason step)
#      → registra agentTypes orquestrador_demo_v1 e agente_suporte_humano_v1
#   3. Registra instâncias de agente no Redis para que o Routing Engine
#      saiba que existem slots disponíveis nos dois pools
#
# Uso:
#   bash scripts/linux/seed-demo.sh
#
# Pré-requisitos:
#   - pm2 start ecosystem.config.js  (ou agent-registry rodando na porta 3300)
#   - AGENT_REGISTRY_URL e TENANT_ID exportados (usa defaults abaixo se não definidos)

set -euo pipefail

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

info()    { printf "${CYAN}[seed]${RESET}  %s\n" "$*"; }
success() { printf "${GREEN}[ok]${RESET}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[warn]${RESET}  %s\n" "$*"; }
die()     { printf "${RED}[error]${RESET} %s\n" "$*" >&2; exit 1; }

REGISTRY_URL="${AGENT_REGISTRY_URL:-http://localhost:3300}"
TENANT_ID="${TENANT_ID:-default}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PlugHub — Seed Demo 2 (LLM + Sentimento)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Agent Registry URL : $REGISTRY_URL"
info "Tenant ID          : $TENANT_ID"
echo ""

# ── Step 1: wait for agent-registry ──────────────────────────────────────────
info "Aguardando agent-registry ficar disponível…"

MAX_WAIT=60
elapsed=0
until curl -sf "$REGISTRY_URL/v1/health" >/dev/null 2>&1; do
  if [ "$elapsed" -ge "$MAX_WAIT" ]; then
    die "agent-registry não respondeu em ${MAX_WAIT}s. Verifique: pm2 logs agent-registry"
  fi
  printf "."
  sleep 2
  elapsed=$((elapsed + 2))
done
echo ""
success "agent-registry disponível"

# ── Step 2: run seed_demo.ts ──────────────────────────────────────────────────
info "Executando seed_demo.ts…"

E2E_DIR="$ROOT/packages/e2e-tests"

if [ ! -d "$E2E_DIR/node_modules" ]; then
  info "  Instalando dependências de e2e-tests…"
  npm install --prefix "$E2E_DIR" --silent
fi

# ts-node from e2e-tests node_modules
TS_NODE="$E2E_DIR/node_modules/.bin/ts-node"
if [ ! -f "$TS_NODE" ]; then
  info "  ts-node não encontrado — instalando…"
  npm install --prefix "$E2E_DIR" ts-node --save-dev --silent
fi

AGENT_REGISTRY_URL="$REGISTRY_URL" \
TENANT_ID="$TENANT_ID" \
  "$TS_NODE" \
  --project "$E2E_DIR/tsconfig.json" \
  "$E2E_DIR/fixtures/seed_demo.ts" \
  && success "seed_demo.ts concluído" \
  || die "seed_demo.ts falhou — veja o erro acima"

# ── Step 3: register pools and agent instances in Redis ───────────────────────
# The Routing Engine reads pool configs and agent instances directly from Redis.
# Key schema (routing-engine registry.py):
#   {tenant}:pool_config:{pool_id}         — PoolConfig JSON (TTL 24h)
#   {tenant}:pool:{pool_id}:instances      — SET of instance_ids
#   {tenant}:pools                         — SET of all pool_ids
#   {tenant}:instance:{instance_id}        — AgentInstance JSON (TTL 24h)
#
# Note: in production the agent SDK publishes agent_login/agent_ready via Kafka
# and the agent-registry publishes pool configs via agent.registry.events.
# These Redis writes replace both for demo convenience.

info "Registrando pools e instâncias de agente no Redis…"

REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
REDIS_HOST=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f1)
REDIS_PORT=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f2)
REDIS_CLI_CMD=""

if command -v redis-cli >/dev/null 2>&1; then
  REDIS_CLI_CMD="redis-cli -h $REDIS_HOST -p $REDIS_PORT"
elif docker ps --format "{{.Names}}" 2>/dev/null | grep -q "plughub-redis"; then
  REDIS_CLI_CMD="docker exec plughub-redis redis-cli"
else
  warn "redis-cli não encontrado — instale com: sudo apt install redis-tools -y"
fi

if [ -n "$REDIS_CLI_CMD" ]; then
  NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  ROUTING_EXPR='{"weight_sla":0.4,"weight_wait":0.2,"weight_tier":0.2,"weight_churn":0.1,"weight_business":0.1}'

  # ── Pool configs (lidos por get_pool / _get_pool_config) ──────────────────
  $REDIS_CLI_CMD SET "${TENANT_ID}:pool_config:demo_ia" \
    "{\"pool_id\":\"demo_ia\",\"tenant_id\":\"${TENANT_ID}\",\"channel_types\":[\"chat\",\"whatsapp\"],\"sla_target_ms\":300000,\"routing_expression\":${ROUTING_EXPR},\"competency_weights\":{},\"aging_factor\":0.4,\"breach_factor\":0.8,\"remote_sites\":[],\"is_human_pool\":false}" \
    EX 86400 >/dev/null \
    && success "Pool config demo_ia registrado"

  $REDIS_CLI_CMD SET "${TENANT_ID}:pool_config:suporte_humano" \
    "{\"pool_id\":\"suporte_humano\",\"tenant_id\":\"${TENANT_ID}\",\"channel_types\":[\"chat\",\"whatsapp\"],\"sla_target_ms\":300000,\"routing_expression\":${ROUTING_EXPR},\"competency_weights\":{},\"aging_factor\":0.4,\"breach_factor\":0.8,\"remote_sites\":[],\"is_human_pool\":true}" \
    EX 86400 >/dev/null \
    && success "Pool config suporte_humano registrado"

  # ── Pool sets ─────────────────────────────────────────────────────────────
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pools" "demo_ia" "suporte_humano" >/dev/null \
    && success "Pool set registrado: demo_ia, suporte_humano"

  # ── Agent instances (AgentInstance JSON — lidos por get_ready_instances) ──
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:inst-01" \
    "{\"instance_id\":\"inst-01\",\"agent_type_id\":\"orquestrador_demo_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"demo_ia\",\"pools\":[\"demo_ia\"],\"execution_model\":\"stateless\",\"max_concurrent\":10,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}" \
    EX 86400 >/dev/null \
    && success "Instância inst-01 registrada (pool: demo_ia)"

  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:inst-02" \
    "{\"instance_id\":\"inst-02\",\"agent_type_id\":\"agente_suporte_humano_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"suporte_humano\",\"pools\":[\"suporte_humano\"],\"execution_model\":\"stateful\",\"max_concurrent\":3,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}" \
    EX 86400 >/dev/null \
    && success "Instância inst-02 registrada (pool: suporte_humano)"

  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:inst-03" \
    "{\"instance_id\":\"inst-03\",\"agent_type_id\":\"agente_suporte_humano_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"suporte_humano\",\"pools\":[\"suporte_humano\"],\"execution_model\":\"stateful\",\"max_concurrent\":3,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}" \
    EX 86400 >/dev/null \
    && success "Instância inst-03 registrada (pool: suporte_humano)"

  # ── Pool instance sets ────────────────────────────────────────────────────
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool:demo_ia:instances" "inst-01" >/dev/null \
    && success "Pool demo_ia:instances → inst-01"
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool:suporte_humano:instances" "inst-02" "inst-03" >/dev/null \
    && success "Pool suporte_humano:instances → inst-02, inst-03"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "${GREEN}  Seed concluído!${RESET}\n"
echo ""
echo "  O fluxo demo está pronto. Para testar:"
echo ""
echo "    Webchat (cliente)   →  conecte no Channel Gateway (porta configurada)"
echo "    Agent Assist (humano) →  http://localhost:5173?pool_id=suporte_humano"
echo "    Dashboard           →  http://localhost:5174"
echo ""
echo "  Para o fluxo LLM (Demo 2), o PLUGHUB_ENTRY_POINT_POOL_ID deve ser 'demo_ia'."
echo "  Para o fluxo IVR (Demo 1), use 'sac_ia' e execute seed.ts no lugar deste script."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
