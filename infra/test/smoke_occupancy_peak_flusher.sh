#!/usr/bin/env bash
# smoke_occupancy_peak_flusher.sh — o LAÇO do flusher, ponta a ponta (P1).
#
# POR QUE ESTE SMOKE EXISTE. Os testes do P1 (`test_pool_occupancy_peak.py`) cobrem os
# PRIMITIVOS (watermark, seed, leitura) e a prova por mutação mostra que cada peça tem
# quem a derrube. Nada disso toca o LAÇO: a virada do minuto, o seed do bucket novo e o
# `_flush_occupancy` publicando só existem dentro do serviço vivo. Um P1 correto nos
# primitivos e com o laço quebrado produziria exatamente o sintoma que o arco combate —
# série sem pico — e nenhum teste ficaria vermelho.
#
# TRÊS PORTÕES, e o do meio é o contrato:
#   A. o flusher está VIVO           → linha nova chegando em pool_occupancy_peaks
#   B. o CONTRATO ponta a ponta      → um pico que sobe a 2 e volta a 0 DENTRO de um
#                                      minuto aparece na série. Contra a amostragem de
#                                      5 s isso dava 0 (ou linha nenhuma).
#   C. achado 1 (skew de capacidade) → nenhuma linha nova com peak > provisioned
#
# JANELA POR `ingested_at`, NUNCA por `minute`: o corte precisa separar o que ESTE run
# gravou do que já estava na tabela. Cortar por `minute` cobraria de linhas antigas
# (pré-deploy, legitimamente enviesadas pelo achado 1) e o portão C reprovaria por
# história, não por defeito.
#
# O run cria um tenant SINTÉTICO (`t_smokepeak_*`): as chaves Redis são limpas no fim, e
# as linhas em ClickHouse ficam isoladas de `tenant_demo` — não poluem relatório nenhum.
#
# Uso:  bash infra/test/smoke_occupancy_peak_flusher.sh
# Pré:  stack demo no ar (routing-engine, kafka, clickhouse, analytics-api).
# Dura: ~2 min (precisa de uma virada de minuto real — é o que está sendo testado).

# Sem `set -e`: vários comandos devolvem não-zero como RESULTADO (query vazia, poll que
# ainda não achou). Abortar no primeiro seria perder o veredicto.
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SVC="routing-engine"
CH() { $DC exec -T clickhouse clickhouse-client -u plughub --password plughub \
         -d plughub_demo -q "$1" < /dev/null 2>/dev/null; }

RAND="$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
TENANT="t_smokepeak_${RAND}"
POOL="smokepeak_pool"
INST="human-smoke-${RAND}"

FAIL=0
INCONCLUSIVE=0

note()  { echo "   $*"; }
ok()    { echo "   ✅ $*"; }
bad()   { echo "   ❌ $*"; FAIL=1; }
# INCONCLUSIVO ≠ vermelho: significa que o portão não chegou a julgar. Sai não-zero
# assim mesmo, porque um run que não mediu não pode ser lido como aprovação.
inc()   { echo "   ⚠️  INCONCLUSIVO: $*"; INCONCLUSIVE=1; }

cleanup() {
  $DC exec -T "$SVC" python - <<PY >/dev/null 2>&1
import asyncio, redis.asyncio as aioredis
from plughub_routing.config import get_settings
async def m():
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    async for k in r.scan_iter("${TENANT}:*"):
        await r.delete(k)
asyncio.run(m())
PY
}
trap cleanup EXIT

echo "── pré-condições ───────────────────────────────────────────────────────────"
# Sonda por EXEC, não por `ps --status`: o que importa é conseguir rodar código dentro
# do serviço, e é isso que a sonda prova. Um `ps` verde com o container inutilizável
# deixaria o smoke falhar mais tarde, num portão, parecendo defeito do código.
if ! $DC exec -T "$SVC" python -c 'import plughub_routing' >/dev/null 2>&1; then
  inc "$SVC não executa código (fora do ar, ou sem o pacote) — nada a medir"; exit 2
