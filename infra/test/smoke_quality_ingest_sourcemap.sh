#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# smoke_quality_ingest_sourcemap.sh  (R13c)
# Proves the per-source identity/pool/version map translates EXTERNAL ids → INTERNAL
# before the canonical events are emitted:
#   - PUT a source_map (Config API) for a fresh tenant
#   - POST a contact carrying EXTERNAL pool/agent ids
#   - assert analytics.segments shows the INTERNAL pool_id + skill/version
#   - assert sampling fires under a campaign targeting the INTERNAL pool
#
# A fresh tenant per run avoids the quality-ingest source_map TTL cache (keyed by
# tenant) returning a stale entry from a previous run.
#
# Prereq: demo stack up (quality-ingest, config-api, evaluation-api, analytics-api).
# Usage:  bash infra/test/smoke_quality_ingest_sourcemap.sh
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
INGEST="${INGEST:-http://localhost:3850}"
EVAL="${EVAL:-http://localhost:3400}"
CONFIG="${CONFIG:-http://localhost:3600}"
CONFIG_ADMIN="${CONFIG_ADMIN:-demo_config_admin_token}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
CH="$DC exec -T clickhouse clickhouse-client -u plughub --password plughub -d plughub_demo -q"
PG="$DC exec -T postgres psql -U plughub -d plughub_demo -tAc"
FAIL=0
ok(){ echo "  ✓ $1"; }
bad(){ echo "  ✗ $1"; FAIL=1; }

TENANT="t_sm_$(date +%s)"          # fresh tenant → no stale source_map cache
CID="ext-sm-$(date +%s)"
INT_POOL="retencao_humano"
EXT_POOL="Genesys-Q-42"
EXT_AGENT="gx-bot-1"

echo "══ 1) wait for services ══"
for svc in "$INGEST/v1/health" "$EVAL/health" "$CONFIG/v1/health"; do
  for i in $(seq 1 30); do $CURL "$svc" >/dev/null 2>&1 && { ok "$svc up"; break; }; \
    [ "$i" = 30 ] && { bad "$svc timeout"; exit 1; }; sleep 1; done
done

echo "══ 2) PUT source_map (tenant=$TENANT): $EXT_POOL→$INT_POOL, $EXT_AGENT→skill_retencao_v2/v9 ══"
PUT=$($CURL -X PUT "$CONFIG/config/quality_ingest/source_map" $JSON \
  -H "X-Admin-Token: $CONFIG_ADMIN" -d "{
    \"tenant_id\":\"$TENANT\",
    \"value\":{
      \"ccaas:genesys\":{
        \"pools\":{\"$EXT_POOL\":\"$INT_POOL\"},
        \"agents\":{\"$EXT_AGENT\":{\"kind\":\"ai\",\"skill_id\":\"skill_retencao_v2\",\"deploy_version\":\"v9\"}}
      }
    }}")
echo "$PUT" | grep -q '"ok":true' && ok "source_map stored" || { bad "PUT source_map failed: $PUT"; exit 1; }

echo "══ 3) create form + active campaign targeting INTERNAL pool $INT_POOL ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"qi_sm\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] && ok "form=$F" || { bad "form create failed"; exit 1; }
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"qi_sm_camp\",\"form_id\":\"$F\",
  \"evaluation_pool_id\":\"$INT_POOL\",\"sampling_rules\":{\"mode\":\"all\"}}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] && ok "campaign=$C (targets $INT_POOL)" || { bad "campaign create failed"; exit 1; }

echo "══ 4) POST contact with EXTERNAL ids (pool=$EXT_POOL agent=$EXT_AGENT) ══"
RESP=$($CURL -X POST "$INGEST/v1/ingest/events" $JSON -H "X-Tenant-ID: $TENANT" -d "[
  {\"event_type\":\"contact.opened\",\"external_contact_id\":\"$CID\",\"source\":\"ccaas:genesys\",
   \"channel\":\"voice\",\"opened_at\":\"2026-06-24T12:00:00Z\"},
  {\"event_type\":\"participant.joined\",\"external_contact_id\":\"$CID\",\"segment_ref\":\"s1\",
   \"external_agent_id\":\"$EXT_AGENT\",\"agent_kind\":\"ai\",\"pool_id\":\"$EXT_POOL\",
   \"started_at\":\"2026-06-24T12:00:05Z\"},
  {\"event_type\":\"message.sent\",\"external_contact_id\":\"$CID\",\"ts\":\"2026-06-24T12:00:10Z\",
   \"author_role\":\"customer\",\"content\":\"ola\",\"masked\":true},
  {\"event_type\":\"participant.left\",\"external_contact_id\":\"$CID\",\"segment_ref\":\"s1\",
   \"ended_at\":\"2026-06-24T12:05:00Z\",\"outcome\":\"resolved\"},
  {\"event_type\":\"contact.closed\",\"external_contact_id\":\"$CID\",\"outcome\":\"resolved\",
   \"closed_at\":\"2026-06-24T12:05:01Z\"}
]")
SID=$(echo "$RESP" | jq -r '.session_ids[0] // empty')
[ -n "$SID" ] && ok "session_id=$SID" || { bad "no session_id: $RESP"; exit 1; }

echo "══ 5) analytics.segments carries INTERNAL identities (translated) ══"
GOT_POOL=""; GOT_FLOW=""; GOT_VER=""
for i in $(seq 1 30); do
  GOT_POOL=$($CH "SELECT any(pool_id) FROM plughub_demo.segments WHERE session_id='$SID'" 2>/dev/null | tr -d '[:space:]')
  [ -n "$GOT_POOL" ] && break
  sleep 2
done
GOT_FLOW=$($CH "SELECT any(flow_id) FROM plughub_demo.segments WHERE session_id='$SID'" 2>/dev/null | tr -d '[:space:]')
GOT_VER=$($CH "SELECT any(deploy_version) FROM plughub_demo.segments WHERE session_id='$SID'" 2>/dev/null | tr -d '[:space:]')
[ "$GOT_POOL" = "$INT_POOL" ]        && ok "segment pool_id translated → $GOT_POOL" || bad "pool_id not translated (got '$GOT_POOL', want '$INT_POOL')"
[ "$GOT_FLOW" = "skill_retencao_v2" ] && ok "segment flow_id from map → $GOT_FLOW"   || bad "flow_id not from map (got '$GOT_FLOW')"
[ "$GOT_VER" = "v9" ]                 && ok "segment deploy_version from map → $GOT_VER" || bad "deploy_version not from map (got '$GOT_VER')"
# external ids must NOT leak as pool/flow
[ "$GOT_POOL" != "$EXT_POOL" ] && ok "external pool did not leak" || bad "external pool leaked into analytics"

echo "══ 6) sampling fired under campaign targeting INTERNAL pool ══"
INST=0
for i in $(seq 1 30); do
  INST=$($PG "SELECT count(*) FROM evaluation.instances WHERE session_id='$SID' AND campaign_id='$C';" 2>/dev/null | tr -d '[:space:]')
  [ "${INST:-0}" -ge 1 ] 2>/dev/null && break
  sleep 2
done
[ "${INST:-0}" -ge 1 ] && ok "evaluation instance created (count=$INST)" || bad "no instance — pool translation may have missed sampling"

echo
[ "$FAIL" = 0 ] && echo "✅ R13c smoke OK — source_map translated external→internal end to end" \
                || { echo "❌ R13c smoke FAILED — see ✗ above"; exit 1; }
