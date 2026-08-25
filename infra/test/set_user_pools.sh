#!/usr/bin/env bash
# set_user_pools.sh — escreve `accessible_pools` de um usuário PELA API OFICIAL.
#
# Existe por três razões, e nenhuma é conveniência:
#
#   1. **A invariante de provisionamento proíbe escrita direta no store** — nada de
#      `psql` no schema `auth`. Todo provisionamento passa pela API do dono do
#      domínio, inclusive o feito à mão numa investigação.
#   2. **Cadeia de curl colada no terminal não roda como se lê.** Quatro linhas
#      coladas de uma vez viram `A=… TOK=… AID=… curl …`: o bash aplica as
#      atribuições DEPOIS de expandir, então `$A` sai vazio nas três e o comando
#      falha em silêncio sob `-s`. Aconteceu em 2026-08-25.
#   3. **Antes e depois, sempre.** Uma escrita de escopo que não imprime o valor
#      anterior não deixa como saber o que foi trocado — nem como desfazer.
#
# Uso:
#   bash infra/test/set_user_pools.sh <email> '<json array de pools>'
#   bash infra/test/set_user_pools.sh admin@plughub.local '[]'
#   bash infra/test/set_user_pools.sh operator@plughub.local '["sac_ia","nps_ia"]'
#
# `[]` significa **todos os pools** — é o contrato do JWT (`models.py:40`), não
# "nenhum". Um usuário com lista vazia é irrestrito; um com lista explícita vê
# apenas o que ela nomeia, e essa lista DEFASA quando um pool novo nasce.
set -u

AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_admin}

EMAIL=${1:-}
POOLS=${2:-}
if [ -z "$EMAIL" ] || [ -z "$POOLS" ]; then
  echo "uso: $0 <email> '<json array>'   (ex.: $0 admin@plughub.local '[]')"
  exit 2
fi
command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }
echo "$POOLS" | jq -e 'type == "array"' >/dev/null 2>&1 \
  || { echo "REPROVADO: '$POOLS' não é um array JSON"; exit 1; }

# ── 1. token ─────────────────────────────────────────────────────────────────
TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
      -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"tenant_id\":\"$TENANT\"}" \
      | jq -r '.access_token // empty')
if [ -z "$TOK" ]; then
  echo "REPROVADO: login de $ADMIN_EMAIL não devolveu access_token."
  echo "           (auth-api de pé em $AUTH? senha mudou? ADMIN_PASS=… sobrescreve)"
  exit 1
fi
echo "token: ${TOK:0:12}…"

# ── 2. o usuário-alvo, e o valor ANTERIOR ────────────────────────────────────
ROW=$(curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" \
      | jq -c --arg e "$EMAIL" '.[]? | select(.email == $e)')
if [ -z "$ROW" ]; then
  echo "REPROVADO: usuário $EMAIL não encontrado no tenant $TENANT"
  exit 1
fi
AID=$(echo "$ROW" | jq -r '.id')
BEFORE=$(echo "$ROW" | jq -c '.accessible_pools')
echo "antes:  $EMAIL → $BEFORE"

# ── 3. escrita ───────────────────────────────────────────────────────────────
OUT=$(curl -s -X PATCH "$AUTH/users/$AID" -H "Authorization: Bearer $TOK" \
      -H 'content-type: application/json' -d "{\"accessible_pools\":$POOLS}")
AFTER=$(echo "$OUT" | jq -c '.accessible_pools // empty')
if [ -z "$AFTER" ]; then
  echo "REPROVADO: o PATCH não devolveu accessible_pools. Resposta crua:"
  echo "$OUT"
  exit 1
fi
echo "depois: $EMAIL → $AFTER"

# ── 4. veredicto ─────────────────────────────────────────────────────────────
if [ "$(echo "$AFTER" | jq -S .)" = "$(echo "$POOLS" | jq -S .)" ]; then
  echo
  echo "OK — escopo gravado."
  [ "$AFTER" = "[]" ] && echo "     (lista vazia = TODOS os pools, pelo contrato do JWT)"
  echo "⚠️  O JWT em uso ainda carrega o valor ANTERIOR: só um LOGOUT + LOGIN"
  echo "    (ou o refresh) reemite o token. Conferir a tela antes disso mede o"
  echo "    token velho e parece que a escrita não pegou."
  exit 0
fi
echo "REPROVADO: gravou $AFTER, pedimos $POOLS"
exit 1
