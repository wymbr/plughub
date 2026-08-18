#!/usr/bin/env bash
# probe_queue_segment_exit_paths.sh — família A: por QUAL porta um segmento de fila fecha?
#
# ── O QUE A RODADA ANTERIOR ENTREGOU ────────────────────────────────────────────
# O log do routing-engine nomeou o mecanismo nas DUAS órfãs, com a mesma linha:
#
#   Queue drain: re-routing session=… to pool=retencao_humano
#                (agent=human-… became ready, NO QUEUE AGENT ACTIVE)
#
# Isto é o ramo ELSE do drain (`kafka_listener.py:707`): o marcador
# `queue:agent_active:{sid}` não existia, então o drain RE-PUBLICOU em vez de dar
# `LPUSH __agent_available__`. Consequência encadeada e completa:
#   marcador ausente → sem LPUSH em `menu:result:{sid}` → o `menu timeout_s:0` do
#   agente de fila nunca destrava → `activate_native_agent` (bridge :5546) NUNCA
#   RETORNA → o `participant_left` da linha :5575 nunca é PRODUZIDO.
# O defeito é de CONTROLE, não de transporte. O contato foi atendido normalmente
# (o humano entrou 26 ms depois) — o que ficou pendurado foi o agente de fila.
#
# ── A PERGUNTA QUE ESTE PROBE FAZ (e que reenquadra o item inteiro) ─────────────
# Se o ramo "human became ready" nunca fecha o segmento de fila, então as 14 filas
# que FECHARAM só podem ter fechado por OUTRA porta. Quais? Há três candidatas, e o
# `outcome` do próprio segmento as separa — é um discriminador que já está gravado:
#
#   outcome='abandoned'        → cliente desconectou; o bridge empurra `session:closed`
#                                para o BLPOP (main.py:6135 conta o agente de fila)
#   outcome='escalated_human'  → o flow completou: recebeu `__agent_available__` OU
#                                uma mensagem do cliente destravou o menu
#   outcome NULL + ended_at    → fechado por outro caminho ainda não nomeado
#
# **Se as 14 forem esmagadoramente `abandoned`, o item muda de natureza**: não é uma
# corrida rara em 2 de 16, é que a porta "humano assumiu" NUNCA fecha a fila — e os 2
# órfãos são apenas os casos em que essa porta foi usada numa sessão que depois fechou.
#
# ── PREVISÃO, escrita ANTES de rodar ────────────────────────────────────────────
#   · segmentos `queue` FECHADOS no tenant: 14 (base remedida em 2026-08-17)
#   · previsto: MAIORIA `abandoned`; ZERO fechados na sequência de um drain
#     "no queue agent active"
#   · se aparecer ≥ 1 fechado logo após um "no queue agent active", a cadeia acima
#     está ERRADA e o marcador não é o discriminador — o item volta à estaca
#
# Read-only.
#
# Uso:  bash infra/test/probe_queue_segment_exit_paths.sh [tenant]

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"

chq()  { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }
rlog() { $DC logs --no-log-prefix -t routing-engine 2>/dev/null; }

