#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# smoke_quality_ingest.sh  (R13a-2)
# Posts a fixture of 1 imported contact (ingestion_event_v1) to the quality-ingest
# endpoint and verifies the whole reused pipeline lit up:
#   (1) ClickHouse populated — analytics.sessions / messages / segments for the
#       deterministically-derived session_id;
#   (2) sampling fired — an evaluation.instance was scheduled under an active
#       campaign targeting the contact's pool.
#
# The module is a pure producer of internal canonical events; this proves an
# EXTERNAL contact flows through the SAME analytics + sampling path as a live one.
#
# Prereq: demo stack up (incl. quality-ingest, evaluation-api, analytics-api).
#   docker compose -f docker-compose.demo.yml up -d quality-ingest
# Usage:
#   bash infra/test/smoke_quality_ingest.sh
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
INGEST="${INGEST:-http://localhost:3850}"
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
POOL="${POOL:-retencao_humano}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
CH="$DC exec -T clickhouse clickhouse-client -u plughub --password plughub -d plughub_demo -q"
PG="$DC exec -T postgres psql -U plughub -d plughub_demo -tAc"
FAIL=0
ok(){ echo "  ✓ $1"; }
bad(){ echo "  ✗ $1"; FAIL=1; }

CID="ext-smoke-$(date +%s)"
OPENED="2026-06-24T12:00:00Z"

echo "══ 1) wait for services ══"
for svc in "$INGEST/v1/health" "$EVAL/health"; do
  for i in $(seq 1 30); do $CURL "$svc" >/dev/null 2>&1 && { ok "$svc up"; break; }; \
    [ "$i" = 30 ] && { bad "$svc timeout"; exit 1; }; sleep 1; done
done

echo "══ 2) create form + active campaign (sampling mode=all, pool=$POOL) ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"qi_smoke\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] && ok "form=$F" || { bad "form create failed"; exit 1; }

C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"qi_smoke_camp\",\"form_id\":\"$F\",
  \"evaluation_pool_id\":\"$POOL\",\"sampling_rules\":{\"mode\":\"all\"}}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] && ok "campaign=$C (active)" || { bad "campaign create failed"; exit 1; }