fi
if [ "$(CH 'SELECT 1')" != "1" ]; then
  inc "ClickHouse inacessível — nada a medir"; exit 2
fi
if [ "$(CH "EXISTS TABLE plughub_demo.pool_occupancy_peaks")" != "1" ]; then
  inc "tabela pool_occupancy_peaks ausente — o consumer nunca criou o DDL"; exit 2
fi
ok "stack acessível"

# Marca do início: tudo que este run julga tem de ter sido INGERIDO depois daqui.
T0="$(CH "SELECT toString(now())")"
note "corte de ingestão (ingested_at >= '$T0')"

# ── Portão B (o cenário roda primeiro; A e C leem o que ele produzir) ─────────
echo
echo "── burst: ocupação sobe a 2 e volta a 0 dentro de UM minuto ───────────────"
# Espera a virada do minuto: o burst tem de cair no INÍCIO de um minuto, para que o
# flusher (tick de 5 s) veja o pool sintético pelo menos uma vez antes da próxima
# virada. Pool que nasce nos últimos segundos do minuto não entra em `seen_pools` e
# não gera linha — seria falha de temporização do smoke, não do código.
SEC="$(date -u +%-S)"
SLEEP=$(( (60 - SEC) % 60 ))
[ "$SLEEP" -gt 0 ] && { note "aguardando ${SLEEP}s pela virada do minuto"; sleep "$SLEEP"; }

BURST="$($DC exec -T -e SMOKE_TENANT="$TENANT" -e SMOKE_POOL="$POOL" -e SMOKE_INST="$INST" \
  "$SVC" python - <<'PY' 2>&1
import asyncio, json, os
import redis.asyncio as aioredis
from plughub_routing.config import get_settings
from plughub_routing.models import AgentInstance
from plughub_routing.registry import (
    InstanceRegistry, _pool_instances_key, minute_bucket,
)

T, P, I = os.environ["SMOKE_TENANT"], os.environ["SMOKE_POOL"], os.environ["SMOKE_INST"]
CAP = 3

async def main():
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    reg = InstanceRegistry(r)
    await reg.set_instance(AgentInstance(
        instance_id=I, agent_type_id="human", tenant_id=T, pools=[P],
        execution_model="stateful", max_concurrent=CAP, current_sessions=0,
        state="ready", source="human_login",
    ))
    await r.sadd(_pool_instances_key(T, P), I)
    # O snapshot precisa existir: o fan-out (e portanto o bump) pula pool sem linha.
    await reg.write_pool_snapshot(
        tenant_id=T, pool_id=P, sla_target_ms=480_000, channel_types=["webchat"],
    )
    b0 = minute_bucket()
    for i in (1, 2):
        occ = await reg.claim_instance(T, I, f"smoke-{i}", None, CAP, pool_id=P)
        if occ != i:
            print(json.dumps({"error": f"claim devolveu {occ}, esperava {i}"})); return
        await reg.mark_busy(T, P, I, f"smoke-{i}")
    for i in (1, 2):
        await reg.release_instance(T, I, f"smoke-{i}")
    print(json.dumps({
        "bucket": b0,
        "rolled": b0 != minute_bucket(),
        "left":   await reg.instance_session_count(T, I),
    }))

asyncio.run(main())
PY
)"

BUCKET="$(printf '%s' "$BURST" | sed -n 's/.*"bucket": *"\([0-9]\{12\}\)".*/\1/p')"
if [ -z "$BUCKET" ]; then
  inc "burst não produziu bucket — saída: $BURST"; exit 2
fi
if printf '%s' "$BURST" | grep -q '"rolled": true'; then
  inc "o burst atravessou a virada do minuto — medição sobre dois buckets, descartada"
  exit 2
fi
if ! printf '%s' "$BURST" | grep -q '"left": 0'; then
  inc "a ocupação não voltou a zero após o burst — o cenário não é o que se afirma: $BURST"
  exit 2
