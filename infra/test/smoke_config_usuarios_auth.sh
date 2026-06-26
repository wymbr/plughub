#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE platform-wide — smoke do gate Bearer+ABAC `config.usuarios` na auth-api.
#
# Migra a gestão de usuários/permissões/grupos de X-Admin-Token (legado) para
# Bearer + ABAC `config.usuarios` (STRICT, sem fallback de admin-token):
#   GET  (leitura) exige config.usuarios >= read_only
#   POST/PUT/PATCH/DELETE (mutação) exige config.usuarios = read_write
#
# Os JWTs são mintados DENTRO do container auth-api (stdlib HS256, mesmo jwt_secret
# que a auth-api valida) — independe do seed de usuários do demo.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

# Minta um JWT HS256 (stdlib) assinado com o jwt_secret da auth-api. $1 = module_config JSON.
mint() {
  $DC exec -T auth-api python - "$JWT_SECRET" "$1" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, json, time, hmac, hashlib, base64
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
secret, mc = sys.argv[1], json.loads(sys.argv[2])
now = int(time.time())
h = b64(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(",",":")).encode())
p = b64(json.dumps({"sub":"smoke","tenant_id":"tenant_demo","roles":["operator"],
                    "module_config": mc, "iat": now, "exp": now+3600},
                   separators=(",",":")).encode())
sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
print(f"{h}.{p}.{b64(sig)}")
PY
}

echo "══ aguardando auth-api ══"
for i in $(seq 1 30); do $CURL "$AUTH/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ mint tokens (rw / ro / sem grant evaluation-config) ══"
TOK_RW=$(mint   '{"config":{"usuarios":{"access":"read_write","scope":[]}}}')
TOK_RO=$(mint   '{"config":{"usuarios":{"access":"read_only","scope":[]}}}')
TOK_NONE=$(mint '{"contacts":{"operacao":{"access":"read_write","scope":[]}}}')
[ -n "$TOK_RW" ] && [ -n "$TOK_RO" ] && [ -n "$TOK_NONE" ] || { echo "  ✗ mint falhou (DC/exec indisponível?)"; exit 1; }
echo "  ✓ tokens mintados"

USERS="$AUTH/auth/users?tenant_id=$TENANT"
# NB: NÃO usar `GROUPS` — é variável especial do bash (array de GIDs); $GROUPS viraria "1000".
GRP_URL="$AUTH/auth/v1/groups?tenant_id=$TENANT"

echo "══ 1. leitura (GET /auth/users) — exige config.usuarios >= read_only ══"
assert "sem Bearer → 401"                401 "$(code "$USERS")"
assert "Bearer read_only → 200"          200 "$(code "$USERS" -H "Authorization: Bearer $TOK_RO")"
assert "Bearer read_write → 200"         200 "$(code "$USERS" -H "Authorization: Bearer $TOK_RW")"
assert "Bearer sem config.usuarios → 403" 403 "$(code "$USERS" -H "Authorization: Bearer $TOK_NONE")"
assert "X-Admin-Token (sem fallback) → 401" 401 "$(code "$USERS" -H "X-Admin-Token: $JWT_SECRET")"

echo "══ 2. grupos (GET /auth/v1/groups) — mesmo gate ══"
assert "sem Bearer → 401"        401 "$(code "$GRP_URL")"
assert "Bearer read_only → 200"  200 "$(code "$GRP_URL" -H "Authorization: Bearer $TOK_RO")"
assert "Bearer sem grant → 403"  403 "$(code "$GRP_URL" -H "Authorization: Bearer $TOK_NONE")"

# A mutação (bcrypt no create_user) bloqueia o worker async da auth-api por alguns
# segundos; fica por ÚLTIMO para não atrasar as leituras acima (curl --max-time).
echo "══ 3. mutação (POST /auth/users) — exige config.usuarios = read_write ══"
NEWBODY="{\"tenant_id\":\"$TENANT\",\"email\":\"smoke_$RANDOM@plughub.local\",\"name\":\"smoke\",\"password\":\"changeme123\",\"roles\":[\"operator\"],\"accessible_pools\":[]}"
assert "read_only → 403"  403 "$(code -X POST "$AUTH/auth/users" -H "Content-Type: application/json" -H "Authorization: Bearer $TOK_RO" -d "$NEWBODY")"
assert "read_write → 201" 201 "$(code -X POST "$AUTH/auth/users" -H "Content-Type: application/json" -H "Authorization: Bearer $TOK_RW" -d "$NEWBODY")"

echo
if [ "$FAIL" = 0 ]; then echo "✅ config.usuarios auth smoke: tudo verde"; else echo "❌ config.usuarios auth smoke: falhas acima"; fi
exit $FAIL
