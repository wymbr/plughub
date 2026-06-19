#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T10-C — visibilidade self-scope em /v1/evaluation/results (spec §17.2).
# Fronteira dura por role: atendente vê só os próprios (evaluated_user_id == sub),
# admin vê tudo. (supervisor=Grupo coberto pelo unit test _compute_result_scope.)
#
# Setup: 2 instances na mesma campanha com evaluated_user_id distintos —
#   A: evaluated_user_id = sub do operator   (deve aparecer p/ o operator)
#   B: evaluated_user_id = "u_outro_xyz"      (NÃO deve aparecer p/ o operator)
# Valida: operator vê só A; admin vê A e B.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
OP_EMAIL="${OP_EMAIL:-operator@plughub.local}";  OP_PASS="${OP_PASS:-changeme_operator}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}";     AD_PASS="${AD_PASS:-changeme_admin}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

# base64url decode (com padding) → extrai claim do payload do JWT
jwt_claim() {  # $1=jwt  $2=claim
  local p; p=$(echo "$1" | cut -d. -f2)
  local m=$(( ${#p} % 4 )); [ $m -eq 2 ] && p="$p=="; [ $m -eq 3 ] && p="$p="
  echo "$p" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r ".$2"
}
login() {  # $1=email $2=pass → echo token
  $CURL -X POST "$AUTH/auth/login" $JSON \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'
}

echo "══ aguardando evaluation-api + auth-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ eval timeout"; exit 1; }; sleep 1; done
for i in $(seq 1 30); do $CURL "$AUTH/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ auth timeout"; exit 1; }; sleep 1; done

echo "══ login operator + admin ══"
TOK_OP=$(login "$OP_EMAIL" "$OP_PASS")
TOK_AD=$(login "$AD_EMAIL" "$AD_PASS")
[ -n "$TOK_OP" ] && [ -n "$TOK_AD" ] || { echo "  ✗ login falhou (ajuste AUTH/credenciais)"; exit 1; }
SUB_OP=$(jwt_claim "$TOK_OP" sub)
[ -n "$SUB_OP" ] && [ "$SUB_OP" != "null" ] || { echo "  ✗ não decodificou sub do operator"; exit 1; }
echo "  ✓ operator sub=$SUB_OP"

echo "══ setup: form + campanha + 2 instances (evaluated_user_id distintos) + ingest ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t10c\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t10c_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup form/camp falhou"; exit 1; }

mk() {  # $1=session $2=evaluated_user_id → ingere e devolve nada
  local iid
  iid=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
    -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$1\",\"evaluated_user_id\":\"$2\"}" \
    | jq -r '.id // .instance_id // empty')
  [ -n "$iid" ] || { echo "  ✗ instance falhou ($1)"; exit 1; }
  $CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
    \"tenant_id\":\"$TENANT\",\"instance_id\":\"$iid\",\"session_id\":\"$1\",\"campaign_id\":\"$C\",
    \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"human_agent\",
    \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":8,\"justification\":\"x\"}]}" >/dev/null
}
mk "t10c_sess_A" "$SUB_OP"
mk "t10c_sess_B" "u_outro_xyz"

echo "══ operator vê só os próprios (evaluated_user_id == sub) ══"
R_OP=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&campaign_id=$C" -H "Authorization: Bearer $TOK_OP")
N_OP=$(echo "$R_OP" | jq -r '[.results[].evaluated_user_id] | length')
HAS_SELF=$(echo "$R_OP" | jq -r "[.results[].evaluated_user_id] | any(. == \"$SUB_OP\")")
HAS_OTHER=$(echo "$R_OP" | jq -r '[.results[].evaluated_user_id] | any(. == "u_outro_xyz")')
echo "    operator vê $N_OP result(s): $(echo "$R_OP" | jq -c '[.results[].evaluated_user_id]')"
assert "operator vê o próprio"      "true"  "$HAS_SELF"
assert "operator NÃO vê o de outro" "false" "$HAS_OTHER"

echo "══ admin vê tudo ══"
R_AD=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&campaign_id=$C" -H "Authorization: Bearer $TOK_AD")
AD_SELF=$(echo "$R_AD" | jq -r "[.results[].evaluated_user_id] | any(. == \"$SUB_OP\")")
AD_OTHER=$(echo "$R_AD" | jq -r '[.results[].evaluated_user_id] | any(. == "u_outro_xyz")')
assert "admin vê o próprio do op" "true" "$AD_SELF"
assert "admin vê o de outro"      "true" "$AD_OTHER"

echo
[ "$FAIL" = 0 ] && echo "✅ T10-C OK — list_results escopa por posse (atendente=self) e admin vê tudo" \
                || { echo "❌ T10-C com falhas"; exit 1; }
