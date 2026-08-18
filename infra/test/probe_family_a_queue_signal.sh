#!/usr/bin/env bash
# probe_family_a_queue_signal.sh — família A do "segmento que nunca fecha": o agente de
# fila ficou bloqueado para sempre?
#
# ── O QUE ESTE PROBE TESTA ──────────────────────────────────────────────────────
# Família A (TODO.md § "Segmento que nunca fecha"): `sac_ia` escala, o participante
# `queue-{sid}` abre ~53 ms depois, o humano assume 6–13 s depois — e o segmento de fila
# NUNCA fecha. Dois casos: 61dd213c e 05f4bc74.
#
# O candidato (NÃO diagnóstico — é o que este probe existe para julgar): o agente de fila
# roda um `menu` com `timeout_s: 0`, e só sai por um LPUSH em `menu:result:{sid}`. Só DOIS
# lugares publicam `__agent_available__`:
#     routing-engine/kafka_listener.py:710   ("Queue drain: signalled queue agent …")
#     routing-engine/main.py:1415            ("Periodic drain: signalled queue agent …")
# `work_task_claim` NÃO sinaliza. Se o humano entrou pela inbox PULL, ninguém desbloqueou
# o BLPOP: `activate_native_agent` nunca retorna, e o `participant_left` da fila nunca
# chega a ser PRODUZIDO — o defeito seria de CONTROLE, não de transporte. Isso separa a
# família A das B*, e muda o conserto inteiro.
#
# ── POR QUE É DIFERENCIAL, E NÃO "grep e olhar" ─────────────────────────────────
# Ausência de log só vale contra uma TESTEMUNHA de presença. Este probe conta o sinal em
# TRÊS populações, lado a lado:
#     (a) as 2 sessões ÓRFÃS de fila                → previsão: 0 sinais
#     (b) as sessões de fila que FECHARAM (14 no tenant, as que caem na janela do log)
#                                                   → previsão: ≥ 1 sinal
#     (c) o log INTEIRO                             → se 0, o instrumento está morto e
#                                                      (a) é INCONCLUSIVO, não veredicto
#
# ── PREVISÃO, escrita ANTES de rodar (na unidade que o probe imprime) ───────────
#   · linhas de sinal em (c), log inteiro:            ≥ 1   — senão INCONCLUSIVO
#   · sessões de (b) DENTRO da janela do log com sinal: ≥ 1
#   · sessões de (a) com sinal:                        0
#   Veredicto CANDIDATO SUSTENTADO exige os três. (a)=2 e (b)=0 ⇒ o sinal não discrimina
#   nada e o candidato MORRE. (a)>0 ⇒ o sinal chegou e o bloqueio é outro: cai nas B*.
#
# Read-only: não escreve em lugar nenhum, não roda e2e (o e2e apaga `tenant_demo:*`).
#
# Uso:  bash infra/test/probe_family_a_queue_signal.sh [tenant]

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"
ORPHANS="61dd213c 05f4bc74"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }
rlog() { $DC logs --no-log-prefix -t routing-engine 2>/dev/null; }
blog() { $DC logs --no-log-prefix -t orchestrator-bridge 2>/dev/null; }

echo "════ preflight ═════════════════════════════════════════════════════════════"
PING=$(chq 'SELECT 1' | tr -d '\r')
if [ "$PING" != "1" ]; then
  echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2
fi

# Janela dos logs. Um container recriado perde o log inteiro — e "não achei a linha"
# num log que começa DEPOIS do fato não é evidência de nada.
R_FIRST=$(rlog | head -n 1 | cut -c1-19)
R_LAST=$(rlog  | tail -n 1 | cut -c1-19)
B_FIRST=$(blog | head -n 1 | cut -c1-19)
B_LAST=$(blog  | tail -n 1 | cut -c1-19)
echo "   log routing-engine     : ${R_FIRST:-∅}  →  ${R_LAST:-∅}"
echo "   log orchestrator-bridge: ${B_FIRST:-∅}  →  ${B_LAST:-∅}"
if [ -z "${R_FIRST:-}" ]; then
  echo "⚠️  INCONCLUSIVO: log do routing-engine vazio."; exit 2
fi

