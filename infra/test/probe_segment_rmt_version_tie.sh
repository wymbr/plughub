#!/usr/bin/env bash
# probe_segment_rmt_version_tie.sh — o `left` chegou e PERDEU o desempate?
#
# O QUE JÁ ESTÁ PROVADO (log do bridge, 2026-08-14 20:15:50, sessão e2764d9b):
#   .635  handle_resume abre o segmento (participant_joined)
#   .641  "Native agent executed … outcome=resolved"  ⇒ activate_native_agent RETORNOU
#   .641  logo, main.py:8170 rodou e o participant_left FOI publicado
#   .643  _close_contact_layer seguiu adiante — o processo não morreu
# E mesmo assim `segments FINAL` devolve `ended_at = NULL`. A perda é a JUSANTE do publish.
#
# O SUSPEITO, e por que ele é específico e não genérico:
#
#   segments → ReplacingMergeTree(ingested_at),  ingested_at DateTime DEFAULT now()
#
# `DateTime` tem resolução de SEGUNDO, e o valor é carimbado no INSERT (o row builder
# `_segment_row` não escreve a coluna). Joined .635 e left .641 caem no MESMO segundo ⇒
# MESMA versão ⇒ empate. `ReplacingMergeTree` não define vencedor em empate: o que
# sobrevive ao merge é arbitrário. Quando sobrevive a linha do joined, `ended_at` fica
# NULL para sempre. Isso explica a INTERMITÊNCIA (é sorteio, não erro determinístico):
# nas mesmas sessões há segmentos de 8, 9, 12, 17, 20 e 27 ms que fecharam bem.
#
# É a mesma família do bug que a tabela `sessions` já pagou — o DDL dela (clickhouse.py
# §75-93) documenta que "a última linha inserida vence" é premissa FALSA, e por isso ela
# ganhou `row_version`. `segments` ficou com uma versão de granularidade grossa demais
# para o que versiona; `participation_intervals` está pior: RMT **sem coluna de versão**,
# apoiada num comentário que promete ordenação do Kafka.
#
# TRÊS SAÍDAS, TRÊS CONSERTOS DIFERENTES — o probe existe para separá-las:
#   (a) 2 linhas, uma com `ended_at`, `ingested_at` IDÊNTICO  → EMPATE. Conserto: coluna
#       de versão de verdade (`coalesce(ended_at, started_at)`), espelhando `sessions`.
#   (b) 2 linhas, `ingested_at` diferente e a NULL mais NOVA   → ORDEM de chegada. Mesma
#       classe, gatilho outro (o joined chegou depois do left).
#   (c) 1 linha só                                            → o left não chegou ao
#       ClickHouse apesar de publicado. Alvo: consumer/Kafka, não dedup.
#
# ⚠️ AMBIGUIDADE CONHECIDA: se o merge de fundo já colapsou as versões, (a) e (c) ficam
# indistinguíveis — as duas mostram 1 linha. Por isso `participation_intervals` entra
# JUNTO e não como enfeite: ela é escrita pelo MESMO consumer a partir do MESMO evento.
# `left_at` preenchido lá prova que o evento CHEGOU e foi processado ⇒ a perda em
# `segments` é de dedup, mesmo com uma linha só. `left_at` vazio nos dois ⇒ (c).
#
# Uso:  bash infra/test/probe_segment_rmt_version_tie.sh [tenant]
# Read-only. Não roda OPTIMIZE (forçar merge destruiria a evidência).

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }

# ── preflight: o leitor SEM FINAL enxerga versões? ──────────────────────────────
# TESTEMUNHA obrigatória: se NENHUM segmento do tenant tiver 2 versões, o instrumento
# não distingue nada e todo resultado abaixo é vacuamente "1 linha". Um segmento
# saudável (joined+left) DEVE ter 2 versões enquanto o merge não passou.
echo "── preflight: distribuição de versões por segmento (tenant inteiro) ────────"
chq "
  SELECT versoes, count() AS segmentos
    FROM (SELECT segment_id, count() AS versoes
            FROM $DB.segments
           WHERE tenant_id='$TENANT'
           GROUP BY segment_id)
   GROUP BY versoes
   ORDER BY versoes
   FORMAT PrettyCompactMonoBlock"
