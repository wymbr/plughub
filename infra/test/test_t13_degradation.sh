#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T13 (core) — degradação: thin-session → instance `skipped`; erro → instance `error`.
# Terminais p/ a camada de trabalho e FORA dos relatórios de qualidade (que filtram
# evaluation_finalized, nunca emitido nesses caminhos). Guard: não transiciona de
# estado terminal. (Fiação no skill — ramo thin + on_failure→error — é follow-up
# e2e-blocked, como o T7b-2.)
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ setup: form + campanha ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t13\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t13_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup falhou"; exit 1; }

new_instance() { $CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"sess-t13-$1\"}" | jq -r '.id // .instance_id // empty'; }
status_of() { $CURL "$EVAL/v1/evaluation/instances/$1?tenant_id=$TENANT" | jq -r '.status'; }
code_skip() { $CURL -o /dev/null -w '%{http_code}' -X POST "$EVAL/v1/evaluation/instances/$1/skip?tenant_id=$TENANT" \
  -H "X-Admin-Token: $ADMIN" $JSON -d '{"reason":"thin_session"}'; }

echo "══ CASO 1 — thin-session → skipped ══"
I1=$(new_instance thin); echo "  instance=$I1 (status inicial=$(status_of "$I1"))"
$CURL -X POST "$EVAL/v1/evaluation/instances/$I1/skip?tenant_id=$TENANT" \
  -H "X-Admin-Token: $ADMIN" $JSON -d '{"reason":"thin_session"}' | jq -c '{status, reason}'
assert "status após skip" skipped "$(status_of "$I1")"

echo "══ CASO 2 — erro de avaliação → error ══"
I2=$(new_instance err)
$CURL -X POST "$EVAL/v1/evaluation/instances/$I2/mark-error?tenant_id=$TENANT" \
  -H "X-Admin-Token: $ADMIN" $JSON -d '{"reason":"evaluation_error","detail":"LLM timeout"}' | jq -c '{status, reason}'
assert "status após mark-error" error "$(status_of "$I2")"

echo "══ CASO 3 — guard: skip de instance já terminal → 409 ══"
assert "http (skip de skipped)" 409 "$(code_skip "$I1")"

echo "══ CASO 4 — relatório Oficial não inclui skipped/error (sem evaluation_finalized) ══"
# Nenhum result finalizado foi gerado p/ estas instances → não aparecem em qualidade.
NRES=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=sess-t13-thin&limit=10" | jq -r '(.results // .data // []) | length')
assert "results de qualidade p/ a sessão thin" 0 "$NRES"

echo
[ "$FAIL" = 0 ] && echo "✅ T13 (core) OK — skipped/error terminais + guard + fora dos relatórios" \
                || { echo "❌ T13 (core) com falhas"; exit 1; }
