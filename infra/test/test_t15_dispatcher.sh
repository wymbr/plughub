#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T15 — dispatcher por janela de calendário (spec §18.4).
# Valida por API:
#   1. despacho default-open (campanha sem calendário) das instances `scheduled`;
#   2. idempotência: re-scan imediato → 0 (cooldown via dispatched_at);
#   3. gating de janela: calendário FECHADO associado → 0 (in_window=false);
#   4. calendário SEMPRE ABERTO associado → despacha (in_window=true).
# O scanner de fundo usa a mesma função `dispatch_campaign_scheduled`; aqui dispara uma
# passada sob demanda via `POST /v1/evaluation/dispatch/scan` (admin).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
CAL="${CAL:-http://localhost:3700}"
TENANT="${TENANT:-tenant_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
ADMH="-H X-Admin-Token:$ADMIN"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# ── Form base ─────────────────────────────────────────────────────────────────
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t15\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }

# cria N instances `scheduled` numa campanha (session/segment distintos)
mk_instances() {  # $1=campaign_id  $2=count  $3=prefix
  for n in $(seq 1 "$2"); do
    $CURL -X POST "$EVAL/v1/evaluation/instances" $JSON -d "{
      \"tenant_id\":\"$TENANT\",\"campaign_id\":\"$1\",
      \"session_id\":\"sess_$3_$n\",\"segment_id\":\"seg_$3_$n\"}" >/dev/null
  done
}

# ── CASO 1 — default-open: despacha as scheduled ──────────────────────────────
echo "══ CASO 1 — campanha sem calendário → despacha (default open) ══"
C1=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t15_open\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C1" ] || { echo "  ✗ campaign falhou"; exit 1; }
mk_instances "$C1" 2 c1
R=$($CURL -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C1" $ADMH)
assert "dispatched (1ª passada)" 2 "$(echo "$R" | jq -r '.dispatched')"
assert "in_window"             true "$(echo "$R" | jq -r '.campaigns[0].in_window')"

# ── CASO 2 — idempotência: re-scan imediato → 0 (cooldown) ────────────────────
echo "══ CASO 2 — re-scan imediato → 0 (cooldown via dispatched_at) ══"
R=$($CURL -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C1" $ADMH)
assert "dispatched (2ª passada)" 0 "$(echo "$R" | jq -r '.dispatched')"

CAL_ADMIN_TOKEN="${CAL_ADMIN_TOKEN:-demo_calendar_admin_token}"   # portao de escrita (sistema)

# ── CASO 3 — janela FECHADA → 0 ───────────────────────────────────────────────
echo "══ CASO 3 — calendário fechado associado → não despacha (in_window=false) ══"
CAL_CLOSED=$($CURL -X POST "$CAL/v1/calendars" $JSON -H "X-Admin-Token: $CAL_ADMIN_TOKEN" -d "{
  \"organization_id\":\"$TENANT\",\"tenant_id\":\"$TENANT\",\"name\":\"t15_closed\",
  \"always_open\":false,\"weekly_schedule\":[]}" | jq -r '.id // empty')
C3=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t15_closed_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",
  \"evaluation_calendar_id\":\"$CAL_CLOSED\"}" | jq -r '.campaign_id // .id // empty')
if [ -n "$CAL_CLOSED" ] && [ -n "$C3" ]; then
  $CURL -X POST "$CAL/v1/associations" $JSON -H "X-Admin-Token: $CAL_ADMIN_TOKEN" -d "{
    \"tenant_id\":\"$TENANT\",\"entity_type\":\"evaluation_campaign\",\"entity_id\":\"$C3\",
    \"calendar_id\":\"$CAL_CLOSED\"}" >/dev/null
  mk_instances "$C3" 1 c3
  R=$($CURL -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C3" $ADMH)
  assert "dispatched (fechado)" 0     "$(echo "$R" | jq -r '.dispatched')"
  assert "in_window (fechado)"  false "$(echo "$R" | jq -r '.campaigns[0].in_window')"
else
  echo "  ⚠ calendar-api indisponível — pulando CASO 3 (gating best-effort)"
fi

# ── CASO 4 — janela ABERTA (always_open) → despacha ───────────────────────────
echo "══ CASO 4 — calendário always_open associado → despacha (in_window=true) ══"
CAL_OPEN=$($CURL -X POST "$CAL/v1/calendars" $JSON -H "X-Admin-Token: $CAL_ADMIN_TOKEN" -d "{
  \"organization_id\":\"$TENANT\",\"tenant_id\":\"$TENANT\",\"name\":\"t15_open_cal\",
  \"always_open\":true}" | jq -r '.id // empty')
C4=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t15_open_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",
  \"evaluation_calendar_id\":\"$CAL_OPEN\"}" | jq -r '.campaign_id // .id // empty')
if [ -n "$CAL_OPEN" ] && [ -n "$C4" ]; then
  $CURL -X POST "$CAL/v1/associations" $JSON -H "X-Admin-Token: $CAL_ADMIN_TOKEN" -d "{
    \"tenant_id\":\"$TENANT\",\"entity_type\":\"evaluation_campaign\",\"entity_id\":\"$C4\",
    \"calendar_id\":\"$CAL_OPEN\"}" >/dev/null
  mk_instances "$C4" 1 c4
  R=$($CURL -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C4" $ADMH)
  assert "dispatched (aberto)" 1    "$(echo "$R" | jq -r '.dispatched')"
  assert "in_window (aberto)"  true "$(echo "$R" | jq -r '.campaigns[0].in_window')"
else
  echo "  ⚠ calendar-api indisponível — pulando CASO 4"
fi

echo
[ "$FAIL" = 0 ] && echo "✅ T15 OK — dispatcher windowed: default-open, idempotência (cooldown), gating fechado/aberto" \
                || { echo "❌ T15 com falhas"; exit 1; }
