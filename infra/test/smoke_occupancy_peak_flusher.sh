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
# CINCO PORTÕES, e o segundo é o contrato:
#   A. o flusher está VIVO           → linha nova chegando em pool_occupancy_peaks
#   B. o CONTRATO ponta a ponta      → um pico que sobe a 2 e volta a 0 DENTRO de um
#                                      minuto aparece na série. Contra a amostragem de
#                                      5 s isso dava 0 (ou linha nenhuma).
#   C. achado 1 (skew de capacidade) → nenhuma linha nova com peak > provisioned
#   D. seed do bucket novo           → nenhum 'watermark AUSENTE' no log
#   E. marcador da F4c               → todo minuto com `__total__` tem `__capacity_*`
#
# POR QUE O PORTÃO E EXISTE. O produtor de `kind_peaks` (`main.py`, laço do flusher que
# lê o rollup `{t}:capacity:snapshot` e amostra `used`/`total_capacity` por tipo) é a
# ÚNICA peça da F4c sem rede: `test_occupancy_series_capacity.py` cobre
# `_flush_occupancy` recebendo `kind_peaks` já pronto — ninguém testa quem o preenche.
# E é exatamente ali que o defeito apareceu na vida real: `__total__ 362` para 356 vagas
# reais, denunciado só pelo dado. Se o rollup parar de ser lido (exceção engolida, chave
# renomeada, `by_kind` vazio), `kind_peaks` fica `{}`, as linhas `__capacity_*` somem e
# o `__total__` volta ao `Σ` inflado por pool — SEM nada ficar vermelho, porque a linha
# continua chegando com um número plausível. O contrato publicado no docstring de
# `_flush_occupancy` é o que este portão cobra:
#
#     minuto sem `__capacity_*` ⇒ `__total__.provisioned_capacity` não é confiável
#
# Contrapositiva: minuto COM `__total__` e SEM `__capacity_*` é a assinatura da falha.
# Única exceção legítima é a janela de arranque (1–2 min pós-restart, rollup ainda não
# publicado) — por isso o portão compara com o instante de boot do container em vez de
# reprovar de cara.
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
# stderr NÃO é engolido (2026-08-02). Um `-q` que erra devolve saída vazia, e vazio é
# lido como "0 linhas" pelos portões — erro virando zero plausível, o defeito que este
# arco persegue. Custou um INCONCLUSIVO falso no smoke irmão da F5 (coluna inexistente
# no `WHERE`), e a mesma armadilha estava latente aqui.
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
  note "medição. **Sobrou UMA causa legítima**: o minuto do BOOT (serviço sobe no meio"
  note "do minuto). Comparar o bucket da mensagem com o instante abaixo antes de tudo."
  note ""
  note "A segunda desculpa — 'o pool nasceu no meio do minuto' — MORREU em 2026-08-02,"
  note "e morreu porque este portão a encontrou: o seed usava a foto da VIRADA enquanto"
  note "\`seen_pools\` crescia o minuto inteiro, então todo login de humano gerava uma"
  note "rajada de AUSENTE (um por pool do agente) e o log tinha álibi permanente — que"
  note "é o mesmo que não ter alarme. O seed passou a rodar na PRIMEIRA VISTA do pool."
  note "Logo: fora do minuto de boot, isto agora é defeito, não ruído."
  $DC logs --since 5m "$SVC" 2>/dev/null | grep 'watermark AUSENTE' \
    | grep -v 't_smokepeak_' | tail -3 | sed 's/^/      /'
  note "instante do boot p/ comparar: $(docker inspect -f '{{.State.StartedAt}}' \
    "$($DC ps -q "$SVC")" 2>/dev/null)"
fi

# ── Portão E · o marcador da F4c (produtor de `kind_peaks`) ──────────────────
echo
echo "── Portão E · linhas __capacity_{kind}__ no minuto ─────────────────────────"
# O tenant SINTÉTICO é excluído de propósito, e não por conveniência: o rollup
# (`compute_tenant_capacity`) parte de `{t}:pools` (o SET de pools do tenant), que este
# smoke nunca escreve — ele só faz SADD em `{t}:pool:{p}:instances`. Logo o rollup
# devolve `{}` para `t_smokepeak_*` e a AUSÊNCIA de `__capacity_*` ali é correta. Cobrar
# dele seria um vermelho garantido que não fala sobre o código.
BOOT_ISO="$(docker inspect -f '{{.State.StartedAt}}' "$($DC ps -q "$SVC")" 2>/dev/null)"
BOOT_EPOCH="$(date -u -d "$BOOT_ISO" +%s 2>/dev/null || echo 0)"

# Denominador primeiro: sem `__total__` no período o portão NÃO JULGOU. Um "0 violações"
# sobre zero linhas é o verde vazio que este arco combate — a mesma família do teste que
# passa por ausência de amostra.
N_TOTAL_ROWS="$(CH "SELECT count() FROM plughub_demo.pool_occupancy_peaks FINAL
                     WHERE ingested_at >= toDateTime('${T0}') AND tenant_id != '${TENANT}'
                       AND pool_id = '__total__'")"
if [ "${N_TOTAL_ROWS:-0}" -eq 0 ]; then
  inc "nenhuma linha \`__total__\` de tenant real no período — nada a julgar"
  note "mesma causa provável do portão A: ambiente sem pool com instância registrada."
