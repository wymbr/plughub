#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# mut_apf01_performance_source — o gate da APF-01 reprova o que ele diz vigiar?
#
#   M0  controle: sem mutação, o gate tem de estar VERDE (senão sai 2).
#   M1  admissão sintética volta ao score (sai `agent_type != 'system'`) → ramo C.
#   M2  a família de escalação encolhe para `'escalated'` só (a MV fazia assim) → ramo B.
#
# ⚠️ O que esta bateria NÃO mede, e por quê (medido em 2026-09-23, janela de 7 dias):
#   · job sem `FINAL` — 0 versões não fundidas na janela, então contar versões e contar
#     segmentos dá o MESMO número hoje. Quem guarda isto é o unitário sobre o SQL
#     (`test_reads_segments_final_not_the_mv_APF01`). O gate só o pegaria com parts
#     ainda não fundidas — logo depois de um fechamento com wrap-up.
#   · filtro de `origin` e regra da transferência — 0 linhas não-live e 0 transferências
#     na janela. Mesmo guarda: o unitário.
# Mutação que não tem população para reprovar não é teste; ela ficaria verde e
# pareceria proteção.
#
# Aplicada com `docker cp` sobre o arquivo do CONTAINER (o gate roda o job num processo
# novo); o original volta no `trap`. Efêmero: nada sobrevive a um `up -d`.
# Saída: 0 = todas pegas · 1 = alguma sobreviveu · 2 = não mediu.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
CT="${CT:-plughub-demo-analytics-api-1}"
F=/app/packages/analytics-api/src/plughub_analytics_api/performance_job.py
GATE="$(dirname "$0")/probe_apf01_performance_source.sh"
ORIG="$(mktemp)"; MUT="$(mktemp)"

docker cp "$CT:$F" "$ORIG" >/dev/null 2>&1 || { echo "INCONCLUSIVO: não li $F do container"; exit 2; }
restaura() { docker cp "$ORIG" "$CT:$F" >/dev/null 2>&1; rm -f "$ORIG" "$MUT"; }
trap restaura EXIT INT TERM
roda() { bash "$GATE" >/dev/null 2>&1; echo $?; }

rc=$(roda)
[ "$rc" = 0 ] || { echo "M0 controle: gate não está verde (rc=$rc) — mutações não provariam nada"; exit 2; }
echo "M0 controle: verde"

SOBREVIVEU=0
muta() {  # $1 nome · $2 expressão sed
  sed "$2" "$ORIG" > "$MUT"
  if cmp -s "$ORIG" "$MUT"; then echo "$1: a mutação NÃO se aplicou — não mede"; SOBREVIVEU=1; return; fi
  docker cp "$MUT" "$CT:$F" >/dev/null 2>&1
  r=$(roda)
  docker cp "$ORIG" "$CT:$F" >/dev/null 2>&1
  if [ "$r" = 1 ]; then echo "$1: pega (gate rc=1)"; else echo "$1: SOBREVIVEU (gate rc=$r)"; SOBREVIVEU=1; fi
}

muta "M1 system volta ao score"      "/^  AND agent_type != 'system'\$/d"
muta "M2 escalacao so 'escalated'"   "s/countIf(outcome IN {escalate_family} OR {is_transfer})/countIf(outcome = 'escalated' OR {is_transfer})/"

[ "$SOBREVIVEU" = 0 ] && { echo "todas pegas"; exit 0; } || exit 1
