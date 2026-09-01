#!/usr/bin/env bash
# smoke_work_task_pending.sh — ADR adr-internal-work-queue-author-bound § D7b, fatia 1.
#
# Prova, contra o DADO, o RELATÓRIO de pendências (`GET /api/work_queue/pending`):
#
#   A  o item aparece com estado `unclaimed` — está no ZSET e ninguém o pegou;
#   B  o ESCOPO `-int` funciona: por default a lista é só de wrap-up (fila interna
#      author-bound), e um item de `formfill_demo` (pool de contato, pooled) NÃO
#      aparece. Este é o teste que impede a tela de virar "todo delegate parqueado";
#   C  `truncated` é EXPLÍCITO quando o teto do SCAN é atingido — resultado parcial
#      mudo seria a mentira tranquila que a § Postura de Engenharia proíbe;
#   D  reivindicado vira `claimed` (fora do ZSET, com lease);
#   E  lease apagada sem re-enfileirar vira `orphaned` — o estado que MEDE a lacuna 2
#      (não há reaper de `claim_lease`). Se ele não existisse, essa condição se
#      disfarçaria de `not_queued` e sumiria.
#
# D e E só rodam com INSTANCE=human-<user_id> de um agente logado; sem isso são
# anunciados como NÃO EXERCITADOS, nunca como sucesso.
#
# `not_queued` (delegate a pool push) NÃO é exercitado aqui: produzi-lo exige um
# skill dedicado, e o custo não se paga — o estado é o ramo trivial do classificador.
#
# Usa o par formfill_demo_ia → formfill_demo (mesmo harness do smoke_acw_expire):
# é o caminho pull genérico e não exige atendimento no Console. Como esse pool NÃO
# tem sufixo `-int`, as asserções de classificação usam `?all=1`.
#
# Pré-requisitos: demo no ar; pools formfill_demo{,_ia} REGISTRADOS via API (senão o
# routing trata o item como push e o drain periódico o disputa); skill_formfill_demo_v1
# deployado. Requer curl + jq.
#
# Uso (raiz do repo):
#   bash infra/test/smoke_work_task_pending.sh
#   INSTANCE=human-<user_id> bash infra/test/smoke_work_task_pending.sh
set -euo pipefail

# CAP-12 (2026-09-01): as rotas `/api/*` do mcp-server exigem credencial. Sem esta
# linha as chamadas abaixo voltam 401, e o script contaria zero item como se a fila
# estivesse vazia. O shim anexa o Bearer so onde ele e conferido — ver _auth.sh.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_auth.sh"; plughub_auth_curl_shim

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
MCP="http://localhost:3100"
POOL_WH="formfill_demo_ia"
POOL_PULL="formfill_demo"
INSTANCE="${INSTANCE:-}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
redis() { $COMPOSE exec -T redis redis-cli "$@"; }

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

# Estado do item SID na resposta do endpoint (com `all=1` — ver cabeçalho).
state_of() {
  $CURL "$MCP/api/work_queue/pending?all=1" \
    | jq -r --arg s "$1" '.items[] | select(.session_id==$s) | .state // empty'
}

echo "══ 1) dispara o workflow (delega o form ao pool pull) ══"
SID=$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"sess_briefing_demo\"}}" \
  | jq -r '.session_id // empty')
[ -n "$SID" ] || { echo "   ✗ trigger não devolveu session_id"; exit 1; }
echo "   sessão do workflow: $SID"

echo "══ 2) aguarda o item parquear em $POOL_PULL ══"
QUEUED=""
for _ in $(seq 1 20); do
  Z=$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')
  [ -n "$Z" ] && { QUEUED=1; break; }
  sleep 1
done
[ -n "$QUEUED" ] || { echo "   ✗ o item não entrou na fila — nada a relatar; abortando"; exit 1; }
echo "   ✓ item na fila"

echo "══ A) o item aparece como 'unclaimed' ══"
ST=$(state_of "$SID")
[ "$ST" = "unclaimed" ] && ok "estado=unclaimed (no ZSET, sem lease)" \
                        || bad "estado='$ST' (esperado unclaimed)"

echo "══ B) escopo: por default a lista é SÓ wrap-up (pools -int) ══"
DEFAULT_HIT=$($CURL "$MCP/api/work_queue/pending" \
  | jq -r --arg s "$SID" '[.items[] | select(.session_id==$s)] | length')
[ "$DEFAULT_HIT" = "0" ] \
  && ok "item de $POOL_PULL (pooled, sem sufixo -int) NÃO aparece no default" \
  || bad "item de $POOL_PULL apareceu sem all=1 — o filtro -int não está valendo"

INT_ONLY=$($CURL "$MCP/api/work_queue/pending" \
  | jq -r '[.items[] | select(.pool_id | endswith("-int") | not)] | length')
[ "$INT_ONLY" = "0" ] && ok "nenhum pool sem sufixo -int na lista default" \
                      || bad "$INT_ONLY item(ns) de pool não-interno vazaram para o default"

