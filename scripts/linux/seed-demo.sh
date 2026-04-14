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

# ── Step 3: register agent instances in Redis ─────────────────────────────────
# The Routing Engine tracks available agent slots in Redis.
# For demo purposes we register one AI orchestrator instance and two human agents.
# Key schema (from routing-engine): agent:{agent_type_id}:{instance_id}
# The engine reads these to determine pool capacity.
#
# Note: in a real deployment the Bridge/SDK publishes agent_login/agent_ready
# via Kafka; these Redis writes are only for demo convenience.

info "Registrando instâncias de agente no Redis para o demo…"

REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
REDIS_HOST=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f1)
REDIS_PORT=$(echo "$REDIS_URL" | sed 's|redis://||' | cut -d: -f2)
REDIS_CLI_CMD=""

# Try docker-based redis-cli first, then local redis-cli
if docker ps --format "{{.Names}}" | grep -q "plughub-redis" 2>/dev/null; then
  REDIS_CLI_CMD="docker exec plughub-redis redis-cli"
elif command -v redis-cli >/dev/null 2>&1; then
  REDIS_CLI_CMD="redis-cli -h $REDIS_HOST -p $REDIS_PORT"
else
  warn "redis-cli não encontrado — instâncias de agente não registradas no Redis"
  warn "O Routing Engine pode não alocar sessões até que um agente faça agent_login via SDK."
  warn "Para registrar manualmente: redis-cli HSET agent:orquestrador_demo_v1:inst-01 status ready pool_id demo_ia tenant_id $TENANT_ID"
fi

if [ -n "$REDIS_CLI_CMD" ]; then
  # Register AI orchestrator instance (plughub-native — active when Bridge is running)
  $REDIS_CLI_CMD HSET "agent:orquestrador_demo_v1:inst-01" \
    status "ready" \
    pool_id "demo_ia" \
    tenant_id "$TENANT_ID" \
    agent_type_id "orquestrador_demo_v1" \
    framework "plughub-native" \
    max_concurrent_sessions "10" \
    current_sessions "0" \
    >/dev/null && success "Instância orquestrador_demo_v1:inst-01 registrada (pool: demo_ia)"

  # Register two human agent instances for the suporte_humano pool
  for i in 01 02; do
    $REDIS_CLI_CMD HSET "agent:agente_suporte_humano_v1:inst-${i}" \
      status "ready" \
      pool_id "suporte_humano" \
      tenant_id "$TENANT_ID" \
      agent_type_id "agente_suporte_humano_v1" \
      framework "human" \
      max_concurrent_sessions "3" \
      current_sessions "0" \
      >/dev/null && success "Instância agente_suporte_humano_v1:inst-${i} registrada (pool: suporte_humano)"
  done

  # Register pool entries
  $REDIS_CLI_CMD SADD "pools:$TENANT_ID" "demo_ia" "suporte_humano" >/dev/null \
    && success "Pools registrados no Redis: demo_ia, suporte_humano"
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
