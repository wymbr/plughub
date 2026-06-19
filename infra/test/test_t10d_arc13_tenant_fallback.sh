#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T10-D — Arc 13 acionável pela UI: _get_tenant cai no claim tenant_id do JWT.
# Os hooks da UI (threads/contest/review) mandam só Authorization: Bearer (sem
# X-Tenant-ID) → antes davam 400 → a UI caía no caminho Arc 6 legado (mexia em
# eval_status mas NÃO em result_state; lista não mudava, deixava recontestar).
#
# Valida, usando SÓ o Bearer (sem X-Tenant-ID):
#   - GET  /instances/{id}/threads → 200 + thread round-1 por critério (criada no ingest)
#   - POST /instances/{id}/contest → 200 (não 400) e result_state passa open → under_review
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
OP_EMAIL="${OP_EMAIL:-operator@plughub.local}"; OP_PASS="${OP_PASS:-changeme_operator}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
jwt_claim() { local p; p=$(echo "$1" | cut -d. -f2); local m=$(( ${#p} % 4 )); [ $m -eq 2 ] && p="$p=="; [ $m -eq 3 ] && p="$p="; echo "$p" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r ".$2"; }

echo "══ aguardando serviços ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ eval timeout"; exit 1; }; sleep 1; done
for i in $(seq 1 30); do $CURL "$AUTH/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ auth timeout"; exit 1; }; sleep 1; done

echo "══ login operator ══"
TOK=$($CURL -X POST "$AUTH/auth/login" $JSON -d "{\"email\":\"$OP_EMAIL\",\"password\":\"$OP_PASS\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOK" ] || { echo "  ✗ login falhou (ajuste AUTH/credenciais)"; exit 1; }
SUB=$(jwt_claim "$TOK" sub)
echo "  ✓ operator sub=$SUB"

echo "══ setup: form + campanha + instance(dono=operator) + ingest (cria threads round-1) ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t10d\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t10d_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
IID=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"t10d_sess\",\"evaluated_user_id\":\"$SUB\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$F" ] && [ -n "$C" ] && [ -n "$IID" ] || { echo "  ✗ setup falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"instance_id\":\"$IID\",\"session_id\":\"t10d_sess\",\"campaign_id\":\"$C\",
  \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"human_agent\",
  \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":8,\"justification\":\"nota do avaliador\"}]}" >/dev/null

echo "══ GET threads SÓ com Bearer (sem X-Tenant-ID) — antes 400 ══"
HDR_TENANT=$($CURL -o /dev/null -w '%{http_code}' "$EVAL/v1/evaluation/instances/$IID/threads" -H "Authorization: Bearer $TOK")
TH=$($CURL "$EVAL/v1/evaluation/instances/$IID/threads" -H "Authorization: Bearer $TOK")
assert "http(threads, só Bearer)" "200" "$HDR_TENANT"
assert "threads round-1 (c1)"     "1"   "$(echo "$TH" | jq -r '.threads | length')"

echo "══ POST contest SÓ com Bearer — antes 400; agora abre contestação (Arc 13) ══"
CODE=$($CURL -o /dev/null -w '%{http_code}' -X POST "$EVAL/v1/evaluation/instances/$IID/contest" \
  -H "Authorization: Bearer $TOK" $JSON \
  -d '{"dimension_ids":["c1"],"reasons":{"c1":"discordo da nota atribuida ao criterio"},"round":1}')
assert "http(contest, só Bearer)" "200" "$CODE"

echo "══ result_state mudou open → under_review (canônico, não o legado) ══"
RID=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&campaign_id=$C" -H "Authorization: Bearer $TOK" | jq -r '.results[0].result_id // .results[0].id')
ST=$($CURL "$EVAL/v1/evaluation/results/$RID?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" | jq -r '.result_state')
assert "result_state pós-contest" "under_review" "$ST"

echo
[ "$FAIL" = 0 ] && echo "✅ T10-D OK — Arc 13 acionável só com Bearer (tenant via JWT); contest move result_state" \
                || { echo "❌ T10-D com falhas"; exit 1; }
