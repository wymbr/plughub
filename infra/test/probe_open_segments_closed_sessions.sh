#!/usr/bin/env bash
# probe_open_segments_closed_sessions.sh — segmento que NUNCA fecha, em sessão FECHADA.
#
# Irmão de `report_open_human_segments.sh`, que só olha `agent_type='human'`. O achado da
# F3 (2026-08-14) atravessa os papéis: `primary` 5 · `queue` 2 · `specialist` 2 = 9 em 676.
#
# ESTE PROBE NÃO CONSERTA NADA. Ele SEPARA a população, porque há três mecanismos
# possíveis e eles pedem consertos DIFERENTES. Todos os três produzem o mesmo sintoma
# (`ended_at IS NULL`), e é por isso que contar 9 não é diagnóstico:
#
#   MID  (superado)  — existe, na MESMA sessão, um segmento que FECHOU **depois** de
#                      este abrir. Prova que o bridge estava vivo e publicando
#                      `participant_left` enquanto este ficou aberto ⇒ o caminho de saída
#                      deste segmento específico não publica. É a hipótese "superação".
#   TAIL (cauda)     — nada na sessão começou nem fechou depois dele. Compatível com
#                      morte/restart do processo: `process_queued`/`process_routed` rodam
#                      em `asyncio.create_task` (fire-and-forget) e só publicam o
#                      `participant_left` DEPOIS que `activate_native_agent` retorna —
#                      logo qualquer restart perde o fechamento de TODO segmento em voo,
#                      em qualquer papel. NÃO é conserto de caminho, é durabilidade.
#   IRMAO (retry)    — há outro segmento na mesma sessão com o MESMO papel e MESMO pool.
#                      Fingerprint do retry do dispatcher (`_MAX_DISPATCH_ATTEMPTS`):
#                      exceção depois do `participant_joined` re-executa o handler, que
#                      abre um segmento NOVO e abandona o anterior.
#
# MID e TAIL não são exclusivos entre si por construção da query (um segmento pode ser
# MID e IRMAO); a coluna que decide o conserto é MID×TAIL.
#
# `FINAL` é obrigatório (`segments` é ReplacingMergeTree): sem ele a versão ABERTA de um
# segmento já fechado ainda aparece e o probe inventa órfão que não existe.
#
# TESTEMUNHA: a coluna `total` ao lado de `abertos` em cada papel. Um probe que só conta
# ausência não distingue "nada aberto" de "leitor quebrado" — se `total` vier 0, o
# veredicto é INCONCLUSIVO, não verde.
#
# Uso:  bash infra/test/probe_open_segments_closed_sessions.sh [tenant]
# Pré:  clickhouse no ar. Read-only: não escreve nada, não apaga nada.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

# ── preflight: PROVAR QUE O LEITOR LÊ ───────────────────────────────────────────
PING=$(chq 'SELECT 1' | tr -d '\r')
if [ "$PING" != "1" ]; then
  echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING') — isto NÃO é 'zero abertos'."
  exit 2
fi
NSESS=$(chq "SELECT count() FROM $DB.sessions FINAL WHERE tenant_id='$TENANT'" | tr -d '\r')
NSEG=$(chq  "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT'" | tr -d '\r')
echo "── preflight (tenant=$TENANT) ──────────────────────────────────────────────"
echo "   sessions=$NSESS  segments=$NSEG"
if [ "${NSEG:-0}" -eq 0 ] 2>/dev/null; then
  echo "⚠️  INCONCLUSIVO: nenhum segmento no tenant — prefixo errado ou base vazia."
  exit 2
fi
echo

# ── 1. baseline por papel, COM testemunha (total) ───────────────────────────────
echo "── 1. abertos × total, por papel — só sessões FECHADAS ─────────────────────"
echo "   (base de 2026-08-14: primary 5 · queue 2 · specialist 2 = 9 em 676)"
chq "
  SELECT g.role                        AS papel,
         countIf(g.ended_at IS NULL)   AS abertos,
         count()                       AS total,
         min(g.date)                   AS primeiro_dia,
         max(g.date)                   AS ultimo_dia
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT'
   GROUP BY papel
   ORDER BY papel
   FORMAT PrettyCompactMonoBlock"
echo

