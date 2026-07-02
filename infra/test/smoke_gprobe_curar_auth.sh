#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# G-PROBE — perna humana `curar` — smoke da curadoria/calibração (evaluation-api).
#
# Slice já implementado (verificado por leitura de código em 2026-07-02):
#   - `curar` no catálogo ABAC (infra/modules.yaml, scopable: pool).
#   - `contestation_router.py`: list_curations / resolve_curation / get_blind_context /
#     blind_rescore / blind_resolve gateados por `_require_curar` (Bearer JWT) +
#     `_check_abac_permission('curar', pool_id, min_access=...)` — leitura=read_only,
#     escrita=read_write. O gate roda ANTES de qualquer lookup no banco, então dá pra
#     validar com um review_id inexistente (403 se falta grant; 404 "not found" se o
#     grant passa, prova que o ABAC não foi o bloqueio).
#   - `seed_auth.py`: supervisor@plughub.local ganhou evaluation.curar=read_write.
#
# Este smoke valida:
#   1. GET  /v1/evaluation/curations                        — sem Bearer → 401
#   2. GET  /v1/evaluation/curations                        — Bearer SEM curar → 403
#   3. GET  /v1/evaluation/curations                        — Bearer curar=read_only → 200
#   4. POST /v1/evaluation/curations/{fake}/resolve         — curar=read_only (insuficiente p/ escrita) → 403
#   5. POST /v1/evaluation/curations/{fake}/resolve         — curar=read_write → 404 (ABAC passou, review não existe)
#   6. GET  /v1/evaluation/curations/{fake}/blind-context    — curar=read_only → 404 (ABAC passou p/ leitura também)
#
# Pré-req: stack demo no ar (evaluation-api). Mint do JWT feito dentro do container
# evaluation-api (pyjwt + jwt_secret), mesmo padrão do smoke_gprobe_service_auth.sh.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

code() { $CURL -o /dev/null -w '%{http_code}' "$@"; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ eval timeout"; exit 1; }; sleep 1; done

# Mesmo mecanismo de mint do smoke de serviço: JWT HS256 assinado com o jwt_secret que
# a evaluation-api valida, independente do seed real de usuários.
mint() {
  $DC exec -T evaluation-api python - "$JWT_SECRET" "$1" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, json, jwt
print(jwt.encode({"sub": "u_smoke_curar", "tenant_id": "tenant_demo", "roles": ["supervisor"],
                  "module_config": json.loads(sys.argv[2])}, sys.argv[1], algorithm="HS256"))
PY
}

echo "══ mint Bearers ══"
TOK_NONE=$(mint '{"evaluation":{"revisar":{"access":"read_write","scope":[]}}}')          # sem curar
TOK_RO=$(mint   '{"evaluation":{"curar":{"access":"read_only","scope":[]}}}')              # curar=read_only
TOK_RW=$(mint   '{"evaluation":{"curar":{"access":"read_write","scope":[]}}}')             # curar=read_write
[ -n "$TOK_NONE" ] && [ -n "$TOK_RO" ] && [ -n "$TOK_RW" ] || { echo "  ✗ mint falhou (DC/exec/pyjwt indisponível?)"; exit 1; }
echo "  ✓ 3 Bearers mintados (sem-curar / read_only / read_write)"

FAKE_ID="evcurationreview_smoke_fake_$$"

echo "══ 1-3. GET /v1/evaluation/curations (list_curations) ══"
# list_curations resolve tenant via _get_tenant (header X-Tenant-ID / claim do JWT) —
# NÃO lê ?tenant_id= da query (nem está na assinatura). Sem X-Tenant-ID nem Bearer,
# _get_tenant 400 antes de _require_curar rodar — por isso o caso "sem Bearer" abaixo
# manda X-Tenant-ID explícito, isolando o gate de auth (401) do gate de tenant (400).
assert "list sem Bearer (com X-Tenant-ID) → 401" 401 "$(code "$EVAL/v1/evaluation/curations" -H "X-Tenant-ID: $TENANT")"
assert "list sem Bearer nem X-Tenant-ID → 400 (gate de tenant, não de auth)" 400 "$(code "$EVAL/v1/evaluation/curations")"
assert "list Bearer SEM curar → 403" 403 "$(code "$EVAL/v1/evaluation/curations?tenant_id=$TENANT" -H "Authorization: Bearer $TOK_NONE")"
assert "list Bearer curar=read_only → 200" 200 "$(code "$EVAL/v1/evaluation/curations?tenant_id=$TENANT" -H "Authorization: Bearer $TOK_RO")"

echo "══ 4-5. POST /v1/evaluation/curations/{id}/resolve ══"
assert "resolve curar=read_only (insuficiente p/ escrita) → 403" 403 \
  "$(code -X POST "$EVAL/v1/evaluation/curations/$FAKE_ID/resolve?tenant_id=$TENANT" -H "Authorization: Bearer $TOK_RO" -H 'Content-Type: application/json' -d '{"status":"approved"}')"
assert "resolve curar=read_write, review inexistente → 404 (ABAC passou)" 404 \
  "$(code -X POST "$EVAL/v1/evaluation/curations/$FAKE_ID/resolve?tenant_id=$TENANT" -H "Authorization: Bearer $TOK_RW" -H 'Content-Type: application/json' -d '{"status":"approved"}')"

echo "══ 6. GET /v1/evaluation/curations/{id}/blind-context ══"
assert "blind-context curar=read_only, review inexistente → 404 (ABAC passou p/ leitura)" 404 \
  "$(code "$EVAL/v1/evaluation/curations/$FAKE_ID/blind-context?tenant_id=$TENANT" -H "Authorization: Bearer $TOK_RO")"

echo
if [ "$FAIL" = 0 ]; then echo "✅ G-PROBE curar smoke: tudo verde"; else echo "❌ G-PROBE curar smoke: falhas acima"; fi
exit $FAIL