echo "   0 segmentos com 2 versões ⇒ merge já colapsou tudo; a seção 1 vira INCONCLUSIVA"
echo "   e o veredicto passa a depender só da seção 2."
echo

# ── 1. as versões CRUAS de cada segmento órfão ─────────────────────────────────
echo "── 1. segments SEM FINAL — uma linha por versão gravada ────────────────────"
chq "
  WITH orfaos AS (
    SELECT g.segment_id AS seg
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
  )
  SELECT r.session_id       AS sessao,
         r.role             AS papel,
         r.participant_id   AS participante,
         r.started_at       AS abriu,
         r.ended_at         AS fechou,
         r.duration_ms      AS dur_ms,
         r.outcome          AS outcome,
         r.ingested_at      AS versao_ingested_at
    FROM $DB.segments AS r
   WHERE r.tenant_id='$TENANT' AND r.segment_id IN (SELECT seg FROM orfaos)
   ORDER BY r.session_id, r.started_at, r.ingested_at
   FORMAT PrettyCompactMonoBlock"
echo

# ── 1b. o veredicto por segmento ────────────────────────────────────────────────
echo "── 1b. veredicto por segmento órfão ────────────────────────────────────────"
chq "
  WITH orfaos AS (
    SELECT g.segment_id AS seg
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
  )
  SELECT r.session_id                                  AS sessao,
         count()                                       AS versoes,
         countIf(r.ended_at IS NOT NULL)               AS com_ended_at,
         uniqExact(r.ingested_at)                      AS ingested_distintos,
         multiIf(count() = 1,                          'UMA linha → ver seção 2',
                 countIf(r.ended_at IS NOT NULL) = 0,  'todas ABERTAS → left não gravou',
                 uniqExact(r.ingested_at) = 1,         '(a) EMPATE de versão',
                 '(b) ORDEM de chegada')               AS veredicto
    FROM $DB.segments AS r
   WHERE r.tenant_id='$TENANT' AND r.segment_id IN (SELECT seg FROM orfaos)
   GROUP BY sessao
   ORDER BY sessao
   FORMAT PrettyCompactMonoBlock"
echo

# ── 2. o mesmo evento na OUTRA tabela — prova de chegada ────────────────────────
echo "── 2. participation_intervals (mesmo consumer, mesmo evento) ───────────────"
echo "   left_at preenchido ⇒ o evento CHEGOU; perda em segments é de dedup."
chq "
  WITH sids AS (
    SELECT DISTINCT g.session_id AS sid, g.participant_id AS pid
      FROM $DB.segments AS g FINAL
     INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                  WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
        ON s.session_id = g.session_id
     WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
  )
  SELECT p.session_id      AS sessao,
         p.participant_id  AS participante,
         p.role            AS papel,
         p.joined_at       AS entrou,
         p.left_at         AS saiu,
         p.duration_ms     AS dur_ms
    FROM $DB.participation_intervals AS p FINAL
   INNER JOIN sids AS k ON k.sid = p.session_id AND k.pid = p.participant_id
   WHERE p.tenant_id='$TENANT'
   ORDER BY p.session_id, p.joined_at
   FORMAT PrettyCompactMonoBlock"
echo

echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   (a) EMPATE            → versão de verdade em segments (coalesce(ended_at,started_at))"
echo "   (b) ORDEM             → mesma correção resolve, e explica também o caso lento"
echo "   (c) left não chegou   → alvo é consumer/Kafka; dedup é inocente"
echo "   Seções 1b e 2 DISCORDANDO na mesma sessão = achado novo, não empate de opinião."