# ── 2. os abertos, nomeados + classificados ─────────────────────────────────────
# CONTAR NÃO É IDENTIFICAR: sem esta tabela não dá para dizer QUAL caminho falhou.
echo "── 2. cada aberto, nomeado e classificado (MID / TAIL / IRMAO) ─────────────"
chq "
  WITH abertos AS (
    SELECT g.session_id AS sid, g.segment_id AS seg, g.role AS rl,
           g.pool_id AS pl, g.agent_type AS at, g.flow_id AS fl, g.started_at AS st
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
  )
  SELECT o.sid                                            AS sessao,
         o.rl                                             AS papel,
         o.pl                                             AS pool,
         o.at                                             AS tipo,
         o.st                                             AS abriu,
         x.fechou_depois                                  AS fechou_depois,
         x.comecou_depois                                 AS comecou_depois,
         x.irmaos_mesmo_papel_pool                        AS irmaos,
         multiIf(x.fechou_depois > 0, 'MID  (superado)',
                 x.comecou_depois > 0, 'MID? (só início depois)',
                 'TAIL (cauda)')                          AS classe
    FROM abertos AS o
    LEFT JOIN (
      SELECT o2.seg AS seg,
             countIf(a.ended_at IS NOT NULL AND a.ended_at > o2.st)          AS fechou_depois,
             countIf(a.segment_id != o2.seg AND a.started_at > o2.st)        AS comecou_depois,
             countIf(a.segment_id != o2.seg AND a.role = o2.rl
                     AND a.pool_id = o2.pl)                                  AS irmaos_mesmo_papel_pool
        FROM abertos AS o2
       INNER JOIN (SELECT * FROM $DB.segments FINAL WHERE tenant_id='$TENANT') AS a
          ON a.session_id = o2.sid
       GROUP BY o2.seg
    ) AS x ON x.seg = o.seg
   ORDER BY o.st
   FORMAT PrettyCompactMonoBlock"
echo

# ── 3. o resumo que decide o conserto ───────────────────────────────────────────
echo "── 3. MID × TAIL por papel — QUAL conserto a população pede ────────────────"
chq "
  WITH abertos AS (
    SELECT g.session_id AS sid, g.segment_id AS seg, g.role AS rl,
           g.pool_id AS pl, g.started_at AS st
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
  )
  SELECT o.rl                                   AS papel,
         countIf(x.fechou_depois > 0)           AS mid,
         countIf(x.fechou_depois = 0)           AS tail,
         countIf(x.irmaos > 0)                  AS com_irmao,
         count()                                AS abertos
    FROM abertos AS o
    LEFT JOIN (
      SELECT o2.seg AS seg,
             countIf(a.ended_at IS NOT NULL AND a.ended_at > o2.st)   AS fechou_depois,
             countIf(a.segment_id != o2.seg AND a.role = o2.rl
                     AND a.pool_id = o2.pl)                           AS irmaos
        FROM abertos AS o2
       INNER JOIN (SELECT * FROM $DB.segments FINAL WHERE tenant_id='$TENANT') AS a
          ON a.session_id = o2.sid
       GROUP BY o2.seg
    ) AS x ON x.seg = o.seg
   GROUP BY papel
   ORDER BY papel
   FORMAT PrettyCompactMonoBlock"
echo

# ── 4. a linha do tempo completa de cada sessão afetada ─────────────────────────
# É aqui que se LÊ o mecanismo: quem entrou logo antes, quem assumiu depois, e se o
# segmento aberto foi engolido por uma alocação (superação) ou ficou órfão numa cauda.
echo "── 4. timeline completa das sessões afetadas ───────────────────────────────"
chq "
  WITH sids AS (
    SELECT DISTINCT g.session_id AS sid
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
  )
  SELECT t.session_id                 AS sessao,
         t.started_at                 AS abriu,
         t.ended_at                   AS fechou,
         t.role                       AS papel,
         t.pool_id                    AS pool,
         t.agent_type                 AS tipo,
         t.participant_id             AS participante,
         t.duration_ms                AS dur_ms,
         t.outcome                    AS outcome
    FROM $DB.segments AS t FINAL
   WHERE t.tenant_id='$TENANT' AND t.session_id IN (SELECT sid FROM sids)
   ORDER BY t.session_id, t.started_at
   FORMAT PrettyCompactMonoBlock"
echo

# ── veredicto de 3 ramos — AUSENTE é INCONCLUSIVO, nunca verde ──────────────────
ABERTOS=$(chq "
  SELECT count()
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL" | tr -d '\r')

echo "── veredicto ───────────────────────────────────────────────────────────────"
case "${ABERTOS:-x}" in
  ''|*[!0-9]*) echo "⚠️  INCONCLUSIVO: contagem não numérica ('$ABERTOS')"; exit 2 ;;
  0)  echo "   ✅ 0 abertos em sessão fechada. Se a base de 08-14 tinha 9, ALGUÉM"
      echo "      apagou/limpou — verificar antes de comemorar (0 também é o valor"
      echo "      de uma tabela truncada)."; exit 0 ;;
  *)  echo "   ⚠️  $ABERTOS aberto(s). Ler a coluna 'classe' da seção 2:"
      echo "      predominância MID  → conserto de CAMINHO (quem esvazia a fila /"
      echo "                            supera o participante não publica o left);"
      echo "      predominância TAIL → conserto de DURABILIDADE (o left só existe"
      echo "                            enquanto a task do bridge viver)."
      exit 1 ;;
esac
