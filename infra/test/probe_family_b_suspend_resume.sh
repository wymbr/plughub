#!/usr/bin/env bash
# probe_family_b_suspend_resume.sh — família B: órfão colado num suspend/resume.
#
# CONTEXTO. Dos 9 segmentos que nunca fecham em sessão fechada, 7 não são de fila. Todos
# nascem colados num `outcome='suspended'`, em pools de workflow (Arc 19). O código explica
# COMO some — `participant_left` só é publicado DEPOIS que `activate_native_agent` retorna,
# tanto em `process_routed` (main.py:4611) quanto em `handle_resume` (:8170) — mas não
# explica POR QUE some às vezes: nas MESMAS sessões há 6 fechamentos com `suspended` de
# 8–27 ms. Alguma coisa separa os que voltam dos que não voltam.
#
# DUAS HIPÓTESES, e elas têm assinaturas TEMPORAIS opostas:
#
#   TIMEOUT  — o esperar foi resolvido POR FORA. O scanner de prazo retoma a workflow pelo
#              caminho on_timeout; o agente que estava sendo esperado (specialist de
#              delegate) continua pendurado e nunca é avisado. É o MESMO formato da família
#              A (a fila que ninguém avisa quando o humano assume). Assinatura: lacuna de
#              MINUTOS entre o órfão e o próximo segmento, e `resumed_at ≈ resume_expires_at`
#              em `session_transitions`.
#
#   RETRY    — exceção dentro do handler DEPOIS do `participant_joined`. `_dispatch_once`
#              re-executa (`_MAX_DISPATCH_ATTEMPTS=3`, backoff 500 ms → 1000 ms), a
#              re-execução abre um segmento NOVO e abandona o anterior. Assinatura: lacuna
#              de ~0,5 s ou ~1,5 s, mesmo `participant_id`, e `sequence_index` consecutivo.
#
# As duas pedem consertos diferentes: TIMEOUT é avisar quem espera; RETRY é tornar o
# `left` independente do retorno da corrotina (ou publicá-lo no `finally`).
#
# ⚠️ A lacuna sozinha NÃO decide: 1,1 s pode ser retry ou um timeout curtíssimo. Por isso o
# probe cruza a lacuna com `session_transitions`, que é a única fonte que nomeia o MOTIVO
# (`suspend_reason`, `resume_origin`, `resume_expires_at`). Sem linha lá, o veredicto para
# aquela sessão é INCONCLUSIVO — não "logo é retry".
#
# Uso:  bash infra/test/probe_family_b_suspend_resume.sh [tenant]
# Read-only.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }

# A tabela do D4 pode não existir em base antiga. Dizer isso é honesto; a ausência dela
# torna o probe INCONCLUSIVO para o motivo, não "prova de retry".
HAS_TR=$(chq "SELECT count() FROM system.tables
               WHERE database='$DB' AND name='session_transitions'" | tr -d '\r')
echo "── preflight ───────────────────────────────────────────────────────────────"
echo "   session_transitions presente: ${HAS_TR:-?}"
NTR=0
if [ "${HAS_TR:-0}" -gt 0 ] 2>/dev/null; then
  NTR=$(chq "SELECT count() FROM $DB.session_transitions FINAL WHERE tenant_id='$TENANT'" | tr -d '\r')
  echo "   linhas de transição no tenant: $NTR   ← TESTEMUNHA: 0 aqui invalida a seção 2"
fi
echo

# ── 1. a LACUNA de cada órfão da família B ──────────────────────────────────────
# `proximo_inicio` = o primeiro segmento que abre DEPOIS do órfão, na mesma sessão.
# É a lacuna que separa TIMEOUT (minutos) de RETRY (~0,5 s / ~1,5 s).
echo "── 1. órfãos não-fila: lacuna até o próximo segmento ───────────────────────"
chq "
  WITH orfaos AS (
    SELECT g.session_id AS sid, g.segment_id AS seg, g.role AS rl, g.pool_id AS pl,
           g.participant_id AS pid, g.sequence_index AS sq,
           g.parent_segment_id AS parent, g.started_at AS st
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL AND g.role != 'queue'
  )
  SELECT o.sid                                        AS sessao,
         o.rl                                         AS papel,
         o.pl                                         AS pool,
         o.pid                                        AS participante,
         o.sq                                         AS seq,
         if(o.parent = '' OR o.parent IS NULL, 'não', 'sim') AS tem_pai,
         o.st                                         AS orfao_abriu,
         x.n_depois                                   AS n_depois,
         if(x.n_depois = 0, toDateTime64(0,3,'UTC'), x.proximo_inicio) AS proximo_inicio,
         x.lacuna_ms                                  AS lacuna_ms,
         -- ⚠️ `minIf` sobre conjunto VAZIO devolve o DEFAULT do tipo (epoch / 0), não
         -- NULL. Ramificar em `IS NULL` fazia 'nada depois' cair no ramo 'lacuna 0 ms'
         -- e sair rotulado RETRY — um valor plausível escondendo uma ausência, que é
         -- exatamente o modo de falha que a § Postura de Engenharia manda caçar.
         -- O discriminador honesto é a CONTAGEM, nunca o valor agregado.
         multiIf(x.n_depois = 0,                  'CAUDA (nada depois)',
                 x.lacuna_ms <  3000,             'curta  → suspeita RETRY',
                 x.lacuna_ms >= 60000,            'longa  → suspeita TIMEOUT',
                 'intermediária → INCONCLUSIVA')  AS suspeita
    FROM orfaos AS o
    LEFT JOIN (
      SELECT o2.seg                                              AS seg,
             countIf(a.started_at > o2.st)                        AS n_depois,
             minIf(a.started_at, a.started_at > o2.st)            AS proximo_inicio,
             minIf(dateDiff('millisecond', o2.st, a.started_at),
                   a.started_at > o2.st)                          AS lacuna_ms
        FROM orfaos AS o2
       INNER JOIN (SELECT * FROM $DB.segments FINAL WHERE tenant_id='$TENANT') AS a
          ON a.session_id = o2.sid
       GROUP BY o2.seg
    ) AS x ON x.seg = o.seg
   ORDER BY o.st
   FORMAT PrettyCompactMonoBlock"
echo

# ── 2. o MOTIVO, que só session_transitions nomeia ──────────────────────────────
if [ "${NTR:-0}" -gt 0 ] 2>/dev/null; then
  echo "── 2. transições suspend→resume das sessões afetadas ───────────────────────"
  echo "   resumed_at ≈ resume_expires_at ⇒ retomada por PRAZO, não por resposta"
  chq "
    WITH sids AS (
      SELECT DISTINCT g.session_id AS sid
        FROM $DB.segments AS g FINAL
       INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                    WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
          ON s.session_id = g.session_id
       WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL AND g.role != 'queue'
    )
    SELECT t.session_id                      AS sessao,
           t.step_id                         AS passo,
           t.suspend_reason                  AS motivo,
           t.suspended_at                    AS suspendeu,
           t.resume_expires_at               AS prazo,
           t.resumed_at                      AS retomou,
           t.resume_origin                   AS origem,
           t.outcome                         AS estado,
           if(t.resumed_at IS NULL OR t.resume_expires_at IS NULL, '?',
              if(abs(dateDiff('second', t.resume_expires_at, t.resumed_at)) <= 90,
                 'POR PRAZO', 'por resposta')) AS retomada_por
      FROM $DB.session_transitions AS t FINAL
     WHERE t.tenant_id='$TENANT' AND t.session_id IN (SELECT sid FROM sids)
     ORDER BY t.session_id, t.suspended_at
     FORMAT PrettyCompactMonoBlock"
else
  echo "── 2. transições ───────────────────────────────────────────────────────────"
  echo "   ⚠️  session_transitions vazia/ausente — INCONCLUSIVO quanto ao MOTIVO."
  echo "      Não concluir 'logo é retry': a ausência do instrumento não é evidência."
fi
echo

# ── 3. controle: as MESMAS sessões fecham segmentos com suspended ───────────────
# Sem esta linha o probe não distingue "o caminho suspend nunca fecha" de "fecha quase
# sempre". A F3 mediu 6 closes com `suspended` nestas sessões — se aqui vier 0, é o
# leitor que está errado, não o mundo.
echo "── 3. TESTEMUNHA: segmentos FECHADOS com outcome='suspended' (mesmas sessões) ──"
chq "
  WITH sids AS (
    SELECT DISTINCT g.session_id AS sid
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL AND g.role != 'queue'
  )
  SELECT t.outcome                AS outcome,
         count()                  AS fechados,
         min(t.duration_ms)       AS dur_min_ms,
         max(t.duration_ms)       AS dur_max_ms
    FROM $DB.segments AS t FINAL
   WHERE t.tenant_id='$TENANT' AND t.ended_at IS NOT NULL
     AND t.session_id IN (SELECT sid FROM sids)
   GROUP BY outcome
   ORDER BY fechados DESC
   FORMAT PrettyCompactMonoBlock"
echo
echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   Cruzar seção 1 × seção 2 POR SESSÃO. 'curta' + sem transição ⇒ RETRY."
echo "   'longa' + 'POR PRAZO' ⇒ TIMEOUT (o mesmo formato da família A). Divergência"
echo "   entre as duas seções na mesma sessão = hipótese nova, não empate."
