#!/usr/bin/env bash
# Outbound Fase 1 — smoke e2e do substrato (DIFF ZERO no scheduler).
#
# Prova a cadeia: Agenda dispara o pool webhook outbound_demo com payload {campaign_id}
# → o skill drena o mailing da campanha (campaign_drain, claim atômico) → percorre as
# entradas (loop) → contabiliza cada entrega (campaign_delivery_result). Depois RE-dispara
# e confere que NÃO houve re-drain (claim idempotente por UNIQUE(campaign_id, entry)).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase1.sh
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

HERE="$(cd "$(dirname "$0")" && pwd)"
TENANT="tenant_demo"
MA="http://localhost:3660"
SC="http://localhost:3650"
ts=(-H "X-Tenant-ID: $TENANT")
jqid() { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }

echo "1) Seed (mailing + 3 entries + campaign) ..."
SEED_OUT=$(bash "$HERE/seed_outbound_demo.sh")
echo "$SEED_OUT"
CAMP=$(echo "$SEED_OUT" | sed -n 's/^CAMPAIGN_ID=//p')
[ -n "$CAMP" ] || { echo "FALHA: sem CAMPAIGN_ID"; exit 1; }

echo "2) Cria agenda (fire_at FUTURO — só /fire dispara; poller não interfere) → outbound_demo ..."
AG=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"E2E outbound fase1\",
  \"target_pool_id\":\"outbound_demo\",
  \"payload\":{\"campaign_id\":\"$CAMP\"},
  \"validity\":{\"starts_at\":\"2026-07-21T00:00:00Z\"},
  \"schedule\":{\"mode\":\"once\",\"fire_at\":\"2030-01-01T09:00:00Z\"}
}" | jqid)
[ -n "$AG" ] || { echo "FALHA: agenda sem id"; exit 1; }
echo "   agenda = $AG"

echo "3) Disparar agora (POST /fire) ..."
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" >/dev/null

# A sessão webhook é ASSÍNCRONA (cold start do pool + alocação). Poll em vez de sleep
# fixo — o skill drena/contabiliza quando a instância sobe (pode passar de 10s a frio).
echo "4) Aguardando o skill drenar + contabilizar (poll até 45s) ..."
DELIV='{"deliveries":[],"total":0}'; N1=0; NC=0
for i in $(seq 1 15); do
  sleep 3
  DELIV=$(curl -s "$MA/v1/campaigns/$CAMP/deliveries" "${ts[@]}")
  N1=$(echo "$DELIV" | python3 -c 'import sys,json;print(json.load(sys.stdin)["total"])')
  NC=$(echo "$DELIV" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(sum(1 for x in d["deliveries"] if x["result"] in ("contacted","responded")))')
  echo "   [t=$((i*3))s] total=$N1 contabilizadas=$NC"
  [ "$NC" -ge 1 ] && break
done

echo "5) Entregas da campanha (esperado: 3 claimadas + contabilizadas):"
echo "$DELIV" | python3 -m json.tool
[ "$N1" -ge 1 ] || { echo "FALHA: nenhuma entrega drenada/claimada em 45s (webhook→skill→drain quebrou?)"; exit 1; }
[ "$NC" -ge 1 ] || { echo "FALHA: skill não marcou nenhuma entrega em 45s (loop/delivery_result quebrou?)"; exit 1; }

echo "6) Re-disparar (prova de NÃO re-drain — claim idempotente) ..."
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" >/dev/null
echo "   aguardando a 2ª sessão rodar o drain (20s) ..."
sleep 20
N2=$(curl -s "$MA/v1/campaigns/$CAMP/deliveries" "${ts[@]}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["total"])')
echo "   total entregas (após re-disparo) = $N2"
if [ "$N2" = "$N1" ]; then
  echo "   PASS: contagem inalterada — sem re-drain (2ª sessão drenou [] pois tudo já foi claimado)."
else
  echo "   FALHA: re-drain criou novas entregas ($N1 → $N2)"; exit 1
fi

echo "7) Limpeza: cancela a agenda de teste ..."
curl -s -X POST "$SC/v1/agendas/$AG/cancel" "${ts[@]}" >/dev/null && echo "   cancelada."
echo "GATE outbound Fase 1 — OK."