fi
# YYYYmmddHHMM → 'YYYY-MM-DD HH:MM:00'
MINUTE="${BUCKET:0:4}-${BUCKET:4:2}-${BUCKET:6:2} ${BUCKET:8:2}:${BUCKET:10:2}:00"
ok "burst limpo no minuto $MINUTE (subiu a 2, voltou a 0, sem nenhuma amostra no meio)"

# O flusher publica o minuto M quando percebe o M+1 (tick de 5 s), e a linha ainda
# atravessa Kafka → analytics-api → ClickHouse.
echo
echo "── aguardando o flush do minuto (virada + Kafka + ingest) ──────────────────"
ROW=""
for _ in $(seq 1 30); do
  sleep 5
  ROW="$(CH "SELECT peak_concurrency, provisioned_capacity FROM plughub_demo.pool_occupancy_peaks FINAL
             WHERE tenant_id='${TENANT}' AND pool_id='${POOL}'
               AND minute = toDateTime64('${MINUTE}', 3, 'UTC')
             FORMAT TSV")"
  [ -n "$ROW" ] && break
done

echo
echo "── Portão B · o contrato ponta a ponta ─────────────────────────────────────"
if [ -z "$ROW" ]; then
  bad "nenhuma linha para (${POOL}, ${MINUTE}) após ~150 s"
  note "o watermark existe (os testes provam), então a quebra está no LAÇO: virada do"
  note "minuto, seed, _flush_occupancy, o producer Kafka ou o consumer da analytics."
  note "Diagnóstico: docker compose -f docker-compose.demo.yml logs --tail=80 $SVC | grep -i occupanc"
else
  PEAK="$(printf '%s' "$ROW" | cut -f1)"
  CAP_="$(printf '%s' "$ROW" | cut -f2)"
  if [ "$PEAK" = "2" ]; then
    ok "pico 2 publicado na série (peak=$PEAK provisioned=$CAP_)"
    note "é o pico que a amostragem de 5 s perdia: subiu e desceu entre duas passadas."
  else
    bad "pico publicado = $PEAK, esperava 2 (provisioned=$CAP_)"
    note "0 significa que o laço publicou o bucket sem ler o watermark — de volta à amostragem."
  fi
fi

# ── Portão A · o flusher está vivo (para os pools REAIS, não só o sintético) ──
echo
echo "── Portão A · linhas novas chegando ────────────────────────────────────────"
N_NEW="$(CH "SELECT count() FROM plughub_demo.pool_occupancy_peaks
              WHERE ingested_at >= toDateTime('${T0}') AND tenant_id != '${TENANT}'")"
N_POOLS="$(CH "SELECT uniqExact(pool_id) FROM plughub_demo.pool_occupancy_peaks
                WHERE ingested_at >= toDateTime('${T0}') AND tenant_id != '${TENANT}'")"
if [ "${N_NEW:-0}" -gt 0 ]; then
  ok "$N_NEW linha(s) nova(s) em $N_POOLS pool(s) além do sintético — o laço serve o tenant real"
else
  inc "nenhuma linha de outro tenant no período"
  note "não é reprovação: se o ambiente não tem pool com instância registrada, não há"
  note "o que publicar. O portão B já provou que o laço funciona. Confirmar com:"
  note "  docker compose -f docker-compose.demo.yml exec -T redis redis-cli --scan --pattern '*:pool:*:instances'"
fi

# ── Portão C · achado 1: peak > capacity é impossível por construção ─────────
echo
echo "── Portão C · skew de capacidade (achado 1) ────────────────────────────────"
# Só pools REAIS: nos marcadores agregados (__reserved__/__shared__/__buffer__) a
# "capacidade" é limite de ADMISSÃO — outra grandeza, a comparação não se aplica.
VIOL="$(CH "SELECT count() FROM plughub_demo.pool_occupancy_peaks FINAL
             WHERE ingested_at >= toDateTime('${T0}')
               AND pool_id NOT LIKE '\\_\\_%'
               AND peak_concurrency > provisioned_capacity")"
