#!/usr/bin/env bash
# smoke_f5_available_agents_null.sh — verificação de RUNTIME da F5.
#
# O QUE A F5 FEZ. `queue_events.available_agents` era `SCARD(pool:instances)` —
# contagem de PERTENCIMENTO vendida como capacidade. A F5 removeu o campo do produtor
# (`_publish_queue_position`, routing-engine) e do relatório; a coluna sobrevive em
# ClickHouse (Nullable, com histórico) porque dropá-la é migração à parte.
#
# POR QUE UM SMOKE, E NÃO SÓ A SUÍTE. A afirmação da F5 é sobre o PAYLOAD que atravessa
# Kafka: "nenhum produtor volta a preencher este campo". Isso tem DOIS produtores, e um
# teste de unidade sobre qualquer um deles não cobre o outro:
#
#   · `conversations.queued`      → `parse_queued`        (escreve `None` hardcoded)
#   · `queue.position_updated`    → `parse_queue_position` (repassa `payload.get(...)`)
#
# O segundo é o perigoso: ele NÃO hardcoda nada — devolve o que vier no payload. Se
# alguém reintroduzir o campo no produtor (um "só para o gráfico voltar"), o consumer
# aceita em silêncio e a coluna volta a se encher de números ambíguos. Só o dado que
# atravessou o barramento distingue os dois casos.
#
# E POR QUE COM CONTATO EM FILA DE VERDADE. Sem tráfego de fila a tabela não recebe
# linha nenhuma, e "0 linhas com valor" é indistinguível de "0 linhas". Esse foi
# exatamente o estado em que o portão ficou quando se tentou verificar sem tráfego:
# INCONCLUSIVO lido como verde. Este script PRODUZ a fila que vai julgar — e, se ainda
# assim nada chegar, sai INCONCLUSIVO (código 2), nunca aprovado.
#
# O contato roda num tenant SINTÉTICO (`t_f5null_*`) com um pool próprio e ZERO
# instâncias: sem recurso, a rota só pode enfileirar. `queue_config` presente ⇒ fila
# ATENDIDA (o caminho que publica os dois eventos); sem ele cairia na fila muda.
#
# Uso:  bash infra/test/smoke_f5_available_agents_null.sh
# Pré:  stack demo no ar (routing-engine, kafka, clickhouse, analytics-api).
# Dura: ~40 s.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SVC="routing-engine"
# stderr NÃO é engolido. Na 1ª versão deste smoke ele era, e a consequência foi exata:
# a query cortava por `ingested_at`, coluna que `queue_events` NÃO TEM (só
# `pool_occupancy_peaks` tem), o ClickHouse devolvia UNKNOWN_IDENTIFIER, a saída vinha
# vazia e o script leu vazio como "0 linhas" → INCONCLUSIVO por defeito do próprio
# portão. Erro engolido virando zero plausível é o defeito que este arco persegue; um
# harness que o comete não pode julgar ninguém.
CH() {
  local out rc
  out="$($DC exec -T clickhouse clickhouse-client -u plughub --password plughub \
           -d plughub_demo -q "$1" < /dev/null 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "   ⚠️  query ClickHouse FALHOU (rc=$rc): ${out%%$'\n'*}" >&2
    return "$rc"
  fi
  printf '%s' "$out"
}

RAND="$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
TENANT="t_f5null_${RAND}"
POOL="f5null_pool"
SESSION="f5null-${RAND}"

FAIL=0
INCONCLUSIVE=0

note() { echo "   $*"; }
ok()   { echo "   ✅ $*"; }
bad()  { echo "   ❌ $*"; FAIL=1; }
inc()  { echo "   ⚠️  INCONCLUSIVO: $*"; INCONCLUSIVE=1; }

cleanup() {
  $DC exec -T -e C_TENANT="$TENANT" -e C_SESSION="$SESSION" "$SVC" python - <<'PY' >/dev/null 2>&1
import asyncio, os
import redis.asyncio as aioredis
from plughub_routing.config import get_settings
T, S = os.environ["C_TENANT"], os.environ["C_SESSION"]
async def m():
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    # As chaves de sessão NÃO são prefixadas por tenant (`session:{id}:*`) — varrer só
    # `{tenant}:*` deixaria rastro que o próximo run herdaria.
    for pat in (f"{T}:*", f"session:{S}:*"):
        async for k in r.scan_iter(pat):
            await r.delete(k)
asyncio.run(m())
PY
}
trap cleanup EXIT

