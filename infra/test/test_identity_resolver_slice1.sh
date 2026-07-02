#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Identity Resolver — Fase A · Slice 1 (Redis-only)
#
# Valida, no channel-gateway (:8010):
#   - POST /v1/channels/webhook/identity/resolve  (Lookup 1: resolve/provision)
#   - GET  /v1/channels/webhook/pending/by-customer/{id}  (Lookup 2)
#   - Cross-canal: resolver por UMA âncora e reconectar por OUTRA do mesmo cliente
#     devolve o mesmo customer_id.
#   - LGPD: nenhuma chave {t}:identity:* contém PII em claro.
#
# Redis: seed direto da pendência + resume_token (via docker compose exec redis).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
CG="${CG:-http://localhost:8010}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"
PHONE="11999990000"
EMAIL="cliente.slice1@example.com"
CURL="curl -s --max-time 15"
FAIL=0
assert()     { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_ne()  { if [ "$2" != "$3" ]; then echo "  ✓ $1 (≠ [$3])"; else echo "  ✗ $1: não deveria ser [$3]"; FAIL=1; fi; }
redis() { $COMPOSE exec -T redis redis-cli "$@"; }

echo "══ aguardando channel-gateway ══"
for i in $(seq 1 30); do $CURL "$CG/health" >/dev/null 2>&1 || $CURL "$CG/" >/dev/null 2>&1 && break; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done
echo "  ✓ no ar"

echo "══ limpeza idempotente ══"
redis --scan --pattern "$TENANT:identity:*"            | xargs -r redis del >/dev/null 2>&1 || true
redis --scan --pattern "$TENANT:customer:prospect:*"   | xargs -r redis del >/dev/null 2>&1 || true
redis --scan --pattern "$TENANT:pending_by_customer:*" | xargs -r redis del >/dev/null 2>&1 || true

echo "══ Lookup 1: provisiona com phone+email ══"
R=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"phone\",\"value\":\"$PHONE\"},{\"kind\":\"email\",\"value\":\"$EMAIL\"}],\"provision\":true}")
echo "    $R"
CID=$(echo "$R" | jq -r '.customer_id')
assert "matched_by (provision)" "provisioned" "$(echo "$R" | jq -r '.matched_by')"
assert "status" "prospect" "$(echo "$R" | jq -r '.status')"
[ -n "$CID" ] && [ "$CID" != "null" ] && echo "  ✓ customer_id=$CID" || { echo "  ✗ sem customer_id"; FAIL=1; }

echo "══ cross-canal: resolver só pelo EMAIL → mesmo cliente ══"
R2=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"email\",\"value\":\"$EMAIL\"}],\"provision\":false}")
assert "mesmo customer_id (email)" "$CID" "$(echo "$R2" | jq -r '.customer_id')"
assert "matched_by (existing)" "existing" "$(echo "$R2" | jq -r '.matched_by')"

echo "══ cross-canal: resolver só pelo PHONE → mesmo cliente ══"
R3=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"phone\",\"value\":\"$PHONE\"}],\"provision\":false}")
assert "mesmo customer_id (phone)" "$CID" "$(echo "$R3" | jq -r '.customer_id')"

echo "══ Lookup 2: pendência sob o cliente (seed direto) ══"
TOKEN="plughub_wh_slice1_token_000000000000000000000"
redis hset "$TENANT:resume_tokens" "$TOKEN" "orig_sess_1:step_x:2030-01-01T00:00:00Z" >/dev/null
PENDING="{\"session_id\":\"orig_sess_1\",\"customer_id\":\"$CID\",\"resume_token\":\"$TOKEN\",\"pool\":\"loja_checkout_io\",\"skill_id\":\"skill_checkout_v1\",\"suspended_at\":\"2026-06-10T12:00:00Z\",\"expires_at\":null,\"policy\":\"offer\",\"intent\":\"retomar_checkout\",\"context_preview\":{}}"
redis hset "$TENANT:pending_by_customer:$CID" "orig_sess_1" "$PENDING" >/dev/null

echo "══ GET pending/by-customer (canal original) ══"
P=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CID?tenant_id=$TENANT")
echo "    $P"
assert "found" "true" "$(echo "$P" | jq -r '.found')"
assert "count" "1" "$(echo "$P" | jq -r '.count')"
assert "resume_token" "$TOKEN" "$(echo "$P" | jq -r '.pendings[0].resume_token')"

echo "══ retomada por OUTRO canal: resolve email → acha a MESMA pendência ══"
CID2=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"anchors\":[{\"kind\":\"email\",\"value\":\"$EMAIL\"}],\"provision\":false}" | jq -r '.customer_id')
P2=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CID2?tenant_id=$TENANT")
assert "cross-canal acha pendência" "orig_sess_1" "$(echo "$P2" | jq -r '.pendings[0].session_id')"

echo "══ stale: token consumido → pendência some ══"
redis hdel "$TENANT:resume_tokens" "$TOKEN" >/dev/null
P3=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CID?tenant_id=$TENANT")
assert "found após consumir token" "false" "$(echo "$P3" | jq -r '.found')"

echo "══ LGPD: índice sem PII em claro ══"
KEYS=$(redis --scan --pattern "$TENANT:identity:*")
if echo "$KEYS" | grep -q "$PHONE"; then echo "  ✗ telefone em claro numa chave identity"; FAIL=1; else echo "  ✓ phone não aparece nas chaves identity"; fi
if echo "$KEYS" | grep -qi "$EMAIL"; then echo "  ✗ email em claro numa chave identity"; FAIL=1; else echo "  ✓ email não aparece nas chaves identity"; fi

echo
[ "$FAIL" = 0 ] && echo "✅ Identity Slice 1 OK — resolve/provision, cross-canal, pendência por cliente, stale, LGPD" \
                || { echo "❌ Identity Slice 1 com falhas"; exit 1; }
