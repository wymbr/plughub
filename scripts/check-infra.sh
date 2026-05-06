#!/usr/bin/env bash
# scripts/check-infra.sh
# Valida que todos os serviços de infraestrutura do PlugHub estão operacionais.
# Uso: bash scripts/check-infra.sh

set -euo pipefail

PASS="\033[0;32m[OK]\033[0m"
FAIL="\033[0;31m[FAIL]\033[0m"
INFO="\033[0;34m[INFO]\033[0m"

errors=0

check() {
  local label="$1"
  local cmd="$2"
  local hint="$3"

  if eval "$cmd" &>/dev/null; then
    printf "  %b  %s\n" "$PASS" "$label"
  else
    printf "  %b  %s\n" "$FAIL" "$label"
    printf "       %b  %s\n" "$INFO" "$hint"
    errors=$((errors + 1))
  fi
}

echo ""
echo "PlugHub — Verificação de Infraestrutura"
echo "═══════════════════════════════════════"
echo ""

# ─────────────────────────────────────────────
# Redis
# ─────────────────────────────────────────────
echo "Redis (localhost:6379)"
check \
  "ping respondeu PONG" \
  "docker exec plughub-redis redis-cli ping | grep -q PONG" \
  "Verifique: docker compose -f docker-compose.infra.yml up -d redis"

check \
  "escrita e leitura de chave" \
  "docker exec plughub-redis redis-cli set plughub:healthcheck ok EX 10 | grep -q OK && \
   docker exec plughub-redis redis-cli get plughub:healthcheck | grep -q ok" \
  "Redis está up mas não aceita escrita — verifique permissões"

echo ""

# ─────────────────────────────────────────────
# Kafka
# ─────────────────────────────────────────────
echo "Kafka (localhost:9092)"
check \
  "broker acessível" \
  "docker exec plughub-kafka /opt/kafka/bin/kafka-broker-api-versions.sh \
     --bootstrap-server localhost:9092" \
  "Verifique: docker compose -f docker-compose.infra.yml up -d kafka"

REQUIRED_TOPICS="conversations.inbound conversations.routed conversations.completed agent.lifecycle conference.joined"
for topic in $REQUIRED_TOPICS; do
  check \
    "topic '$topic' existe" \
    "docker exec plughub-kafka /opt/kafka/bin/kafka-topics.sh \
       --bootstrap-server localhost:9092 --list | grep -q '^${topic}$'" \
    "Recrie os topics: docker compose -f docker-compose.infra.yml up kafka-init"
done

echo ""

# ─────────────────────────────────────────────
# PostgreSQL
# ─────────────────────────────────────────────
echo "PostgreSQL (localhost:5432)"
check \
  "banco 'plughub' acessível" \
  "docker exec plughub-postgres pg_isready -U plughub -d plughub" \
  "Verifique: docker compose -f docker-compose.infra.yml up -d postgres"

check \
  "query SELECT 1 executada" \
  "docker exec plughub-postgres psql -U plughub -d plughub -c 'SELECT 1' | grep -q '1 row'" \
  "PostgreSQL up mas query falhou — verifique credenciais em .env.infra"

echo ""

# ─────────────────────────────────────────────
# ClickHouse
# ─────────────────────────────────────────────
echo "ClickHouse (localhost:8123)"
check \
  "HTTP /ping respondeu Ok." \
  "curl -sf http://localhost:8123/ping | grep -q 'Ok.'" \
  "Verifique: docker compose -f docker-compose.infra.yml up -d clickhouse"

check \
  "banco 'plughub_metrics' existe" \
  "curl -sf 'http://localhost:8123/?user=plughub&password=plughub&query=SHOW+DATABASES' | grep -q 'plughub_metrics'" \
  "Execute: bash scripts/init-clickhouse.sql ou rode o init manual"

check \
  "tabela 'rule_triggers' existe" \
  "curl -sf 'http://localhost:8123/?user=plughub&password=plughub&query=EXISTS+TABLE+plughub_metrics.rule_triggers' | grep -q '^1'" \
  "Execute: docker exec plughub-clickhouse clickhouse-client --user plughub --password plughub --query \"\$(cat scripts/init-clickhouse.sql)\""

check \
  "tabela 'session_outcomes' existe" \
  "curl -sf 'http://localhost:8123/?user=plughub&password=plughub&query=EXISTS+TABLE+plughub_metrics.session_outcomes' | grep -q '^1'" \
  "Execute o init-clickhouse.sql novamente"

echo ""

# ─────────────────────────────────────────────
# Resultado final
# ─────────────────────────────────────────────
echo "═══════════════════════════════════════"
if [ "$errors" -eq 0 ]; then
  printf "%b  Todos os serviços operacionais. Pronto para implementação.\n\n" "$PASS"
  exit 0
else
  printf "%b  %d verificação(ões) falharam. Corrija antes de continuar.\n\n" "$FAIL" "$errors"
  exit 1
fi