echo "── pré-condições ───────────────────────────────────────────────────────────"
if ! $DC exec -T "$SVC" python -c 'import plughub_routing' >/dev/null 2>&1; then
  inc "$SVC não executa código (fora do ar, ou sem o pacote) — nada a medir"; exit 2
fi
if [ "$(CH 'SELECT 1')" != "1" ]; then
  inc "ClickHouse inacessível — nada a medir"; exit 2
fi
if [ "$(CH "EXISTS TABLE plughub_demo.queue_events")" != "1" ]; then
  inc "tabela queue_events ausente — o consumer nunca criou o DDL"; exit 2
fi
# As colunas que os portões citam existem? Coluna renomeada faz a query falhar, e
# query que falha devolve vazio — indistinguível de "não há linha" para quem só olha o
# número. Checar o schema ANTES é mais barato que descobrir isso pelo veredicto.
for col in tenant_id event_type available_agents; do
  if [ "$(CH "SELECT count() FROM system.columns
               WHERE database='plughub_demo' AND table='queue_events' AND name='${col}'")" != "1" ]; then
    inc "coluna \`${col}\` ausente em queue_events — o schema mudou e os portões não se aplicam"
    exit 2
  fi
done
ok "stack acessível; schema de queue_events confere"

# CORTE PELO TENANT, não por tempo. `queue_events` não tem `ingested_at` (é
# `ReplacingMergeTree` sobre `timestamp`/`date`), e o tenant sintético é único por run —
# logo ele já separa perfeitamente o que ESTE run gravou dos 142 registros históricos
# com valor não-nulo, legítimos e anteriores à F5. Um corte temporal aqui seria
# redundante e, pior, dependeria de relógios que não são o mesmo.
note "corte pelo tenant sintético '$TENANT' (único por execução)"

echo
echo "── provisionando pool sem recurso e publicando o contato ───────────────────"
PUB="$($DC exec -T -e S_TENANT="$TENANT" -e S_POOL="$POOL" -e S_SESSION="$SESSION" \
  "$SVC" python - <<'PY' 2>&1
import asyncio, json, os
from datetime import datetime, timezone
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer
from plughub_routing.config import get_settings

T, P, S = os.environ["S_TENANT"], os.environ["S_POOL"], os.environ["S_SESSION"]

async def main():
    st = get_settings()
    r  = aioredis.from_url(st.redis_url, decode_responses=True)

    # Config do pool no cache do PRÓPRIO routing-engine (a fonte que `get_pool` lê).
    # `queue_config` presente ⇒ fila ATENDIDA — é o ramo que publica
    # `queue.position_updated`. Sem instância nenhuma, `_allocate` não tem o que alocar.
    await r.set(f"{T}:pool_config:{P}", json.dumps({
        "pool_id": P, "tenant_id": T,
        "channel_types": ["webchat"], "sla_target_ms": 300000,
        "agent_kind": "human", "is_human_pool": True,
        "queue_config": {"agent_type_id": "agente_fila_v1", "skill_id": "skill_fila_v1",
                         "max_wait_s": 1800},
    }))
    await r.sadd(f"{T}:pools", P)

    prod = AIOKafkaProducer(
        bootstrap_servers=st.kafka_brokers,
        value_serializer=lambda v: json.dumps(v).encode(),
    )
    await prod.start()
    try:
        await prod.send_and_wait(st.kafka_topic_inbound, {
            "session_id": S, "tenant_id": T, "customer_id": f"cus_{S}",
            "channel": "webchat", "pool_id": P,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        await prod.stop()
    print(json.dumps({"published": True, "brokers": st.kafka_brokers}))

asyncio.run(main())
PY
)"

if ! printf '%s' "$PUB" | grep -q '"published": true'; then
  inc "não foi possível publicar o contato — saída: $PUB"; exit 2
fi
ok "contato $SESSION publicado em conversations.inbound (pool $POOL, zero instâncias)"

echo
echo "── aguardando fila → Kafka → analytics → ClickHouse ────────────────────────"
N_ROWS=0
for _ in $(seq 1 12); do
  sleep 5
  N_ROWS="$(CH "SELECT count() FROM plughub_demo.queue_events
                 WHERE tenant_id = '${TENANT}'")"
  [ "${N_ROWS:-0}" -gt 0 ] && break
done

echo
echo "── Portão 1 · o contato ENTROU EM FILA (denominador do portão 2) ───────────"
if [ "${N_ROWS:-0}" -eq 0 ]; then
  inc "nenhuma linha em queue_events para o tenant sintético após ~60 s"
  note "o portão 2 NÃO chegou a julgar: sem linha, 'zero valores não-nulos' é vazio,"
  note "não aprovação. Antes de suspeitar da F5, checar se o contato foi roteado ou"
  note "descartado — as duas causas conhecidas são pool sem \`queue_config\` (cai na"
  note "fila muda, que não publica position_updated) e canal com max_wait 0."
  note "Diagnóstico: $DC logs --tail=80 $SVC | grep -E '$SESSION|Queued session'"
  note "             $DC logs --tail=80 analytics-api | grep -i queue"
  exit 2
fi
ok "$N_ROWS linha(s) de fila gravada(s) — há o que julgar"

# Os DOIS produtores têm de aparecer: se só um chegou, o portão 2 cobre metade do
# contrato e o verde valeria pela metade — sem dizer por qual metade.
TYPES="$(CH "SELECT arrayStringConcat(groupUniqArray(event_type), ',')
              FROM plughub_demo.queue_events
             WHERE tenant_id = '${TENANT}'")"
note "event_type presentes: ${TYPES:-<nenhum>}"
for want in queued position_updated; do
  case ",${TYPES}," in
    *",${want},"*) ok "produtor \`${want}\` exercitado" ;;
    *) inc "produtor \`${want}\` NÃO produziu linha — este run cobre só parte do contrato" ;;
  esac
