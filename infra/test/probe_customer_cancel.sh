#!/usr/bin/env bash
# probe_customer_cancel.sh — 2026-09-14  (PID-15)
#
# PERGUNTA: o cliente que provou identidade consegue CANCELAR o próprio pedido em análise
#           — e continua sem conseguir DECIDI-LO?
#
# O DEFEITO QUE O ORIGINOU (reproduzido ao vivo antes do fix)
#   O intake do limite oferece "Cancelar solicitação" e chama `workflow_resume` com
#   `decision=rejected` sobre o token de `aprovar`; o processo trata `rejected` como "o
#   cliente cancelou" (`on_reject → encerrar_cancelado_cliente`). Mas `aprovar` é tarefa
#   de APROVAÇÃO, e a AUT-46 exige Bearer humano para toda retomada dela: o MCP tomava 401,
#   o cliente lia "Não consegui processar sua solicitação agora" e o processo seguia
#   `suspended`. Anterior à PID-13; a população do botão era zero (188 sessões), mas o
#   botão estava no menu vivo.
#
# TRÊS RAMOS
#   A  CENSO — a dispensa exige as quatro condições (capacidade + atestado, sem Bearer,
#      só `rejected`, exigência no token), roda ANTES do portão humano e não existe na rota
#      externa. Mutação sobre CÓPIA: tirar o "só rejected".
#   B  DECIDIR × CANCELAR NO MESMO TOKEN — sessão que provou por OTP: `decision=input` é
#      recusado e o token segue vivo; controle, `decision=rejected` retoma e o processo
#      termina em `encerrar_cancelado_cliente`.
#   C  PELO CHAT — CPF → OTP → menu de continuidade → "Cancelar solicitação": o cliente lê
#      a confirmação, e o processo termina pelo ramo do cliente.
#
# A decisão do aprovador pelo portão segue coberta pelo `probe_resume_requirement.sh` (D).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
COMPOSE="docker compose -p plughub-demo -f docker-compose.demo.yml"
CG="${CG:-http://localhost:8010}"
MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
MAIN=packages/channel-gateway/src/plughub_channel_gateway/main.py
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
jexpr() { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }
fim_do_processo() {  # $1 sessão → último to_step antes do __complete__
  for _ in $(seq 1 20); do
    v=$(docker exec "$PG" psql -U plughub -d plughub_demo -Atc "select x->>'from_step' from session_pipeline_state s,
        jsonb_array_elements(s.transitions) x where s.session_id='$1' and x->>'to_step'='__complete__'
        order by s.persisted_at desc limit 1" 2>/dev/null)
    [ -n "$v" ] && { echo "$v"; return; }
    sleep 1
  done
}
retoma() {  # $1 decisão · $2 token → JSON da fase retoma-s1
  docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - retoma-s1 $TENANT $1 x $2" \
    < infra/test/_resume_requirement_exercise.cjs 2>/dev/null | tail -1
}
vivo() { docker exec plughub-demo-redis-1 redis-cli hexists "$TENANT:resume_tokens" "$1" 2>/dev/null; }

echo "════════════════════════════════════════════════════════════════════"
echo " o cliente provado cancela o próprio pedido — e não o decide?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
LIMPO="d['helper_encontrado'] and d['exige_capacidade_e_atestado'] and d['bearer_segue_caminho_humano'] and d['so_rejected'] and d['exige_exigencia_no_token'] and d['rota_consulta_antes_do_portao_humano'] and d['externa_sem_dispensa']"
C=$(python3 infra/test/_customer_cancel_census.py "$MAIN")
echo "   $C"
if [ "$(jexpr "$C" "d['helper_encontrado']")" != "True" ]; then
  incon "o censo não achou _customer_cancel_allowed — não mediu nada"
elif [ "$(jexpr "$C" "$LIMPO")" = "True" ]; then
  ok "quatro condições, antes do portão humano, só na rota interna"
else
  falha "censo sujo: $C"
fi
TMP=$(mktemp --suffix=.py)
python3 - "$MAIN" "$TMP" <<'EOF'
import sys
s = open(sys.argv[1], encoding="utf-8").read()
a = 'if str((body.payload or {}).get("decision") or "") != "rejected":'
assert s.count(a) == 1, "ancora da mutacao"
open(sys.argv[2], "w", encoding="utf-8").write(s.replace(a, "if False:", 1))
EOF
CM=$(python3 infra/test/_customer_cancel_census.py "$TMP"); rm -f "$TMP"
[ "$(jexpr "$CM" "not d['so_rejected']")" = "True" ] && ok "M1 (cliente podendo decidir) acusada" || falha "M1 não acusada: $CM"

echo ""
echo "── B · DECIDIR × CANCELAR NO MESMO TOKEN ───────────────────────────────"
# shellcheck source=/dev/null
. infra/test/_auth.sh
plughub_gw_service_shim
. infra/test/_resume_requirement_setup.sh
if ! rr_setup; then
  incon "cenário não montou: $RR_ERRO"
else
  TK=$(jexpr "$RESULT" "d.get('token','')")
  PROC=$(jexpr "$PEND" "d['pendings'][0]['session_id']")
  if [ "$(jexpr "$RESULT" "d['casos']['s1_recebe_token']")" != "True" ] || [ -z "$TK" ]; then
    incon "a sessão que provou não levou o token — não há o que retomar ($RESULT)"
  else
    R=$(retoma input "$TK")
    [ "$(jexpr "$R" "d['r']['isError'] and 'http_401' in d['r']['body'].get('error','')")" = "True" ] \
      && ok "decision=input pelo cliente provado: recusado (401 — decidir é do aprovador)" \
      || falha "decision=input não foi recusado: $R"
    [ "$(vivo "$TK")" = "1" ] && ok "o token segue vivo depois da tentativa de decidir" || falha "o token sumiu na tentativa de decidir"
    R=$(retoma rejected "$TK")
    [ "$(jexpr "$R" "not d['r']['isError'] and d['r']['body'].get('resumed') is True")" = "True" ] \
      && ok "controle: decision=rejected pela mesma sessão retoma" \
      || falha "decision=rejected não retomou: $R"
    FIM=$(fim_do_processo "$PROC")
    [ "$FIM" = "encerrar_cancelado_cliente" ] && ok "o processo terminou em encerrar_cancelado_cliente" \
      || falha "o processo terminou em '${FIM:-<não terminou em 20 s>}'"
  fi
fi

echo ""
echo "── C · PELO CHAT ──────────────────────────────────────────────────────"
. infra/test/_customer_cancel_exercise.sh
if ! cc_run; then
  incon "chat não montou: $CC_ERRO"
else
  printf '%s' "$CC_OUT" | grep -q "Solicitação cancelada" \
    && ok "o cliente lê a confirmação do cancelamento" \
    || falha "o cliente não leu a confirmação: $(printf '%s' "$CC_OUT" | grep '^NOTIFY' | tail -2)"
  FIM=$(fim_do_processo "$CC_PROC")
  [ "$FIM" = "encerrar_cancelado_cliente" ] && ok "o processo terminou pelo ramo do cliente" \
    || falha "o processo terminou em '${FIM:-<não terminou em 20 s>}'"
  N=$(docker logs --since "$CC_DESDE" plughub-demo-channel-gateway-1 2>&1 | grep -c "PID-15: cancelamento do cliente provado aceito")
  [ "$N" -ge 1 ] && ok "a dispensa do Bearer foi a PID-15 (log nomeado)" || falha "o cancelamento não passou pela dispensa da PID-15"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"; exit 0