echo "══ C) truncated é explícito ao bater o teto do SCAN ══"
# Determinismo: com UMA só chave no ledger, `truncated=false` é a resposta CORRETA
# (nada foi truncado) e o teste reprovaria sem motivo. Semeia-se uma segunda chave
# para que o teto de 1 seja necessariamente atingido.
#
# O pool dela não existe de propósito: sem `pool_config` no cache, o item tem de sair
# como `unknown` — ausência de infra NÃO pode virar presunção de "push". Cobre o 5º
# estado sem cenário próprio.
DUMMY="__smoke_pending_dummy__"
redis SET "${TENANT}:work_task:${DUMMY}" \
  '{"pool_id":"__smoke_no_such_pool__","queue_session_id":"'"$DUMMY"'","resume_token":"tok_smoke","step_id":"smoke","assigned_to":"","deadline":"","created_at":"2026-01-01T00:00:00+00:00"}' \
  EX 120 >/dev/null

# jq: `.truncated // empty` seria uma armadilha — o operador `//` dispara em `null`
# E em `false`, então um `false` legítimo viraria "campo ausente". Lê-se cru.
TRUNC=$($CURL "$MCP/api/work_queue/pending?all=1&max_keys=1" | jq -r '.truncated')
case "$TRUNC" in
  true)  ok "truncated=true com max_keys=1 e ≥2 chaves — a parcialidade é declarada" ;;
  false) bad "truncated=false com max_keys=1 e ≥2 chaves — o teto não está sendo declarado" ;;
  *)     bad "campo truncated ausente/inválido na resposta (veio '$TRUNC')" ;;
esac

DUMMY_ST=$($CURL "$MCP/api/work_queue/pending?all=1" \
  | jq -r --arg s "$DUMMY" '.items[] | select(.session_id==$s) | .state // empty')
[ "$DUMMY_ST" = "unknown" ] \
  && ok "pool sem pool_config → estado=unknown (não presumido como push)" \
  || bad "pool sem pool_config → estado='$DUMMY_ST' (esperado unknown)"

redis DEL "${TENANT}:work_task:${DUMMY}" >/dev/null

echo "══ C2) campos que a tela consome estão presentes ══"
ROW=$($CURL "$MCP/api/work_queue/pending?all=1" | jq -c --arg s "$SID" '.items[] | select(.session_id==$s)')
for f in pool_id state age_ms deadline overdue dispatch_mode; do
  echo "$ROW" | jq -e "has(\"$f\")" >/dev/null 2>&1 \
    && ok "campo $f presente" || bad "campo $f AUSENTE"
done
echo "   linha: $ROW"

# ── D/E: só com instância humana logada ──────────────────────────────────────
if [ -n "$INSTANCE" ]; then
  echo "══ D) reivindicado vira 'claimed' ══"
  R=$($CURL -X POST "$MCP/api/work_queue/claim/$SID" $JSON \
      -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INSTANCE\"}")
  if [ "$(echo "$R" | jq -r '.claimed // false')" = "true" ]; then
    sleep 2
    ST=$(state_of "$SID")
    [ "$ST" = "claimed" ] && ok "estado=claimed (fora do ZSET, com lease)" \
                          || bad "estado='$ST' (esperado claimed)"
    HOLDER=$($CURL "$MCP/api/work_queue/pending?all=1" \
      | jq -r --arg s "$SID" '.items[] | select(.session_id==$s) | .claimed_by // empty')
    [ "$HOLDER" = "$INSTANCE" ] && ok "claimed_by = $INSTANCE" \
                                || bad "claimed_by='$HOLDER' (esperado $INSTANCE)"

    echo "══ E) lease apagada sem re-enfileirar vira 'orphaned' ══"
    # Simula EXATAMENTE a lacuna 2: a lease expira e nada devolve o item à fila.
    # É a única forma de produzir a condição sem esperar `claim_lease_s`.
    redis DEL "${TENANT}:pool:${POOL_PULL}:claim:${SID}" >/dev/null
    sleep 1
    ST=$(state_of "$SID")
    [ "$ST" = "orphaned" ] && ok "estado=orphaned — a condição da lacuna 2 é VISÍVEL" \
                           || bad "estado='$ST' (esperado orphaned) — a lacuna 2 seguiria invisível"
  else
    bad "claim falhou ($(echo "$R" | jq -r '.reason // "?"')) — D e E não exercitados"
  fi
else
  echo "══ D/E) cenários REIVINDICADO e ÓRFÃO — NÃO EXERCITADOS ══"
  echo "   ⚠️  rode com INSTANCE=human-<user_id> de um agente logado. Sem isso, esta"
  echo "      execução NÃO diz nada sobre 'claimed' nem sobre 'orphaned'."
fi

echo "══ 9) limpeza — encerra o item para não deixar pendência de teste ══"
# Sem token de supervisor aqui: apaga o ledger e o parqueamento direto, que é o
# necessário para o ambiente não acumular lixo entre execuções.
redis DEL "${TENANT}:work_task:${SID}" >/dev/null 2>&1 || true
redis ZREM "${TENANT}:pool:${POOL_PULL}:queue" "$SID" >/dev/null 2>&1 || true
redis DEL "${TENANT}:queue_contact:${SID}" >/dev/null 2>&1 || true
echo "   ✓ vestígios do teste removidos"

echo
echo "══════════════════════════════════════"
echo "  passou: $pass    falhou: $fail"
[ "$fail" -eq 0 ] || exit 1
echo "  ✅ relatório de pendências (fatia 1) em vigor"
