#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# mut_apf02_session_complexity — o gate da APF-02 reprova o que ele diz vigiar?
#
#   M0  controle: sem mutação, o gate tem de estar VERDE (senão sai 2).
#   M1  a subquery de sessões perde o alias `s` (o defeito original: code 47,
#       `data_unavailable` para sempre) → ramo B.
#   M2  `resolved_count` volta a contar a transferência (perde a regra da TRF-01)
#       → ramo C. População medida em 2026-09-23: 4 transferências, todas `resolved`.
#
# ⚠️ O que esta bateria NÃO mede, e por quê (medido em 2026-09-23):
#   · agregação sem `FINAL` — 0 versões não fundidas em `segments`, então contar
#     versões e contar segmentos dá o MESMO número hoje. Quem guarda isto é o unitário
#     sobre o SQL (`test_reads_segments_final_not_the_mv_APF02`). O gate só o pegaria
#     com parts ainda não fundidas — logo depois de um fechamento com wrap-up.
# Mutação que não tem população para reprovar não é teste; ela ficaria verde e
# pareceria proteção.
#
# Aplicada com `docker cp` sobre o arquivo do CONTAINER (o gate importa a função num
# processo novo); o original volta no `trap`. Efêmero: nada sobrevive a um `up -d`.
# Saída: 0 = todas pegas · 1 = alguma sobreviveu · 2 = não mediu.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
CT="${CT:-plughub-demo-analytics-api-1}"
F=/app/packages/analytics-api/src/plughub_analytics_api/reports_query.py
GATE="$(dirname "$0")/probe_apf02_session_complexity.sh"
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

# Endereço por intervalo: o mesmo texto aparece em outras 11 queries do arquivo, e a
# mutação tem de atingir só a função sob teste.
R='/^def _fetch_session_complexity/,/^# ─── Arc 8/'
muta "M1 subquery sem alias s"        "${R} s/FROM {db}.sessions AS s FINAL/FROM {db}.sessions FINAL/"
muta "M2 resolved conta transferencia" "${R} s/countIf(outcome = 'resolved'  AND {_NOT_TRANSFER_SQL}) AS resolved_count/countIf(outcome = 'resolved') AS resolved_count/"

[ "$SOBREVIVEU" = 0 ] && { echo "todas pegas"; exit 0; } || exit 1
