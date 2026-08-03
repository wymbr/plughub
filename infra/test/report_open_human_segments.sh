#!/usr/bin/env bash
# report_open_human_segments.sh — segmentos HUMANOS que nunca fecharam, por pool e por dia.
#
# A PERGUNTA QUE ESTE RELATÓRIO RESPONDE não é "quantos órfãos limpar". É:
# **o conserto de 2026-07-30 (H1/H2) segurou?**
#
# Os 87 órfãos foram medidos em 2026-07-29. O produtor de `participant_left` humano na
# família pull entrou em 2026-07-30 (o segmento de wrap-up passou a fechar com
# `close_reason=task_submitted` e duração real). Logo:
#
#   · órfão com `date` ANTERIOR a 2026-07-30  → dívida histórica, candidata a limpeza;
#   · órfão com `date` POSTERIOR              → **o fix não cobriu algum caminho**, e
#     isso vale muito mais que a limpeza. Limpar sem olhar a data apagaria a evidência.
#
# Por isso o corte por dia vem ANTES do total, e o total sozinho não é resposta.
#
# `FINAL` é obrigatório: `segments` é ReplacingMergeTree, e sem ele a versão ABERTA de um
# segmento já fechado ainda aparece — o relatório inventaria órfãos que não existem.
#
# Uso:  bash infra/test/report_open_human_segments.sh [tenant]
# Pré:  clickhouse no ar.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"
CUT="2026-07-30"        # data do fix H1/H2

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

if ! chq 'SELECT 1' >/dev/null 2>&1; then
  echo "⚠️  INCONCLUSIVO: clickhouse não respondeu — isto NÃO é 'zero órfãos'."
  exit 2
fi

_WHERE="tenant_id = '$TENANT' AND agent_type = 'human' AND ended_at IS NULL"

echo "── segmentos humanos ABERTOS — por dia × pool (tenant=$TENANT) ─────────────"
chq "
  SELECT date,
         pool_id,
         count()                                   AS abertos,
         min(started_at)                           AS primeiro,
         max(started_at)                           AS ultimo
    FROM $DB.segments FINAL
   WHERE $_WHERE
   GROUP BY date, pool_id
   ORDER BY date, pool_id
   FORMAT PrettyCompactMonoBlock"

echo
echo "── veredicto do FIX (corte em $CUT) ────────────────────────────────────────"
chq "
  SELECT multiIf(date <  toDate('$CUT'), 'ANTES do fix (dívida histórica)',
                 date >= toDate('$CUT'), '⚠️ DEPOIS do fix — INVESTIGAR',
                 'indeterminado')          AS janela,
         count()                           AS abertos,
         uniq(pool_id)                     AS pools
    FROM $DB.segments FINAL
   WHERE $_WHERE
   GROUP BY janela
   ORDER BY janela
   FORMAT PrettyCompactMonoBlock"

# Pools de SMOKE: existem só para exercitar o claim e, por desenho, abandonam itens
# (o cenário E do `smoke_work_task_pending.sh` apaga a lease sem re-enfileirar, de
# propósito, para produzir o estado `orphaned`). Órfão nesses pools COM sessão ainda
# aberta é a lacuna 2 acontecendo — condição conhecida, com dono, medida em 2026-08-03.
#
# Por que classificar em vez de só contar (2026-08-03): o relatório ficaria vermelho
# para sempre por 9 resíduos explicados, e alarme que nunca fica verde treina a pessoa
# a ignorá-lo — perde-se o vermelho que importa. Por que NÃO virar allowlist cega: o
# critério exige **sessão aberta** junto. Segmento aberto com sessão FECHADA nestes
# mesmos pools continua INEXPLICADO, porque aí é vazamento de teardown, não abandono.
SMOKE_POOLS="'formfill_demo','ramal_test','survey_journey_wf'"

UNEXPLAINED=$(chq "
  SELECT count()
    FROM (SELECT session_id, pool_id FROM $DB.segments FINAL
           WHERE $_WHERE AND date >= toDate('$CUT')) AS g
    LEFT JOIN (SELECT session_id, closed_at FROM $DB.sessions FINAL
                WHERE tenant_id = '$TENANT') AS s USING (session_id)
   WHERE NOT (g.pool_id IN ($SMOKE_POOLS) AND s.closed_at IS NULL);" | tr -d '\r')

EXPLAINED=$(chq "
  SELECT count()
    FROM (SELECT session_id, pool_id FROM $DB.segments FINAL
           WHERE $_WHERE AND date >= toDate('$CUT')) AS g
    LEFT JOIN (SELECT session_id, closed_at FROM $DB.sessions FINAL
                WHERE tenant_id = '$TENANT') AS s USING (session_id)
   WHERE g.pool_id IN ($SMOKE_POOLS) AND s.closed_at IS NULL;" | tr -d '\r')

echo
echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   pós-$CUT explicados (pool de smoke + sessão ABERTA = lacuna 2): ${EXPLAINED:-?}"
if [ "${UNEXPLAINED:-0}" -gt 0 ] 2>/dev/null; then
  echo "   ⚠️  ${UNEXPLAINED} INEXPLICADO(S). NÃO limpar antes de entender:"
  echo "       · sessão FECHADA + segmento aberto → vazamento de teardown (código);"
  echo "       · pool de APROVAÇÃO → a lacuna conhecida, nunca corrigida junto com o"
  echo "         wrap-up (aprovação usa o mesmo delegate+pool)."
  exit 1
fi
echo "   ✅ nenhum INEXPLICADO depois de $CUT — o fix H1/H2 segurou."
echo "   Os anteriores são dívida histórica. Antes de apagar, note que DELETE em"
echo "   ClickHouse é mutação assíncrona (ALTER TABLE ... DELETE) e não é transacional:"
echo "   conferir a contagem DEPOIS, não presumir pelo comando ter retornado."