echo "════ preflight ═════════════════════════════════════════════════════════════"
PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2; }
N_Q=$(chq "SELECT count() FROM $DB.segments FINAL
            WHERE tenant_id='$TENANT' AND role='queue'" | tr -d '\r')
echo "   segmentos role='queue' no tenant: $N_Q   (esperado 16 = 14 fechados + 2 abertos)"
[ "${N_Q:-0}" -gt 0 ] 2>/dev/null || { echo "⚠️  INCONCLUSIVO: nenhum segmento de fila."; exit 2; }
echo

echo "════ 1. por qual PORTA as filas fecharam — o outcome é o discriminador ════"
chq "
  SELECT ifNull(g.outcome,'∅')                    AS outcome,
         countIf(g.ended_at IS NOT NULL)          AS fechados,
         countIf(g.ended_at IS NULL)              AS abertos,
         min(g.started_at)                        AS primeiro,
         max(g.started_at)                        AS ultimo
    FROM $DB.segments AS g FINAL
   WHERE g.tenant_id='$TENANT' AND g.role='queue'
   GROUP BY outcome ORDER BY fechados DESC
   FORMAT PrettyCompactMonoBlock"
echo

echo "════ 2. as 16, uma a uma (a fila fechou? a sessão fechou? quanto durou?) ══"
chq "
  SELECT substring(g.session_id,1,8)              AS sessao,
         g.pool_id                                AS pool,
         g.started_at                             AS fila_abriu,
         ifNull(toString(g.ended_at),'—')         AS fila_fechou,
         ifNull(toString(g.duration_ms),'—')      AS dur_ms,
         ifNull(g.outcome,'∅')                    AS outcome,
         ifNull(toString(s.closed_at),'ABERTA')   AS sessao_fechou,
         ifNull(s.close_reason,'∅')               AS close_reason
    FROM $DB.segments AS g FINAL
    LEFT JOIN (SELECT session_id, closed_at, close_reason
                 FROM $DB.sessions FINAL WHERE tenant_id='$TENANT') AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.role='queue'
   ORDER BY g.started_at
   FORMAT PrettyCompactMonoBlock"
echo

echo "════ 3. TESTEMUNHA — o ramo do drain em cada fila que caiu na janela do log ═"
echo "   'no queue agent active' = ramo ELSE (re-publica) · 'signalled' = ramo do LPUSH"
SIDS=$(chq "SELECT DISTINCT g.session_id FROM $DB.segments AS g FINAL
             WHERE g.tenant_id='$TENANT' AND g.role='queue'
             ORDER BY g.session_id" | tr -d '\r')
NO_AGENT_CLOSED=0; NO_AGENT_OPEN=0; IN_LOG=0
for SID in $SIDS; do
  TOT=$(rlog | grep -c "$SID")
  [ "${TOT:-0}" -eq 0 ] && continue
  IN_LOG=$((IN_LOG+1))
  NOAG=$(rlog | grep "$SID" | grep -c 'no queue agent active')
  SIG=$(rlog  | grep "$SID" | grep -c 'signalled queue agent')
  ENDED=$(chq "SELECT countIf(ended_at IS NOT NULL) FROM $DB.segments FINAL
                WHERE tenant_id='$TENANT' AND role='queue' AND session_id='$SID'" | tr -d '\r')
  ESTADO=$([ "${ENDED:-0}" -gt 0 ] && echo FECHADA || echo ABERTA)
  echo "   ${SID:0:8} · fila $ESTADO · 'no queue agent active': $NOAG · 'signalled': $SIG"
  if [ "${NOAG:-0}" -gt 0 ]; then
    [ "${ENDED:-0}" -gt 0 ] && NO_AGENT_CLOSED=$((NO_AGENT_CLOSED+1)) || NO_AGENT_OPEN=$((NO_AGENT_OPEN+1))
  fi
done
echo "   filas dentro da janela do log: $IN_LOG"
echo

echo "════ 4. o ramo ELSE é raro ou é a REGRA? (todas as sessões, não só as de fila)"
echo "   ocorrências no log inteiro:"
printf '     no queue agent active : %s\n' "$(rlog | grep -c 'no queue agent active')"
printf '     signalled queue agent : %s\n' "$(rlog | grep -c 'signalled queue agent')"
printf '     Activating queue agent (bridge, só se o log alcançar): %s\n' \
  "$($DC logs --no-log-prefix orchestrator-bridge 2>/dev/null | grep -c 'Activating queue agent')"
echo

echo "════ veredicto ═════════════════════════════════════════════════════════════"
echo "   filas com ramo ELSE e segmento ABERTO  : $NO_AGENT_OPEN"
echo "   filas com ramo ELSE e segmento FECHADO : $NO_AGENT_CLOSED  (previsto 0)"
if [ "${IN_LOG:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⇒ INCONCLUSIVO: nenhuma fila caiu na janela do log."; exit 2
fi
if [ "${NO_AGENT_CLOSED:-0}" -eq 0 ] && [ "${NO_AGENT_OPEN:-0}" -gt 0 ]; then
  echo "   ⇒ CADEIA SUSTENTADA: o ramo 'no queue agent active' e o segmento aberto andam"
  echo "     juntos, e nenhum segmento fechou por esse ramo. O conserto é dar ao caminho"
  echo "     'humano assumiu sem agente de fila' uma forma de ENCERRAR o agente de fila —"
  echo "     não é fechar segmento à força no session_closed."; exit 0
fi
if [ "${NO_AGENT_CLOSED:-0}" -gt 0 ]; then
  echo "   ⇒ CADEIA REFUTADA: há fila FECHADA depois do mesmo ramo ELSE. O marcador não"
  echo "     é o discriminador; olhar a seção 2 para o que difere nesses casos."; exit 1
fi
echo "   ⇒ INCONCLUSIVO: o ramo ELSE não aparece em nenhuma fila da janela."
exit 2
