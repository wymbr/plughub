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
# VOZ-32: a borda SIP é OPT-IN, e a chave mora no `.env.demo` (local, fora do git) para valer
# também na subida automática do logon. Ligada, entra a camada que publica as portas.
# `SIP_USE_EXTERNAL_IP` é derivada daqui, nunca uma segunda chave: porta publicada com endereço
# interno no SDP é chamada muda.
# VOZ-43: as senhas dos troncos NÃO passam por aqui — o `sip-seed` as lê do `.env.demo` nos dois
# estados. Aqui só se EXIGE, antes de abrir: toda senha que um tronco declara tem de existir, e a de
# demo não pode ser a do `.env.demo.example` (está no repositório). Falhou: a borda fica FECHADA.
SIP_EDGE=false
SIP_EDGE_RECUSA=""
env_val() { sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -1; }
if grep -qE '^PLUGHUB_SIP_EDGE=true[[:space:]]*$' "$REPO_ROOT/.env.demo" 2>/dev/null; then
  for k in $(grep -ho '"auth_password_env": *"[A-Z_]*"' "$REPO_ROOT"/infra/sip/*.json 2>/dev/null | grep -o '[A-Z_]*"$' | tr -d '"' | sort -u); do
    [ -n "$(env_val "$k" "$REPO_ROOT/.env.demo")" ] || SIP_EDGE_RECUSA="$SIP_EDGE_RECUSA $k ausente no .env.demo;"
  done
  demo=$(env_val SIP_TRUNK_PASSWORD_DEMO "$REPO_ROOT/.env.demo")
  if [ -n "$demo" ] && [ "$demo" = "$(env_val SIP_TRUNK_PASSWORD_DEMO "$REPO_ROOT/.env.demo.example")" ]; then
    SIP_EDGE_RECUSA="$SIP_EDGE_RECUSA SIP_TRUNK_PASSWORD_DEMO é a do .env.demo.example (está no repositório);"
  fi
  if [ -z "$SIP_EDGE_RECUSA" ]; then
    SIP_EDGE=true
    export SIP_USE_EXTERNAL_IP=true
    COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.demo.yml" -f "$REPO_ROOT/docker-compose.sip-edge.yml")
  fi
fi
LOG_DIR="$REPO_ROOT/.logs"
LOG="$LOG_DIR/up-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$LOG_DIR"
cd "$REPO_ROOT"

# BOO-01 (2026-09-23): UMA subida por vez. A tarefa de logon (`up-at-login.sh`) e a
# chamada manual podem coincidir, e dois `up -d` concorrentes disputam o mesmo
# container (recreate × start) — o modo de falha que deixou o agent-registry morto
# em 2026-09-23. A segunda chamada ESPERA a primeira e então reconcilia de novo,
# o que é barato com a stack de pé. O lock é do kernel: morre com o processo.
exec 9>"$LOG_DIR/.up.lock"
if ! flock -n 9; then
  echo "⏳ outro up.sh está rodando (manual ou a tarefa de logon) — esperando ele terminar…"
  if ! flock -w "${UP_LOCK_WAIT_S:-1200}" 9; then
    echo "⚠️  INCONCLUSIVO: o outro up.sh não terminou em ${UP_LOCK_WAIT_S:-1200}s."
    echo "   ps -ef | grep up.sh   ·   logs em $LOG_DIR"
    exit 2
  fi
fi

if [ "${1:-}" = "--pull" ]; then
  echo "── pull das imagens de infra ──────────────────────────────────────"
  "${COMPOSE[@]}" pull --ignore-buildable 2>&1 | tee -a "$LOG"
fi

echo "── up -d (reconcilia + espera health) ─────────────────────────────"
echo "   log: $LOG"
if [ "$SIP_EDGE" = true ]; then
  echo "   ⚠️  borda SIP LIGADA (PLUGHUB_SIP_EDGE=true): 5060/UDP e RTP 10000–10100/UDP publicados."
  echo "      Classificação: bash infra/test/probe_sip_edge_surface.sh"
elif [ -n "$SIP_EDGE_RECUSA" ]; then
  echo "   ❌ borda SIP pedida (PLUGHUB_SIP_EDGE=true) e NÃO aberta — segue fechada:$SIP_EDGE_RECUSA"
fi
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
