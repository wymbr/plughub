#!/usr/bin/env bash
# Testemunha da COLISÃO de id do segmento de espera (opção A, 2026-09-01).
#
# Pergunta: uma sessão que espera em DUAS filas emite dois `participant_left`
# com o MESMO `segment_id` (`uuid5(tenant, session_id)`, sem discriminador de
# passagem), e o ReplacingMergeTree guarda UMA linha — a última.
#
# O probe compara três coisas para a mesma sessão:
#   (1) quantas SAÍDAS de fila o routing-engine registrou  → log, fato do produtor
#   (2) quantas LINHAS `role='queue'` sobreviveram          → ClickHouse, fato do consumidor
#   (3) o `segment_id` gravado × o `uuid5` previsto         → prova que é a MESMA identidade
#
# (1) é o contador-TESTEMUNHA de presença ao lado de (2), que é o contador de
# ausência: sem ele, "1 linha" não distingue COLISÃO de "só houve uma espera".
#
# Veredicto:
#   saídas 2 · linhas 1 · id casa  → COLISÃO (vermelho: o conserto é o discriminador)
#   saídas 2 · linhas 2            → sem colisão (o id já discrimina; rever a hipótese)
#   saídas 1                       → não é colisão: cobertura — uma passagem não emitiu
#   saídas 0                       → INCONCLUSIVO: a janela do log não cobre a sessão
#
# Uso:
#   infra/test/q_queue_collision_witness.sh <session_id> [tenant]
set -uo pipefail

SID="${1:-}"
TENANT="${2:-tenant_demo}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"

if [ -z "$SID" ]; then
  echo "uso: $0 <session_id> [tenant]" >&2
  exit 2
fi

echo "== sessão $SID  (tenant $TENANT)"

echo "-- (1) saídas de fila registradas pelo routing-engine"
# `|| true`: grep sem match sai 1, e com `set -e` isso mataria o probe ANTES de
# imprimir o veredicto — ausência viraria silêncio, que é o modo de falha que
# este repositório já catalogou (teste que não pode reprovar).
LOG_LINES=$($DC logs routing-engine 2>&1 | grep 'mute queue exit' | grep -c "$SID" || true)
$DC logs routing-engine 2>&1 | grep 'mute queue exit' | grep "$SID" || true
echo "   saídas = $LOG_LINES"

echo "-- (2) linhas de segmento no ClickHouse"
# FINAL: sem ele o RMT devolve as versões não mescladas e a contagem mente para cima.
CH_Q="SELECT role, pool_id, duration_ms, outcome, segment_id FROM segments AS s FINAL WHERE tenant_id='$TENANT' AND session_id='$SID' ORDER BY started_at FORMAT TSV"
$DC exec -T clickhouse clickhouse-client -d plughub_demo -q "$CH_Q" < /dev/null

CH_Q2="SELECT count() FROM segments AS s FINAL WHERE tenant_id='$TENANT' AND session_id='$SID' AND role='queue'"
CH_QUEUE=$($DC exec -T clickhouse clickhouse-client -d plughub_demo -q "$CH_Q2" < /dev/null | tr -d '\r')
echo "   linhas role=queue = ${CH_QUEUE:-INCONCLUSIVO}"

echo "-- (3) identidades previstas pelas DUAS fórmulas"
# A fórmula ANTIGA fica no probe de propósito: ela é a assinatura da
# descontinuidade declarada em 2026-08-24. Linha gravada ANTES da mudança casa
# com `queue-wait (antiga)`; linha gravada DEPOIS não casa com nenhuma das duas
# sem o `first_queued_ms` daquela passagem — que é apagado na saída e por isso
# não é reconstruível aqui. Não bater NÃO é defeito: é o esperado pós-mudança.
python3 - "$TENANT" "$SID" <<'PY'
import sys, uuid
tenant, sid = sys.argv[1], sys.argv[2]
ns = uuid.NAMESPACE_URL
print("   queue-wait (antiga, sem carimbo) =",
      uuid.uuid5(ns, f"plughub:queue-wait:{tenant}:{sid}"))
print("   queue-agent (bridge, sem carimbo) =",
      uuid.uuid5(ns, f"plughub:queue-agent:{tenant}:{sid}"))
print("   queue-wait (atual) = uuid5('plughub:queue-wait:{t}:{sid}:{first_queued_ms}')")
print("      ⇒ não calculável fora do produtor: o carimbo é apagado na saída.")
PY

echo "== veredicto"
if [ "$LOG_LINES" -eq 0 ]; then
  echo "   INCONCLUSIVO — a janela do log não cobre esta sessão"
elif [ "$LOG_LINES" -eq 1 ]; then
  echo "   NÃO é colisão — houve uma única saída de fila registrada"
elif [ "${CH_QUEUE:-0}" -lt "$LOG_LINES" ]; then
  echo "   COLISÃO: $LOG_LINES saídas → ${CH_QUEUE} linha(s). Confira o id em (3)."
else
  echo "   SEM colisão: $LOG_LINES saídas → ${CH_QUEUE} linhas"
fi
