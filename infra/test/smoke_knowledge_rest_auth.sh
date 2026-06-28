#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE platform-wide — smoke do surface REST /v1/knowledge (mcp-server-knowledge).
#
# Antes da fatia: a KnowledgePage estava MORTA (rotas /v1/knowledge/* não existiam em
# lugar nenhum). Esta fatia construiu o REST (search + snippets CRUD) na mcp-server-knowledge
# com gate DUAL: X-Service-Token (publish de CalibrationNote da eval-api) OU Bearer + ABAC
# `evaluation.gerir_rubrica` (read p/ search, read_write p/ snippets) — a UI.
#
# JWT mintado no container config-api (stdlib, mesmo jwt_secret da auth-api).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
KN="${KN:-http://localhost:3401}"
TENANT="${TENANT:-tenant_demo}"
NS="${NS:-smoke_gprobe}"
SVC="${SVC:-changeme_knowledge_service_token_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_pass() { case "$2" in 401|403) echo "  ✗ $1: gate BARROU ($2)"; FAIL=1;; *) echo "  ✓ $1: gate passou ($2)";; esac; }
code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

mint() {  # $1 = module_config JSON ; minta no config-api (mesmo segredo)
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

SEARCH="$KN/v1/knowledge/search?tenant_id=$TENANT&query=smoke&namespace=$NS"
SNIPPETS="$KN/v1/knowledge/snippets"
BODY="{\"tenant_id\":\"$TENANT\",\"namespace\":\"$NS\",\"content\":\"smoke test snippet\",\"source_ref\":\"smoke_gprobe_ref\"}"
post() { code -X POST "$SNIPPETS" -H "Content-Type: application/json" "$@" -d "$BODY"; }

echo "══ aguardando mcp-server-knowledge ══"
for i in $(seq 1 30); do $CURL "$KN/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ mint tokens (gerir_rubrica rw / ro / sem grant) ══"
TOK_RW=$(mint   '{"evaluation":{"gerir_rubrica":{"access":"read_write","scope":[]}}}')
TOK_RO=$(mint   '{"evaluation":{"gerir_rubrica":{"access":"read_only","scope":[]}}}')
TOK_NONE=$(mint '{"contacts":{"operacao":{"access":"read_write","scope":[]}}}')
[ -n "$TOK_RW" ] && [ -n "$TOK_RO" ] && [ -n "$TOK_NONE" ] || { echo "  ✗ mint falhou"; exit 1; }
echo "  ✓ tokens mintados"

echo "══ 1. search (GET) — exige gerir_rubrica >= read_only ══"
assert "sem credencial → 401"          401 "$(code "$SEARCH")"
assert "Bearer read_only → 200"        200 "$(code "$SEARCH" -H "Authorization: Bearer $TOK_RO")"
assert "Bearer sem grant → 403"        403 "$(code "$SEARCH" -H "Authorization: Bearer $TOK_NONE")"

echo "══ 2. snippets (POST) — exige gerir_rubrica = read_write OU service-token ══"
assert      "sem credencial → 401"        401 "$(post)"
assert      "Bearer read_only → 403"      403 "$(post -H "Authorization: Bearer $TOK_RO")"
assert_pass "Bearer read_write (UI)"      "$(post -H "Authorization: Bearer $TOK_RW")"
assert_pass "X-Service-Token (eval-api)"  "$(post -H "x-service-token: $SVC")"

echo "══ cleanup (apaga o snippet de teste) ══"
SID=$($CURL "$SEARCH&query=snippet" -H "Authorization: Bearer $TOK_RW" | jq -r '.results[0].snippet_id // empty' 2>/dev/null)
[ -n "$SID" ] && $CURL -X DELETE "$SNIPPETS/$SID?tenant_id=$TENANT" -H "Authorization: Bearer $TOK_RW" >/dev/null 2>&1

echo
if [ "$FAIL" = 0 ]; then echo "✅ knowledge REST-auth smoke: tudo verde"; else echo "❌ knowledge REST-auth smoke: falhas acima"; fi
exit $FAIL
