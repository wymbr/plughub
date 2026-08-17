#!/usr/bin/env bash
# probe_orphan_concurrency_rate.sh — órfão correlaciona com a MESMA instância em
# duas sessões ao mesmo tempo?
#
# POR QUE ESTE TESTE, e não mais uma reprodução. O smoke serial de 2026-08-17 rodou o
# ciclo completo em `limite_processo` (o pool de um dos órfãos) e fechou os 3 segmentos
# novos: 9 abertos antes, 9 depois, zero WARNING no publish. O caminho funciona sozinho.
# O que a população vinha apontando desde o início é OUTRA coisa:
#   · `formfill_demo_ia-001` — duas sessões com 435 ms de diferença, uma órfã em cada;
#   · `limite_retorno-003`   — órfão em 8a5d3ce3 e fe7c611d, FECHADO em fb5dcfea;
#   · `limite_ia-003`        — em e2764d9b, dois eventos na mesma janela de 6 ms; o do
#                              specialist chegou, o do primary não.
# Isto é um teste DIFERENCIAL sobre dado que já existe, com 750 testemunhas que fecharam.
#
# ⚠️ POR QUE A MEDIDA É "início a ±W", e não sobreposição de intervalo. Órfão NÃO TEM
# `ended_at` — é a definição dele. Qualquer medida que dependa do fim é enviesada por
# construção CONTRA o grupo em teste (o órfão teria intervalo indefinido ou infinito, e
# o resultado seria consequência do defeito, não evidência sobre ele). `started_at` está
# presente nos dois grupos e é medido do mesmo jeito.
#
# ⚠️ CONFUNDIMENTO QUE A §1 SOZINHA NÃO CONTROLA: instância movimentada tem vizinho por
# banalidade. Se `limite_retorno-003` tem 50 segmentos e 2 são órfãos, comparar contra o
# tenant inteiro compara "instância ocupada" com "instância ociosa", não órfão com
# fechado. Por isso a §2 refaz a conta **restrita às instâncias que têm pelo menos um
# órfão** — mesma instância, mesmos hábitos, só muda o desfecho. É a §2 que decide.
#
# Uso:  bash infra/test/probe_orphan_concurrency_rate.sh [tenant] [janela_s]
# Read-only.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
W="${2:-10}"
DB="plughub_demo"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }

