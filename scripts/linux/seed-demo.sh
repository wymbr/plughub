#!/usr/bin/env bash
# seed-demo.sh — Seed dos 4 pools padronizados do PlugHub Demo
#
# O que faz:
#   1. Aguarda o agent-registry estar saudável (até 60s)
#   2. Registra pools e instâncias no Redis para que o Routing Engine
#      saiba que existem slots disponíveis:
#        demo_ia        → demo-ia-001   (agente_demo_ia_v1,  stateless, max 10)
#        sac_ia         → sac-ia-001    (agente_sac_ia_v1,   stateless, max 10)
#        fila_humano    → fila-ia-001   (agente_fila_v1,     stateless, max 50)
#        retencao_humano → retencao-humano-001 (agente_retencao_humano_v1, stateful, max 3)
#
#   Os fluxos IA são carregados via YAML fallback (SKILLS_DIR) — não é necessário
#   registrar skills no Agent Registry para ambiente de desenvolvimento.
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
TENANT_ID="${TENANT_ID:-tenant_demo}"

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

# ── Step 2: register pools and agent instances in Redis ───────────────────────
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

  # ── Pool configs (TTL 24h) ────────────────────────────────────────────────
  for pool_id in demo_ia sac_ia fila_humano; do
    $REDIS_CLI_CMD SET "${TENANT_ID}:pool_config:${pool_id}" \
      "{\"pool_id\":\"${pool_id}\",\"tenant_id\":\"${TENANT_ID}\",\"channel_types\":[\"webchat\",\"whatsapp\"],\"sla_target_ms\":300000,\"routing_expression\":${ROUTING_EXPR},\"competency_weights\":{},\"aging_factor\":0.4,\"breach_factor\":0.8,\"remote_sites\":[],\"is_human_pool\":false}" \
      EX 86400 >/dev/null \
      && success "Pool config ${pool_id} registrado"
  done

  $REDIS_CLI_CMD SET "${TENANT_ID}:pool_config:retencao_humano" \
    "{\"pool_id\":\"retencao_humano\",\"tenant_id\":\"${TENANT_ID}\",\"channel_types\":[\"webchat\",\"whatsapp\"],\"sla_target_ms\":300000,\"routing_expression\":${ROUTING_EXPR},\"competency_weights\":{},\"aging_factor\":0.4,\"breach_factor\":0.8,\"remote_sites\":[],\"is_human_pool\":true}" \
    EX 86400 >/dev/null \
    && success "Pool config retencao_humano registrado"

  # ── Pool sets ─────────────────────────────────────────────────────────────
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pools" "demo_ia" "sac_ia" "fila_humano" "retencao_humano" >/dev/null \
    && success "Pool set registrado: demo_ia, sac_ia, fila_humano, retencao_humano"

  # ── Agent instances (sem TTL — instâncias demo são permanentes) ──────────────
  # Instâncias de agentes IA têm o TTL renovado pelo mark_busy (KEEPTTL).
  # A instância humana (retencao-humano-001) nunca recebe mark_busy enquanto
  # estiver em fila, portanto não deve ter TTL — caso contrário expira após 24h
  # e o routing engine não encontra capacidade.
  #
  # Para cada instância também gravamos um template permanente
  # (instance_template:{id}) usado por refreshPoolInstances para recriar
  # a chave se ela expirar ou for deletada acidentalmente.

  INST_DEMO_IA="{\"instance_id\":\"demo-ia-001\",\"agent_type_id\":\"agente_demo_ia_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"demo_ia\",\"pools\":[\"demo_ia\"],\"execution_model\":\"stateless\",\"max_concurrent\":10,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}"
  INST_SAC_IA="{\"instance_id\":\"sac-ia-001\",\"agent_type_id\":\"agente_sac_ia_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"sac_ia\",\"pools\":[\"sac_ia\"],\"execution_model\":\"stateless\",\"max_concurrent\":10,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}"
  INST_FILA="{\"instance_id\":\"fila-ia-001\",\"agent_type_id\":\"agente_fila_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"fila_humano\",\"pools\":[\"fila_humano\"],\"execution_model\":\"stateless\",\"max_concurrent\":50,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}"
  INST_RETENCAO="{\"instance_id\":\"retencao-humano-001\",\"agent_type_id\":\"agente_retencao_humano_v1\",\"tenant_id\":\"${TENANT_ID}\",\"pool_id\":\"retencao_humano\",\"pools\":[\"retencao_humano\"],\"execution_model\":\"stateful\",\"max_concurrent\":3,\"current_sessions\":0,\"status\":\"ready\",\"registered_at\":\"${NOW}\"}"

  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:demo-ia-001"         "$INST_DEMO_IA"    >/dev/null && success "Instância demo-ia-001 registrada (pool: demo_ia)"
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:sac-ia-001"          "$INST_SAC_IA"    >/dev/null && success "Instância sac-ia-001 registrada (pool: sac_ia)"
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:fila-ia-001"         "$INST_FILA"      >/dev/null && success "Instância fila-ia-001 registrada (pool: fila_humano)"
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance:retencao-humano-001" "$INST_RETENCAO"  >/dev/null && success "Instância retencao-humano-001 registrada (pool: retencao_humano)"

  # ── Templates permanentes (usados por refreshPoolInstances para auto-recovery) ──
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance_template:demo-ia-001"         "$INST_DEMO_IA"   >/dev/null
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance_template:sac-ia-001"          "$INST_SAC_IA"    >/dev/null
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance_template:fila-ia-001"         "$INST_FILA"      >/dev/null
  $REDIS_CLI_CMD SET "${TENANT_ID}:instance_template:retencao-humano-001" "$INST_RETENCAO"  >/dev/null
  success "Templates de instância gravados (sem TTL)"

  # ── Pool rosters permanentes (mapeamento pool → instance_ids, sem TTL) ───────
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool_roster:demo_ia"        "demo-ia-001"         >/dev/null
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool_roster:sac_ia"         "sac-ia-001"          >/dev/null
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool_roster:fila_humano"    "fila-ia-001"         >/dev/null
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool_roster:retencao_humano" "retencao-humano-001" >/dev/null
  success "Pool rosters gravados (sem TTL)"

  # ── Pool instance sets (dinâmicos — gerenciados pelo routing engine) ──────────
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool:demo_ia:instances"        "demo-ia-001"         >/dev/null && success "Pool demo_ia:instances → demo-ia-001"
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool:sac_ia:instances"         "sac-ia-001"          >/dev/null && success "Pool sac_ia:instances → sac-ia-001"
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool:fila_humano:instances"    "fila-ia-001"         >/dev/null && success "Pool fila_humano:instances → fila-ia-001"
  $REDIS_CLI_CMD SADD "${TENANT_ID}:pool:retencao_humano:instances" "retencao-humano-001" >/dev/null && success "Pool retencao_humano:instances → retencao-humano-001"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "${GREEN}  Seed concluído!${RESET}\n"
echo ""
echo "  Pools registrados: demo_ia, sac_ia, fila_humano, retencao_humano"
echo ""
echo "  Próximos passos:"
echo ""
echo "    Agent Assist (humano) →  http://localhost:5173?agent=Carlos&pool=retencao_humano"
echo ""
echo "    Cliente demo_ia (IVR) →  wscat -c ws://localhost:8010/ws/chat/demo_ia"
echo "    Cliente sac_ia  (LLM) →  wscat -c ws://localhost:8010/ws/chat/sac_ia"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
