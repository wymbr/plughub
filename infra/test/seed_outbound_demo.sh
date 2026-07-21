#!/usr/bin/env bash
# Outbound Fase 1 — seed do demo: cria um mailing + 3 entries + uma campaign apontando
# o pool webhook outbound_demo. Escreve TUDO via a API oficial do mailing-api (invariante
# "provisioning only via official API"). Imprime MAILING_ID / CAMPAIGN_ID no final.
#
# Uso (raiz do repo, demo no ar):  bash infra/test/seed_outbound_demo.sh
set -euo pipefail

TENANT="tenant_demo"
MA="http://localhost:3660"
POOL="outbound_demo"
ts=(-H "X-Tenant-ID: $TENANT")

# Extrai o PRIMEIRO campo "id":"..." (top-level) — "mailing_id"/"agenda_id" não casam
# porque exigimos aspas coladas ao 'id'.
jqid() { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }

MAILING=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' -d '{
  "name":"Demo mailing (outbound Fase 1)",
  "description":"Audiência de demonstração do substrato outbound",
  "dedup_policy":"customer_context",
  "metadata_contract":"outbound_demo_v1"
}' | jqid)
[ -n "$MAILING" ] || { echo "FALHA: mailing sem id (mailing-api no ar em :3660?)"; exit 1; }

for i in 1 2 3; do
  curl -s -X POST "$MA/v1/mailings/$MAILING/entries" "${ts[@]}" -H 'content-type: application/json' -d "{
    \"customer_id\":\"cus_demo_$i\",
    \"contacts\":{\"webchat\":\"demo-$i\"},
    \"metadata\":{\"target\":\"cus_demo_$i\",\"mensagem\":\"Olá, cliente $i\",\"grain\":\"session\",\"channel\":\"webchat\"},
    \"source\":\"seed:outbound_demo\"
  }" >/dev/null
done

CAMP=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"Demo campaign (outbound Fase 1)\",
  \"mailing_id\":\"$MAILING\",
  \"pool_id\":\"$POOL\",
  \"batch_size\":50
}" | jqid)
[ -n "$CAMP" ] || { echo "FALHA: campaign sem id"; exit 1; }

echo "MAILING_ID=$MAILING"
echo "CAMPAIGN_ID=$CAMP"
