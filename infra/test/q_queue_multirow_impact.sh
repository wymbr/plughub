#!/usr/bin/env bash
# Exposição do `anyIf` do relatório Fila/SLA a MÚLTIPLAS linhas `role='queue'`.
#
# Por que existe: `reports_query.py:5740-5760` colapsa cada sessão numa linha com
# `anyIf(pool_id, role='queue')` e `anyIf(outcome, role='queue')`. Com UMA linha
# de fila por sessão isso é determinístico. Com DUAS, `anyIf` não dobra — SORTEIA,
# e o `q_outcome` sorteado alimenta `abandoned`/`abandon_rate`/`handoff`.
#
# Discriminar o `queue_wait_segment_id` (opção A) cria a segunda linha de
# propósito. Este probe responde se o problema JÁ EXISTE ou se a mudança o
# INTRODUZ — duas conclusões com consertos de tamanho diferente.
#
# (1) distribuição de linhas `role='queue'` por sessão  → estado atual
# (2) das que têm ≥2, quantas DISCORDAM em outcome/pool → exposição real do anyIf
#     (duas linhas que concordam não produzem sorteio observável)
#
# (2) é o discriminador: contar só (1) confundiria "há duas linhas" com "o
# relatório mente". Um teste de igualdade só julga se a população contiver o
# caso em que os valores diferem.
#
# Uso: infra/test/q_queue_multirow_impact.sh [tenant]
set -uo pipefail

TENANT="${1:-tenant_demo}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CH="$DC exec -T clickhouse clickhouse-client -d plughub_demo -q"

echo "== tenant $TENANT"

echo "-- (1) linhas role='queue' por sessão (distribuição)"
$DC exec -T clickhouse clickhouse-client -d plughub_demo -q "
SELECT q AS linhas_queue, count() AS sessoes
FROM (SELECT session_id, countIf(role = 'queue') AS q
      FROM segments AS s FINAL
      WHERE tenant_id = '$TENANT'
      GROUP BY session_id)
WHERE q > 0
GROUP BY q ORDER BY q FORMAT TSV" < /dev/null

echo "-- (2) sessões com >=2 linhas queue: concordam ou discordam?"
$DC exec -T clickhouse clickhouse-client -d plughub_demo -q "
SELECT session_id,
       count()                        AS linhas,
       uniqExact(outcome)             AS outcomes_distintos,
       uniqExact(pool_id)             AS pools_distintos,
       groupArray(outcome)            AS outcomes,
       groupArray(pool_id)            AS pools
FROM segments AS s FINAL
WHERE tenant_id = '$TENANT' AND role = 'queue'
GROUP BY session_id
HAVING linhas >= 2
ORDER BY linhas DESC LIMIT 20 FORMAT TSV" < /dev/null

echo "-- (3) testemunha de presença: total de linhas role='queue' no tenant"
# Sem esta contagem, "0 sessões com >=2" não distingue "não há exposição" de
# "não há segmento de fila nenhum" (janela/tabela vazia = INCONCLUSIVO).
$DC exec -T clickhouse clickhouse-client -d plughub_demo -q "
SELECT count() FROM segments AS s FINAL
WHERE tenant_id = '$TENANT' AND role = 'queue' FORMAT TSV" < /dev/null
