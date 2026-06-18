#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T17-backfill — job batch sobre segmentos persistidos (spec §18.5).
# Valida o CONTRATO + idempotência do endpoint admin (não depende de volume de dados):
#   1. sem period_start → 400 (backfill exige a janela de dados);
#   2. com period_start → 200 com summary {scanned, created, skipped_dup, ...};
#   3. idempotência: 2ª passada cria 0 (dedup por (campaign_id, segment_id)) e varre o
#      mesmo total.
# A criação real de instances depende de `analytics.segments` ter segmentos do pool na
# janela (e2e-dependente da analytics-api/ClickHouse, como demais gotchas).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
CURL="curl -s --max-time 30"
JSON='-H Content-Type:application/json'
ADMH="-H X-Admin-Token:$ADMIN"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t17bf\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }

# ── CASO 1 — sem period_start → 400 ───────────────────────────────────────────
echo "══ CASO 1 — campanha sem period_start → 400 ══"
C0=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t17bf_nostart\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
CODE=$($CURL -o /dev/null -w '%{http_code}' -X POST \
  "$EVAL/v1/evaluation/campaigns/$C0/backfill?tenant_id=$TENANT" $ADMH)
assert "status sem period_start" 400 "$CODE"

# ── CASO 2 — com janela passada → 200 + summary ───────────────────────────────
echo "══ CASO 2 — janela [2020,2030] → 200 com summary ══"
C1=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t17bf_win\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",
  \"period_start\":\"2020-01-01T00:00:00Z\",\"period_end\":\"2030-01-01T00:00:00Z\",
  \"sampling_rules\":{\"mode\":\"all\"}}" | jq -r '.campaign_id // .id // empty')
[ -n "$C1" ] || { echo "  ✗ campaign falhou"; exit 1; }
R1=$($CURL -X POST "$EVAL/v1/evaluation/campaigns/$C1/backfill?tenant_id=$TENANT" $ADMH)
echo "    summary: $(echo "$R1" | jq -c '{scanned,created,skipped_dup,skipped_sample}')"
assert "tem campo scanned"    true "$(echo "$R1" | jq -r 'has("scanned")')"
assert "tem campo created"    true "$(echo "$R1" | jq -r 'has("created")')"
assert "tem campo skipped_dup" true "$(echo "$R1" | jq -r 'has("skipped_dup")')"
SCANNED1=$(echo "$R1" | jq -r '.scanned')
CREATED1=$(echo "$R1" | jq -r '.created')

# ── CASO 3 — idempotência: 2ª passada cria 0, varre o mesmo total ─────────────
echo "══ CASO 3 — re-run → created=0 (dedup), scanned igual ══"
R2=$($CURL -X POST "$EVAL/v1/evaluation/campaigns/$C1/backfill?tenant_id=$TENANT" $ADMH)
assert "created (2ª passada)" 0 "$(echo "$R2" | jq -r '.created')"
assert "scanned estável"  "$SCANNED1" "$(echo "$R2" | jq -r '.scanned')"
# se a 1ª criou K>0, a 2ª deve tê-los como skipped_dup (>= K)
DUP2=$(echo "$R2" | jq -r '.skipped_dup')
if [ "${CREATED1:-0}" -gt 0 ] 2>/dev/null; then
  if [ "$DUP2" -ge "$CREATED1" ] 2>/dev/null; then echo "  ✓ skipped_dup($DUP2) ≥ created1($CREATED1)"; \
    else echo "  ✗ skipped_dup($DUP2) < created1($CREATED1)"; FAIL=1; fi
else
  echo "  ⓘ sem segmentos na janela (created1=0) — idempotência validada trivialmente"
fi

echo
[ "$FAIL" = 0 ] && echo "✅ T17-backfill OK — contrato (400 sem janela; summary) + idempotência (dedup)" \
                || { echo "❌ T17-backfill com falhas"; exit 1; }
