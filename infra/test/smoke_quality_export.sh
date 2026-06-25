#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# smoke_quality_export.sh  (R13d)
# Proves the internal exporter closes the loop: it reads an existing internal
# session from ClickHouse and re-emits it through the quality-ingest contract,
# producing a NEW re-evaluation session that flows through analytics + sampling.
#
# Chain: import a contact (→ CH sessions/segments/messages) → export it →
#        assert the re-eval session has the ORIGINAL pool + transcript + a
#        scheduled evaluation instance.
#
# Prereq: demo stack up (quality-ingest, quality-export, evaluation-api, clickhouse).
# Usage:  bash infra/test/smoke_quality_export.sh
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
INGEST="${INGEST:-http://localhost:3850}"
EXPORT="${EXPORT:-http://localhost:3852}"
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
POOL="${POOL:-retencao_humano}"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
CH="$DC exec -T clickhouse clickhouse-client -u plughub --password plughub -d plughub_demo -q"
PG="$DC exec -T postgres psql -U plughub -d plughub_demo -tAc"
FAIL=0
ok(){ echo "  ✓ $1"; }
bad(){ echo "  ✗ $1"; FAIL=1; }

CID="ext-exp-$(date +%s)"

echo "══ 1) wait for services ══"
for svc in "$INGEST/v1/health" "$EXPORT/v1/health" "$EVAL/health"; do
  for i in $(seq 1 30); do $CURL "$svc" >/dev/null 2>&1 && { ok "$svc up"; break; }; \
    [ "$i" = 30 ] && { bad "$svc timeout"; exit 1; }; sleep 1; done
done

echo "══ 2) active campaign targeting $POOL (mode=all) ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"qi_exp\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"qi_exp_camp\",\"form_id\":\"$F\",
  \"evaluation_pool_id\":\"$POOL\",\"sampling_rules\":{\"mode\":\"all\"}}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] && ok "campaign=$C" || { bad "campaign create failed"; exit 1; }

