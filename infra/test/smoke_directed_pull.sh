#!/usr/bin/env bash
# Camada B — pull direcionado ("ramal"): smoke do gate de elegibilidade de claim.
#
# Prova que um work item da fila pull reservado a um recurso (assigned_to) só é
# claimable por (a) o próprio dono, ou (b) qualquer um do pool APÓS o transbordo
# (idade >= fallback_to_pool_after_s). Reserva permanente (sem fallback) nunca
# transborda. INVARIANTE preservado: assigned_to é filtro de claim sobre trabalho
# pooled — NÃO alvo de roteamento (o pool segue a unidade endereçável).
#
# Como o Routing Engine é o único árbitro (o gate vive DENTRO de work_task_claim),
# o teste exercita o endpoint real POST /v1/work_queue/claim. Semeamos direto no
# Redis o par de instâncias humanas + os itens da fila (o produtor real do item
# direcionado é a Camada D/bridge, fora deste escopo — aqui só o primitivo).
#
# Uso (da raiz do repo, demo no ar):  bash infra/test/smoke_directed_pull.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
POOL="ramal_test"
USER_A="userA"
USER_B="userB"
INST_A="human-${USER_A}"
INST_B="human-${USER_B}"

R() { $COMPOSE exec -T redis redis-cli "$@" >/dev/null; }

# claim(session, instance_id) → ecoa o JSON de resposta do árbitro.
# routing-engine não publica porta no host nem exige X-Admin-Token (rede interna);
# usamos python (garantido na imagem) para POST em localhost:3550.
claim() {
  local sid="$1" inst="$2"
  $COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$sid','instance_id':'$inst'}).encode()
req=urllib.request.Request('http://localhost:3550/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())
"
}

inst_json() {  # $1 = instance_id
  printf '{"instance_id":"%s","agent_type_id":"human","tenant_id":"%s","status":"ready","max_concurrent":5,"current_sessions":0,"pools":["%s"],"source":"human_login"}' "$1" "$TENANT" "$POOL"
}

now_ms() { date +%s%3N; }

PASS=0; FAIL=0
check() {  # $1 = descrição  $2 = JSON de resposta  $3 = espera claimed (true|false)  $4 = espera reason (ou "")
  local desc="$1" resp="$2" want_claimed="$3" want_reason="${4:-}"
  local ok=1
  echo "$resp" | grep -q "\"claimed\": *$want_claimed" || ok=0
  if [ -n "$want_reason" ]; then
    echo "$resp" | grep -q "\"reason\": *\"$want_reason\"" || ok=0
  fi
  if [ "$ok" = 1 ]; then echo "  PASS — $desc"; PASS=$((PASS+1))
  else echo "  FAIL — $desc"; echo "         resp: $resp"; FAIL=$((FAIL+1)); fi
}

echo "0) Semeando instâncias humanas ($INST_A, $INST_B) ..."
R SET "${TENANT}:instance:${INST_A}" "$(inst_json "$INST_A")"
R SET "${TENANT}:instance:${INST_B}" "$(inst_json "$INST_B")"
R SADD "${TENANT}:pool:${POOL}:instances" "$INST_A" "$INST_B"

NOW=$(now_ms)
S1="sess_ramal_1_$$"
S2="sess_ramal_2_$$"
S3="sess_ramal_3_$$"

qc() {  # $1=session  $2=extra_json (campos assigned_*/fallback)
  printf '{"session_id":"%s","tenant_id":"%s","pool_id":"%s","queued_at_ms":%s%s}' "$1" "$TENANT" "$POOL" "$NOW" "$2"
}

echo "1) S1 — reservado a $USER_A, fallback 3s, recém-enfileirado (na janela) ..."
R SET "${TENANT}:queue_contact:${S1}" "$(qc "$S1" ',"assigned_to":"userA","fallback_to_pool_after_s":3')"
R ZADD "${TENANT}:pool:${POOL}:queue" "$NOW" "$S1"
check "userB NÃO pode reivindicar dentro da janela"  "$(claim "$S1" "$INST_B")"  false  reserved_to_other
check "userA (dono) reivindica dentro da janela"     "$(claim "$S1" "$INST_A")"  true   ""

echo "2) S2 — reservado a $USER_A, fallback 2s, âncora 5s no passado (TRANSBORDADO) ..."
OLD=$((NOW - 5000))
R SET "${TENANT}:queue_contact:${S2}" "$(qc "$S2" ",\"assigned_to\":\"userA\",\"fallback_to_pool_after_s\":2,\"assigned_at_ms\":${OLD}")"
R ZADD "${TENANT}:pool:${POOL}:queue" "$OLD" "$S2"
check "userB reivindica após o transbordo"           "$(claim "$S2" "$INST_B")"  true   ""

echo "3) S3 — reservado a $USER_A, SEM fallback (reserva permanente) ..."
R SET "${TENANT}:queue_contact:${S3}" "$(qc "$S3" ',"assigned_to":"userA"')"
R ZADD "${TENANT}:pool:${POOL}:queue" "$NOW" "$S3"
check "userB NUNCA transborda numa reserva permanente" "$(claim "$S3" "$INST_B")" false reserved_to_other
check "userA (dono) reivindica a reserva permanente"   "$(claim "$S3" "$INST_A")" true  ""

echo "4) Limpeza ..."
R DEL "${TENANT}:instance:${INST_A}" "${TENANT}:instance:${INST_B}" \
      "${TENANT}:instance:${INST_A}:sessions" "${TENANT}:instance:${INST_B}:sessions" \
      "${TENANT}:queue_contact:${S1}" "${TENANT}:queue_contact:${S2}" "${TENANT}:queue_contact:${S3}" \
      "${TENANT}:pool:${POOL}:queue" "${TENANT}:pool:${POOL}:instances" \
      "${TENANT}:pool:${POOL}:busy_instances" || true

echo
echo "======================================"
echo "  PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" = 0 ] && echo "  ✅ SMOKE OK" || { echo "  ❌ SMOKE FALHOU"; exit 1; }
