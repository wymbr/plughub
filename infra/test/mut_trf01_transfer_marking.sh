#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# mut_trf01_transfer_marking — o gate da TRF-01 reprova as três formas do defeito?
#
#   M0  controle: sem mutação, o gate tem de estar VERDE (senão sai 2).
#   M1  armadilha do NULL — `_NOT_TRANSFER_SQL` sem `coalesce`: resolvidos com
#       close_reason NULL somem da conta. Tem de reprovar o ramo B.
#   M2  transferred_count volta a ler `outcome = 'transferred'` (a marcação que o
#       wrap-up reescreve). Tem de reprovar o ramo A.
#   M3  resolução volta a incluir a transferência. Tem de reprovar o ramo B.
#
# A mutação é aplicada com `docker cp` sobre o arquivo do CONTAINER (o gate executa
# as funções lá dentro, num processo novo a cada rodada) e o original é devolvido no
# `trap`. `docker cp` é efêmero por definição — nada aqui sobrevive a um `up -d`.
# Saída: 0 = todas pegas · 1 = alguma sobreviveu · 2 = não mediu.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
CT="${CT:-plughub-demo-analytics-api-1}"
F=/app/packages/analytics-api/src/plughub_analytics_api/reports_query.py
GATE="$(dirname "$0")/probe_trf01_transfer_marking.sh"
ORIG="$(mktemp)"; MUT="$(mktemp)"

docker cp "$CT:$F" "$ORIG" >/dev/null 2>&1 || { echo "INCONCLUSIVO: não li $F do container"; exit 2; }
restaura() { docker cp "$ORIG" "$CT:$F" >/dev/null 2>&1; rm -f "$ORIG" "$MUT"; }
trap restaura EXIT INT TERM

roda() { bash "$GATE" >/dev/null 2>&1; echo $?; }

rc=$(roda)
[ "$rc" = 0 ] || { echo "M0 controle: gate não está verde (rc=$rc) — mutações não provariam nada"; exit 2; }
echo "M0 controle: verde"

SOBREVIVEU=0
muta() {  # $1 nome · $2 sed
  sed "$2" "$ORIG" > "$MUT"
  if cmp -s "$ORIG" "$MUT"; then echo "$1: a mutação NÃO se aplicou (sed sem efeito) — não mede"; SOBREVIVEU=1; return; fi
  docker cp "$MUT" "$CT:$F" >/dev/null 2>&1
  r=$(roda)
  docker cp "$ORIG" "$CT:$F" >/dev/null 2>&1
  if [ "$r" = 1 ]; then echo "$1: pega (gate rc=1)"; else echo "$1: SOBREVIVEU (gate rc=$r)"; SOBREVIVEU=1; fi
}

muta "M1 NULL sem coalesce" \
  's/^_NOT_TRANSFER_SQL = .*/_NOT_TRANSFER_SQL = "close_reason != '"'"'agent_transfer'"'"'"/'
muta "M2 transferred pelo outcome" \
  "s/countIf({_IS_TRANSFER_SQL})                                   AS transferred_count/countIf(outcome = 'transferred')                              AS transferred_count/"
muta "M3 resolucao inclui transferencia" \
  "s/countIf(outcome = 'resolved'  AND {_NOT_TRANSFER_SQL})        AS resolved_count/countIf(outcome = 'resolved')                                 AS resolved_count/"

[ "$SOBREVIVEU" = 0 ] && { echo "todas pegas"; exit 0; } || exit 1
