#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T11 — Relatórios Oficial × Operacional (§17.3).
# A (ingest): publica um evento evaluation_finalized no Kafka → o consumer da
#   analytics-api grava em evaluation_finalized (invariante de qualidade).
# B (query): semeia +2 finalized + 1 provisório (evaluation_results) direto no CH,
#   e valida GET /reports/evaluations/quality:
#     - mode=oficial → só finalized (3); fatiável por finalize_reason
#     - mode=operacional → finalized ∪ provisório (4; provisional_n=1)
# Escopado por campaign_id único p/ asserts determinísticos.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
ANALYTICS="${ANALYTICS:-http://localhost:3500}"
CH="${CH:-http://localhost:8123}"; CH_USER="${CH_USER:-plughub}"; CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
CAMP="${CAMP:-t11_camp_$$}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ aguardando analytics-api ══"
for i in $(seq 1 30); do $CURL "$ANALYTICS/v1/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ A — publica evaluation_finalized no Kafka (consumer → evaluation_finalized) ══"
EVENT="{\"event_type\":\"evaluation_finalized\",\"event_id\":\"t11ev_$$\",\"tenant_id\":\"$TENANT\",\"result_id\":\"evr_t11k_$$\",\"instance_id\":\"t11_k_$$\",\"session_id\":\"t11_sess_k\",\"campaign_id\":\"$CAMP\",\"final_score\":9.0,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"seg_k\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1200,\"timestamp\":\"2026-06-19T12:00:00Z\"}"
echo "$EVENT" | $COMPOSE exec -T kafka /opt/kafka/bin/kafka-console-producer.sh \
  --topic evaluation.events --bootstrap-server kafka:29092 >/dev/null 2>&1 \
  || { echo "  ⚠ falha ao publicar no Kafka (ajuste COMPOSE); seguindo só com seed direto"; }

echo "  aguardando consumo (até ~25s)…"
GOT=0
for i in $(seq 1 25); do
  N=$(ch "SELECT count() FROM $DB.evaluation_finalized WHERE campaign_id='$CAMP' AND instance_id='t11_k_$$'")
  [ "${N:-0}" -ge 1 ] && { GOT=1; break; }; sleep 1
done
assert "A: finalized ingerido via Kafka" "1" "$GOT"

echo "══ B — seed direto: +2 finalized (revised/upheld) + 1 provisório ══"
ch "INSERT INTO $DB.evaluation_finalized
    (instance_id,result_id,session_id,tenant_id,campaign_id,final_score,finalize_reason,contestation_state,evaluated_agent_type,segment_id,form_version,round,process_duration_ms,timestamp,date)
    FORMAT JSONEachRow
{\"instance_id\":\"t11_a\",\"result_id\":\"evr_a\",\"session_id\":\"s_a\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$CAMP\",\"final_score\":0.7,\"finalize_reason\":\"revised\",\"contestation_state\":\"revised\",\"evaluated_agent_type\":\"human_agent\",\"segment_id\":\"seg_a\",\"form_version\":1,\"round\":2,\"process_duration_ms\":1000,\"timestamp\":\"2026-06-19 12:01:00.000\",\"date\":\"2026-06-19\"}
{\"instance_id\":\"t11_b\",\"result_id\":\"evr_b\",\"session_id\":\"s_b\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$CAMP\",\"final_score\":0.5,\"finalize_reason\":\"upheld\",\"contestation_state\":\"upheld\",\"evaluated_agent_type\":\"human_agent\",\"segment_id\":\"seg_b\",\"form_version\":1,\"round\":2,\"process_duration_ms\":1000,\"timestamp\":\"2026-06-19 12:02:00.000\",\"date\":\"2026-06-19\"}" >/dev/null
ch "INSERT INTO $DB.evaluation_results
    (result_id,instance_id,session_id,tenant_id,evaluator_id,form_id,campaign_id,overall_score,eval_status,locked,compliance_flags,timestamp,date)
    FORMAT JSONEachRow
{\"result_id\":\"evr_prov\",\"instance_id\":\"t11_prov\",\"session_id\":\"s_prov\",\"tenant_id\":\"$TENANT\",\"evaluator_id\":\"ag\",\"form_id\":\"f\",\"campaign_id\":\"$CAMP\",\"overall_score\":0.6,\"eval_status\":\"submitted\",\"locked\":0,\"compliance_flags\":[],\"timestamp\":\"2026-06-19 12:03:00.000\",\"date\":\"2026-06-19\"}" >/dev/null
sleep 1

Q="$ANALYTICS/reports/evaluations/quality?tenant_id=$TENANT&campaign_id=$CAMP&from_dt=2026-06-01&to_dt=2026-06-30"

echo "══ mode=oficial (só finalized = 3) ══"
R=$($CURL "$Q&mode=oficial")
echo "    $(echo "$R" | jq -c '{mode,tf:.meta.total_finalized,tp:.meta.total_provisional,reasons:.finalize_reasons}')"
assert "oficial mode"             "oficial" "$(echo "$R" | jq -r '.mode')"
assert "oficial total_finalized"  "3"       "$(echo "$R" | jq -r '.meta.total_finalized')"
assert "oficial total_provisional" "0"      "$(echo "$R" | jq -r '.meta.total_provisional')"
assert "oficial grupo n"          "3"       "$(echo "$R" | jq -r '.data[0].n')"
assert "reason uncontested"       "1"       "$(echo "$R" | jq -r '.finalize_reasons.uncontested // 0')"
assert "reason revised"           "1"       "$(echo "$R" | jq -r '.finalize_reasons.revised // 0')"

echo "══ mode=operacional (finalized ∪ provisório = 4) ══"
R=$($CURL "$Q&mode=operacional")
echo "    $(echo "$R" | jq -c '{mode,n:.data[0].n,fin:.data[0].finalized_n,prov:.data[0].provisional_n}')"
assert "operacional mode"            "operacional" "$(echo "$R" | jq -r '.mode')"
assert "operacional grupo n"         "4"           "$(echo "$R" | jq -r '.data[0].n')"
assert "operacional finalized_n"     "3"           "$(echo "$R" | jq -r '.data[0].finalized_n')"
assert "operacional provisional_n"   "1"           "$(echo "$R" | jq -r '.data[0].provisional_n')"

echo "══ fatiamento: mode=oficial & finalize_reason=revised → 1 ══"
R=$($CURL "$Q&mode=oficial&finalize_reason=revised")
assert "slice revised total_finalized" "1" "$(echo "$R" | jq -r '.meta.total_finalized')"

echo
[ "$FAIL" = 0 ] && echo "✅ T11 OK — evaluation_finalized via Kafka + /quality Oficial×Operacional + fatiamento" \
                || { echo "❌ T11 com falhas"; exit 1; }
