#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T8-D — campo ABAC `gerir_rubrica` no módulo evaluation (spec §16.3).
# O auth-api faz upsert do permission_schema do modules.yaml no boot (ON CONFLICT DO
# UPDATE), então após --force-recreate auth-api o campo novo aparece em /modules/evaluation.
# Valida que o campo existe e é independente de `formularios` (separação de deveres:
# "mantenedor de prompt" ≠ "gerir formulários"). Gate da página Rubrica/Prompt aponta p/ ele.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando auth-api ══"
for i in $(seq 1 30); do $CURL "$AUTH/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# Endpoint de LISTA (o que a tela de Access consome). NB: o router do auth-api tem
# prefix="/auth" → os módulos ficam em /auth/modules (mesmo prefixo de /auth/login).
# Robusto à forma: schema sob `permission_schema` ou `schema`, objeto ou string-JSON.
M=$($CURL "$AUTH/auth/modules?tenant_id=$TENANT")
SCHEMA=$(echo "$M" | jq -c '
  ((. // []) | map(select(.module_id=="evaluation")) | .[0]) as $m
  | ($m.permission_schema // $m.schema) as $s
  | if   ($s|type)=="object" then $s
    elif ($s|type)=="string" then ($s|fromjson)
    else {} end' 2>/dev/null || echo '{}')

echo "  (debug) keys do schema: $(echo "$SCHEMA" | jq -rc 'keys')"

echo "══ CASO 1 — campo gerir_rubrica existe no schema do módulo evaluation ══"
assert "gerir_rubrica presente" true "$(echo "$SCHEMA" | jq -r 'has("gerir_rubrica")')"
assert "label do campo" "Gerenciar rubrica/prompt do avaliador" "$(echo "$SCHEMA" | jq -r '.gerir_rubrica.label')"
assert "default none" none "$(echo "$SCHEMA" | jq -r '.gerir_rubrica.default')"

echo "══ CASO 2 — independente de formularios (separação de deveres) ══"
assert "formularios ainda existe" true "$(echo "$SCHEMA" | jq -r 'has("formularios")')"
assert "são campos distintos" true "$(echo "$SCHEMA" | jq -r '(has("gerir_rubrica") and has("formularios"))')"

echo
[ "$FAIL" = 0 ] && echo "✅ T8-D OK — campo ABAC gerir_rubrica no módulo evaluation (gate da Rubrica/Prompt)" \
                || { echo "❌ T8-D com falhas (rodou --force-recreate auth-api?)"; exit 1; }
