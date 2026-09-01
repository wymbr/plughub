#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Journey J5 — smoke: escrita de contexto COMPARTILHADO (`@ctx.journey.*`).
#
# Prova que uma escrita imperativa de tag `journey.*` roteia para o hash do
# PROCESSO (raiz canônica, TTL 30d) — não para o hash da sessão (que evapora em 4h
# e não é visto pelos outros contatos da journey). O caminho testado é
# `writeContextTag` (journey.ts), COMPARTILHADO por `context_set` (tool) e
# `/api/inject-context` (supervisor). Dirigimos pelo endpoint HTTP (curl-ável); a
# lógica de roteamento é a mesma do context_set.
#
# Cenário hermético (semeado no Redis, sem depender do ciclo de sessão):
#   S2 = contato-folha; proveniência S2.root_session_id = S1; alias S1 → S0.
#   ⇒ raiz canônica de S2 = S0 (proveniência S2→S1 + union-find S1→S0).
#
# Asserções:
#   1. inject `journey.pedido_id` em S2  → cai em {t}:ctx:journey:S0  (canônica!)
#   2. NÃO vaza para {t}:ctx:S2 (hash da sessão)
#   3. inject `session.foo` em S2         → cai em {t}:ctx:S2 (sessão), não na journey
#   4. o hash da journey ganhou TTL (~30d), não o default de sessão
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_journey_context.sh
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
MCP="${MCP:-http://localhost:3100}"       # mcp-server-plughub (inject-context)
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
redis() { $COMPOSE exec -T redis redis-cli "$@"; }

STAMP=$(date +%s)
S0="j5root_$STAMP"    # raiz canônica da journey
S1="j5mid_$STAMP"     # raiz de proveniência de S2 (absorvida por S0 via alias)
S2="j5leaf_$STAMP"    # o contato onde a escrita acontece
JHASH="${TENANT}:ctx:journey:${S0}"
SHASH="${TENANT}:ctx:${S2}"
ALIAS="${TENANT}:journey:aliases"

echo "══ aguardando mcp-server + auth-api ══"
for i in $(seq 1 30); do $CURL "$MCP/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ mcp timeout"; exit 1; }; sleep 1; done
for i in $(seq 1 30); do $CURL "$AUTH/health" >/dev/null 2>&1 && break; [ "$i" = 30 ] && { echo "  ✗ auth timeout"; exit 1; }; sleep 1; done

echo "══ login admin ══"
TOK=$($CURL -X POST "$AUTH/auth/login" $JSON -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOK" ] || { echo "  ✗ login falhou"; exit 1; }
echo "  ✓ token obtido"

echo "══ seed do cenário (S2 →prov S1 →alias S0) ══"
# tenant resolvido pelo inject-context via session:{id}:meta
redis SET "session:${S2}:meta" "{\"tenant_id\":\"${TENANT}\"}" >/dev/null
# proveniência: S2.root_session_id = S1  (formato ContextEntry)
redis HSET "$SHASH" "core.contact.root_session_id" \
  "{\"value\":\"${S1}\",\"confidence\":1.0,\"source\":\"seed\",\"visibility\":\"agents_only\",\"updated_at\":\"${STAMP}\"}" >/dev/null
# aresta de alias na floresta: S1 absorvida por S0
redis HSET "$ALIAS" "$S1" "$S0" >/dev/null
echo "  ✓ semeado (meta, proveniência, alias)"

inject() {  # $1=key $2=value → HTTP status
  $CURL -o /dev/null -w '%{http_code}' -X POST "$MCP/api/inject-context/${S2}" \
    -H "Authorization: Bearer $TOK" $JSON -d "{\"key\":\"$1\",\"value\":\"$2\"}"
}

echo "══ 1) inject journey.pedido_id em S2 ══"
ST=$(inject "journey.pedido_id" "PED-J5-${STAMP}")
assert "http status" "200" "$ST"
HAS_J=$(redis HEXISTS "$JHASH" "journey.pedido_id")
assert "journey.pedido_id na raiz canônica ($JHASH)" "1" "$HAS_J"
VAL_J=$(redis HGET "$JHASH" "journey.pedido_id" | jq -r '.value')
assert "valor na journey" "PED-J5-${STAMP}" "$VAL_J"

echo "══ 2) NÃO vaza para o hash da sessão ══"
HAS_S=$(redis HEXISTS "$SHASH" "journey.pedido_id")
assert "journey.pedido_id ausente em {t}:ctx:S2" "0" "$HAS_S"

echo "══ 3) tag de sessão continua na sessão ══"
ST=$(inject "session.foo" "bar-${STAMP}")
assert "http status" "200" "$ST"
HAS_SF=$(redis HEXISTS "$SHASH" "session.foo")
assert "session.foo no hash da sessão" "1" "$HAS_SF"
HAS_JF=$(redis HEXISTS "$JHASH" "session.foo")
assert "session.foo ausente na journey" "0" "$HAS_JF"

echo "══ 4) TTL do processo (~30d), não o default de sessão ══"
TTL_J=$(redis TTL "$JHASH")
# 30d = 2592000s; aceita >29d (folga p/ expire recém-setado)
if [ "$TTL_J" -gt 2505600 ]; then echo "  ✓ TTL journey = ${TTL_J}s (>29d)"; else echo "  ✗ TTL journey inesperado: ${TTL_J}s"; FAIL=1; fi

echo "══ cleanup ══"
redis DEL "$JHASH" "$SHASH" "session:${S2}:meta" >/dev/null
redis HDEL "$ALIAS" "$S1" >/dev/null
echo "  ✓ limpo"

echo
if [ "$FAIL" = 0 ]; then echo "PASS — J5: journey.* roteia para a raiz canônica do processo; sessão intacta."; else echo "FAIL — ver ✗ acima."; exit 1; fi
