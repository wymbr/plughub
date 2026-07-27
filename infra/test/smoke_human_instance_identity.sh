#!/usr/bin/env bash
# F1 do ADR `adr-human-agent-pool-scoped-identity` — liveness ≠ identidade: smoke.
#
# Prova, pelo caminho REAL (Kafka `agent.lifecycle` → routing-engine), que:
#   1) um `agent_heartbeat` (pong de UMA das N conexões WS do humano) NÃO encolhe
#      `pools[]` nem troca `agent_type_id` da instância;
#   2) o mesmo vale para um produtor LEGADO (pré-F1), que ainda manda
#      `pools:[poolId]` + `agent_type_id` da própria conexão — a defesa vive no
#      consumidor, não depende de todo produtor estar atualizado;
#   3) `agent_ready` SEGUE sendo autoritativo: logout parcial ainda remove o pool
#      (senão o agente ficaria preso num pool do qual saiu).
#
# Por que isso importa: antes da F1, o Console (que abre um WS por pool) fazia a
# identidade da instância oscilar a cada 15 s conforme qual conexão pingou por
# último — e é esse `agent_type_id` que vira `conversations.routed.agent_type_id`,
# com o qual o bridge escolhe o que executar. Um contato chegou a rodar
# `skill_wrapup_detached_v1` na própria sessão e se autofechar.
#
# Cobertura complementar (unitária, mesma regra):
#   docker compose -f docker-compose.demo.yml exec -T routing-engine \
#     python -m pytest src/plughub_routing/tests/test_human_instance_identity.py -q
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_human_instance_identity.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
USER_ID="userF1smoke"
INST="human-${USER_ID}"
POOL_A="f1_pool_a"
POOL_B="f1_pool_b"
POOL_C="f1_pool_c"
IKEY="${TENANT}:instance:${INST}"

R()  { $COMPOSE exec -T redis redis-cli "$@" >/dev/null; }
RQ() { $COMPOSE exec -T redis redis-cli "$@" | tr -d '\r'; }

FAILED=0
ok()   { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; FAILED=1; }

# Lê um campo do JSON da instância (mesma URL de Redis que o serviço usa).
field() {
  $COMPOSE exec -T routing-engine python3 -c "
import asyncio,json
import redis.asyncio as aioredis
from plughub_routing.config import get_settings
async def main():
    r=aioredis.from_url(get_settings().redis_url,decode_responses=True)
    raw=await r.get('$IKEY')
    print('' if not raw else json.dumps(json.loads(raw).get('$1')))
    await r.aclose()
asyncio.run(main())
" | tr -d '\r'
}

# Publica um evento em agent.lifecycle e espera o consumo.
# Broker e tópico saem do MESMO settings que o serviço usa (`PLUGHUB_KAFKA_BROKERS`,
# `kafka:29092` no demo) — adivinhar `KAFKA_BROKERS`/9092 aponta para o lugar errado.
publish() {
  $COMPOSE exec -T routing-engine python3 -c "
import asyncio,json,sys
from aiokafka import AIOKafkaProducer
from plughub_routing.config import get_settings
async def main():
    s=get_settings()
    p=AIOKafkaProducer(bootstrap_servers=s.kafka_brokers)
    await p.start()
    try:
        await p.send_and_wait(s.kafka_topic_lifecycle, json.dumps(json.loads(sys.argv[1])).encode())
    finally:
        await p.stop()
asyncio.run(main())
" "$1"
  sleep 2
}

cleanup() {
  R DEL "$IKEY" "${TENANT}:routing:instance:${INST}:meta" "${IKEY}:sessions" || true
  for p in "$POOL_A" "$POOL_B" "$POOL_C"; do
    R SREM "${TENANT}:pool:${p}:instances" "$INST" || true
  done
}
trap cleanup EXIT

echo "── setup: humano logado em 3 pools (como registerHumanAgent escreve) ──────"
cleanup
$COMPOSE exec -T redis redis-cli SET "$IKEY" "$(cat <<JSON
{"instance_id":"$INST","agent_type_id":"human_agent_${POOL_A}","user_id":"$USER_ID","user_login":"$USER_ID@demo.local","tenant_id":"$TENANT","pool_id":"$POOL_A","pools":["$POOL_A","$POOL_B","$POOL_C"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"status":"ready","source":"human_login"}
JSON
)" >/dev/null
for p in "$POOL_A" "$POOL_B" "$POOL_C"; do R SADD "${TENANT}:pool:${p}:instances" "$INST"; done
echo "  pools=$(field pools)  agent_type_id=$(field agent_type_id)"

