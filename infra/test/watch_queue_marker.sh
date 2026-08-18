#!/usr/bin/env bash
# watch_queue_marker.sh — leitor da reprodução do Problema 34 (segmento de fila que não fecha).
#
# Lê, a partir de um INSTANTE ABSOLUTO, as três evidências que decidem o caso:
#   1. o bridge escreveu o marcador? (`marker SET` / `marker NÃO escrito`)
#   2. o flow do agente de fila resolveu? (slot `current` × FALLBACK LEGADO × nenhum)
#   3. o drain sinalizou ou re-roteou — e, se re-roteou, qual era o TTL da chave
#   4. o segmento `queue` que nasceu na janela fechou?
#
# ⚠️ Instante ABSOLUTO, nunca `--since 300s`: janela por duração soma a execução anterior
# e faz duas reproduções virarem uma. Pegue o T0 ANTES de mexer na UI:
#
#     T0=$(date -u +%FT%TZ); echo "$T0"
#     …faça o fluxo…
#     bash infra/test/watch_queue_marker.sh "$T0"
#
# Uso:  bash infra/test/watch_queue_marker.sh <T0-ISO-UTC> [tenant]

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
T0="${1:-}"
TENANT="${2:-tenant_demo}"
DB="plughub_demo"

if [ -z "$T0" ]; then
  echo "uso: bash infra/test/watch_queue_marker.sh <T0-ISO-UTC> [tenant]"
  echo "     T0=\$(date -u +%FT%TZ)  ← pegue ANTES do fluxo"
  exit 2
fi

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

echo "════ janela: desde $T0 (UTC) ═══════════════════════════════════════════════"
echo

echo "── 1+2. bridge: marcador e resolução do flow ───────────────────────────────"
$DC logs --no-log-prefix -t --since "$T0" orchestrator-bridge 2>/dev/null \
  | grep -Ei 'marker SET|marker NÃO escrito|marker DELETE|Activating queue agent|slot .current|FALLBACK LEGADO|NENHUM slot|No executable flow|Queue agent completed' \
  | sed 's/^/   /'
echo

echo "── 3. routing: fila e drain (o TTL só aparece no ramo que re-roteia) ───────"
$DC logs --no-log-prefix -t --since "$T0" routing-engine 2>/dev/null \
  | grep -Ei 'Queued session|Queue drain|signalled queue agent|QUEUE TIMEOUT' \
  | sed 's/^/   /'
echo

echo "── 4. os segmentos 'queue' nascidos na janela ──────────────────────────────"
# `started_at` em UTC; o T0 vem em ISO com 'T' e 'Z' — normaliza para o formato do CH.
T0_CH=$(printf '%s' "$T0" | tr 'T' ' ' | tr -d 'Z')
chq "
  SELECT substring(g.session_id,1,8)         AS sessao,
         g.pool_id                           AS pool,
         g.started_at                        AS abriu,
         ifNull(toString(g.ended_at),'ABERTO') AS fechou,
         ifNull(toString(g.duration_ms),'—')   AS dur_ms,
         ifNull(g.outcome,'∅')               AS outcome
    FROM $DB.segments AS g FINAL
   WHERE g.tenant_id='$TENANT' AND g.role='queue' AND g.started_at >= '$T0_CH'
   ORDER BY g.started_at
   FORMAT PrettyCompactMonoBlock"
echo

echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   marcador escrito + drain com 'signalled'      ⇒ o caminho do sinal VIVE"
echo "   marcador escrito + drain com 'ttl=-2'         ⇒ a chave SUMIU entre os dois"
echo "                                                    (é o fio aberto do Problema 34)"
echo "   marcador escrito + drain com 'ttl>0' e ELSE   ⇒ o drain leu e ignorou: bug de leitura"
echo "   'marker NÃO escrito'                          ⇒ o SET falhava, e era isso o tempo todo"
echo "   nenhuma linha de marcador                     ⇒ o agente de fila nem foi ativado:"
echo "                                                    o contato não chegou a enfileirar"