else
  # (tenant, minuto) que publicou `__total__` e NÃO publicou nenhuma `__capacity_*`.
  VIOL_E="$(CH "SELECT tenant_id, toString(minute) FROM plughub_demo.pool_occupancy_peaks FINAL
                 WHERE ingested_at >= toDateTime('${T0}') AND tenant_id != '${TENANT}'
                 GROUP BY tenant_id, minute
                 HAVING countIf(pool_id = '__total__') > 0
                    AND countIf(startsWith(pool_id, '__capacity_')) = 0
                 ORDER BY minute FORMAT TSV")"
  E_HARD=0; E_BOOT=0
  while IFS=$'\t' read -r v_tenant v_minute; do
    [ -z "${v_minute:-}" ] && continue
    M_EPOCH="$(date -u -d "${v_minute}Z" +%s 2>/dev/null || echo 0)"
    # Janela de arranque: até 2 min depois do boot o rollup pode não ter sido publicado
    # ainda. Documentado no docstring de `_flush_occupancy` como caso conhecido.
    if [ "$BOOT_EPOCH" -gt 0 ] && [ "$M_EPOCH" -gt 0 ] \
       && [ "$M_EPOCH" -lt $(( BOOT_EPOCH + 120 )) ]; then
      E_BOOT=$(( E_BOOT + 1 ))
      note "janela de arranque tolerada: $v_tenant $v_minute (boot $BOOT_ISO)"
    else
      E_HARD=$(( E_HARD + 1 ))
      note "sem \`__capacity_*\`: $v_tenant $v_minute"
    fi
  done <<< "$VIOL_E"

  if [ "$E_HARD" -eq 0 ]; then
    ok "todo minuto com \`__total__\` trouxe as linhas por tipo ($N_TOTAL_ROWS linha(s) conferida(s))"
    [ "$E_BOOT" -gt 0 ] && note "$E_BOOT minuto(s) na janela de arranque, tolerados por desenho."
  else
    bad "$E_HARD minuto(s) com \`__total__\` e SEM \`__capacity_*\` fora da janela de arranque"
    note "o produtor de \`kind_peaks\` parou: o rollup \`{t}:capacity:snapshot\` não está"
    note "sendo lido (exceção engolida em refresh_tenant_capacity/get_tenant_capacity, ou"
    note "\`by_kind\` vazio). Consequência silenciosa: \`__total__.provisioned_capacity\`"
    note "volta ao Σ por pool, que INFLA capacidade compartilhada (defeito C)."
    note "Diagnóstico: $DC logs --tail=120 $SVC | grep -E 'rollup de capacidade|sem rollup por tipo'"
    note "             $DC exec -T redis redis-cli --scan --pattern '*:capacity:snapshot'"
  fi

  # E2 — `peak > provisioned` nas linhas por TIPO. O portão C exclui todo `__%`, então
  # estas linhas ficam fora dele; e são justamente as que o achado 1 ensinou a checar.
  VIOL_E2="$(CH "SELECT count() FROM plughub_demo.pool_occupancy_peaks FINAL
                  WHERE ingested_at >= toDateTime('${T0}') AND tenant_id != '${TENANT}'
                    AND startsWith(pool_id, '__capacity_')
                    AND peak_concurrency > provisioned_capacity")"
  if [ "${VIOL_E2:-0}" = "0" ]; then
    ok "nenhuma linha por tipo com peak > provisioned"
  else
    bad "$VIOL_E2 linha(s) \`__capacity_*\` com peak > provisioned"
    note "\`used\` e \`total_capacity\` vieram de leituras diferentes do rollup — a"
    note "captura por tipo deixou de ser do mesmo instante (achado 1, um nível acima)."
  fi

  # E3 — Σ das capacidades por tipo == capacidade do `__total__`. É a definição do
  # produtor (`dedup = Σ kind_caps`), então divergir significa que uma das duas linhas
  # foi calculada por outro caminho. Cada instância entra em UM balde de tipo, logo esta
  # soma é legítima — ao contrário de `by_channel`, que é projeção.
  VIOL_E3="$(CH "SELECT tenant_id, toString(minute),
                        anyIf(provisioned_capacity, pool_id = '__total__') AS tot,
                        sumIf(provisioned_capacity, startsWith(pool_id, '__capacity_')) AS soma
                   FROM plughub_demo.pool_occupancy_peaks FINAL
                  WHERE ingested_at >= toDateTime('${T0}') AND tenant_id != '${TENANT}'
                  GROUP BY tenant_id, minute
                 HAVING countIf(pool_id = '__total__') > 0
                    AND countIf(startsWith(pool_id, '__capacity_')) > 0
                    AND tot != soma
                  ORDER BY minute LIMIT 5 FORMAT TSV")"
  if [ -z "$VIOL_E3" ]; then
    ok "\`__total__.provisioned\` == Σ das linhas por tipo"
  else
    bad "\`__total__\` diverge da soma por tipo — a capacidade deduplicada tem dois donos"
    printf '%s\n' "$VIOL_E3" | sed 's/^/      /'
    note "colunas: tenant · minuto · __total__ · Σ por tipo."
  fi
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
