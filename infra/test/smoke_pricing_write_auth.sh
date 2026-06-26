#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE platform-wide — smoke do gate de ESCRITA da pricing-api (dual).
#
# As mutações (provisionar resources, ativar/desativar reservas) deixam de exigir
# X-Admin-Token e aceitam:
#   - X-Admin-Token (back-compat — seed_pricing/sistema), OU
#   - Bearer + ABAC `config.plataforma` (read_write) — billing tratado como config de
#     plataforma (decisão da sessão: reusa config.plataforma, sem campo billing novo).
#
# JWTs mintados DENTRO do container pricing-api (stdlib HS256, mesmo jwt_secret).
# Usa um tenant descartável (smoke_gprobe_pricing) e limpa no fim.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
PRICE="${PRICE:-http://localhost:3900}"
TENANT="${TENANT:-smoke_gprobe_pricing}"
ADMIN="${ADMIN:-demo_pricing_admin_token}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

mint() {  # $1 = module_config JSON
  $DC exec -T pricing-api python - "$JWT_SECRET" "$1" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, json, time, hmac, hashlib, base64
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
secret, mc = sys.argv[1], json.loads(sys.argv[2])
now = int(time.time())
h = b64(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(",",":")).encode())
p = b64(json.dumps({"sub":"smoke","tenant_id":"tenant_demo","module_config":mc,
                    "iat":now,"exp":now+3600}, separators=(",",":")).encode())
sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
print(f"{h}.{p}.{b64(sig)}")
PY
}

URL="$PRICE/v1/pricing/resources/$TENANT"
BODY='{"installation_id":"smoke","resource_type":"ai_agent","quantity":1,"pool_type":"base","billing_unit":"monthly"}'
post() { code -X POST "$URL" -H "Content-Type: application/json" "$@" -d "$BODY"; }

echo "══ aguardando pricing-api ══"
for i in $(seq 1 30); do $CURL "$PRICE/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ mint tokens (plataforma / sem grant) ══"
TOK_PLAT=$(mint '{"config":{"plataforma":{"access":"read_write","scope":[]}}}')
TOK_NONE=$(mint '{"config":{"plataforma":{"access":"read_only","scope":[]}}}')
[ -n "$TOK_PLAT" ] && [ -n "$TOK_NONE" ] || { echo "  ✗ mint falhou"; exit 1; }
echo "  ✓ tokens mintados"

echo "══ POST /v1/pricing/resources (upsert) — gate dual ══"
assert "sem credencial → 403"            403 "$(post)"
assert "Bearer plataforma read_only → 403" 403 "$(post -H "Authorization: Bearer $TOK_NONE")"
assert "X-Admin-Token → 200"             200 "$(post -H "X-Admin-Token: $ADMIN")"
assert "Bearer plataforma:rw → 200"      200 "$(post -H "Authorization: Bearer $TOK_PLAT")"

echo "══ cleanup (remove os resources de teste via admin-token) ══"
IDS=$($CURL "$PRICE/v1/pricing/resources/$TENANT" | jq -r '.[].resource_id // .[].id // empty' 2>/dev/null)
for id in $IDS; do
  $CURL -X DELETE "$PRICE/v1/pricing/resources/$TENANT/$id" -H "X-Admin-Token: $ADMIN" >/dev/null 2>&1
done

echo
if [ "$FAIL" = 0 ]; then echo "✅ pricing-api write-auth smoke: tudo verde"; else echo "❌ pricing-api write-auth smoke: falhas acima"; fi
exit $FAIL
