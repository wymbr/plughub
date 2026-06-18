#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T7a — agregação determinística form-driven + validação no ingest (§5.2/§16.2).
#
# - a overall_score do "LLM" é DESCARTADA e recomputada de criterion_responses
#   pelos pesos/tipos do form (snapshot pinado / form vivo);
# - threads round-1 nascem POR CRITÉRIO (author evaluator_ai);
# - criterion_responses inválido (criterion inexistente / fora de faixa) → 422.
#
# Endpoints de forms/campaigns/instances/ingest são abertos (tenant_id em query/body);
# /threads usa header X-Tenant-ID.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CURL="curl -s --max-time 10"
JSON='-H Content-Type:application/json'
FAIL=0

assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
# comparação NUMÉRICA (jq 1.7 preserva 7.0; 7 == 7.0)
assert_num() { if awk "BEGIN{exit !(($2)==($3))}" 2>/dev/null; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# ── setup: form (1 dim, 2 critérios score peso 1, max 10) + campanha + instance ──
echo "══ setup ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t7a_agg\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"Atendimento\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"Clareza\",\"type\":\"score\",\"weight\":1,\"max_score\":10},
      {\"criterion_id\":\"c2\",\"label\":\"Resolução\",\"type\":\"score\",\"weight\":1,\"max_score\":10}
    ]}]}" | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t7a_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"retencao_humano\",\"evaluation_pool_id\":\"retencao_humano\"}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] || { echo "  ✗ campaign falhou"; exit 1; }
echo "  form=$F campaign=$C"

new_instance() { # session_suffix -> instance_id
  $CURL -X POST "$EVAL/v1/evaluation/instances" $JSON -d "{
    \"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"sess_t7a_$1\"}" \
    | jq -r '.id // .instance_id // empty'
}

ingest() { # instance_id body_extra -> full json (com http code anexado via -w não; usamos só corpo)
  local iid="$1"; shift
  $CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
    \"tenant_id\":\"$TENANT\",\"instance_id\":\"$iid\",\"session_id\":\"s\",
    \"campaign_id\":\"$C\",\"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",
    \"evaluated_agent_type\":\"human_agent\", $1 }"
}
ingest_code() { # instance_id body_extra -> http code
  local iid="$1"; shift
  $CURL -o /dev/null -w '%{http_code}' -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
    \"tenant_id\":\"$TENANT\",\"instance_id\":\"$iid\",\"session_id\":\"s\",
    \"campaign_id\":\"$C\",\"form_id\":\"$F\",\"evaluator_agent_id\":\"a\",
    \"evaluated_agent_type\":\"human_agent\", $1 }"
}

echo "══ CASO 1 — overall do LLM (2.0) é DESCARTADO; recomputa 7.0 de (8+6)/2 ══"
I1=$(new_instance ok); echo "  instance=$I1"
R=$(ingest "$I1" '"overall_score":2.0,"criterion_responses":[
  {"criterion_id":"c1","score":8,"max_score":10,"na":false,"notes":"clara"},
  {"criterion_id":"c2","score":6,"max_score":10,"na":false,"notes":"resolveu"}]')
assert_num "overall_score recomputado" 7 "$(echo "$R" | jq -r .overall_score)"
assert_num "dim d1 score"              7 "$(echo "$R" | jq -r '.final_scores_by_dimension[] | select(.dimension_id=="d1") | .score')"
assert "threads round-1 criados"       2 "$(echo "$R" | jq -r .contestation_threads_created)"

echo "══ CASO 2 — threads round-1 por critério, author evaluator_ai ══"
T=$($CURL "$EVAL/v1/evaluation/instances/$I1/threads" -H "X-Tenant-ID: $TENANT")
assert "threads.count"                 2 "$(echo "$T" | jq -r '.threads | length')"
assert "autores evaluator_ai"          2 "$(echo "$T" | jq -r '[.threads[] | select(.author_type=="evaluator_ai" and .round==1)] | length')"
assert "critérios contemplados (c1,c2)" "c1 c2" "$(echo "$T" | jq -r '[.threads[].dimension_id] | sort | join(" ")')"

echo "══ CASO 3 — validação: criterion inexistente → 422 ══"
I2=$(new_instance ghost)
assert "http (ghost criterion)" 422 "$(ingest_code "$I2" '"criterion_responses":[{"criterion_id":"ghost","score":5}]')"

echo "══ CASO 4 — validação: score fora de faixa → 422 ══"
I3=$(new_instance range)
assert "http (score 99 > max 10)" 422 "$(ingest_code "$I3" '"criterion_responses":[{"criterion_id":"c1","score":99}]')"

echo "══ CASO 5 — na em critério sem na_allowed → 422 ══"
I4=$(new_instance na)
assert "http (na não permitido)" 422 "$(ingest_code "$I4" '"criterion_responses":[{"criterion_id":"c1","na":true}]')"

echo
[ "$FAIL" = 0 ] && echo "✅ T7a OK — nota recomputada do form + validação + threads por critério" \
                || { echo "❌ T7a com falhas"; exit 1; }
