#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T8-B1 — Composição + preview do prompt (spec §5.1/§16.3).
# Valida por API o compositor: prompt = instruções gerais + critérios (com
# scoring_guidance) + notas de calibração + transcript placeholder.
#   1. preview com rubric_body DRAFT + form → prompt contém a rubrica e os critérios;
#   2. sem rubrica publicada → built-in default (source=builtin_default);
#   3. com rubrica default do tenant publicada → source=tenant_default;
#   4. critério auto_computed marcado como "NÃO avaliar"; criteria_count exclui ele.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
RT="$EVAL/v1/evaluation/rubric-templates"
FAIL=0
assert()      { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_has()  { if echo "$2" | grep -qF "$3"; then echo "  ✓ $1 contém '$3'"; else echo "  ✗ $1 NÃO contém '$3'"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

T="${TENANT}_t8b_$RANDOM"   # tenant isolado

echo "══ setup: form com c1 (score+scoring_guidance) e c2 (auto_computed) ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$T\",\"name\":\"t8b\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"Empatia\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"Acolhimento\",\"type\":\"score\",\"weight\":1,\"max_score\":10,
       \"scoring_guidance\":\"0=ríspido; 10=acolhedor e personalizado\"},
      {\"criterion_id\":\"c2\",\"label\":\"TMA\",\"type\":\"auto_computed\",\"weight\":0}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }

echo "══ CASO 1 — preview com rubric_body draft + form ══"
P=$($CURL -X POST "$RT/preview" $JSON -d "{
  \"tenant_id\":\"$T\",\"form_id\":\"$F\",\"rubric_body\":\"DRAFT: seja imparcial e cite evidência.\"}")
assert "rubric_source" explicit_body "$(echo "$P" | jq -r '.rubric_source')"
assert "criteria_count (exclui auto)" 1 "$(echo "$P" | jq -r '.criteria_count')"
CP=$(echo "$P" | jq -r '.composed_prompt')
assert_has "prompt" "$CP" "DRAFT: seja imparcial e cite evidência."
assert_has "prompt" "$CP" "Acolhimento"
assert_has "prompt" "$CP" "0=ríspido; 10=acolhedor e personalizado"
assert_has "prompt" "$CP" "auto_computed → NÃO avaliar"

echo "══ CASO 2 — sem rubrica publicada → built-in default ══"
P=$($CURL -X POST "$RT/preview" $JSON -d "{\"tenant_id\":\"$T\",\"form_id\":\"$F\"}")
assert "rubric_source (built-in)" builtin_default "$(echo "$P" | jq -r '.rubric_source')"
assert_has "prompt default" "$(echo "$P" | jq -r '.composed_prompt')" "avaliador de qualidade"

echo "══ CASO 3 — default do tenant publicada → source=tenant_default ══"
RID=$($CURL -X POST "$RT" $JSON -d "{\"tenant_id\":\"$T\",\"scope\":\"tenant\",\"body\":\"RUBRICA TENANT publicada.\"}" | jq -r '.id')
$CURL -X POST "$RT/$RID/publish?tenant_id=$T" $JSON -d '{}' >/dev/null
P=$($CURL -X POST "$RT/preview" $JSON -d "{\"tenant_id\":\"$T\",\"form_id\":\"$F\"}")
assert "rubric_source (resolvido)" tenant_default "$(echo "$P" | jq -r '.rubric_source')"
assert_has "prompt tenant" "$(echo "$P" | jq -r '.composed_prompt')" "RUBRICA TENANT publicada."

echo
[ "$FAIL" = 0 ] && echo "✅ T8-B1 OK — composição: rubrica (draft/built-in/resolvida) + critérios + scoring_guidance + auto_computed" \
                || { echo "❌ T8-B1 com falhas"; exit 1; }
