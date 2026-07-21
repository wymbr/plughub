#!/usr/bin/env bash
# Outbound Fase 3b — portão de OPT-OUT GLOBAL (do_not_contact) via API.
#
# Prova: (a) mailing_unsubscribe scope=global grava `do_not_contact` no cadastro do
# cliente (via Identity Resolver); (b) o contact_eligibility_check veta `opt_out`
# (maior precedência, sem claim); (c) uma campanha `transactional` FURA o opt-out
# (notificação obrigatória). O opt-out vive no cadastro, não no outbound.
#
# Requer o Identity Resolver habilitado (PLUGHUB_IDENTITY_RESOLVER_ENABLED=true) e a
# mailing-api com identity_api_url → channel-gateway.
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase3b.sh
set -euo pipefail

TENANT="tenant_demo"
MA="http://localhost:3660"
ts=(-H "X-Tenant-ID: $TENANT")
jqid()  { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
jqget() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))"; }
STAMP=$(date +%s)
CUS="cus_3b_$STAMP"

# cria mailing+entry(CUS)+campanha → ecoa campaign_id ; $1 = transactional (true|false)
mk_campaign() {
  local m
  m=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
        -d '{"name":"3b","dedup_policy":"customer"}' | jqid)
  curl -s -X POST "$MA/v1/mailings/$m/entries" "${ts[@]}" -H 'content-type: application/json' \
       -d "{\"customer_id\":\"$CUS\",\"metadata\":{\"channel\":\"webchat\"}}" >/dev/null
  curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
    -d "{\"name\":\"3b camp\",\"mailing_id\":\"$m\",\"pool_id\":\"outbound_demo\",\"transactional\":$1}" | jqid
}

echo "1) unsubscribe GLOBAL → grava do_not_contact.all no cadastro (via identity) ..."
U=$(curl -s -X POST "$MA/v1/unsubscribe" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"customer_id\":\"$CUS\",\"scope\":\"global\"}")
echo "   $U"
[ "$(echo "$U" | jqget do_not_contact_set)" = "True" ] || { echo "FALHA: do_not_contact não gravado (Identity Resolver habilitado?)"; exit 1; }

echo "2) eligibility em campanha NÃO-transactional → esperado allowed:False, reason:opt_out, claimed:False ..."
CG=$(mk_campaign false)
R=$(curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"customer_id\":\"$CUS\",\"channel\":\"webchat\",\"campaign_id\":\"$CG\",\"claim\":true}")
echo "   $R"
[ "$(echo "$R" | jqget allowed)" = "False" ]   || { echo "FALHA: opt-out deveria vetar"; exit 1; }
[ "$(echo "$R" | jqget reason)"  = "opt_out" ] || { echo "FALHA: reason esperado opt_out"; exit 1; }
[ "$(echo "$R" | jqget claimed)" = "False" ]   || { echo "FALHA: não deveria claimar sob opt-out"; exit 1; }
echo "   PASS: opt-out global vetou o contato (sem claim)."

echo "3) eligibility em campanha TRANSACTIONAL → esperado allowed:True (fura o opt-out) ..."
CGT=$(mk_campaign true)
RT=$(curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"customer_id\":\"$CUS\",\"channel\":\"webchat\",\"campaign_id\":\"$CGT\",\"claim\":true}")
echo "   $RT"
[ "$(echo "$RT" | jqget allowed)" = "True" ] || { echo "FALHA: campanha transactional deveria furar o opt-out"; exit 1; }
echo "   PASS: campanha transactional fura o opt-out (notificação obrigatória)."

echo "GATE outbound Fase 3b — OK."
