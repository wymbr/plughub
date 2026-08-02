#!/usr/bin/env bash
# Wrap-up unificado Phase 2 — hand-off da vaga: smoke.
#
# Prova que, no close de um contato com wrap-up INLINE seguindo, a vaga da origem é
# TROCADA por um hold ("__wrapup_hold__::{origin}::{pool_id}::{expires_at_ms}") em vez de
# liberada, e que o auto-claim do wrap-up a HERDA (swap net 0). A ocupação nunca
# oscila → um contato push não toma a vaga na janela a max_concurrent=1.
#
# Cobre as duas camadas:
#   A) semáforo (harness python no routing-engine): swap → herança → expiração;
#   B) árbitro real (POST /v1/work_queue/claim): só item `auto_attend` reivindicado
#      pelo DONO herda; item comum (pull manual) e push NÃO herdam.
#
# O produtor do flag `keep_slot_for_wrapup` é o bridge (agent_done do humano, quando
# o pool tem on_human_end side=agent dispatch=inline) — fora deste escopo; aqui o
# hold é criado pelo primitivo `swap_to_hold`, que é exatamente o que o
# remove_conversation chama ao ver o flag.
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_wrapup_slot_handoff.sh
# Spec: docs/product/wrapup-slot-handoff-phase2-spec.md
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
POOL="handoff_test"
USER_A="userHA"
INST="human-${USER_A}"
SETKEY="${TENANT}:instance:${INST}:sessions"

R()  { $COMPOSE exec -T redis redis-cli "$@" >/dev/null; }
RQ() { $COMPOSE exec -T redis redis-cli "$@" | tr -d '\r'; }

inst_json() {  # $1 = max_concurrent
  printf '{"instance_id":"%s","agent_type_id":"human","tenant_id":"%s","status":"ready","max_concurrent":%s,"current_sessions":0,"pools":["%s"],"source":"human_login"}' \
    "$INST" "$TENANT" "$1" "$POOL"
}

