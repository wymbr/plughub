#!/usr/bin/env bash
# probe_participant_event_in_kafka.sh — o `participant_left` chegou ao TÓPICO?
#
# ── A pergunta que restou, e por que ela é binária ──────────────────────────────
# Para a sessão de reprodução, o segmento `queue` ficou ABERTO e o evento de saída não
# está em NENHUMA das duas tabelas (`segments` e `participation_intervals`). Isso põe o
# defeito de um lado ou do outro de uma linha só — o tópico Kafka:
#
#   `left` NO tópico  ⇒ o bridge publicou; quem perdeu foi o CONSUMIDOR
#                       (o parser não filtra: `parse_participant_event` grava as duas
#                        linhas para os dois tipos de evento, sem condição)
#   `left` FORA        ⇒ o bridge NÃO publicou, e sem uma linha de log — nem o ramo do
#                       produtor ausente, nem o do `except`, nem o do roster (que também
#                       é try/except com WARNING). Os três calam juntos, o que só é
#                       possível se o corpo da task de `_publish_participant_event`
#                       nunca executou. Alvo passa a ser as **78 `asyncio.create_task`
#                       do bridge, nenhuma com referência guardada** (`_bg_tasks`,
#                       `add_done_callback`: zero ocorrências no arquivo).
#
# ── PREVISÃO, escrita ANTES de rodar ────────────────────────────────────────────
#   · `participant_joined` para o `queue-{sid}`: **1** (é o que está no ClickHouse — se
#     vier 0, o leitor está errado e o resto do probe é INCONCLUSIVO, não veredicto)
#   · `participant_left`  para o `queue-{sid}`: **0** — previsto ausente
#   · para o `sac_ia-001` da mesma sessão: 1 e 1 (o par que FUNCIONOU, lado a lado,
#     no mesmo tópico e no mesmo segundo — é a testemunha que impede ler "0 e 0"
#     como se fosse ausência de tráfego)
#
# Read-only: consumidor de console em grupo efêmero, `--from-beginning`, não commita
# offset de ninguém.
#
# Uso:  bash infra/test/probe_participant_event_in_kafka.sh <session_id> [timeout_ms]

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SID="${1:-}"
TIMEOUT="${2:-15000}"
TOPIC="conversations.participants"

if [ -z "$SID" ]; then
  echo "uso: bash infra/test/probe_participant_event_in_kafka.sh <session_id> [timeout_ms]"
  exit 2
fi

# O binário muda conforme a imagem, e na `apache/kafka:3.7.0` ele NÃO está no PATH
# (vive em /opt/kafka/bin — o healthcheck do compose usa o caminho absoluto). Por isso
# o teste é de EXISTÊNCIA do arquivo, não `command -v`: o detector anterior deu
# "INCONCLUSIVO: nenhum console-consumer" numa imagem que tem o binário.
BIN=""
for cand in /opt/kafka/bin/kafka-console-consumer.sh \
            /opt/bitnami/kafka/bin/kafka-console-consumer.sh \
            kafka-console-consumer.sh kafka-console-consumer; do
  if $DC exec -T kafka sh -c "[ -x '$cand' ] || command -v '$cand'" < /dev/null > /dev/null 2>&1; then
    BIN="$cand"; break
  fi
done
if [ -z "$BIN" ]; then
  echo "⚠️  INCONCLUSIVO: nenhum console-consumer encontrado na imagem do kafka."; exit 2
fi
echo "── leitor: $BIN · tópico: $TOPIC · timeout: ${TIMEOUT}ms ──"

DUMP=$($DC exec -T kafka "$BIN" \
        --bootstrap-server localhost:29092 \
        --topic "$TOPIC" --from-beginning --timeout-ms "$TIMEOUT" \
        < /dev/null 2>/dev/null)

TOTAL=$(printf '%s\n' "$DUMP" | grep -c .)
echo "   mensagens lidas no tópico: $TOTAL"
if [ "${TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo "⚠️  INCONCLUSIVO: tópico vazio pelo leitor — não confundir com 'evento ausente'."
  exit 2
fi

SESS=$(printf '%s\n' "$DUMP" | grep "$SID")
N_SESS=$(printf '%s\n' "$SESS" | grep -c .)
echo "   mensagens desta sessão: $N_SESS"
echo

echo "── por participante × tipo ─────────────────────────────────────────────────"
printf '%s\n' "$SESS" | python3 -c '
import sys, json
from collections import defaultdict
tally = defaultdict(lambda: defaultdict(int))
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except Exception:
        continue
    tally[ev.get("participant_id","?")][ev.get("type","?")] += 1
for pid in sorted(tally):
    row = tally[pid]
    print("   %-46s joined=%d  left=%d" % (
        pid, row.get("participant_joined",0), row.get("participant_left",0)))
'
echo

echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   um participante com joined=1 left=1  ⇒ o par funciona neste tópico (TESTEMUNHA)"
echo "   o 'queue-…' com joined=1 left=0      ⇒ o bridge NÃO publicou: alvo é o produtor"
echo "   o 'queue-…' com joined=1 left=1      ⇒ publicou: alvo é o consumidor/ClickHouse"