done

echo
echo "── Portão 2 · available_agents só recebe NULL ──────────────────────────────"
NOT_NULL="$(CH "SELECT count() FROM plughub_demo.queue_events
                 WHERE tenant_id = '${TENANT}'
                   AND available_agents IS NOT NULL")"
if [ "${NOT_NULL:-0}" = "0" ]; then
  ok "as $N_ROWS linha(s) trouxeram available_agents NULL"
  note "NULL aqui é a resposta honesta: 'esta grandeza não é medida'. Zero seria o"
  note "valor plausível que a F5 removeu justamente por ser indistinguível de medição."
else
  bad "$NOT_NULL linha(s) NOVA(s) com available_agents preenchido — a F5 regrediu"
  CH "SELECT event_type, pool_id, available_agents
      FROM plughub_demo.queue_events
      WHERE tenant_id = '${TENANT}'
        AND available_agents IS NOT NULL LIMIT 5 FORMAT TSV" | sed 's/^/      /'
  note "um produtor voltou a mandar o campo. \`parse_queue_position\` repassa o payload"
  note "sem filtrar, então a origem é o publisher — procurar em routing-engine"
  note "(\`_publish_queue_position\`) antes do consumer."
fi

echo
echo "── Portão 3 · o produtor não tem mais de onde tirar o número ───────────────"
# Portão de código, e ele existe por uma razão: o portão 2 mede o CAMINHO PERCORRIDO por
# este contato. Um produtor ressuscitado em ramo que este cenário não visita passaria.
# `get_available_count` era a fonte única do campo; enquanto ela não existir, não há
# como repreenchê-lo sem escrever código novo — e aí o diff é visível na revisão.
#
# O padrão casa DEFINIÇÃO (`def get_available_count`) ou CHAMADA (`.get_available_count(`),
# nunca a menção solta: o nome sobrevive de propósito em dois comentários que explicam a
# remoção (`registry.py`, `main.py`), e um grep pelo nome cru reprovaria por causa deles —
# um vermelho que não é vermelho, exatamente o que este arco combate.
GAC="$($DC exec -T "$SVC" sh -c \
        "grep -rnE 'def get_available_count|\.get_available_count\(' /app 2>/dev/null \
         | grep -v '\.pyc' | head -5")"
if [ -z "$GAC" ]; then
  ok "\`get_available_count\` não existe na imagem em execução"
else
  bad "\`get_available_count\` reapareceu:"
  printf '%s\n' "$GAC" | sed 's/^/      /'
  note "era \`SCARD(pool:instances)\` — pertencimento, não vaga. O substituto honesto"
  note "é o rollup deduplicado \`{t}:capacity:snapshot\`, nunca esta contagem."
fi

echo
if [ "$FAIL" -ne 0 ]; then
  echo "RESULTADO: ❌ available_agents voltou a ser preenchido."
  exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
  echo "RESULTADO: ⚠️  INCONCLUSIVO — algum portão não chegou a julgar (ver acima)."
  echo "Um run que não mediu não é aprovação; rodar de novo ou corrigir a pré-condição."
  exit 2
else
  echo "RESULTADO: contato real enfileirado, os dois produtores exercitados, nenhum"
  echo "preencheu available_agents."
fi
