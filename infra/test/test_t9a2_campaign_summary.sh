#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T9-A2.1 — sumário por campanha (nível 1 da lista de Avaliações).
# Valida GET /reports/campaign-summary: contagens por result_state, split humano/IA,
# total e finalize_reason. Monta 3 avaliações na campanha:
#   AI score 8  → finalized (auto_ai)
#   AI score 2  → ai_review (flagged por score_extremes [5,9])
#   humano sc 8 → open (janela de contestação)
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ setup: form + campanha + regra score_extremes [5,9] ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t9a2\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t9a2_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/campaigns/$C/sampling-rules" -H "X-Tenant-ID: $TENANT" $JSON \
  -d '{"rule_type":"score_extremes","params":{"min":5,"max":9},"enabled":true}' >/dev/null

mk_inst() { $CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$1\"}" | jq -r '.id // .instance_id'; }
ingest() { # instance score agent_type
  $CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
    \"tenant_id\":\"$TENANT\",\"instance_id\":\"$1\",\"session_id\":\"s\",\"campaign_id\":\"$C\",
    \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"$3\",
    \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":$2,\"justification\":\"x\"}]}" >/dev/null; }

ingest "$(mk_inst s9a2_fin)" 8 ai_agent       # → finalized auto_ai
ingest "$(mk_inst s9a2_air)" 2 ai_agent       # → ai_review (flagged)
ingest "$(mk_inst s9a2_open)" 8 human_agent    # → open (contestação)

echo "══ sumário da campanha ══"
S=$($CURL "$EVAL/v1/evaluation/reports/campaign-summary?tenant_id=$TENANT&campaign_id=$C" | jq -c ".summaries[\"$C\"]")
echo "    $S"
assert "total_results"            3 "$(echo "$S" | jq -r '.total_results')"
assert "result_state.finalized"   1 "$(echo "$S" | jq -r '.result_state.finalized // 0')"
assert "result_state.ai_review"   1 "$(echo "$S" | jq -r '.result_state.ai_review // 0')"
assert "result_state.open"        1 "$(echo "$S" | jq -r '.result_state.open // 0')"
assert "evaluated.ai_agent"       2 "$(echo "$S" | jq -r '.evaluated.ai_agent // 0')"
assert "evaluated.human_agent"    1 "$(echo "$S" | jq -r '.evaluated.human_agent // 0')"
assert "finalize_reason.auto_ai"  1 "$(echo "$S" | jq -r '.finalize_reason.auto_ai // 0')"

echo
[ "$FAIL" = 0 ] && echo "✅ T9-A2.1 OK — campaign-summary agrega result_state / humano-IA / finalize_reason / total" \
                || { echo "❌ T9-A2.1 com falhas"; exit 1; }
