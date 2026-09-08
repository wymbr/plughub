#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE platform-wide — smoke do gate Bearer+ABAC `config.users` na auth-api.
#
# Migra a gestão de usuários/permissões/grupos de X-Admin-Token (legado) para
# Bearer + ABAC `config.users` (STRICT, sem fallback de admin-token):
#   GET  (leitura) exige config.users >= read_only
#   POST/PUT/PATCH/DELETE (mutação) exige config.users = read_write
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
# ⚠️ O campo e `config.users`, nao `config.usuarios` — medido em 2026-09-08. Este
# script mintava o nome em portugues, retirado pela regra de linguagem (identificador
# tecnico em ingles), e as QUATRO assercoes positivas viviam vermelhas: as leituras
# davam 403 e a criacao tambem. As negativas continuavam verdes, mas pelo motivo
# ERRADO — o token "sem grant" e o token "com grant" nao tinham nenhum dos dois. E o
# controle positivo ao lado do negativo que denuncia isso; sem ele o script parecia
# medir um portao que ele nao alcancava.
TOK_RW=$(mint   '{"config":{"users":{"access":"read_write","scope":[]}}}')
TOK_RO=$(mint   '{"config":{"users":{"access":"read_only","scope":[]}}}')
TOK_NONE=$(mint '{"contacts":{"monitorar":{"access":"read_write","scope":[]}}}')
[ -n "$TOK_RW" ] && [ -n "$TOK_RO" ] && [ -n "$TOK_NONE" ] || { echo "  ✗ mint falhou (DC/exec indisponível?)"; exit 1; }
echo "  ✓ tokens mintados"

USERS="$AUTH/auth/users?tenant_id=$TENANT"
# NB: NÃO usar `GROUPS` — é variável especial do bash (array de GIDs); $GROUPS viraria "1000".
GRP_URL="$AUTH/auth/v1/groups?tenant_id=$TENANT"

echo "══ 1. leitura (GET /auth/users) — exige config.users >= read_only ══"
assert "sem Bearer → 401"                401 "$(code "$USERS")"
assert "Bearer read_only → 200"          200 "$(code "$USERS" -H "Authorization: Bearer $TOK_RO")"
assert "Bearer read_write → 200"         200 "$(code "$USERS" -H "Authorization: Bearer $TOK_RW")"
assert "Bearer sem config.users → 403" 403 "$(code "$USERS" -H "Authorization: Bearer $TOK_NONE")"
assert "X-Admin-Token (sem fallback) → 401" 401 "$(code "$USERS" -H "X-Admin-Token: $JWT_SECRET")"

echo "══ 2. grupos (GET /auth/v1/groups) — mesmo gate ══"
assert "sem Bearer → 401"        401 "$(code "$GRP_URL")"
assert "Bearer read_only → 200"  200 "$(code "$GRP_URL" -H "Authorization: Bearer $TOK_RO")"
assert "Bearer sem grant → 403"  403 "$(code "$GRP_URL" -H "Authorization: Bearer $TOK_NONE")"

# A mutação (bcrypt no create_user) bloqueia o worker async da auth-api por alguns
# segundos; fica por ÚLTIMO para não atrasar as leituras acima (curl --max-time).
echo "══ 3. mutação (POST /auth/users) — exige config.users = read_write ══"
# `roles: []` de proposito: o que este script mede e o PORTAO DA ROTA
# (`config.users = read_write`), e desde a MOD-02 o CORPO passa por um segundo
# julgamento — o guard de rank, que recusa quem concede o que nao detem. Com
# `roles: ["operator"]` o 403 viria do guard, e o script diria "a rota barrou"
# sobre uma recusa que a rota nao tomou. O guard tem probe proprio
# (`probe_rank_grant_guard.sh`, 9 cenarios).
SMOKE_EMAIL="smoke_$RANDOM@plughub.local"
NEWBODY="{\"tenant_id\":\"$TENANT\",\"email\":\"$SMOKE_EMAIL\",\"name\":\"smoke\",\"password\":\"changeme123\",\"roles\":[],\"accessible_pools\":[]}"
assert "read_only → 403"  403 "$(code -X POST "$AUTH/auth/users" -H "Content-Type: application/json" -H "Authorization: Bearer $TOK_RO" -d "$NEWBODY")"
assert "read_write → 201" 201 "$(code -X POST "$AUTH/auth/users" -H "Content-Type: application/json" -H "Authorization: Bearer $TOK_RW" -d "$NEWBODY")"

echo "══ cleanup (o usuario de teste nao pode sobreviver — contamina o censo) ══"
NOVO_ID=$($CURL "$USERS" -H "Authorization: Bearer $TOK_RW"           | python3 -c "import sys,json;print(next((u['id'] for u in json.load(sys.stdin) if u['email']=='$SMOKE_EMAIL'),''))" 2>/dev/null)
if [ -n "$NOVO_ID" ]; then
  echo "  removido: $SMOKE_EMAIL -> $(code -X DELETE "$AUTH/auth/users/$NOVO_ID" -H "Authorization: Bearer $TOK_RW")"
fi

echo
if [ "$FAIL" = 0 ]; then echo "✅ config.users auth smoke: tudo verde"; else echo "❌ config.users auth smoke: falhas acima"; fi
exit $FAIL
