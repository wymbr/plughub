#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE platform-wide — smoke do gate de ESCRITA do agent-registry (dual).
#
# As mutações de config (pools/skills/channels/channel-endpoints) deixam de ser
# abertas e exigem:
#   - X-Service-Token (callers internos: RegistrySyncer/deploy), OU
#   - Bearer + ABAC `config.resources` (read_write) — a UI (PoolsPage/registry.ts).
# GET (leituras) seguem abertos. instances/operational/pool-slots NÃO são gateados.
#
# Sonda: DELETE de um skill inexistente — o middleware roda ANTES do handler, então
# 401/403 = barrado pelo gate; 404 (não encontrado) = gate passou.
# JWT mintado no container config-api (stdlib, mesmo jwt_secret da auth-api).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
REG="${REG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
SVC="${SVC:-changeme_agent_registry_service_token_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_pass() { case "$2" in 401|403) echo "  ✗ $1: gate BARROU ($2)"; FAIL=1;; *) echo "  ✓ $1: gate passou ($2)";; esac; }
code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

mint() {  # $1 = module_config JSON ; minta no config-api (python stdlib, mesmo segredo)
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

del() { code -X DELETE "$REG/v1/skills/smoke_gprobe_nonexistent" -H "x-tenant-id: $TENANT" "$@"; }

echo "══ aguardando agent-registry ══"
for i in $(seq 1 30); do $CURL "$REG/v1/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ mint tokens (resources rw / ro / sem grant) ══"
TOK_RW=$(mint   '{"config":{"resources":{"access":"read_write","scope":[]}}}')
TOK_RO=$(mint   '{"config":{"resources":{"access":"read_only","scope":[]}}}')
TOK_NONE=$(mint '{"contacts":{"operacao":{"access":"read_write","scope":[]}}}')
[ -n "$TOK_RW" ] && [ -n "$TOK_RO" ] && [ -n "$TOK_NONE" ] || { echo "  ✗ mint falhou"; exit 1; }
echo "  ✓ tokens mintados"

echo "══ 1. mutação gateada (DELETE /v1/skills/:id) ══"
assert       "sem credencial → 401"            401 "$(del)"
assert       "Bearer read_only → 403"          403 "$(del -H "Authorization: Bearer $TOK_RO")"
assert       "Bearer sem grant → 403"          403 "$(del -H "Authorization: Bearer $TOK_NONE")"
assert_pass  "X-Service-Token (callers internos)" "$(del -H "x-service-token: $SVC")"
assert_pass  "Bearer resources:rw (UI)"           "$(del -H "Authorization: Bearer $TOK_RW")"

echo "══ 2. leitura aberta (GET /v1/skills) ══"
assert "GET lista sem credencial → 200" 200 "$(code "$REG/v1/skills?tenant_id=$TENANT" -H "x-tenant-id: $TENANT")"

echo
if [ "$FAIL" = 0 ]; then echo "✅ agent-registry write-auth smoke: tudo verde"; else echo "❌ agent-registry write-auth smoke: falhas acima"; fi
exit $FAIL
