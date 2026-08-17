#!/usr/bin/env bash
# probe_orphan_segment_exception.sh — o órfão da família B nasceu de uma EXCEÇÃO?
#
# O QUE MUDOU (2026-08-17). A hipótese TIMEOUT morreu na medição: `session_transitions`
# diz que TODAS as 6 lacunas foram retomadas **por resposta** (`resumed_at` a horas ou dias
# do `resume_expires_at`), inclusive as duas de 7 e 10 minutos. Ninguém foi retomado por
# prazo. Logo o órfão não é "o esperado que ninguém avisou".
#
# O QUE A MEDIÇÃO MOSTROU NO LUGAR — três formas, e a posição do órfão é o discriminador:
#
#   B1 (3×)  o órfão é a janela de RESUME: abre 15–19 ms DEPOIS do `resumed_at` e nada
#            mais acontece na sessão. `handle_resume` (main.py:8139 joined → :8170 left)
#            não chegou ao left.
#   B2 (2×)  o órfão é o SPECIALIST que abre 11–18 ms depois do suspend do primary
#            (`limite_retorno-003`, com pai). O primary retoma minutos depois e fecha em
#            8–12 ms; o specialist fica pendurado.
#   B3 (1×)  o órfão é a janela PRÉ-suspend (`seq=0`), e a de resume fecha normalmente —
#            o espelho exato do B1, no mesmo pool e no mesmo segundo (`formfill_demo_ia-001`
#            atendendo duas sessões com 1 s de diferença: 04d68192 e 3c124d3b).
#   B4 (1×)  sem NENHUMA linha em `session_transitions` (7ccbbc6c) — caso à parte.
#
# A HIPÓTESE QUE ISTO SUSTENTA. Exceção dentro do handler DEPOIS do `participant_joined`.
# `_dispatch_once` re-tenta 3× (500 ms → 1000 ms), mas o resume é **terminal-uma-vez** (ADR
# requeue, fase F: o token é consumido, e a 2ª tentativa recebe 409/404 e volta cedo) — por
# isso a re-execução NÃO abre um segmento novo, e o órfão fica sozinho. Isso explica ao
# mesmo tempo por que não há segmento duplicado e por que a sessão não continua.
#
# COMO ISSO SE FALSIFICA. Se a hipótese vale, o log do bridge tem `[retry` ou `[dlq]` no
# instante do órfão, e a exceção está NOMEADA lá. Se o log estiver limpo naquele instante,
# a hipótese cai — e o próximo suspeito passa a ser a morte da task (`create_task` sem
# await) ou perda no caminho de publicação.
#
# ⚠️ LIMITE DO INSTRUMENTO: log de container é rotativo e some no `up -d` (recreate). Um
# grep vazio pode significar "não aconteceu" OU "o log não existe mais". Por isso o probe
# imprime PRIMEIRO o instante mais antigo que o log ainda alcança — sem essa âncora, um
# vazio não é resposta. Órfãos anteriores a esse instante são INCONCLUSIVOS por construção.
#
# Uso:  bash infra/test/probe_orphan_segment_exception.sh [tenant]
# Read-only.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"
SVC="orchestrator-bridge"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }

# ── 1. o ALCANCE do log — a âncora sem a qual um grep vazio não significa nada ──
echo "── 1. até onde o log do bridge ainda alcança ───────────────────────────────"
# `docker compose logs --timestamps` imprime o PREFIXO DO SERVIÇO antes do carimbo
# ("orchestrator-bridge-1  | 2026-…Z msg"), então `cut -d' ' -f1` devolve o nome do
# container e a âncora vira lixo que PARECE data. Casar o padrão ISO é o único jeito
# de ler o campo certo, independente de prefixo e de largura de coluna.
LOGF=$(mktemp)
$DC logs --timestamps --tail 100000 "$SVC" > "$LOGF" 2>/dev/null
TS_RE='[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
PRIMEIRA=$(grep -oE "$TS_RE" "$LOGF" | head -1)
ULTIMA=$(grep -oE "$TS_RE" "$LOGF" | tail -1)
NLINHAS=$(wc -l < "$LOGF" | tr -d ' ')
echo "   linhas retidas:  ${NLINHAS:-0}"
echo "   janela do log:   ${PRIMEIRA:-<nenhuma>}  →  ${ULTIMA:-<nenhuma>}"
if [ -z "${PRIMEIRA:-}" ]; then
  echo "⚠️  INCONCLUSIVO: sem carimbo de tempo no log — nada abaixo pode ser lido"
  echo "    como ausência de erro."
  rm -f "$LOGF"; exit 2
