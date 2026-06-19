#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T9-C.fix — justificativa + evidência por critério no ingest (alimenta o nível 3).
# Antes: create_criterion_responses gravava só `notes`/`evidence`; o avaliador IA emite
# `justification` + (às vezes) `evidence_entries` → a justificativa por critério sumia e
# os chips de evidência (clicáveis → transcript, C.3) ficavam vazios na UI.
#
# Valida que o ingest agora persiste:
#   - notes ← justification (fallback)
#   - evidence ← evidence (chave que já batia)  E  evidence ← evidence_entries (fallback)
#   - evidence volta como ARRAY parseado com stream_entry_id (consumível pela UI)
# Imprime a URL do nível 3 p/ conferência manual no browser.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
CH="${CH:-http://localhost:8123}"; CH_USER="${CH_USER:-plughub}"; CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
SESS="${SESS:-t9cfix_sess_1}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ seed ClickHouse: 2 msgs (alvos de evidência) + 1 segmento ══"
ch "ALTER TABLE $DB.messages DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
ch "ALTER TABLE $DB.segments DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
sleep 1
ch "INSERT INTO $DB.messages
    (message_id,tenant_id,session_id,author_id,author_role,channel,content_type,visibility,content,timestamp,date)
    FORMAT JSONEachRow
{\"message_id\":\"mfix1\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"bom dia, meu nome e joao, como posso ajudar?\",\"timestamp\":\"2026-06-19 12:01:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"mfix2\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"quero cancelar meu plano\",\"timestamp\":\"2026-06-19 12:01:30.000\",\"date\":\"2026-06-19\"}" >/dev/null
ch "INSERT INTO $DB.segments
    (segment_id,session_id,tenant_id,participant_id,pool_id,agent_type_id,instance_id,role,agent_type,sequence_index,started_at,ended_at,duration_ms,date)
    FORMAT JSONEachRow
{\"segment_id\":\"seg_fix\",\"session_id\":\"$SESS\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"ag1\",\"pool_id\":\"$EVAL_POOL_ID\",\"agent_type_id\":\"agente_x_v1\",\"instance_id\":\"i1\",\"role\":\"primary\",\"agent_type\":\"human_agent\",\"sequence_index\":0,\"started_at\":\"2026-06-19 12:00:30.000\",\"ended_at\":\"2026-06-19 12:02:30.000\",\"duration_ms\":120000,\"date\":\"2026-06-19\"}" >/dev/null
sleep 1

echo "══ setup: form (c1,c2) + campanha + instance + ingest com justification/evidence ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t9cfix\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"Saudação\",\"type\":\"score\",\"weight\":1,\"max_score\":10},
      {\"criterion_id\":\"c2\",\"label\":\"Resolução\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t9cfix_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
I=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$SESS\",\"segment_id\":\"seg_fix\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$F" ] && [ -n "$C" ] && [ -n "$I" ] || { echo "  ✗ setup falhou (form=$F camp=$C inst=$I)"; exit 1; }

# c1 usa chave `evidence`; c2 usa `evidence_entries` (testa os dois fallbacks).
$CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"instance_id\":\"$I\",\"session_id\":\"$SESS\",\"campaign_id\":\"$C\",
  \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"human_agent\",
  \"criterion_responses\":[
    {\"criterion_id\":\"c1\",\"score\":8,\"justification\":\"A saudacao seguiu o protocolo: o agente se identificou e ofereceu ajuda de forma cordial no inicio do atendimento.\",
     \"evidence\":[{\"stream_entry_id\":\"mfix1\",\"excerpt\":\"bom dia, meu nome e joao\",\"relevance_note\":\"abertura cordial com identificacao\"}]},
    {\"criterion_id\":\"c2\",\"score\":6,\"justification\":\"A resolucao foi parcial; o pedido de cancelamento foi compreendido mas a tratativa nao concluiu dentro do esperado.\",
     \"evidence_entries\":[{\"stream_entry_id\":\"mfix2\",\"excerpt\":\"quero cancelar meu plano\",\"relevance_note\":\"pedido explicito do cliente\"}]}
  ]}" >/dev/null

RID=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=$SESS" \
  | jq -r '.results[0].result_id // .results[0].id // empty')
[ -n "$RID" ] || { echo "  ✗ result_id não encontrado"; exit 1; }

echo "══ GET /results/{id}/criteria — justificativa + evidência persistidas ══"
R=$($CURL "$EVAL/v1/evaluation/results/$RID/criteria?tenant_id=$TENANT")
echo "    $(echo "$R" | jq -c '[.criterion_responses[] | {id:.criterion_id, notes:(.notes!=null), ev:(.evidence|length), sid:.evidence[0].stream_entry_id}]')"
C1=$(echo "$R" | jq -c '.criterion_responses[] | select(.criterion_id=="c1")')
C2=$(echo "$R" | jq -c '.criterion_responses[] | select(.criterion_id=="c2")')
assert "c1 notes preenchida"   "true"  "$(echo "$C1" | jq -r '(.notes!=null and (.notes|length>0))')"
assert "c1 evidence (n)"       "1"     "$(echo "$C1" | jq -r '.evidence|length')"
assert "c1 evidence stream_id" "mfix1" "$(echo "$C1" | jq -r '.evidence[0].stream_entry_id')"
assert "c2 notes (justification fallback)" "true" "$(echo "$C2" | jq -r '(.notes!=null and (.notes|length>0))')"
assert "c2 evidence (evidence_entries fallback)" "1" "$(echo "$C2" | jq -r '.evidence|length')"
assert "c2 evidence stream_id" "mfix2" "$(echo "$C2" | jq -r '.evidence[0].stream_entry_id')"

echo
echo "  ▶ confira no browser: /evaluation/evaluations/$C/$RID"
echo "    (justificativa sob cada critério; clique no chip mfix1/mfix2 → rola/destaca no transcript)"
echo
[ "$FAIL" = 0 ] && echo "✅ T9-C.fix OK — ingest persiste justification→notes e evidence/evidence_entries→evidence com stream_entry_id" \
                || { echo "❌ T9-C.fix com falhas"; exit 1; }
