#!/usr/bin/env bash
# setup.sh — PlugHub first-time setup for Ubuntu with PM2
#
# Runs once per machine. Safe to re-run (all steps are idempotent).
#
# What it does:
#   1. Checks prerequisites (Node, Python, Docker, PM2)
#   2. Installs PM2 globally if missing
#   3. Installs Node dependencies and builds TypeScript packages
#   4. Installs Python packages (all services) into a virtualenv or system pip
#   5. Creates Kafka topics
#   6. Initialises ClickHouse schema
#   7. Runs Prisma DB migrations (agent-registry)
#
# Usage:
#   bash scripts/linux/setup.sh
#
# Prerequisites (install before running):
#   - Node.js 20+    →  https://nodejs.org  or  nvm install 20
#   - Python 3.11+   →  sudo apt install python3.11 python3.11-venv python3-pip
#   - Docker + Compose plugin  →  https://docs.docker.com/engine/install/ubuntu/
#   - Infrastructure running   →  docker compose -f docker-compose.infra.yml up -d

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

info()    { printf "${CYAN}[setup]${RESET} %s\n" "$*"; }
success() { printf "${GREEN}[ok]${RESET}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[warn]${RESET}  %s\n" "$*"; }
die()     { printf "${RED}[error]${RESET} %s\n" "$*" >&2; exit 1; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PlugHub — Ambiente de Demo (Ubuntu + PM2)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 0: prerequisites check ───────────────────────────────────────────────
info "Verificando pré-requisitos…"

command -v node  >/dev/null 2>&1 || die "Node.js não encontrado. Instale: https://nodejs.org ou nvm install 20"
command -v npm   >/dev/null 2>&1 || die "npm não encontrado (deveria vir com Node.js)."
command -v python3 >/dev/null 2>&1 || die "Python 3 não encontrado. sudo apt install python3.11"
command -v pip3  >/dev/null 2>&1 || command -v pip >/dev/null 2>&1 || die "pip não encontrado. sudo apt install python3-pip"
command -v docker >/dev/null 2>&1 || die "Docker não encontrado. https://docs.docker.com/engine/install/ubuntu/"

NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
PYTHON_VER=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1-2)

[ "$NODE_VER" -ge 20 ] || die "Node.js 20+ obrigatório (encontrado: v${NODE_VER})"

success "Node $(node --version)  |  Python ${PYTHON_VER}  |  Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# ── Step 1: PM2 ───────────────────────────────────────────────────────────────
info "Verificando PM2…"
if ! command -v pm2 >/dev/null 2>&1; then
  info "Instalando PM2 globalmente…"
  npm install -g pm2
  success "PM2 instalado: $(pm2 --version)"
else
  success "PM2 já instalado: $(pm2 --version)"
fi

# ── Step 2: Node packages — install + build ───────────────────────────────────
info "Instalando dependências Node e compilando TypeScript…"
info "  (ordem respeita dependências internas via file: references)"

# Packages that need install + tsc build, in strict dependency order:
#   schemas (base) → sdk, skill-flow-engine, mcp-server, agent-registry
#   → skill-flow-service (depends on skill-flow-engine)
# UIs run in dev mode via vite — only need npm install, no tsc build.
BUILD_PACKAGES=(
  "packages/schemas"
  "packages/sdk"
  "packages/skill-flow-engine"
  "packages/mcp-server-plughub"
  "packages/agent-registry"
  "packages/e2e-tests/services/skill-flow-service"
)

INSTALL_ONLY_PACKAGES=(
  "packages/e2e-tests"
  "packages/agent-assist-ui"
  "packages/dashboard/ui"
)

for pkg in "${BUILD_PACKAGES[@]}"; do
  dir="$ROOT/$pkg"
  if [ -f "$dir/package.json" ]; then
    info "  npm install + build  →  $pkg"
    # Remove local @plughub packages from node_modules before installing so that
    # file: dependencies are always re-copied from the current dist/ — npm does
    # not re-copy file: deps on subsequent installs if node_modules already exists.
    rm -rf "$dir/node_modules/@plughub"
    npm install --prefix "$dir" --silent \
      || die "npm install falhou em $pkg"
    (cd "$dir" && npm run build --silent) \
      || die "tsc build falhou em $pkg — veja o erro acima"
    success "  $pkg"
  else
    warn "  Ignorando $pkg (sem package.json)"
  fi
done

for pkg in "${INSTALL_ONLY_PACKAGES[@]}"; do
  dir="$ROOT/$pkg"
  if [ -f "$dir/package.json" ]; then
    info "  npm install          →  $pkg"
    npm install --prefix "$dir" --silent \
      || warn "npm install falhou em $pkg"
    success "  $pkg"
  else
    warn "  Ignorando $pkg (sem package.json)"
  fi
done

# ── Step 3: Python packages ───────────────────────────────────────────────────
info "Instalando pacotes Python…"

# Detect pip command
PIP="pip3"
command -v pip3 >/dev/null 2>&1 || PIP="pip"

# Optional: use a virtualenv if PLUGHUB_PYTHON points to one
if [ -n "${PLUGHUB_PYTHON:-}" ] && [[ "$PLUGHUB_PYTHON" == *".venv"* ]]; then
  VENV_DIR="$ROOT/.venv"
  if [ ! -d "$VENV_DIR" ]; then
    info "  Criando virtualenv em .venv…"
    python3 -m venv "$VENV_DIR"
  fi
  PIP="$VENV_DIR/bin/pip"
  success "  Usando virtualenv: $VENV_DIR"
fi