echo
echo "── 1) heartbeat pós-F1 (heartbeat_pool=B, sem pools/agent_type_id) ────────"
publish "{\"event\":\"agent_heartbeat\",\"tenant_id\":\"$TENANT\",\"instance_id\":\"$INST\",\"heartbeat_pool\":\"$POOL_B\",\"user_id\":\"$USER_ID\",\"user_login\":\"$USER_ID@demo.local\",\"status\":\"ready\",\"execution_model\":\"stateful\",\"max_concurrent_sessions\":3,\"timestamp\":\"2026-07-27T12:00:00Z\"}"
POOLS=$(field pools)
[[ "$POOLS" == *"$POOL_A"* && "$POOLS" == *"$POOL_B"* && "$POOLS" == *"$POOL_C"* ]] \
  && ok "membership intacta: $POOLS" || fail "membership encolheu: $POOLS"
[[ "$(field agent_type_id)" == "\"human_agent_${POOL_A}\"" ]] \
  && ok "agent_type_id preservado" || fail "agent_type_id trocado: $(field agent_type_id)"

echo
echo "── 2) heartbeat LEGADO (pools:[B] + agent_type_id da conexão) ─────────────"
publish "{\"event\":\"agent_heartbeat\",\"tenant_id\":\"$TENANT\",\"instance_id\":\"$INST\",\"agent_type_id\":\"human_agent_${POOL_B}\",\"pools\":[\"$POOL_B\"],\"current_sessions\":0,\"user_id\":\"$USER_ID\",\"status\":\"ready\",\"execution_model\":\"stateful\",\"max_concurrent_sessions\":3,\"timestamp\":\"2026-07-27T12:00:05Z\"}"
POOLS=$(field pools)
[[ "$POOLS" == *"$POOL_A"* && "$POOLS" == *"$POOL_C"* ]] \
  && ok "payload legado ignorado: $POOLS" || fail "payload legado corrompeu: $POOLS"
[[ "$(field agent_type_id)" == "\"human_agent_${POOL_A}\"" ]] \
  && ok "agent_type_id preservado" || fail "agent_type_id trocado: $(field agent_type_id)"
for p in "$POOL_A" "$POOL_B" "$POOL_C"; do
  [[ "$(RQ SISMEMBER "${TENANT}:pool:${p}:instances" "$INST")" == "1" ]] \
    && ok "segue no pool set de $p" || fail "sumiu do pool set de $p"
done

echo
echo "── 3) agent_ready (logout parcial) AINDA é autoritativo ───────────────────"
publish "{\"event\":\"agent_ready\",\"tenant_id\":\"$TENANT\",\"instance_id\":\"$INST\",\"agent_type_id\":\"human_agent_${POOL_A}\",\"pools\":[\"$POOL_A\",\"$POOL_C\"],\"status\":\"ready\",\"execution_model\":\"stateful\",\"current_sessions\":0,\"timestamp\":\"2026-07-27T12:00:10Z\"}"
POOLS=$(field pools)
[[ "$POOLS" != *"$POOL_B"* && "$POOLS" == *"$POOL_A"* && "$POOLS" == *"$POOL_C"* ]] \
  && ok "logout parcial removeu $POOL_B: $POOLS" || fail "logout parcial não aplicou: $POOLS"
[[ "$(RQ SISMEMBER "${TENANT}:pool:${POOL_B}:instances" "$INST")" == "0" ]] \
  && ok "removido do pool set de $POOL_B" || fail "ainda no pool set de $POOL_B"

echo
if [[ $FAILED -eq 0 ]]; then
  echo "✅ smoke_human_instance_identity: OK"
else
  echo "❌ smoke_human_instance_identity: FALHOU"
  echo "   dica: o warning 'human instance membership SHRANK' no log do routing-engine"
  echo "   nomeia quem encolheu — \$COMPOSE logs routing-engine | grep -i 'SHRANK\\|divergence'"
fi
exit $FAILED
