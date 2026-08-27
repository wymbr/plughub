#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T9-C1 — leitura de transcript mascarado com janela de segmento (analytics-api).
# Valida GET /v1/transcript/sessions/{id}:
#   - scope=segment + segment_id  → janela [started_at, ended_at] do segmento
#   - scope=contact               → sessão inteira
#   - segment_id desconhecido     → fallback p/ contact (flag scope=contact)
#   - stream_entry_id presente (== message_id, alinha evidência C.3); masked=true
#
# Semeia direto no ClickHouse (porta 8123, db plughub_demo):
#   4 mensagens m0..m3 (12:00 / 12:01 / 12:02 / 12:03)
#   1 segmento seg_mid [12:00:30 , 12:02:30]  → janela cobre m1,m2 (2 msgs)
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim
ANALYTICS="${ANALYTICS:-http://localhost:3500}"
CH="${CH:-http://localhost:8123}"
CH_USER="${CH_USER:-plughub}"
CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
SESS="${SESS:-t9c1_sess_1}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando analytics-api ══"
for i in $(seq 1 30); do $CURL "$ANALYTICS/v1/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ limpeza idempotente (linhas do teste) ══"
ch "ALTER TABLE $DB.messages DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
ch "ALTER TABLE $DB.segments DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
sleep 1

echo "══ seed: 4 mensagens + 1 segmento ══"
MSG_RESP=$(ch "INSERT INTO $DB.messages
    (message_id,tenant_id,session_id,author_id,author_role,channel,content_type,visibility,content,timestamp,date)
    FORMAT JSONEachRow
{\"message_id\":\"m0\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"meu cartao termina ****1234\",\"timestamp\":\"2026-06-19 12:00:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"m1\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"confirmo o final 1234\",\"timestamp\":\"2026-06-19 12:01:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"m2\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"obrigado\",\"timestamp\":\"2026-06-19 12:02:00.000\",\"date\":\"2026-06-19\"}
{\"message_id\":\"m3\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"ate logo\",\"timestamp\":\"2026-06-19 12:03:00.000\",\"date\":\"2026-06-19\"}")

SEG_RESP=$(ch "INSERT INTO $DB.segments
    (segment_id,session_id,tenant_id,participant_id,pool_id,agent_type_id,instance_id,role,agent_type,sequence_index,started_at,ended_at,duration_ms,date)
    FORMAT JSONEachRow
{\"segment_id\":\"seg_mid\",\"session_id\":\"$SESS\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"ag1\",\"pool_id\":\"retencao_humano\",\"agent_type_id\":\"agente_x_v1\",\"instance_id\":\"i1\",\"role\":\"primary\",\"agent_type\":\"ai_agent\",\"sequence_index\":0,\"started_at\":\"2026-06-19 12:00:30.000\",\"ended_at\":\"2026-06-19 12:02:30.000\",\"duration_ms\":120000,\"date\":\"2026-06-19\"}")
sleep 1
[ -z "$MSG_RESP$SEG_RESP" ] || echo "  ⚠ seed resp: msgs='$MSG_RESP' segs='$SEG_RESP'"
assert "count msgs semeadas" "4" "$(ch "SELECT count() FROM $DB.messages WHERE session_id='$SESS'")"
assert "count segs semeados" "1" "$(ch "SELECT count() FROM $DB.segments WHERE session_id='$SESS'")"

echo "══ scope=segment (janela cobre m1,m2) ══"
R=$($CURL "$ANALYTICS/v1/transcript/sessions/$SESS?tenant_id=$TENANT&segment_id=seg_mid&scope=segment")
echo "    $(echo "$R" | jq -c '{scope,masked,n:(.messages|length),ids:[.messages[].stream_entry_id]}')"
assert "scope"               "segment" "$(echo "$R" | jq -r '.scope')"
assert "masked"              "true"    "$(echo "$R" | jq -r '.masked')"
assert "n_messages(segment)" "2"       "$(echo "$R" | jq -r '.messages|length')"
assert "primeira msg id"     "m1"      "$(echo "$R" | jq -r '.messages[0].stream_entry_id')"
assert "ultima msg id"       "m2"      "$(echo "$R" | jq -r '.messages[-1].stream_entry_id')"

echo "══ scope=contact (sessão inteira = 4) ══"
R=$($CURL "$ANALYTICS/v1/transcript/sessions/$SESS?tenant_id=$TENANT&scope=contact")
assert "scope"               "contact" "$(echo "$R" | jq -r '.scope')"
assert "n_messages(contact)" "4"       "$(echo "$R" | jq -r '.messages|length')"

echo "══ segment_id desconhecido → fallback contact ══"
R=$($CURL "$ANALYTICS/v1/transcript/sessions/$SESS?tenant_id=$TENANT&segment_id=nao_existe&scope=segment")
assert "scope(fallback)"     "contact" "$(echo "$R" | jq -r '.scope')"
assert "n_messages(fallback)" "4"      "$(echo "$R" | jq -r '.messages|length')"

echo "══ conteúdo mascarado preservado (display_partial) ══"
R=$($CURL "$ANALYTICS/v1/transcript/sessions/$SESS?tenant_id=$TENANT&scope=contact")
assert "m0 content" "meu cartao termina ****1234" "$(echo "$R" | jq -r '.messages[0].content')"

echo
[ "$FAIL" = 0 ] && echo "✅ T9-C1 OK — transcript janelado por segmento, fallback p/ contato, mascarado, stream_entry_id alinhado" \
                || { echo "❌ T9-C1 com falhas"; exit 1; }