if [ "${VIOL:-0}" = "0" ]; then
  ok "nenhuma linha nova com peak > provisioned"
  note "a assinatura do achado 1 ('peak 1 / provisioned 0') não reaparece."
else
  bad "$VIOL linha(s) nova(s) com peak > provisioned — a capacidade voltou a ser lida no flush"
  CH "SELECT tenant_id, pool_id, minute, peak_concurrency, provisioned_capacity
      FROM plughub_demo.pool_occupancy_peaks FINAL
      WHERE ingested_at >= toDateTime('${T0}') AND pool_id NOT LIKE '\\_\\_%'
        AND peak_concurrency > provisioned_capacity
      LIMIT 5 FORMAT TSV" | sed 's/^/      /'
fi

# ── Portão D · o seed da VIRADA rodou (o que A não consegue separar) ────────
echo
echo "── Portão D · seed do bucket novo ──────────────────────────────────────────"
# Por que A não basta: um pool ocioso gera linha nos DOIS casos — seed da virada
# gravando 0, ou seed ausente e o flusher publicando 0 com o log "watermark AUSENTE".
# Mesma linha, hipóteses opostas. O log é o único discriminador, e é justamente por
# isso que aquela ausência é logada em vez de degradar em silêncio.
#
# O TENANT SINTÉTICO É EXCLUÍDO — e a razão é o próprio harness, não uma conveniência.
# O `trap cleanup` apaga as chaves do tenant DEPOIS deste portão, inclusive o watermark
# que a virada acabou de semear; na virada seguinte o flusher vai lê-lo, não acha, e
# loga. Observado em 2026-08-02 (`pool=smokepeak_pool bucket=…1731`, serviço no ar
# desde 17:19 — nem boot, nem pool nascendo no meio do minuto). Numa 2ª execução dentro
# da janela de 5 min esse rastro faria o portão acusar o CÓDIGO por sujeira do smoke —
# um vermelho que não é vermelho, a mesma família de defeito que este arco combate.
MISSING="$($DC logs --since 5m "$SVC" 2>/dev/null \
            | grep 'watermark AUSENTE' | grep -vc 't_smokepeak_')"
if [ "${MISSING:-0}" = "0" ]; then
  ok "nenhum 'watermark AUSENTE' — o seed da virada cobriu todos os pools"
else
  bad "$MISSING ocorrência(s) de 'watermark AUSENTE' (fora o tenant sintético) em 5 min"
  note "o flusher publicou 0 para pool cujo bucket ninguém semeou: zero com cara de"
  note "medição. Antes de acusar a virada, checar as duas causas legítimas — o bucket"
  note "da mensagem é o do BOOT (serviço sobe no meio do minuto), ou o pool NASCEU no"
  note "meio do minuto (o seed daquela virada passou sem ele). Recorrente, em pool"
  note "estável e minuto qualquer, é o seed da virada não rodando."
  $DC logs --since 5m "$SVC" 2>/dev/null | grep 'watermark AUSENTE' \
    | grep -v 't_smokepeak_' | tail -3 | sed 's/^/      /'
  note "instante do boot p/ comparar: $(docker inspect -f '{{.State.StartedAt}}' \
    "$($DC ps -q "$SVC")" 2>/dev/null)"
fi

echo
if [ "$FAIL" -ne 0 ]; then
  echo "RESULTADO: ❌ o laço do flusher não entrega o que os primitivos garantem."
  exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
  echo "RESULTADO: ⚠️  INCONCLUSIVO — algum portão não chegou a julgar (ver acima)."
  echo "Um run que não mediu não é aprovação; rodar de novo ou corrigir a pré-condição."
  exit 2
else
  echo "RESULTADO: o laço publica o pico gravado nas transições, ponta a ponta."
fi
