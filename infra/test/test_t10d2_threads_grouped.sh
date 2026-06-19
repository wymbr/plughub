#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T10-D2 — leitura AGRUPADA de threads Arc 13 (GET /instances/{id}/threads).
# O storage é plano (1 linha por entry); a UI espera 1 thread por dimensão com
# entries[] + current_state + original/current_score (0–1). Valida o agrupamento:
#   ingest (thread round-1 evaluator) → operator contesta (entry human_agent)
#   → GET threads agrupado: 1 thread c1, current_state=contested, entries≥2,
#     original_score=0.8 (8/10), dimension_label=C1
#   → supervisor revisa (upheld) → current_state=upheld
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
OP_EMAIL="${OP_EMAIL:-operator@plughub.local}"; OP_PASS="${OP_PASS:-changeme_operator}"
SUP_EMAIL="${SUP_EMAIL:-supervisor@plughub.local}"; SUP_PASS="${SUP_PASS:-changeme_supervisor}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
jwt_claim() { local p; p=$(echo "$1" | cut -d. -f2); local m=$(( ${#p} % 4 )); [ $m -eq 2 ] && p="$p=="; [ $m -eq 3 ] && p="$p="; echo "$p" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r ".$2"; }
login() { $CURL -X POST "$AUTH/auth/login" $JSON -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'; }

echo "══ login operator + supervisor ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ eval timeout"; exit 1; }; sleep 1; done
TOK_OP=$(login "$OP_EMAIL" "$OP_PASS"); TOK_SUP=$(login "$SUP_EMAIL" "$SUP_PASS")
[ -n "$TOK_OP" ] && [ -n "$TOK_SUP" ] || { echo "  ✗ login falhou"; exit 1; }
SUB_OP=$(jwt_claim "$TOK_OP" sub)
echo "  ✓ operator sub=$SUB_OP"

echo "══ setup: form(c1 max10) + campanha + instance(dono=op) + ingest ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t10d2\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t10d2_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
IID=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"t10d2_sess\",\"evaluated_user_id\":\"$SUB_OP\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$F" ] && [ -n "$C" ] && [ -n "$IID" ] || { echo "  ✗ setup falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"instance_id\":\"$IID\",\"session_id\":\"t10d2_sess\",\"campaign_id\":\"$C\",
  \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"human_agent\",
  \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":8,\"justification\":\"nota atribuida pelo avaliador ao criterio um\"}]}" >/dev/null

echo "══ operator contesta c1 (Bearer) ══"
$CURL -X POST "$EVAL/v1/evaluation/instances/$IID/contest" -H "Authorization: Bearer $TOK_OP" $JSON \
  -d '{"dimension_ids":["c1"],"reasons":{"c1":"discordo da nota atribuida ao criterio um, faltou contexto"},"round":1}' >/dev/null

echo "══ GET threads AGRUPADO ══"
T=$($CURL "$EVAL/v1/evaluation/instances/$IID/threads" -H "Authorization: Bearer $TOK_OP")
echo "    $(echo "$T" | jq -c '{n:(.threads|length), st:.threads[0].current_state, ne:(.threads[0].entries|length), orig:.threads[0].original_score, lbl:.threads[0].dimension_label}')"
assert "n_threads"          "1"          "$(echo "$T" | jq -r '.threads | length')"
assert "dimension_id"       "c1"         "$(echo "$T" | jq -r '.threads[0].dimension_id')"
assert "dimension_label"    "C1"         "$(echo "$T" | jq -r '.threads[0].dimension_label')"
assert "current_state"      "contested"  "$(echo "$T" | jq -r '.threads[0].current_state')"
assert "original_score 0.8" "0.8"        "$(echo "$T" | jq -r '.threads[0].original_score')"
GE=$(echo "$T" | jq -r '.threads[0].entries | length'); [ "${GE:-0}" -ge 2 ] && echo "  ✓ entries ≥ 2 = $GE" || { echo "  ✗ entries ≥ 2: veio $GE"; FAIL=1; }
assert "entry0 = evaluator_ai" "evaluator_ai" "$(echo "$T" | jq -r '.threads[0].entries[0].author_role')"

echo "══ supervisor revisa (upheld) → estado muda ══"
$CURL -X POST "$EVAL/v1/evaluation/instances/$IID/review" -H "Authorization: Bearer $TOK_SUP" $JSON \
  -d '{"dimension_decisions":[{"dimension_id":"c1","decision":"upheld","justification":"apos analisar a evidencia e o contexto do atendimento mantenho a nota original do avaliador pois reflete o desempenho observado"}],"reviewer_id":"sup"}' >/dev/null
T2=$($CURL "$EVAL/v1/evaluation/instances/$IID/threads" -H "Authorization: Bearer $TOK_SUP")
assert "current_state pós-review" "upheld" "$(echo "$T2" | jq -r '.threads[0].current_state')"

echo
[ "$FAIL" = 0 ] && echo "✅ T10-D2 OK — /threads agrupado por dimensão (entries/state/score); contest→contested, review→upheld" \
                || { echo "❌ T10-D2 com falhas"; exit 1; }
