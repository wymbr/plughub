#!/usr/bin/env bash
# probe_entry_pool_base.sh — F1b: a BASE, contada (antes de tocar em código).
#
# Não é gate: é MEDIÇÃO. Imprime números, não veredicto — o veredicto ramifica
# depois, sobre o valor medido. Cada bloco traz o seu contador-TESTEMUNHA, para
# que um `0` não possa ser lido como "não diverge" quando na verdade é "o join
# não casou".
#
# Uso: bash infra/test/probe_entry_pool_base.sh
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
CH() { $DC exec -T clickhouse clickhouse-client -q "$1" </dev/null; }
DB=plughub_demo
T=tenant_demo

echo "=== 0. TESTEMUNHA: população de sessions ==="
echo "(sessoes / sem_pool / com_pool)"
CH "
SELECT count() AS sessoes,
       countIf(pool_id='')  AS sem_pool,
       countIf(pool_id!='') AS com_pool
FROM $DB.sessions FINAL WHERE tenant_id='$T' FORMAT TSV"

echo
echo "=== 0b. TESTEMUNHA: sessões que o JOIN alcança ==="
echo "(sessoes_com_ao_menos_um_segmento_com_pool)"
CH "
SELECT uniqExact(session_id)
FROM $DB.segments FINAL
WHERE tenant_id='$T' AND pool_id!='' FORMAT TSV"

echo
echo "=== 1. DIVERGENTES por par (sessions.pool_id -> pool do 1o segmento) ==="
echo "(sessions_pool / primeiro_segmento / n)"
CH "
WITH primeiro AS (
  SELECT session_id, argMin(pool_id, started_at) AS p
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT s.pool_id AS sessions_pool, primeiro.p AS primeiro_segmento, count() AS n
FROM $DB.sessions AS s FINAL
INNER JOIN primeiro ON primeiro.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.pool_id != primeiro.p
GROUP BY sessions_pool, primeiro_segmento
ORDER BY n DESC FORMAT TSV"

echo
echo "=== 1b. TESTEMUNHA do mesmo join: CONCORDANTES ==="
echo "(n_concordantes)"
CH "
WITH primeiro AS (
  SELECT session_id, argMin(pool_id, started_at) AS p
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT count()
FROM $DB.sessions AS s FINAL
INNER JOIN primeiro ON primeiro.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.pool_id = primeiro.p FORMAT TSV"

echo
echo "=== 2. O caso que NÃO se deriva de segmento: pool sem segmento nenhum ==="
echo "(n_sessoes_com_pool_e_zero_segmentos)"
CH "
SELECT count()
FROM $DB.sessions AS s FINAL
WHERE s.tenant_id='$T' AND s.pool_id != ''
  AND s.session_id NOT IN (
    SELECT session_id FROM $DB.segments FINAL WHERE tenant_id='$T')
FORMAT TSV"

echo
echo "=== 3. RISCO LATENTE: o overload de pool_id no whatsapp ==="
echo "(canal / sessoes / com_pool) — whatsapp publica phone_number_id NO CAMPO pool_id"
CH "
SELECT channel, count() AS sessoes, countIf(pool_id!='') AS com_pool
FROM $DB.sessions FINAL WHERE tenant_id='$T'
GROUP BY channel ORDER BY sessoes DESC FORMAT TSV"

echo
echo "=== 4. Quem lê como FILA: pool da sessão x pool do segmento role='queue' ==="
echo "(sessions_pool / queue_pool / n) — vazio aqui = a inversao do 5439 nao muda numero"
CH "
WITH fila AS (
  SELECT session_id, any(pool_id) AS qp
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND role='queue' AND pool_id!=''
  GROUP BY session_id)
SELECT s.pool_id AS sessions_pool, fila.qp AS queue_pool, count() AS n
FROM $DB.sessions AS s FINAL
INNER JOIN fila ON fila.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.pool_id != fila.qp
GROUP BY sessions_pool, queue_pool ORDER BY n DESC FORMAT TSV"

echo
echo "=== 4b. TESTEMUNHA: sessões COM segmento de fila ==="
echo "(n_sessoes_com_segmento_queue)"
CH "
SELECT uniqExact(session_id) FROM $DB.segments FINAL
WHERE tenant_id='$T' AND role='queue' FORMAT TSV"

echo
echo "=== 5. ABAC: a mudança de significado tem QUEM a sofra? ==="
echo "(usuarios_total / com_accessible_pools_nao_vazio)"
$DC exec -T postgres psql -U plughub -d plughub_demo -tAF$'\t' -c "
SELECT count(*),
       count(*) FILTER (
         WHERE accessible_pools IS NOT NULL
           AND coalesce(array_length(accessible_pools, 1), 0) > 0)
FROM auth.users" </dev/null 2>&1 | head -3

echo
echo "=== 6. Órfãos: session_id em segments SEM linha em sessions ==="
echo "(n_orfaos) — 416 x 407 na rodada de 2026-08-14"
CH "
SELECT count() FROM (
  SELECT DISTINCT session_id FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id!=''
) AS g
WHERE g.session_id NOT IN (
  SELECT session_id FROM $DB.sessions FINAL WHERE tenant_id='$T')
FORMAT TSV"

echo
echo "=== 6b. Os órfãos, NOMEADOS por pool e papel (contar nao e identificar) ==="
echo "(pool / role / agent_type / n)"
CH "
SELECT pool_id, role, agent_type, count() AS n
FROM $DB.segments AS sg FINAL
WHERE sg.tenant_id='$T' AND sg.pool_id!=''
  AND sg.session_id NOT IN (
    SELECT session_id FROM $DB.sessions FINAL WHERE tenant_id='$T')
GROUP BY pool_id, role, agent_type ORDER BY n DESC FORMAT TSV"
