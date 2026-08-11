#!/usr/bin/env bash
# infra/scripts/up.sh
#
# Subida RECONCILIADORA da stack demo. Use isto, não o botão Start do Docker
# Desktop.
#
# A diferença não é de gosto:
#   • `up -d`  compara cada container com o compose, RECRIA o que divergiu e
#     espera os gates `condition: service_healthy` do `depends_on`.
#   • Start / `compose start` apenas inicia o container que existe, com a
#     configuração que ele tinha quando foi criado, sem esperar health nenhum.
#
# Uma stack montada aos pedaços (`build X` + `up -d X`, serviço a serviço) vira
# um conjunto de containers de idades diferentes; só o `up -d` completo a
# reconcilia.
#
# O log vai para arquivo SEMPRE. O modo de falha que motivou este script foi
# mudo — serviço ausente sem mensagem —, e diagnosticar depois exige a saída da
# subida que falhou, que se perde no scrollback ou num recreate posterior.
#
# Uso:
#   ./infra/scripts/up.sh          # sobe e reconcilia
#   ./infra/scripts/up.sh --pull   # idem, atualizando imagens de infra

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.demo.yml")
LOG_DIR="$REPO_ROOT/.logs"
LOG="$LOG_DIR/up-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$LOG_DIR"
cd "$REPO_ROOT"

if [ "${1:-}" = "--pull" ]; then
  echo "── pull das imagens de infra ──────────────────────────────────────"
  "${COMPOSE[@]}" pull --ignore-buildable 2>&1 | tee -a "$LOG"
fi

echo "── up -d (reconcilia + espera health) ─────────────────────────────"
echo "   log: $LOG"
"${COMPOSE[@]}" up -d --scale e2e-runner=0 > >(tee -a "$LOG") 2>&1
RC=$?

echo
if [ "$RC" -ne 0 ]; then
  echo "❌ up -d falhou (exit=$RC). As linhas que nomeiam a causa:"
  grep -iE 'error|unhealthy|dependency failed|exited' "$LOG" | head -20
  echo
  echo "   Log completo: $LOG"
  echo "   NÃO suba os serviços na mão antes de ler o log — o conserto manual"
  echo "   apaga o estado que identifica a causa."
  exit "$RC"
fi

# exit 0 do `up` não é o veredicto: um serviço pode ter subido e morrido logo
# depois. Conferir o estado é um segundo teste, com ramo próprio.
echo "── conferência de estado ──────────────────────────────────────────"
ONESHOTS='auth-seed|config-seed|dialog-seed|eval-seed|kafka-init|minio-init|pricing-seed|e2e-runner'
DOWN="$("${COMPOSE[@]}" ps -a --format '{{.Service}} {{.State}}' \
        | grep -v ' running' | grep -vE "^($ONESHOTS) " || true)"

if [ -n "$DOWN" ]; then
  echo "⚠️  serviço(s) fora de 'running' — não são one-shots de seed:"
  echo "$DOWN"
  echo
  echo "   docker compose -f docker-compose.demo.yml logs --tail=60 <serviço>"
  exit 1
fi

echo "✅ Stack no ar, todos os serviços em 'running'."
echo "   UI: http://localhost:5174/login  (admin@plughub.local / changeme_admin)"