fi
echo "   ⚠️  Todo órfão ANTERIOR a $PRIMEIRA é INCONCLUSIVO por falta de janela."
echo

# ── 2. os órfãos e seus instantes, para cruzar com o log ────────────────────────
echo "── 2. órfãos não-fila, com o instante a procurar no log ────────────────────"
ORFAOS=$(chq "
  SELECT concat(g.session_id, ' ', toString(g.started_at), ' ', g.pool_id, ' ', g.role)
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL AND g.role != 'queue'
   ORDER BY g.started_at
   FORMAT TSV")
echo "$ORFAOS" | sed 's/^/   /'
echo

# ── 3. retry / DLQ no log inteiro — a assinatura da hipótese ───────────────────
# CONTADOR-TESTEMUNHA ao lado: sem saber quantas linhas o log tem no total, "0 retries"
# é indistinguível de "grep errado".
echo "── 3. assinatura de exceção no bridge (log inteiro retido) ─────────────────"
N_RETRY=$(grep -c '\[retry'  "$LOGF" || true)
N_DLQ=$(grep -c '\[dlq\]'    "$LOGF" || true)
N_TRACE=$(grep -c 'Traceback' "$LOGF" || true)
echo "   [retry ...] : ${N_RETRY:-0}"
echo "   [dlq]       : ${N_DLQ:-0}"
echo "   Traceback   : ${N_TRACE:-0}"
echo "   (testemunha: ${NLINHAS} linhas — 0/0/0 com log cheio é resultado;"
echo "    0/0/0 com log curto é só falta de janela)"
echo

# Traceback SEM retry/dlq é achado por si: significa exceção que NÃO passou pelo
# dispatcher — logo não foi re-tentada nem foi para a DLQ, e morreu calada dentro de
# uma task. É exatamente o modo de falha que fecha um segmento sem publicar o left.
if [ "${N_TRACE:-0}" -gt 0 ] || [ "${N_RETRY:-0}" -gt 0 ] || [ "${N_DLQ:-0}" -gt 0 ]; then
  echo "── 3b. as exceções, com carimbo e 6 linhas de contexto ─────────────────────"
  grep -nE '\[retry|\[dlq\]|Traceback' "$LOGF" | tail -12 | cut -d: -f1 | while read -r LN; do
    START=$(( LN > 2 ? LN - 2 : 1 ))
    sed -n "${START},$((LN+6))p" "$LOGF" | sed 's/^/   /'
    echo "   ┄┄┄"
  done
  echo
fi

# ── 4. o log de CADA sessão órfã ────────────────────────────────────────────────
# `echo … | while read` + `done < /dev/null` = o laço lê /dev/null, NÃO o pipe: o
# redirecionamento do `done` vence o pipe e o corpo nunca roda. A regra operacional
# ("todo docker exec leva < /dev/null") vale para o COMANDO de dentro, nunca para o
# `done` de um laço alimentado por pipe. Aqui não há pipe: o laço lê um here-string.
echo "── 4. log por sessão órfã ──────────────────────────────────────────────────"
while read -r SID REST; do
  [ -n "$SID" ] || continue
  HITS=$(grep -c "$SID" "$LOGF" || true)
  if [ "${HITS:-0}" -eq 0 ]; then
    echo "   ── $SID  ($REST)  → 0 linhas (fora da janela do log ⇒ INCONCLUSIVO)"
  else
    echo "   ── $SID  ($REST)  → ${HITS} linha(s)"
    grep "$SID" "$LOGF" \
      | grep -iE 'error|retry|dlq|exception|warn|resume|participant|suspend' \
      | tail -12 | sed 's/^/      /'
  fi
done <<< "$ORFAOS"
echo
rm -f "$LOGF"

echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   Órfão ANTERIOR à linha mais antiga retida (seção 1) = INCONCLUSIVO, não limpo."
echo "   Erro no instante do órfão ⇒ hipótese EXCEÇÃO sustentada, e o texto o nomeia."
echo "   Log cheio cobrindo o instante e SEM erro ⇒ hipótese cai; próximo suspeito é a"
echo "   morte da task de publish (create_task sem await) ou perda no caminho Kafka."