# (c) TESTEMUNHA GLOBAL: o instrumento está vivo?
SIG_ALL=$(rlog | grep -c 'signalled queue agent')
DRAIN_ALL=$(rlog | grep -c 'Queue drain')
echo "   'signalled queue agent' no log INTEIRO: $SIG_ALL   ('Queue drain' qualquer: $DRAIN_ALL)"
if [ "${SIG_ALL:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⚠️  ZERO no log inteiro — a ausência nas sessões órfãs NÃO julga o candidato."
  echo "      (o log é INFO: routing-engine roda 'python -m', então basicConfig(INFO) vale;"
  echo "       zero aqui significa que nenhum drain ocorreu no período, não que não loga.)"
fi
echo

echo "════ 1. as duas sessões ÓRFÃS — quem são, e como o humano entrou ═══════════"
FULL_ORPHANS=""
for P in $ORPHANS; do
  SID=$(chq "SELECT DISTINCT session_id FROM $DB.sessions FINAL
              WHERE tenant_id='$TENANT' AND session_id LIKE '${P}%' LIMIT 1" | tr -d '\r')
  if [ -z "$SID" ]; then
    echo "   ⚠️  prefixo $P não resolveu para nenhuma sessão — INCONCLUSIVO para este caso."
    continue
  fi
  FULL_ORPHANS="$FULL_ORPHANS $SID"
  echo "── $SID ────────────────────────────────────────────────"
  chq "
    SELECT g.sequence_index AS seq, g.role AS papel, g.pool_id AS pool,
           g.agent_type AS tipo, g.participant_id AS participante,
           g.started_at AS inicio, g.ended_at AS fim, g.outcome AS outcome
      FROM $DB.segments AS g FINAL
     WHERE g.tenant_id='$TENANT' AND g.session_id='$SID'
     ORDER BY g.started_at
     FORMAT PrettyCompactMonoBlock"
done
echo

echo "════ 2. o SINAL nas sessões órfãs (previsão: 0 em cada) ════════════════════"
A_HIT=0
for SID in $FULL_ORPHANS; do
  N=$(rlog | grep "$SID" | grep -c 'signalled queue agent')
  TOT=$(rlog | grep -c "$SID")
  IN_WINDOW=$([ "$TOT" -gt 0 ] && echo sim || echo "NÃO (fora da janela do log?)")
  echo "   $SID → sinais: $N   · linhas com o id no routing-engine: $TOT · na janela: $IN_WINDOW"
  [ "${N:-0}" -gt 0 ] && A_HIT=$((A_HIT+1))
  if [ "${TOT:-0}" -gt 0 ]; then
    echo "     ── linhas (routing-engine) ──"
    rlog | grep "$SID" | tail -n 25 | sed 's/^/       /'
  fi
done
echo

echo "════ 3. TESTEMUNHA — sessões de fila que FECHARAM ══════════════════════════"
echo "   (mesmo papel, mesmo mecanismo; só entram as que caem na janela do log)"
CLOSED_SIDS=$(chq "
  SELECT DISTINCT g.session_id
    FROM $DB.segments AS g FINAL
   WHERE g.tenant_id='$TENANT' AND g.role='queue' AND g.ended_at IS NOT NULL
   ORDER BY g.session_id" | tr -d '\r')
B_TOTAL=0; B_IN=0; B_HIT=0
for SID in $CLOSED_SIDS; do
  B_TOTAL=$((B_TOTAL+1))
  TOT=$(rlog | grep -c "$SID")
  [ "${TOT:-0}" -eq 0 ] && continue
  B_IN=$((B_IN+1))
  N=$(rlog | grep "$SID" | grep -c 'signalled queue agent')
  [ "${N:-0}" -gt 0 ] && B_HIT=$((B_HIT+1))
  echo "   $SID → sinais: $N"
done
echo "   fila FECHADA no tenant: $B_TOTAL · dentro da janela do log: $B_IN · com sinal: $B_HIT"
echo

echo "════ 4. o lado do bridge nas duas órfãs ════════════════════════════════════"
for SID in $FULL_ORPHANS; do
  TOT=$(blog | grep -c "$SID")
  echo "── $SID · linhas no bridge: $TOT"
  if [ "${TOT:-0}" -gt 0 ]; then
    blog | grep "$SID" | grep -Ei 'participant|DESCARTADO|NÃO PUBLICADO|queue|escalat|close' \
      | tail -n 30 | sed 's/^/     /'
  fi
done
echo

echo "════ veredicto ═════════════════════════════════════════════════════════════"
echo "   sinal no log inteiro (c) : $SIG_ALL"
echo "   órfãs com sinal      (a) : $A_HIT  (previsto 0)"
echo "   fechadas com sinal   (b) : $B_HIT de $B_IN na janela  (previsto ≥ 1)"
if [ "${SIG_ALL:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⇒ INCONCLUSIVO: instrumento sem nenhuma ocorrência; nada a comparar."; exit 2
fi
if [ "${B_IN:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⇒ INCONCLUSIVO: nenhuma sessão-testemunha caiu na janela do log."; exit 2
fi
if [ "${A_HIT:-0}" -eq 0 ] && [ "${B_HIT:-0}" -gt 0 ]; then
  echo "   ⇒ CANDIDATO SUSTENTADO: a fila órfã nunca recebeu o sinal; as que fecharam,"
  echo "     sim. Próximo passo é NOMEAR como o humano entrou (push × claim pull) —"
  echo "     a seção 1 mostra o pool e o participante."; exit 0
fi
if [ "${A_HIT:-0}" -gt 0 ]; then
  echo "   ⇒ CANDIDATO MORTO: o sinal CHEGOU e o segmento ficou aberto assim mesmo."
  echo "     O bloqueio é a jusante do BLPOP — reclassificar junto com as famílias B."; exit 1
fi
echo "   ⇒ CANDIDATO NÃO DISCRIMINA: nem órfãs nem fechadas têm sinal. O sinal não é a"
echo "     variável; as que fecham saem por outro caminho (mensagem do cliente, timeout)."
exit 1
