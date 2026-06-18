#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T12 — gate ai_review (sinalizados). Score fora de faixa (regra score_extremes da
# campanha) → result_state `ai_review` ANTES de publicar; o ai-review resolve
# (avaliado IA → finalize auto_ai). Em-faixa → finaliza direto (auto_ai).
#
# Code-only na evaluation-api. Testável via rota HTTP de ingest + sampling-rules.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
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
  \"tenant_id\":\"$TENANT\",\"name\":\"t12\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t12_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/campaigns/$C/sampling-rules" \
  -H "X-Tenant-ID: $TENANT" $JSON -d '{"rule_type":"score_extremes","params":{"min":5,"max":9},"enabled":true}' >/dev/null

new_instance() { $CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"sess-t12-$1\"}" | jq -r '.id // .instance_id // empty'; }
ingest() { # instance score -> result_id
  $CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
    \"tenant_id\":\"$TENANT\",\"instance_id\":\"$1\",\"session_id\":\"s\",\"campaign_id\":\"$C\",
    \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"ai_agent\",
    \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":$2,\"justification\":\"sintético\"}]}" \
    | jq -r '.result_id // empty'; }
rstate() { $CURL "$EVAL/v1/evaluation/results/$1?tenant_id=$TENANT" | jq -r '.result_state // empty'; }

echo "══ CASO 1 — score 2 (< min 5) → FLAGGED → ai_review (não finaliza) ══"
I1=$(new_instance flag); R1=$(ingest "$I1" 2)
echo "  result=$R1"
assert "result_state após ingest (flagged)" ai_review "$(rstate "$R1")"

echo "══ CASO 2 — ai-review resolve → finalized (auto_ai) ══"
$CURL -X POST "$EVAL/v1/evaluation/instances/$I1/ai-review" \
  -H "X-Tenant-ID: $TENANT" -H "X-Admin-Token: $ADMIN" $JSON -d '{"notes":"revisado, ok"}' | jq -c '{result_state, published_to}'
assert "result_state após ai-review" finalized "$(rstate "$R1")"

echo "══ CASO 3 — score 8 (em [5,9]) → NÃO flagged → finaliza direto (auto_ai) ══"
I2=$(new_instance ok); R2=$(ingest "$I2" 8)
assert "result_state após ingest (em faixa)" finalized "$(rstate "$R2")"

echo "══ CASO 4 — ai-review num result já finalizado → 409 ══"
CODE=$($CURL -o /dev/null -w '%{http_code}' -X POST "$EVAL/v1/evaluation/instances/$I2/ai-review" \
  -H "X-Tenant-ID: $TENANT" -H "X-Admin-Token: $ADMIN" $JSON -d '{}')
assert "http (ai-review de finalizado)" 409 "$CODE"

echo
[ "$FAIL" = 0 ] && echo "✅ T12 OK — gate ai_review (flag por faixa) + resolução + bypass em-faixa" \
                || { echo "❌ T12 com falhas"; exit 1; }