echo "══ 3) seed an internal session via import (open/join/2 msgs/left/close) ══"
RESP=$($CURL -X POST "$INGEST/v1/ingest/events" $JSON -H "X-Tenant-ID: $TENANT" -d "[
  {\"event_type\":\"contact.opened\",\"external_contact_id\":\"$CID\",\"source\":\"ccaas:seed\",
   \"channel\":\"voice\",\"opened_at\":\"2026-06-24T12:00:00Z\"},
  {\"event_type\":\"participant.joined\",\"external_contact_id\":\"$CID\",\"segment_ref\":\"s1\",
   \"external_agent_id\":\"agt-1\",\"agent_kind\":\"ai\",\"pool_id\":\"$POOL\",
   \"started_at\":\"2026-06-24T12:00:05Z\",\"skill_id\":\"skill_exp_v1\",\"deploy_version\":\"v1\"},
  {\"event_type\":\"message.sent\",\"external_contact_id\":\"$CID\",\"ts\":\"2026-06-24T12:00:10Z\",
   \"author_role\":\"customer\",\"content\":\"preciso de ajuda\",\"masked\":true},
  {\"event_type\":\"message.sent\",\"external_contact_id\":\"$CID\",\"ts\":\"2026-06-24T12:00:20Z\",
   \"author_role\":\"agent\",\"content\":\"claro\",\"masked\":true,\"segment_ref\":\"s1\"},
  {\"event_type\":\"participant.left\",\"external_contact_id\":\"$CID\",\"segment_ref\":\"s1\",
   \"ended_at\":\"2026-06-24T12:05:00Z\",\"outcome\":\"resolved\"},
  {\"event_type\":\"contact.closed\",\"external_contact_id\":\"$CID\",\"outcome\":\"resolved\",
   \"closed_at\":\"2026-06-24T12:05:01Z\",\"close_reason\":\"flow_complete\"}
]")
ORIG=$(echo "$RESP" | jq -r '.session_ids[0] // empty')
[ -n "$ORIG" ] && ok "internal session_id=$ORIG" || { bad "import failed: $RESP"; exit 1; }

echo "══ 4) wait until CH has the internal session (segments+messages) ══"
for i in $(seq 1 30); do
  SG=$($CH "SELECT count() FROM plughub_demo.segments WHERE session_id='$ORIG'" 2>/dev/null | tr -d '[:space:]')
  MG=$($CH "SELECT count() FROM plughub_demo.messages WHERE session_id='$ORIG'" 2>/dev/null | tr -d '[:space:]')
  [ "${SG:-0}" -ge 1 ] 2>/dev/null && [ "${MG:-0}" -ge 2 ] 2>/dev/null && break
  sleep 2
done
[ "${SG:-0}" -ge 1 ] && [ "${MG:-0}" -ge 2 ] && ok "CH ready (segments=$SG messages=$MG)" || { bad "CH not populated (segments=${SG:-0} messages=${MG:-0})"; exit 1; }

echo "══ 5) export the internal session → re-emit for re-evaluation ══"
REEVAL=""
for i in $(seq 1 15); do
  EXP=$($CURL -X POST "$EXPORT/v1/export/sessions" $JSON -d "{\"tenant_id\":\"$TENANT\",\"session_ids\":[\"$ORIG\"]}")
  REEVAL=$(echo "$EXP" | jq -r '.reeval_session_ids[0] // empty')
  [ -n "$REEVAL" ] && break
  sleep 2
done
echo "  resp: $EXP"
[ -n "$REEVAL" ] && ok "re-eval session_id=$REEVAL (≠ original)" || { bad "export produced no re-eval session"; exit 1; }
[ "$REEVAL" != "$ORIG" ] && ok "re-eval id distinct from original" || bad "re-eval id collided with original"

echo "══ 6) re-eval session in CH carries original pool + transcript ══"
GOT_POOL=""; GOT_FLOW=""; RE_MSG=0
for i in $(seq 1 30); do
  GOT_POOL=$($CH "SELECT any(pool_id) FROM plughub_demo.segments WHERE session_id='$REEVAL'" 2>/dev/null | tr -d '[:space:]')
  RE_MSG=$($CH "SELECT count() FROM plughub_demo.messages WHERE session_id='$REEVAL'" 2>/dev/null | tr -d '[:space:]')
  [ -n "$GOT_POOL" ] && [ "${RE_MSG:-0}" -ge 2 ] 2>/dev/null && break
  sleep 2
done
GOT_FLOW=$($CH "SELECT any(flow_id) FROM plughub_demo.segments WHERE session_id='$REEVAL'" 2>/dev/null | tr -d '[:space:]')
[ "$GOT_POOL" = "$POOL" ]          && ok "re-eval segment pool = original ($GOT_POOL)" || bad "re-eval pool wrong (got '$GOT_POOL')"
[ "$GOT_FLOW" = "skill_exp_v1" ]   && ok "re-eval flow_id preserved ($GOT_FLOW)"       || bad "re-eval flow_id wrong (got '$GOT_FLOW')"
[ "${RE_MSG:-0}" -ge 2 ]           && ok "re-eval transcript present (messages=$RE_MSG)" || bad "re-eval messages count=${RE_MSG:-0} (<2)"

echo "══ 7) sampling fired for the re-eval session ══"
INST=0
for i in $(seq 1 30); do
  INST=$($PG "SELECT count(*) FROM evaluation.instances WHERE session_id='$REEVAL' AND campaign_id='$C';" 2>/dev/null | tr -d '[:space:]')
  [ "${INST:-0}" -ge 1 ] 2>/dev/null && break
  sleep 2
done
[ "${INST:-0}" -ge 1 ] && ok "re-eval evaluation instance scheduled (count=$INST)" || bad "no instance for re-eval session"

echo
[ "$FAIL" = 0 ] && echo "✅ R13d smoke OK — internal session re-evaluated through the same contract" \
                || { echo "❌ R13d smoke FAILED — see ✗ above"; exit 1; }
