#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE platform-wide — smoke do gate de ESCRITA da config-api (dual).
#
# O PUT/DELETE genérico (`/config/{namespace}/{key}`) aceita:
#   - X-Admin-Token (back-compat — telas ainda não migradas), OU
#   - Bearer + ABAC `config.{campo}` (read_write), com o campo mapeado por namespace:
#       masking|audit_policy → config.masking ; canais(webchat/…) → config.canais ;
#       default (Platform) → config.plataforma.
# Telas migradas nesta fatia: Platform (config.plataforma) + Masking (config.masking).
#
# JWTs mintados DENTRO do container config-api (stdlib HS256, mesmo jwt_secret).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${ADMIN:-demo_config_admin_token}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

mint() {  # $1 = module_config JSON
  $DC exec -T config-api python - "$JWT_SECRET" "$1" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
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

# PUT helper: namespace/key + headers extras.
put() {  # $1=ns $2=key ; resto = headers
  local ns="$1" key="$2"; shift 2
  code -X PUT "$CFG/config/$ns/$key" -H "Content-Type: application/json" "$@" \
    -d "{\"value\":\"smoke\",\"tenant_id\":\"$TENANT\"}"
}

echo "══ aguardando config-api ══"
for i in $(seq 1 30); do $CURL "$CFG/config?tenant_id=$TENANT" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ mint tokens (plataforma / masking / sem grant) ══"
TOK_PLAT=$(mint '{"config":{"plataforma":{"access":"read_write","scope":[]}}}')
TOK_MASK=$(mint '{"config":{"masking":{"access":"read_write","scope":[]}}}')
TOK_NONE=$(mint '{"contacts":{"operacao":{"access":"read_write","scope":[]}}}')
[ -n "$TOK_PLAT" ] && [ -n "$TOK_MASK" ] && [ -n "$TOK_NONE" ] || { echo "  ✗ mint falhou"; exit 1; }
echo "  ✓ tokens mintados"

echo "══ 1. back-compat: X-Admin-Token ══"
assert "admin-token → 200"        200 "$(put routing smoke_gprobe -H "X-Admin-Token: $ADMIN")"
assert "admin-token errado → 401" 401 "$(put routing smoke_gprobe -H "X-Admin-Token: wrong")"
assert "sem credencial → 401"     401 "$(put routing smoke_gprobe)"

echo "══ 2. Platform (namespace default → config.plataforma) ══"
assert "Bearer plataforma:rw → 200"     200 "$(put routing smoke_gprobe -H "Authorization: Bearer $TOK_PLAT")"
assert "Bearer masking (campo errado) → 403" 403 "$(put routing smoke_gprobe -H "Authorization: Bearer $TOK_MASK")"
assert "Bearer sem grant → 403"         403 "$(put routing smoke_gprobe -H "Authorization: Bearer $TOK_NONE")"

echo "══ 3. Masking (namespace masking/audit_policy → config.masking) ══"
assert "Bearer masking:rw (masking) → 200"      200 "$(put masking smoke_gprobe -H "Authorization: Bearer $TOK_MASK")"
assert "Bearer masking:rw (audit_policy) → 200" 200 "$(put audit_policy smoke_gprobe -H "Authorization: Bearer $TOK_MASK")"
assert "Bearer plataforma (campo errado) → 403" 403 "$(put masking smoke_gprobe -H "Authorization: Bearer $TOK_PLAT")"

echo "══ cleanup (remove as chaves de teste via admin-token) ══"
for nk in "routing/smoke_gprobe" "masking/smoke_gprobe" "audit_policy/smoke_gprobe"; do
  $CURL -X DELETE "$CFG/config/$nk?tenant_id=$TENANT&admin_token=$ADMIN" >/dev/null 2>&1
done

echo
if [ "$FAIL" = 0 ]; then echo "✅ config-api write-auth smoke: tudo verde"; else echo "❌ config-api write-auth smoke: falhas acima"; fi
exit $FAIL
