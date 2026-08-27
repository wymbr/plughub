#!/usr/bin/env bash
# mk_unrestricted_principal.sh — cria/garante o principal IRRESTRITO POR DECLARACAO.
#
# Passo 2 do plano `accessible_pools` (2026-08-27). Dois gates deste repositorio
# comparam um agregado da API contra um ledger lido DIRETO, logo so fecham sob um
# principal que enxergue o tenant inteiro — e, medido, NENHUM usuario do ambiente via
# tudo (o admin tem 22 dos 36 pools). Ate aqui eles dependiam do caminho SEM HEADER,
# e era isso que travava o endurecimento do demo.
#
# O arranjo abaixo e o unico que sobrevive ao passo 3: `unrestricted: true` COM lista
# VAZIA. Com lista nao-vazia o ramo restritivo vence e a declaracao fica inerte — de
# proposito, para que um `unrestricted` setado por engano nunca ALARGUE um operador
# escopado.
#
# Idempotente: 409 -> PATCH garantindo o estado.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/_auth.sh"

TOK="$(plughub_token)"
EMAIL="${UNRESTRICTED_EMAIL:-probe@plughub.local}"
PASS="${UNRESTRICTED_PASS:-changeme_probe}"

echo "--- usuarios com unrestricted=true hoje ---"
curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" \
  | jq -r '.[] | select(.unrestricted==true) | "  " + .email' 2>/dev/null || echo "  (nenhum)"

echo "--- admin: pools declarados ---"
curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" \
  | jq -r ".[] | select(.email==\"$_PH_EMAIL\") | \"  accessible_pools=\" + (.accessible_pools|length|tostring) + \" unrestricted=\" + (.unrestricted|tostring)"

echo "--- criando $EMAIL ---"
BODY=$(cat <<JSON
{"tenant_id":"$TENANT","email":"$EMAIL","password":"$PASS",
 "name":"Probe - irrestrito declarado","roles":["admin"],
 "accessible_pools":[],"unrestricted":true}
JSON
)
RESP="$(curl -s -w '\n%{http_code}' -X POST "$AUTH/users" \
  -H 'content-type: application/json' -H "Authorization: Bearer $TOK" -d "$BODY")"
CODE="$(printf '%s' "$RESP" | tail -1)"
JSON_OUT="$(printf '%s' "$RESP" | sed '$d')"
echo "  HTTP $CODE"
if [ "$CODE" = "409" ]; then
  echo "  ja existe — garantindo unrestricted=true"
  UID_P="$(curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" \
           | jq -r ".[] | select(.email==\"$EMAIL\") | .id" | head -1)"
  curl -s -X PATCH "$AUTH/users/$UID_P" -H 'content-type: application/json' \
    -H "Authorization: Bearer $TOK" -d '{"unrestricted":true,"accessible_pools":[]}' \
    | jq -r '"  " + .email + " unrestricted=" + (.unrestricted|tostring) + " pools=" + (.accessible_pools|length|tostring)'
else
  printf '%s' "$JSON_OUT" | jq -r '"  " + (.email // "?") + " unrestricted=" + ((.unrestricted // "?")|tostring) + " pools=" + ((.accessible_pools // [])|length|tostring)'
fi