echo "══ 3) POST ingestion_event_v1 stream (1 contact: open/join/2 msgs/left/close) ══"
RESP=$($CURL -X POST "$INGEST/v1/ingest/events" $JSON -H "X-Tenant-ID: $TENANT" -d "[
  {\"event_type\":\"contact.opened\",\"external_contact_id\":\"$CID\",\"source\":\"ccaas:smoke\",
   \"channel\":\"voice\",\"opened_at\":\"$OPENED\",\"customer_ref\":\"cust-smoke\"},
  {\"event_type\":\"participant.joined\",\"external_contact_id\":\"$CID\",\"segment_ref\":\"s1\",
   \"external_agent_id\":\"agt-1\",\"agent_kind\":\"ai\",\"pool_id\":\"$POOL\",
   \"started_at\":\"2026-06-24T12:00:05Z\",\"skill_id\":\"skill_smoke_v1\",\"deploy_version\":\"v1\"},
  {\"event_type\":\"message.sent\",\"external_contact_id\":\"$CID\",\"ts\":\"2026-06-24T12:00:10Z\",
   \"author_role\":\"customer\",\"content\":\"meu cpf e 123.456.789-01\",\"masked\":false},
  {\"event_type\":\"message.sent\",\"external_contact_id\":\"$CID\",\"ts\":\"2026-06-24T12:00:20Z\",
   \"author_role\":\"agent\",\"content\":\"vou ajudar\",\"masked\":true,\"segment_ref\":\"s1\"},
  {\"event_type\":\"participant.left\",\"external_contact_id\":\"$CID\",\"segment_ref\":\"s1\",
   \"ended_at\":\"2026-06-24T12:05:00Z\",\"outcome\":\"resolved\"},
  {\"event_type\":\"contact.closed\",\"external_contact_id\":\"$CID\",\"outcome\":\"resolved\",
   \"closed_at\":\"2026-06-24T12:05:01Z\",\"close_reason\":\"flow_complete\"}
]")
echo "  resp: $RESP"
SID=$(echo "$RESP" | jq -r '.session_ids[0] // empty')
EMITTED=$(echo "$RESP" | jq -r '.canonical_emitted // 0')
[ -n "$SID" ] && ok "session_id=$SID (emitted=$EMITTED)" || { bad "no session_id in response"; exit 1; }

echo "══ 4) ClickHouse populated for $SID (allow consumer lag) ══"
S_OK=0; M_OK=0; G_OK=0
for i in $(seq 1 30); do
  SC=$($CH "SELECT count() FROM plughub_demo.sessions WHERE session_id='$SID'" 2>/dev/null | tr -d '[:space:]')
  MC=$($CH "SELECT count() FROM plughub_demo.messages WHERE session_id='$SID'" 2>/dev/null | tr -d '[:space:]')
  GC=$($CH "SELECT count() FROM plughub_demo.segments WHERE session_id='$SID'" 2>/dev/null | tr -d '[:space:]')
  [ "${SC:-0}" -ge 1 ] 2>/dev/null && S_OK=1
  [ "${MC:-0}" -ge 2 ] 2>/dev/null && M_OK=1
  [ "${GC:-0}" -ge 1 ] 2>/dev/null && G_OK=1
  [ "$S_OK$M_OK$G_OK" = "111" ] && break
  sleep 2
done
[ "$S_OK" = 1 ] && ok "analytics.sessions row present (count=$SC)" || bad "no analytics.sessions row (count=${SC:-0})"
[ "$M_OK" = 1 ] && ok "analytics.messages >=2 (count=$MC)"        || bad "messages count=${MC:-0} (<2)"
[ "$G_OK" = 1 ] && ok "analytics.segments row present (count=$GC)" || bad "no analytics.segments row (count=${GC:-0})"

echo "  -- segment detail (pool/agent_type/flow_id/deploy_version/channel):"
$CH "SELECT pool_id, agent_type, flow_id, deploy_version, channel FROM plughub_demo.segments WHERE session_id='$SID' FORMAT Vertical" 2>/dev/null | sed 's/^/     /'
echo "  -- masking net-pass check (customer message must NOT contain raw CPF):"
RAW=$($CH "SELECT count() FROM plughub_demo.messages WHERE session_id='$SID' AND position(content, '123.456.789-01') > 0" 2>/dev/null | tr -d '[:space:]')
[ "${RAW:-0}" = "0" ] && ok "no raw CPF in stored messages" || bad "raw CPF leaked into messages"

echo "══ 4b) R13b — consumer Y rebuilt session_stream_events (PG) for $SID ══"
SSE_MSG=0; SSE_NULL_OK=0
for i in $(seq 1 30); do
  SSE_MSG=$($PG "SELECT count(*) FROM session_stream_events WHERE tenant_id='$TENANT' AND session_id='$SID' AND event_type='message';" 2>/dev/null | tr -d '[:space:]')
  [ "${SSE_MSG:-0}" -ge 2 ] 2>/dev/null && break
  sleep 2
done
[ "${SSE_MSG:-0}" -ge 2 ] && ok "session_stream_events message rows >=2 (count=$SSE_MSG)" || bad "session_stream_events messages count=${SSE_MSG:-0} (<2)"
SSE_OPEN=$($PG "SELECT count(*) FROM session_stream_events WHERE session_id='$SID' AND event_type='session_opened';" 2>/dev/null | tr -d '[:space:]')
SSE_CLOSE=$($PG "SELECT count(*) FROM session_stream_events WHERE session_id='$SID' AND event_type='session_closed';" 2>/dev/null | tr -d '[:space:]')
[ "${SSE_OPEN:-0}" -ge 1 ] && ok "session_opened row present" || bad "no session_opened row"
[ "${SSE_CLOSE:-0}" -ge 1 ] && ok "session_closed row present" || bad "no session_closed row"
# original_content must be NULL for imported (review-blind by construction)
SSE_LEAK=$($PG "SELECT count(*) FROM session_stream_events WHERE session_id='$SID' AND original_content IS NOT NULL;" 2>/dev/null | tr -d '[:space:]')
[ "${SSE_LEAK:-0}" = "0" ] && ok "original_content NULL on all imported rows" || bad "original_content leaked on imported rows (count=$SSE_LEAK)"

echo "══ 5) sampling fired — evaluation.instance scheduled for $SID ══"
INST=0
for i in $(seq 1 30); do
  INST=$($PG "SELECT count(*) FROM evaluation.instances WHERE session_id='$SID' AND campaign_id='$C';" 2>/dev/null | tr -d '[:space:]')
  [ "${INST:-0}" -ge 1 ] 2>/dev/null && break
  sleep 2
done
[ "${INST:-0}" -ge 1 ] && ok "evaluation instance(s) created (count=$INST)" || bad "no evaluation instance for $SID/$C"
$PG "SELECT id, segment_id, evaluated_user_id, deploy_version, status FROM evaluation.instances WHERE session_id='$SID';" 2>/dev/null | sed 's/^/     /'

echo
[ "$FAIL" = 0 ] && echo "✅ R13a-2 smoke OK — external contact flowed through analytics + sampling" \
                || { echo "❌ R13a-2 smoke FAILED — see ✗ above"; exit 1; }