# claim(session, instance) → JSON do árbitro (mesmo endpoint da Console/mcp-server)
claim() {
  local sid="$1" inst="$2"
  $COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$sid','instance_id':'$inst'}).encode()
req=urllib.request.Request('http://localhost:3550/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())
"
}

# harness: chama o primitivo direto (o pacote é pip install -e . no container)
sem() {  # $1 = trecho python usando `reg`
  $COMPOSE exec -T routing-engine python3 - <<PY
import asyncio
import redis.asyncio as aioredis
from plughub_routing.registry import InstanceRegistry
async def main():
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    reg = InstanceRegistry(r)
    tenant, inst = "$TENANT", "$INST"
    $1
asyncio.run(main())
PY
}

holds() { RQ SMEMBERS "$SETKEY" | grep -c '^__wrapup_hold__::' || true; }
occ()   { RQ SCARD "$SETKEY"; }

PASS=0; FAIL=0
chk() { if [ "$2" = "$3" ]; then echo "  PASS — $1 (=$2)"; PASS=$((PASS+1)); else echo "  FAIL — $1 (got '$2', want '$3')"; FAIL=$((FAIL+1)); fi; }
chkj() {  # $1=desc $2=json $3=claimed(true|false) $4=reason(opcional)
  local ok=1
  echo "$2" | grep -q "\"claimed\": *$3" || ok=0
  [ -n "${4:-}" ] && { echo "$2" | grep -q "\"reason\": *\"$4\"" || ok=0; }
  if [ "$ok" = 1 ]; then echo "  PASS — $1"; PASS=$((PASS+1)); else echo "  FAIL — $1"; echo "         resp: $2"; FAIL=$((FAIL+1)); fi
}

cleanup() {
  R DEL "${TENANT}:instance:${INST}" "$SETKEY" \
        "${TENANT}:pool:${POOL}:instances" "${TENANT}:pool:${POOL}:queue" \
        "${TENANT}:pool:${POOL}:busy_instances" \
        "${TENANT}:queue_contact:${W1}" "${TENANT}:queue_contact:${W2}" \
        "${TENANT}:queue_contact:${P1}" >/dev/null 2>&1 || true
}
W1="sess_wrapup_auto_$$"; W2="sess_wrapup_manual_$$"; P1="sess_push_$$"
trap cleanup EXIT

echo "0) Semeando instância humana $INST (max_concurrent=1) ..."
R SET "${TENANT}:instance:${INST}" "$(inst_json 1)"
R SADD "${TENANT}:pool:${POOL}:instances" "$INST"
R DEL "$SETKEY"

# ── A) semáforo ──────────────────────────────────────────────────────────────
echo "1) Origem ocupa a vaga; close com wrap-up inline → swap_to_hold (net 0) ..."
sem 'await reg.claim_instance(tenant, inst, "ses-origin", None, 1)' >/dev/null
chk "ocupação após o contato" "$(occ)" "1"
sem 'await reg.swap_to_hold(tenant, inst, "ses-origin", 90)' >/dev/null
chk "ocupação após o close (vaga SEGURA, não liberada)" "$(occ)" "1"
chk "hold presente no SET" "$(holds)" "1"

echo "2) Push na janela é RECUSADO (não herda hold) ..."
OUT=$(sem 'print(await reg.claim_instance(tenant, inst, "ses-push-x", None, 1))')
chk "claim de push retorna -1" "$(echo "$OUT" | tr -d '\r')" "-1"
chk "hold intacto" "$(holds)" "1"

echo "3) Wrap-up (can_inherit_hold) HERDA o hold — ocupação segue 1 ..."
OUT=$(sem 'print(await reg.claim_instance(tenant, inst, "ses-wrapup", None, 1, can_inherit_hold=True))')
chk "claim herdeiro retorna 1" "$(echo "$OUT" | tr -d '\r')" "1"
chk "ocupação continua 1 (nunca 0 nem 2)" "$(occ)" "1"
chk "hold consumido" "$(holds)" "0"
sem 'await reg.release_instance(tenant, inst, "ses-wrapup")' >/dev/null
chk "release do wrap-up devolve a vaga" "$(occ)" "0"

echo "4) Vazamento: wrap-up nunca chega → hold EXPIRA e a vaga volta ao push ..."
sem 'await reg.claim_instance(tenant, inst, "ses-origin2", None, 1)' >/dev/null
sem 'await reg.swap_to_hold(tenant, inst, "ses-origin2", -1)' >/dev/null   # já expirado
chk "hold (expirado) presente antes do próximo claim" "$(holds)" "1"
OUT=$(sem 'print(await reg.claim_instance(tenant, inst, "ses-push-y", None, 1))')
chk "push passa após o hold expirar" "$(echo "$OUT" | tr -d '\r')" "1"
chk "hold expirado descartado" "$(holds)" "0"
sem 'await reg.release_instance(tenant, inst, "ses-push-y")' >/dev/null

# ── B) árbitro real (work_task_claim) ────────────────────────────────────────
NOW=$($COMPOSE exec -T redis redis-cli TIME | head -1 | tr -d '\r')000
qc() {  # $1=session $2=extra
  printf '{"session_id":"%s","tenant_id":"%s","pool_id":"%s","queued_at_ms":%s%s}' "$1" "$TENANT" "$POOL" "$NOW" "$2"
}

echo "5) Item de wrap-up AUTO-ATENDIDO do dono herda o hold via /work_queue/claim ..."
R DEL "$SETKEY"
sem 'await reg.claim_instance(tenant, inst, "ses-origin3", None, 1)' >/dev/null
sem 'await reg.swap_to_hold(tenant, inst, "ses-origin3", 90)' >/dev/null
R SET "${TENANT}:queue_contact:${W1}" "$(qc "$W1" ",\"assigned_to\":\"${USER_A}\",\"auto_attend\":true")"
R ZADD "${TENANT}:pool:${POOL}:queue" "$NOW" "$W1"
chkj "claim auto_attend do dono é aceito (herda, sem capacidade livre)" "$(claim "$W1" "$INST")" true ""
chk "ocupação continua 1" "$(occ)" "1"
chk "hold consumido" "$(holds)" "0"

echo "6) Item de pull MANUAL (sem auto_attend) NÃO herda — recusado por capacidade ..."
R DEL "$SETKEY"
sem 'await reg.claim_instance(tenant, inst, "ses-origin4", None, 1)' >/dev/null
sem 'await reg.swap_to_hold(tenant, inst, "ses-origin4", 90)' >/dev/null
R SET "${TENANT}:queue_contact:${W2}" "$(qc "$W2" ",\"assigned_to\":\"${USER_A}\"")"
R ZADD "${TENANT}:pool:${POOL}:queue" "$NOW" "$W2"
chkj "claim manual recusado (hold é só do auto-atendimento)" "$(claim "$W2" "$INST")" false no_capacity
chk "hold intacto após a recusa" "$(holds)" "1"
chk "item re-enfileirado (rollback)" "$(RQ ZSCORE "${TENANT}:pool:${POOL}:queue" "$W2" | grep -c '[0-9]' || true)" "1"

echo
echo "======================================"
echo "  PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" = 0 ] && echo "  ✅ SMOKE OK" || { echo "  ❌ SMOKE FALHOU"; exit 1; }
