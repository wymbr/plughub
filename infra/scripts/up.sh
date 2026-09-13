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
# GAT-05 (2026-09-13). Aqui havia uma lista FIXA de one-shots excluídos PELO NOME,
# sem olhar o exit code: seed morto lia igual a seed concluído (o `auth-seed` saiu 1
# por dias e isto dizia "Stack no ar"), e o `context-map-seed`, que nunca entrou na
# lista, reprovava toda subida CORRETA. A classificação agora é derivada do compose
# e o julgamento mora em `_up_state_verdict.py`, que tem probe próprio.
VERDICT="$REPO_ROOT/infra/scripts/_up_state_verdict.py"
ONESHOT_WAIT_S="${ONESHOT_WAIT_S:-180}"
command -v python3 >/dev/null || {
  echo "⚠️  INCONCLUSIVO: python3 ausente — sem ele não há como julgar os one-shots."
  exit 2
}
CFG="$LOG_DIR/up-compose-$(date +%s).json"
if ! "${COMPOSE[@]}" config --format json > "$CFG" 2>>"$LOG"; then
  echo "⚠️  INCONCLUSIVO: \`docker compose config\` falhou (ver $LOG)."
  exit 2
fi

# One-shot pode ainda estar rodando: `up -d` só espera quem é dependência declarada.
# Espera limitada; esgotado o prazo, o veredicto é INCONCLUSIVO, nunca verde.
PS="$LOG_DIR/up-ps-$(date +%s).tsv"
PS_FMT="{{.Service}}"$'\t'"{{.State}}"$'\t'"{{.ExitCode}}"   # TAB explícito, não colado
deadline=$(( $(date +%s) + ONESHOT_WAIT_S ))
while :; do
  "${COMPOSE[@]}" ps -a --format "$PS_FMT" > "$PS" 2>>"$LOG"
  OUT="$(python3 "$VERDICT" "$CFG" "$PS")"; V=$?
  [ "$V" -ne 2 ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "⚠️  INCONCLUSIVO: $OUT — após ${ONESHOT_WAIT_S}s."
    echo "   docker compose -f docker-compose.demo.yml logs --tail=60 <serviço>"
    rm -f "$CFG" "$PS"; exit 2
  fi
  sleep 5
done
rm -f "$CFG" "$PS"

case "$V" in
  0) echo "   $OUT" ;;
  1) echo "❌ $OUT" | sed '2,$s/^/   /'
     echo
     echo "   docker compose -f docker-compose.demo.yml logs --tail=60 <serviço>"
     exit 1 ;;
  *) echo "⚠️  $OUT"; exit 2 ;;
esac

echo "✅ Stack no ar: long-running em 'running' e one-shots concluídos com exit 0."
echo "   UI: http://localhost:5174/login  (admin@plughub.local / changeme_admin)"
