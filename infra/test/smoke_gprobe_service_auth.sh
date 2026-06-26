#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE fase 2 — smoke da credencial de serviço + leitura any-of (evaluation-api).
#
# Valida os três tipos de gate introduzidos:
#   1. _require_service (STRICT X-Service-Token) — endpoint de sistema (/dispatch/scan):
#        sem token → 401 ; com X-Service-Token → 200.
#   2. _require_service_or_eval_write — ação de ops dual (/campaigns/{id}/dispatch):
#        sem credencial → 401 ; com X-Service-Token → 200 ; com Bearer (admin, ABAC
#        formularios:rw) → 200.
#   3. _require_any_evaluation — leitura de lista (/forms):
#        anônimo → 200 (degrada) ; Bearer admin → 200 ; Bearer SEM grant evaluation → 403.
#
# Pré-req: stack demo no ar (evaluation-api + auth-api). Setup de form/campanha usa o
# Bearer do admin (Fase 1 gateia create_form/create_campaign em formularios:rw).
# Mint do JWT sem grant evaluation é feito dentro do container evaluation-api (pyjwt +
# jwt_secret), pelo mesmo segredo que a API valida.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
SVC="${SVC:-changeme_eval_service_token_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ eval timeout"; exit 1; }; sleep 1; done

# Minta um JWT HS256 assinado com o MESMO jwt_secret que a evaluation-api valida (no
# container, que tem pyjwt). Independe do seed de usuários do demo — o gate valida só a
# assinatura + o module_config. $1 = module_config JSON.
mint() {
  $DC exec -T evaluation-api python - "$JWT_SECRET" "$1" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, json, jwt
print(jwt.encode({"sub": "u_smoke", "tenant_id": "tenant_demo", "roles": ["operator"],
                  "module_config": json.loads(sys.argv[2])}, sys.argv[1], algorithm="HS256"))
PY
}

echo "══ mint Bearer com evaluation.formularios:read_write (setup + caminho da UI) ══"
TOK_RW=$(mint '{"evaluation":{"formularios":{"access":"read_write","scope":[]}}}')
[ -n "$TOK_RW" ] || { echo "  ✗ mint falhou (DC/exec/pyjwt indisponível?)"; exit 1; }
echo "  ✓ Bearer formularios:rw mintado"
BH="Authorization: Bearer $TOK_RW"   # header completo (passado como UM arg via -H "$BH")

echo "══ setup: form + campanha (com Bearer admin — Fase 1) ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -H "$BH" -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"gprobe_smoke\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -H "$BH" -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"gprobe_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup form/camp falhou (Bearer admin tem formularios:rw?)"; exit 1; }
echo "  ✓ form=$F campaign=$C"

echo "══ 1. _require_service (STRICT) — POST /dispatch/scan ══"
assert "scan sem token → 401" 401 "$(code -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C")"
assert "scan com X-Service-Token → 200" 200 "$(code -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C" -H "X-Service-Token: $SVC")"
assert "scan com X-Admin-Token (sem fallback) → 401" 401 "$(code -X POST "$EVAL/v1/evaluation/dispatch/scan?tenant_id=$TENANT&campaign_id=$C" -H "X-Admin-Token: $SVC")"

echo "══ 2. _require_service_or_eval_write — POST /campaigns/{id}/dispatch ══"
assert "dispatch sem credencial → 401" 401 "$(code -X POST "$EVAL/v1/evaluation/campaigns/$C/dispatch?tenant_id=$TENANT")"
assert "dispatch com X-Service-Token → 200" 200 "$(code -X POST "$EVAL/v1/evaluation/campaigns/$C/dispatch?tenant_id=$TENANT" -H "X-Service-Token: $SVC")"
assert "dispatch com Bearer admin (formularios:rw) → 200" 200 "$(code -X POST "$EVAL/v1/evaluation/campaigns/$C/dispatch?tenant_id=$TENANT" -H "$BH")"

echo "══ 3. _require_any_evaluation — GET /forms ══"
assert "lista anônima → 200 (degrada)" 200 "$(code "$EVAL/v1/evaluation/forms?tenant_id=$TENANT")"
assert "lista com Bearer admin → 200" 200 "$(code "$EVAL/v1/evaluation/forms?tenant_id=$TENANT" -H "$BH")"

# JWT com module_config SEM nenhum grant evaluation → 403 (mesmo segredo, outro módulo).
NOEVAL=$(mint '{"contacts":{"operacao":{"access":"read_write","scope":[]}}}')
if [ -n "$NOEVAL" ]; then
  assert "lista com Bearer SEM grant evaluation → 403" 403 \
    "$(code "$EVAL/v1/evaluation/forms?tenant_id=$TENANT" -H "Authorization: Bearer $NOEVAL")"
else
  echo "  ⚠ pulado: não consegui mintar JWT sem grant (DC/exec indisponível)"
fi

echo
if [ "$FAIL" = 0 ]; then echo "✅ G-PROBE service-auth smoke: tudo verde"; else echo "❌ G-PROBE service-auth smoke: falhas acima"; fi
exit $FAIL
