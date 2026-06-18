#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T17 (core) — janela de DADOS da campanha (period_start/period_end por closed_at).
# Valida o round-trip dos campos (create/update/get). O filtro FORWARD em si roda no
# consumer de conversations.session_closed (_sample_one_target → _within_campaign_window,
# lógica pura: closed_at ∈ [period_start, period_end], NULL=aberto). Backfill = follow-up.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
norm() { echo "$1" | sed 's/+00:00/Z/; s/\.000000Z/Z/; s/ /T/'; }  # tolera variações de formato

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t17\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }

echo "══ CASO 1 — create com janela [2020,2021] → persiste ══"
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t17_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",
  \"period_start\":\"2020-01-01T00:00:00Z\",\"period_end\":\"2021-01-01T00:00:00Z\"}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] || { echo "  ✗ campaign falhou"; exit 1; }
G=$($CURL "$EVAL/v1/evaluation/campaigns/$C?tenant_id=$TENANT")
assert "period_start" 2020-01-01T00:00:00Z "$(norm "$(echo "$G" | jq -r '.period_start')")"
assert "period_end"   2021-01-01T00:00:00Z "$(norm "$(echo "$G" | jq -r '.period_end')")"

echo "══ CASO 2 — update period_end → 2030 ══"
$CURL -X PUT "$EVAL/v1/evaluation/campaigns/$C?tenant_id=$TENANT" $JSON \
  -d '{"period_end":"2030-01-01T00:00:00Z"}' >/dev/null
G=$($CURL "$EVAL/v1/evaluation/campaigns/$C?tenant_id=$TENANT")
assert "period_end (após update)" 2030-01-01T00:00:00Z "$(norm "$(echo "$G" | jq -r '.period_end')")"

echo "══ CASO 3 — create sem janela → NULL (aberto) ══"
C2=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t17_open\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
G2=$($CURL "$EVAL/v1/evaluation/campaigns/$C2?tenant_id=$TENANT")
assert "period_start aberto (null)" null "$(echo "$G2" | jq -r '.period_start')"
assert "period_end aberto (null)"   null "$(echo "$G2" | jq -r '.period_end')"

echo
[ "$FAIL" = 0 ] && echo "✅ T17 (core) OK — janela de dados persiste (create/update/get); filtro forward no consumer" \
                || { echo "❌ T17 (core) com falhas"; exit 1; }
