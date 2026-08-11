#!/usr/bin/env bash
# probe_sequence_index.sh — o `sequence_index` sobrevive ao participant_left?
#
# Contexto: até 2026-08-10 o índice era calculado no `participant_joined` e NUNCA
# persistido; todo `participant_left` humano o reconstruía como 0 e, como
# `analytics.segments` é ReplacingMergeTree, a linha do left apagava a do join.
# Ver TODO.md § "`sequence_index` apagado pelo `participant_left`".
#
# Uso:  bash infra/test/probe_sequence_index.sh <session_id> [tenant_id]
#
# DUAS lentes, e a segunda tem PRAZO:
#   A) `FINAL`      — o que os consumidores leem (estado mesclado).
#   B) sem `FINAL`  — as versões cruas do join e do left. O merge do ClickHouse
#      APAGA esta evidência; horas depois as partes já foram mescladas e a lente B
#      sai muda. Rodar logo após o submit.
#
# Veredicto de TRÊS estados — um `skipped`/vazio NUNCA sai verde:
#   0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO (o instrumento não alcançou o dado)
set -uo pipefail

SESSION="${1:-}"
TENANT="${2:-tenant_demo}"
DB="${CH_DB:-plughub_demo}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"

[ -n "$SESSION" ] || { echo "uso: bash $0 <session_id> [tenant_id]"; exit 3; }

# `< /dev/null` é obrigatório: com `exec -T` o clickhouse-client herda o stdin do
# script e o consome — dentro de um laço, o laço roda uma vez só, sem erro.
CH() { $COMPOSE exec -T clickhouse clickhouse-client -u plughub --password plughub \
         -d "$DB" --query "$1" < /dev/null 2>&1 | tr -d '\r'; }

echo "══ sequence_index — session=$SESSION tenant=$TENANT db=$DB ══"

# ── Preflight: o instrumento responde? (duas leituras quebradas são iguais) ──────
PRE="$(CH "SELECT 41 + 1")"
[ "$PRE" = "42" ] || {
  echo "   ⛔ INSTRUMENTO QUEBRADO — clickhouse-client não devolveu 42:"
  echo "      $PRE"; exit 3; }

# ── Lente A — estado mesclado (o que a atribuição e o export leem) ──────────────
echo
echo "── A) segments FINAL, ordem cronológica ───────────────────────────────────"
CH "SELECT sequence_index AS seq, role, agent_type, user_login,
           toString(started_at) AS started_at, duration_ms, outcome
    FROM segments FINAL
    WHERE tenant_id = '$TENANT' AND session_id = '$SESSION'
    ORDER BY started_at ASC
    FORMAT PrettyCompactMonoBlock"

# Conjunto que a atribuição agrega — MESMO filtro de _session_agent_attribution_sql
SEQS="$(CH "SELECT sequence_index FROM segments FINAL
            WHERE tenant_id = '$TENANT' AND session_id = '$SESSION'
              AND role = 'primary' AND agent_type != 'system'
            ORDER BY started_at ASC")"
N="$(printf '%s\n' "$SEQS" | grep -c '[0-9]' || true)"

HUMANS="$(CH "SELECT count() FROM segments FINAL
              WHERE tenant_id = '$TENANT' AND session_id = '$SESSION'
                AND role = 'primary' AND agent_type = 'human'")"

echo
echo "   primários não-sintéticos: N=$N   (destes, humanos: ${HUMANS:-?})"
echo "   sequência observada:      $(printf '%s' "$SEQS" | tr '\n' ' ')"

# Um `0` só é resposta se soubermos sobre quantas amostras foi calculado.
if [ "$N" -eq 0 ]; then
  echo "   ⚠️  INCONCLUSIVO — nenhum segmento primário não-sintético nesta sessão."
  echo "      Ou o session_id está errado, ou o consumer ainda não ingeriu."
  exit 3
fi
if [ "${HUMANS:-0}" -eq 0 ]; then
  echo "   ⚠️  INCONCLUSIVO — nenhum segmento HUMANO primário."
  echo "      Este probe julga o caminho que zerava o índice, e ele é o do humano."
  echo "      Reivindique e submeta o item no Console antes de medir."
  exit 3
fi

# ── Critério: contíguo a partir de 0, sem repetição ────────────────────────────
# `xargs` normaliza os DOIS lados (colapsa espaço, tira borda). Sem isto o
# `seq | tr` deixa um espaço FINAL que `$()` não remove — `$()` come newline, não
# espaço — e a comparação reprova com as duas strings imprimindo idênticas na tela.
# Reprovou assim na primeira execução, 2026-08-10: o portão julgou a própria
# montagem e vestiu de veredicto sobre o alvo.
EXPECTED="$(seq 0 $((N - 1)) | xargs)"
OBSERVED="$(printf '%s\n' "$SEQS" | xargs)"
echo "   esperado (contíguo):      $EXPECTED"

# ── Lente B — versões cruas: join e left concordam? (tem PRAZO) ─────────────────
echo
echo "── B) sem FINAL — versões cruas por segmento (o merge apaga isto) ──────────"
CH "SELECT segment_id, count() AS versoes,
           groupArray(sequence_index) AS indices_crus,
           uniqExact(sequence_index)  AS distintos
    FROM segments
    WHERE tenant_id = '$TENANT' AND session_id = '$SESSION'
      AND role = 'primary' AND agent_type = 'human'
    GROUP BY segment_id
    FORMAT PrettyCompactMonoBlock"

DIVERG="$(CH "SELECT count() FROM (
                SELECT segment_id FROM segments
                WHERE tenant_id = '$TENANT' AND session_id = '$SESSION'
                  AND role = 'primary' AND agent_type = 'human'
                GROUP BY segment_id
                HAVING uniqExact(sequence_index) > 1)")"

MULTI="$(CH "SELECT count() FROM (
               SELECT segment_id FROM segments
               WHERE tenant_id = '$TENANT' AND session_id = '$SESSION'
                 AND role = 'primary' AND agent_type = 'human'
               GROUP BY segment_id
               HAVING count() > 1)")"

echo
echo "   segmentos humanos com >1 versão crua: ${MULTI:-?}"
echo "   destes, com índices DIVERGENTES:      ${DIVERG:-?}"
if [ "${MULTI:-0}" -eq 0 ]; then
  echo "   ⚠️  lente B MUDA — só há uma versão por segmento. Duas leituras possíveis:"
  echo "      (a) o merge já rodou e comeu a evidência (medir mais cedo), ou"
  echo "      (b) o left ainda não foi publicado. NÃO conte isto como aprovação."
fi

# ── Veredicto ──────────────────────────────────────────────────────────────────
echo
RC=0
if [ "$OBSERVED" != "$EXPECTED" ]; then
  echo "❌ REPROVOU (lente A) — a sequência não é contígua a partir de 0."
  echo "   Repetição em 0 é a assinatura exata do left sobrescrevendo o join."
  RC=1
fi
if [ "${DIVERG:-0}" -gt 0 ]; then
  echo "❌ REPROVOU (lente B) — join e left gravaram índices DIFERENTES."
  echo "   O defeito segue vivo: o ReplacingMergeTree vai manter o do left."
  RC=1
fi
[ "$RC" -eq 0 ] && echo "✅ OK — sequência $OBSERVED, e join/left concordam onde houve evidência."
exit "$RC"
