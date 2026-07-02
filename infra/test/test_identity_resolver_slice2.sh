#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Identity Resolver — Fase A · Slice 2 (durabilidade PG + fallback)
#
# Valida no channel-gateway (:8010) + Postgres (plughub_demo, schema identity):
#   - promoção efêmero→PG no gatilho concreto (registro de pendência via delegate)
#   - cadastro durável: identity.customers + customer_secondary_keys populados
#   - fallback Redis→PG: após limpar o índice Redis (TTL/cold), resolver por âncora
#     ainda encontra o cliente (matched_by="durable") e reidrata o índice Redis
#
# Tenant dedicado para isolar a limpeza. Redis via docker compose exec; PG via psql.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
CG="${CG:-http://localhost:8010}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_id_smoke}"
PHONE="11988887777"
EMAIL="durabilidade@example.com"
POOL="loja_checkout_io"
TOKEN="plughub_wh_slice2_token_000000000000000000000"
ORIG="orig_sess_slice2"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_ge() { if [ "$2" -ge "$3" ] 2>/dev/null; then echo "  ✓ $1 = $2 (>= $3)"; else echo "  ✗ $1: esperado >= $3, veio [$2]"; FAIL=1; fi; }
redis() { $COMPOSE exec -T redis redis-cli "$@"; }
psql()  { $COMPOSE exec -T postgres psql -U plughub -d plughub_demo -tAc "$1"; }
# NB: `redis` is a shell function (docker exec) → cannot use xargs, and must NOT
# run inside a `while read` pipe (the inner `docker exec -T` steals the pipe stdin).
# Capture the keys first, then loop with word-splitting (hashes have no spaces).
del_pattern() {
  local keys k
  keys=$(redis --scan --pattern "$1")
  for k in $keys; do [ -n "$k" ] && redis del "$k" </dev/null >/dev/null; done
}

echo "══ aguardando channel-gateway ══"
for i in $(seq 1 30); do $CURL "$CG/health" >/dev/null 2>&1 || $CURL "$CG/" >/dev/null 2>&1 && break; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done
echo "  ✓ no ar"

echo "══ schema identity existe (ensure_schema no startup) ══"
assert "schema identity" "identity" "$(psql "SELECT nspname FROM pg_namespace WHERE nspname='identity'")"

echo "══ limpeza idempotente (tenant $TENANT) ══"
del_pattern "$TENANT:identity:*"
del_pattern "$TENANT:customer:prospect:*"
del_pattern "$TENANT:pending_by_customer:*"
redis hdel "$TENANT:resume_tokens" "$TOKEN" >/dev/null 2>&1 || true
psql "DELETE FROM identity.customer_secondary_keys WHERE tenant_id='$TENANT'" >/dev/null 2>&1 || true
psql "DELETE FROM identity.customers WHERE tenant_id='$TENANT'" >/dev/null 2>&1 || true

echo "══ delegate → provisiona + pendência + PROMOÇÃO ao PG ══"
# seed resume_token para a pendência ficar 'viva'
redis hset "$TENANT:resume_tokens" "$TOKEN" "$ORIG:step_x:2030-01-01T00:00:00Z" >/dev/null
D=$($CURL -X POST "$CG/v1/channels/webhook/delegate" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL\",\"customer_id\":\"legacy_ci\",\"origin_session_id\":\"$ORIG\",\"resume_token\":\"$TOKEN\",\"context\":{\"phone\":\"$PHONE\",\"email\":\"$EMAIL\",\"skill_id\":\"skill_checkout_v1\",\"intent\":\"retomar_checkout\"},\"timeout_hours\":48}")
echo "    delegate → $D"
sleep 1

echo "══ resolve por email → customer_id (do índice Redis) ══"
CID=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"email\",\"value\":\"$EMAIL\"}],\"provision\":false}" | jq -r '.customer_id')
[ -n "$CID" ] && [ "$CID" != "null" ] && echo "  ✓ customer_id=$CID" || { echo "  ✗ sem customer_id"; FAIL=1; }

echo "══ cadastro durável populado no PG ══"
assert "customers row" "1" "$(psql "SELECT count(*) FROM identity.customers WHERE tenant_id='$TENANT' AND customer_id='$CID'")"
assert_ge "secondary_keys (phone+email)" "$(psql "SELECT count(*) FROM identity.customer_secondary_keys WHERE tenant_id='$TENANT' AND customer_id='$CID'")" "2"

echo "══ LGPD: PG guarda só hash (sem PII em claro) ══"
if [ "$(psql "SELECT count(*) FROM identity.customer_secondary_keys WHERE tenant_id='$TENANT' AND value_hash LIKE '%$PHONE%'")" = "0" ]; then
  echo "  ✓ telefone não aparece em value_hash"; else echo "  ✗ telefone em claro no PG"; FAIL=1; fi

echo "══ fallback: apaga índice Redis (cold) e resolve de novo ══"
del_pattern "$TENANT:identity:*"
assert "índice Redis vazio" "" "$(redis --scan --pattern "$TENANT:identity:*")"
R=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"email\",\"value\":\"$EMAIL\"}],\"provision\":false}")
echo "    $R"
assert "matched_by (durable)" "durable" "$(echo "$R" | jq -r '.matched_by')"
assert "mesmo customer_id" "$CID" "$(echo "$R" | jq -r '.customer_id')"

echo "══ índice Redis reidratado após fallback ══"
REHYDRATED=$(redis --scan --pattern "$TENANT:identity:email:*" | head -1)
[ -n "$REHYDRATED" ] && echo "  ✓ chave email reidratada" || { echo "  ✗ índice não reidratou"; FAIL=1; }

echo "══ fallback também pelo phone → mesmo cliente ══"
assert "resolve phone (durable)" "$CID" \
  "$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
     -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"phone\",\"value\":\"$PHONE\"}],\"provision\":false}" | jq -r '.customer_id')"

echo
[ "$FAIL" = 0 ] && echo "✅ Identity Slice 2 OK — promoção ao PG, cadastro durável, fallback Redis→PG + reidratação, LGPD" \
                || { echo "❌ Identity Slice 2 com falhas"; exit 1; }