PYTHON_PACKAGES=(
  "packages/ai-gateway"
  "packages/routing-engine"
  "packages/rules-engine"
  "packages/channel-gateway"
  "packages/conversation-writer"
  "packages/clickhouse-consumer"
  "packages/orchestrator-bridge"
  "packages/dashboard/api"
)

for pkg in "${PYTHON_PACKAGES[@]}"; do
  dir="$ROOT/$pkg"
  if [ -f "$dir/pyproject.toml" ]; then
    info "  pip install  →  $pkg"
    "$PIP" install -e "$dir" --quiet || \
      "$PIP" install -e "$dir" --quiet --break-system-packages || \
      warn "  Falha ao instalar $pkg — verifique manualmente"
    success "  $pkg"
  else
    warn "  Ignorando $pkg (sem pyproject.toml)"
  fi
done

# ── Step 4: Kafka topics ──────────────────────────────────────────────────────
info "Criando Kafka topics (idempotente)…"

KAFKA_CONTAINER="plughub-kafka"
KAFKA_BIN="/opt/kafka/bin/kafka-topics.sh"
BOOTSTRAP="localhost:9092"

if docker ps --format "{{.Names}}" | grep -q "^${KAFKA_CONTAINER}$"; then
  TOPICS=(
    "conversations.inbound"
    "conversations.routed"
    "conversations.completed"
    "conversations.events"
    "agent.lifecycle"
    "conference.joined"
    "insights.events"
    "outbound.notifications"
    "rules.triggers"
  )

  for topic in "${TOPICS[@]}"; do
    if docker exec "$KAFKA_CONTAINER" "$KAFKA_BIN" \
        --bootstrap-server "$BOOTSTRAP" --list 2>/dev/null | grep -q "^${topic}$"; then
      success "  topic '$topic' (já existe)"
    else
      docker exec "$KAFKA_CONTAINER" "$KAFKA_BIN" \
        --bootstrap-server "$BOOTSTRAP" \
        --create --topic "$topic" \
        --partitions 3 --replication-factor 1 \
        --if-not-exists 2>/dev/null \
        && success "  topic '$topic' criado" \
        || warn "  Não foi possível criar topic '$topic' — Kafka container '$KAFKA_CONTAINER' disponível?"
    fi
  done
else
  warn "Container '$KAFKA_CONTAINER' não está rodando. Inicie a infra e execute:"
  warn "  docker compose -f docker-compose.infra.yml up -d"
  warn "  Depois recrie os topics manualmente ou execute este script novamente."
fi

# ── Step 5: ClickHouse schema ──────────────────────────────────────────────────
info "Inicializando schema do ClickHouse…"

CH_CONTAINER="plughub-clickhouse"
SQL_FILE="$ROOT/scripts/init-clickhouse.sql"

if docker ps --format "{{.Names}}" | grep -q "^${CH_CONTAINER}$"; then
  if [ -f "$SQL_FILE" ]; then
    docker exec "$CH_CONTAINER" clickhouse-client \
      --user plughub --password plughub \
      --multiquery \
      --query "$(cat "$SQL_FILE")" 2>/dev/null \
      && success "ClickHouse schema aplicado" \
      || warn "Erro ao aplicar ClickHouse schema — pode já existir (normal)"
  else
    warn "Arquivo $SQL_FILE não encontrado — schema ClickHouse não aplicado"
  fi
else
  warn "Container '$CH_CONTAINER' não está rodando — schema ClickHouse não aplicado"
  warn "Execute após iniciar a infra: docker exec plughub-clickhouse clickhouse-client --user plughub --password plughub --multiquery --query \"\$(cat scripts/init-clickhouse.sql)\""
fi

# ── Step 6: Prisma migrations ─────────────────────────────────────────────────
info "Executando Prisma migrations (agent-registry)…"

REGISTRY_DIR="$ROOT/packages/agent-registry"

if [ -f "$REGISTRY_DIR/package.json" ] && grep -q "prisma" "$REGISTRY_DIR/package.json" 2>/dev/null; then
  # Check DATABASE_URL is set and Postgres is reachable
  DB_URL="${DATABASE_URL:-postgresql://plughub:plughub@localhost:5432/plughub}"
  if docker ps --format "{{.Names}}" | grep -q "plughub-postgres" 2>/dev/null || \
     pg_isready -h localhost -p 5432 -U plughub >/dev/null 2>&1; then
    (cd "$REGISTRY_DIR" && DATABASE_URL="$DB_URL" npx prisma migrate deploy --schema prisma/schema.prisma 2>/dev/null) \
      && success "Prisma migrations aplicadas" \
      || warn "Prisma migrate falhou — tente manualmente em packages/agent-registry"
  else
    warn "PostgreSQL não acessível — migrations não aplicadas"
    warn "Execute depois: cd packages/agent-registry && npx prisma migrate deploy"
  fi
else
  warn "Prisma não encontrado em agent-registry — ignorando"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "${GREEN}  Setup concluído!${RESET}\n"
echo ""
echo "  Próximos passos:"
echo "    1. Edite scripts/linux/set-env.sh com sua PLUGHUB_ANTHROPIC_API_KEY"
echo "    2. source scripts/linux/set-env.sh"
echo "    3. bash scripts/linux/seed-demo.sh   # seed do demo (executar uma vez)"
echo "    4. pm2 start ecosystem.config.js"
echo "    5. pm2 logs                          # acompanhar logs"
echo "    6. pm2 status                        # verificar status dos serviços"
echo ""
echo "  UIs:"
echo "    Agent Assist  →  http://localhost:5173"
echo "    Dashboard     →  http://localhost:5174"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
