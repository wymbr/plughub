#!/usr/bin/env bash
# Camada C — ACW como regra de agent_ready (`acw_gate: none|soft|hard`): smoke.
#
# Prova que get_ready_instances (o leitor que o _allocate usa) honra o acw_gate do
# pool: em `hard`, uma instância com wrap-up DETACHED pendente (marker :acw_pending)
# é EXCLUÍDA do roteamento; sem o marker, segue elegível; em `none`/`soft` nada é
# bloqueado. O ACW bloqueante clássico do wrap-up INLINE (wrap_up_pending) é
# independente e não é tocado por este campo.
#
# O produtor do marker :acw_pending (o wrap-up detached de um pool hard) é a
# Camada E — aqui o marker é semeado à mão para exercitar a regra de roteamento em
# isolamento. Chama get_ready_instances direto num harness python dentro do
# routing-engine (o pacote é pip install -e .; sem endpoint HTTP p/ o leitor).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_acw_gate.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
POOL="acw_test"
A="human-acwA"     # instância COM wrap-up detached pendente
B="human-acwB"     # instância SEM pendência (controle)

R() { $COMPOSE exec -T redis redis-cli "$@" >/dev/null; }

inst_json() { printf '{"instance_id":"%s","agent_type_id":"human","tenant_id":"%s","status":"ready","max_concurrent":5,"current_sessions":0,"pools":["%s"]}' "$1" "$TENANT" "$POOL"; }
set_gate() { R SET "${TENANT}:pool_config:${POOL}" "{\"pool_id\":\"${POOL}\",\"tenant_id\":\"${TENANT}\",\"acw_gate\":\"$1\"}"; }

# Harness: get_ready_instances(tenant_demo, acw_test) → instance_ids ordenados, CSV.
ready() {
  $COMPOSE exec -T routing-engine python3 - <<'PY'
import asyncio
import redis.asyncio as aioredis
from plughub_routing.registry import InstanceRegistry
async def main():
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    reg = InstanceRegistry(r)
    res = await reg.get_ready_instances("tenant_demo", "acw_test")
    print(",".join(sorted(i.instance_id for i in res)))
asyncio.run(main())
PY
}

PASS=0; FAIL=0
chk() { if [ "$2" = "$3" ]; then echo "  PASS — $1 (=$2)"; PASS=$((PASS+1)); else echo "  FAIL — $1 (got '$2', want '$3')"; FAIL=$((FAIL+1)); fi; }

echo "0) Semeando pool $POOL (2 instâncias ready; $A com :acw_pending, $B sem) ..."
R SET "${TENANT}:instance:${A}" "$(inst_json "$A")"
R SET "${TENANT}:instance:${B}" "$(inst_json "$B")"
R SADD "${TENANT}:pool:${POOL}:instances" "$A" "$B"
R SET "${TENANT}:instance:${A}:acw_pending" "1"

echo "1) acw_gate=hard → $A (pendente) é EXCLUÍDO; $B segue elegível ..."
set_gate hard
OUT=$(ready | tr -d '\r')
chk "hard exclui a instância com wrap-up pendente" "$OUT" "$B"

echo "2) acw_gate=none → ambos elegíveis (não bloqueia) ..."
set_gate none
OUT=$(ready | tr -d '\r')
chk "none inclui as duas" "$OUT" "$A,$B"

echo "3) acw_gate=soft → ambos elegíveis (não bloqueia; supervisor vê pendências) ..."
set_gate soft
OUT=$(ready | tr -d '\r')
chk "soft inclui as duas" "$OUT" "$A,$B"

echo "4) Limpeza ..."
R DEL "${TENANT}:instance:${A}" "${TENANT}:instance:${B}" "${TENANT}:instance:${A}:acw_pending" \
      "${TENANT}:pool:${POOL}:instances" "${TENANT}:pool_config:${POOL}" || true

echo
echo "======================================"
echo "  PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" = 0 ] && echo "  ✅ SMOKE OK" || { echo "  ❌ SMOKE FALHOU"; exit 1; }
