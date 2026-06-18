#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T8-B2 — body EFETIVO da rubrica (com fallback built-in) p/ o runtime.
# Valida o endpoint GET /rubric-templates/effective consumido pelo mcp-server
# (evaluation_context_get → rubric_instructions):
#   1. sem rubrica → builtin_default (body nunca nulo);
#   2. default do tenant publicada → tenant_default;
#   3. override de campanha publicado → campaign_override.
# A fiação mcp-server/skill (rubric_instructions ao reason, prompt_id renomeado) é
# inspecionada — runtime do avaliador é e2e-blocked (gotcha 1).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
RT="$EVAL/v1/evaluation/rubric-templates"
FAIL=0
assert()     { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_has() { if echo "$2" | grep -qF "$3"; then echo "  ✓ $1 contém '$3'"; else echo "  ✗ $1 NÃO contém '$3'"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

T="${TENANT}_t8b2_$RANDOM"; CAMP="camp_t8b2_$RANDOM"

echo "══ CASO 1 — sem rubrica → builtin_default (body presente) ══"
E=$($CURL "$RT/effective?tenant_id=$T")
assert "source (sem rubrica)" builtin_default "$(echo "$E" | jq -r '.source')"
assert_has "body built-in" "$(echo "$E" | jq -r '.body')" "avaliador de qualidade"

echo "══ CASO 2 — default do tenant publicada → tenant_default ══"
RID=$($CURL -X POST "$RT" $JSON -d "{\"tenant_id\":\"$T\",\"scope\":\"tenant\",\"body\":\"RUBRICA TENANT B2.\"}" | jq -r '.id')
$CURL -X POST "$RT/$RID/publish?tenant_id=$T" $JSON -d '{}' >/dev/null
E=$($CURL "$RT/effective?tenant_id=$T")
assert "source (tenant pub)" tenant_default "$(echo "$E" | jq -r '.source')"
assert_has "body tenant" "$(echo "$E" | jq -r '.body')" "RUBRICA TENANT B2."

echo "══ CASO 3 — override de campanha publicado → campaign_override ══"
OV=$($CURL -X POST "$RT" $JSON -d "{\"tenant_id\":\"$T\",\"scope\":\"campaign\",\"campaign_id\":\"$CAMP\",\"body\":\"OVERRIDE CAMP B2.\"}" | jq -r '.id')
$CURL -X POST "$RT/$OV/publish?tenant_id=$T" $JSON -d '{}' >/dev/null
assert "source (campaign)" campaign_override "$($CURL "$RT/effective?tenant_id=$T&campaign_id=$CAMP" | jq -r '.source')"
assert "source (sem camp = tenant)" tenant_default "$($CURL "$RT/effective?tenant_id=$T" | jq -r '.source')"

echo
[ "$FAIL" = 0 ] && echo "✅ T8-B2 OK — effective rubric (builtin/tenant/override) p/ rubric_instructions do avaliador" \
                || { echo "❌ T8-B2 com falhas"; exit 1; }
