#!/usr/bin/env bash
# Outbound Fase 5a — FAN-OUT (dispatcher/worker) no fluxo real.
#
# Prova o paralelismo do substrato: uma Agenda dispara o pool DISPATCHER
# (outbound_dispatch) com { campaign_id }; o dispatcher drena N entradas (claim atômico)
# e dispara N WORKERS independentes (outbound_worker) via workflow_trigger (fire-and-
# forget). Cada worker roda o gate de fadiga (claim) → registra `contacted` → collect
# (lazy, suspende). Observável: as N deliveries vão claimed → contacted (só acontece se
# os workers rodaram — se o fan-out falhasse, ficariam em claimed).
#
# Decisão B (2026-07-22): o contato usa o collect LAZY existente (entrega convite +
# suspende). O e2e completo (engajamento → responded + survey_record) é a 5b.
#
# Requer: skills novas (dispatch/worker) publicadas nos slots dos pools novos
# (outbound_dispatch/outbound_worker) e a cadeia com workflow_trigger + tools outbound.
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase5a.sh
# ⚠️ UTF-8 explicito na saida do python — e esta linha E o conserto, nao um paliativo.
#
# Nesta bancada o `stdout` do python usa cp1252. Um `print` com acento sai em bytes
# cp1252, e todo consumidor a jusante — `grep` com padrao UTF-8, outro python, o proprio
# shell — deixa de casar sobre um texto que ESTA la. Medido com A/B em 2026-09-02
# (CNS-18): sem a env, `grep -c 'meta NAO escrito'` devolve 0 pelos DOIS caminhos
# (arquivo e variavel); com a env, devolve 1 pelos dois.
#
# ⚠️ O diagnostico levou TRES tentativas e as duas primeiras foram publicadas erradas:
# `sys.stdin` (CNS-12) e a variavel de shell (CNS-17). Nao era o fluxo de ENTRADA nem o
# transporte — era a SAIDA. Variavel e arquivo sao ambos inocentes, e `docker logs`
# tambem: medido, sobrevive intacto pelos dois. Se voce for mexer nisto, o teste que
# separa as hipoteses e o A/B na propria env, com UMA variavel por vez.
export PYTHONIOENCODING=utf-8

set -euo pipefail

TENANT="tenant_demo"
MA="http://localhost:3660"
SC="http://localhost:3650"
ts=(-H "X-Tenant-ID: $TENANT")
jqid() { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
STAMP=$(date +%s)
N=3

echo "1) mailing + $N entries (clientes distintos, canal webchat) ..."
M=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"name\":\"5a $STAMP\",\"dedup_policy\":\"customer\"}" | jqid)
[ -n "$M" ] || { echo "FALHA: mailing sem id"; exit 1; }
for i in $(seq 1 $N); do
  CUS="cus_5a_${STAMP}_$i"
  curl -s -X POST "$MA/v1/mailings/$M/entries" "${ts[@]}" -H 'content-type: application/json' -d "{
    \"customer_id\":\"$CUS\",\"contacts\":{\"webchat\":\"w$i\"},
    \"metadata\":{\"channel\":\"webchat\",\"mensagem\":\"oi $i\"}
  }" >/dev/null
done

echo "2) campanha (pool = outbound_dispatch) ..."
C=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"name\":\"5a camp\",\"mailing_id\":\"$M\",\"pool_id\":\"outbound_dispatch\",\"batch_size\":50}" | jqid)
[ -n "$C" ] || { echo "FALHA: campanha sem id"; exit 1; }

echo "3) agenda (fire futuro) + fire → dispara o dispatcher ..."
AG=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"5a\",\"target_pool_id\":\"outbound_dispatch\",\"payload\":{\"campaign_id\":\"$C\"},
  \"validity\":{\"starts_at\":\"2026-07-21T00:00:00Z\"},
  \"schedule\":{\"mode\":\"once\",\"fire_at\":\"2030-01-01T09:00:00Z\"}
}" | jqid)
[ -n "$AG" ] || { echo "FALHA: agenda sem id"; exit 1; }
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" >/dev/null

echo "4) poll deliveries → esperado $N contacted (workers em paralelo rodaram) ..."
count_contacted() {
  curl -s "$MA/v1/campaigns/$C/deliveries" "${ts[@]}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin)['deliveries'];print(sum(1 for x in d if x['result']=='contacted'))"
}
total_deliveries() {
  curl -s "$MA/v1/campaigns/$C/deliveries" "${ts[@]}" \
    | python3 -c "import sys,json;print(len(json.load(sys.stdin)['deliveries']))"
}
CONTACTED=0
for _ in $(seq 1 20); do
  sleep 3
  CONTACTED=$(count_contacted)
  [ "$CONTACTED" = "$N" ] && break
done
TOT=$(total_deliveries)
echo "   deliveries=$TOT contacted=$CONTACTED (esperado $N)"
[ "$TOT" = "$N" ]       || { echo "FALHA: dispatcher deveria drenar/claimar $N entradas (drenou $TOT)"; exit 1; }
[ "$CONTACTED" = "$N" ] || { echo "FALHA: os $N workers deveriam marcar contacted (só $CONTACTED) — fan-out não rodou?"; exit 1; }
echo "   PASS: dispatcher espalhou $N workers em paralelo; cada um contatou (claimed→contacted)."

echo "5) Limpeza ..."
curl -s -X POST "$SC/v1/agendas/$AG/cancel" "${ts[@]}" >/dev/null || true
echo "GATE outbound Fase 5a — OK."