NSEG=$(chq "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT'" | tr -d '\r')
NORF=$(chq "SELECT count() FROM $DB.segments FINAL
             WHERE tenant_id='$TENANT' AND ended_at IS NULL" | tr -d '\r')
echo "── preflight ───────────────────────────────────────────────────────────────"
echo "   segmentos=$NSEG  abertos=$NORF  janela=±${W}s"
if [ "${NORF:-0}" -eq 0 ] 2>/dev/null; then
  echo "⚠️  INCONCLUSIVO: zero abertos — nada a comparar."; exit 2
fi
echo

_VIZ="
  WITH segs AS (
    SELECT segment_id, session_id, participant_id, started_at, ended_at, role, pool_id
      FROM $DB.segments FINAL
     WHERE tenant_id='$TENANT'
  ),
  viz AS (
    SELECT a.segment_id                                       AS seg,
           any(a.session_id)                                  AS sess_ref,
           any(a.participant_id)                              AS part_ref,
           any(a.role)                                        AS role_ref,
           any(a.pool_id)                                     AS pool_ref,
           any(a.started_at)                                  AS ini_ref,
           max(a.ended_at IS NULL)                            AS aberto,
           countIf(b.session_id != a.session_id
                   AND abs(dateDiff('second', a.started_at, b.started_at)) <= $W)  AS nW,
           countIf(b.session_id != a.session_id
                   AND abs(dateDiff('second', a.started_at, b.started_at)) <= 2)   AS n2
      FROM segs AS a
     INNER JOIN segs AS b ON b.participant_id = a.participant_id
     GROUP BY a.segment_id
  )"

# ── 1. taxa global — sujeita ao confundimento, serve de contexto ────────────────
echo "── 1. taxa de vizinhança, tenant inteiro (CONTEXTO, não veredicto) ─────────"
chq "$_VIZ
  SELECT if(v.aberto, 'ORFAO', 'fechado')                    AS grupo,
         count()                                             AS segmentos,
         countIf(v.nW > 0)                                   AS com_vizinho_W,
         round(100 * countIf(v.nW > 0) / count(), 1)         AS pct_W,
         countIf(v.n2 > 0)                                   AS com_vizinho_2s,
         round(100 * countIf(v.n2 > 0) / count(), 1)         AS pct_2s
    FROM viz AS v
   GROUP BY grupo
   ORDER BY grupo
   FORMAT PrettyCompactMonoBlock"
echo

# ── 2. o teste que decide — mesma instância, só muda o desfecho ────────────────
echo "── 2. RESTRITO às instâncias que têm ≥1 órfão (controla o confundimento) ───"
chq "$_VIZ,
  culpados AS (SELECT DISTINCT part_ref AS p FROM viz WHERE aberto)
  SELECT if(v.aberto, 'ORFAO', 'fechado')                    AS grupo,
         count()                                             AS segmentos,
         countIf(v.nW > 0)                                   AS com_vizinho_W,
         round(100 * countIf(v.nW > 0) / count(), 1)         AS pct_W,
         countIf(v.n2 > 0)                                   AS com_vizinho_2s,
         round(100 * countIf(v.n2 > 0) / count(), 1)         AS pct_2s
    FROM viz AS v
   INNER JOIN culpados AS c ON c.p = v.part_ref
   GROUP BY grupo
   ORDER BY grupo
   FORMAT PrettyCompactMonoBlock"
echo
echo "   ⚠️  Se 'fechado' aqui tiver POUCOS segmentos, a comparação é fraca por"
echo "       tamanho de amostra — dizer isso é parte do resultado, não rodapé."
echo

# ── 3. por instância culpada: quantos abriram, quantos ficaram órfãos ──────────
echo "── 3. por instância com órfão: desfecho × vizinhança ───────────────────────"
chq "$_VIZ,
  culpados AS (SELECT DISTINCT part_ref AS p FROM viz WHERE aberto)
  SELECT v.part_ref                                          AS instancia,
         count()                                             AS segmentos,
         countIf(v.aberto)                                   AS orfaos,
         round(avgIf(v.nW, v.aberto), 2)                     AS viz_medio_orfao,
         round(avgIf(v.nW, NOT v.aberto), 2)                 AS viz_medio_fechado
    FROM viz AS v
   INNER JOIN culpados AS c ON c.p = v.part_ref
   GROUP BY instancia
   ORDER BY orfaos DESC, segmentos DESC
   FORMAT PrettyCompactMonoBlock"
echo

# ── 4. cada órfão e os vizinhos que ele teve ───────────────────────────────────
echo "── 4. cada órfão, com a contagem de vizinhos ───────────────────────────────"
chq "$_VIZ
  SELECT v.sess_ref   AS sessao,
         v.part_ref   AS instancia,
         v.role_ref   AS papel,
         v.pool_ref   AS pool,
         v.ini_ref    AS abriu,
         v.nW         AS vizinhos_W,
         v.n2         AS vizinhos_2s
    FROM viz AS v
   WHERE v.aberto
   ORDER BY v.ini_ref
   FORMAT PrettyCompactMonoBlock"
echo

echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   §2 com pct_W do ORFAO bem acima do fechado ⇒ concorrência da MESMA instância"
echo "      em sessões diferentes; alvo = o que colide entre duas ativações."
echo "   §2 com as duas taxas parecidas ⇒ concorrência CAI junto com as outras. Aí não"
echo "      há mais hipótese barata: resta esperar o próximo caso com o WARNING de"
echo "      publish já instalado, que agora nomeia a falha em vez de engoli-la."
