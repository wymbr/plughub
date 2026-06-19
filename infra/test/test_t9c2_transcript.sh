#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T9-C2 — evaluation-api: GET /v1/evaluation/results/{id}/transcript.
# Orquestra result → session_id+segment_id, gate por ABAC de avaliação (aqui
# anônimo = aberto por tenant) e DELEGA ao analytics-api (T9-C1) a leitura
# mascarada sobre analytics.messages, janelada pelo segmento avaliado.
#
# Setup: semeia ClickHouse (4 msgs + 1 segmento seg_mid [12:00:30,12:02:30] →
# janela cobre m1,m2). Cria form+campanha+instance(session+segment)+ingest;
# o ingest propaga instance.segment_id → result. Valida via evaluation-api:
#   scope=segment → 2 msgs (m1,m2);  scope=contact → 4;  masked=true; ids alinhados.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
CH="${CH:-http://localhost:8123}"
CH_USER="${CH_USER:-plughub}"; CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
SESS="${SESS:-t9c2_sess_1}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ seed ClickHouse: 4 msgs + 1 segmento ══"
ch "ALTER TABLE $DB.messages DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
ch "ALTER TABLE $DB.segments DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
sleep 1
ch "INSERT INTO $DB.messages
    (message_id,tenant_id,session_id,author_id,author_role,channel,content_type,visibility,content,timestamp,date)
    FORMAT JSONEachRow
{\"message_id\":\"m0\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"cartao ****1234\",\"timestamp\":\"2026-06-19 12:00:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"m1\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"confirmo final 1234\",\"timestamp\":\"2026-06-19 12:01:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"m2\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"obrigado\",\"timestamp\":\"2026-06-19 12:02:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"m3\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"ate logo\",\"timestamp\":\"2026-06-19 12:03:00.000\",\"date\":\"2026-06-19\"}" >/dev/null
ch "INSERT INTO $DB.segments
    (segment_id,session_id,tenant_id,participant_id,pool_id,agent_type_id,instance_id,role,agent_type,sequence_index,started_at,ended_at,duration_ms,date)
    FORMAT JSONEachRow
{\"segment_id\":\"seg_mid\",\"session_id\":\"$SESS\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"ag1\",\"pool_id\":\"$EVAL_POOL_ID\",\"agent_type_id\":\"agente_x_v1\",\"instance_id\":\"i1\",\"role\":\"primary\",\"agent_type\":\"human_agent\",\"sequence_index\":0,\"started_at\":\"2026-06-19 12:00:30.000\",\"ended_at\":\"2026-06-19 12:02:30.000\",\"duration_ms\":120000,\"date\":\"2026-06-19\"}" >/dev/null
sleep 1
assert "msgs semeadas" "4" "$(ch "SELECT count() FROM $DB.messages WHERE session_id='$SESS'")"

echo "══ setup: form + campanha + instance(seg_mid) + ingest ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t9c2\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t9c2_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup falhou (form=$F camp=$C)"; exit 1; }
I=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$SESS\",\"segment_id\":\"seg_mid\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$I" ] || { echo "  ✗ instance falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"instance_id\":\"$I\",\"session_id\":\"$SESS\",\"campaign_id\":\"$C\",
  \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"human_agent\",
  \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":8,\"justification\":\"x\"}]}" >/dev/null
RID=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=$SESS" \
  | jq -r '.results[0].result_id // .results[0].id // empty')
[ -n "$RID" ] || { echo "  ✗ result_id não encontrado"; exit 1; }
echo "  ✓ result_id=$RID (segment_id propagado do instance)"

echo "══ transcript scope=segment (janela cobre m1,m2) ══"
R=$($CURL "$EVAL/v1/evaluation/results/$RID/transcript?tenant_id=$TENANT&scope=segment")
echo "    $(echo "$R" | jq -c '{scope,masked,seg:.segment_id,n:(.messages|length),ids:[.messages[].stream_entry_id]}')"
assert "scope"                "segment" "$(echo "$R" | jq -r '.scope')"
assert "segment_id"           "seg_mid" "$(echo "$R" | jq -r '.segment_id')"
assert "masked"               "true"    "$(echo "$R" | jq -r '.masked')"
assert "n_messages(segment)"  "2"       "$(echo "$R" | jq -r '.messages|length')"
assert "primeira msg id"      "m1"      "$(echo "$R" | jq -r '.messages[0].stream_entry_id')"
assert "ultima msg id"        "m2"      "$(echo "$R" | jq -r '.messages[-1].stream_entry_id')"
assert "conteúdo mascarado"   "confirmo final 1234" "$(echo "$R" | jq -r '.messages[0].content')"

echo "══ transcript scope=contact (sessão inteira = 4) ══"
R=$($CURL "$EVAL/v1/evaluation/results/$RID/transcript?tenant_id=$TENANT&scope=contact")
assert "scope"                "contact" "$(echo "$R" | jq -r '.scope')"
assert "n_messages(contact)"  "4"       "$(echo "$R" | jq -r '.messages|length')"

echo "══ result inexistente → 404 ══"
CODE=$($CURL -o /dev/null -w '%{http_code}' "$EVAL/v1/evaluation/results/nao_existe/transcript?tenant_id=$TENANT")
assert "http(result inexistente)" "404" "$CODE"

echo
[ "$FAIL" = 0 ] && echo "✅ T9-C2 OK — evaluation-api orquestra result→sessão/segmento e delega transcript mascarado ao analytics-api" \
                || { echo "❌ T9-C2 com falhas"; exit 1; }
