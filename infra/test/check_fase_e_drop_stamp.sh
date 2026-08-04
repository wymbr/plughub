#!/usr/bin/env bash
# check_fase_e_drop_stamp.sh — SONDA (lê, não cria) da Fase E (D8) do ADR
# `adr-work-item-requeue-and-agent-affinity.md`.
#
# Não é smoke: a condição só nasce de uma queda de transporte REAL. O script
# afirma sobre a janela recente e se declara INCONCLUSIVO quando não há amostra —
# nunca passa por ausência de dado.
#
# COMO CRIAR A CONDIÇÃO
#   bash infra/test/smoke_formfill_renderer.sh   # gera o item
#   → reivindicar no Console → **F5** → esperar ~5 s → rodar esta sonda
#   (o F5 fecha o WS ~2 s, e é isso que o bridge vê como `agent_disconnect`)
#
# O QUE É AFIRMADO, e o que faria cada afirmação reprovar:
#
#   A) o drop publica `agent_released`, nunca `agent_done`
#      reprova se o `if reason == "agent_disconnect"` do bridge for perdido num
#      merge — sintoma zero, o routing continua liberando a vaga igual;
#   B) o segmento fechado pela queda carrega `close_reason='agent_disconnect'`
#      reprova se `_segment_close_reason_from_transport` não estiver no call site
#      do lado do agente. É a lacuna 6, e o modo de falha é AUSÊNCIA — que num
#      relatório se parece com "nenhum problema";
#   C) o `agent_hangup` não vazou para a queda
#      reprova se alguém "consertar" a lacuna acrescentando `agent_disconnect` ao
#      mapa de CONTATO: aí o valor aparece, o teste B fica verde, e o segmento
#      passa a afirmar que o atendente encerrou o atendimento.
#
# Códigos: 0 tudo verde · 1 reprovou · 2 INCONCLUSIVO · 3 instrumento quebrado
#
# Uso (raiz do repo):
#   bash infra/test/check_fase_e_drop_stamp.sh
#   SINCE_MIN=120 bash infra/test/check_fase_e_drop_stamp.sh
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
SINCE_MIN="${SINCE_MIN:-30}"

CH() { $COMPOSE exec -T clickhouse clickhouse-client -q "$1" < /dev/null 2>/dev/null | tr -d '\r'; }

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

echo "══ Fase E — carimbo da queda · janela ${SINCE_MIN} min ══"

# ── PREFLIGHT — provar que o leitor lê, contra valor conhecido ───────────────
# Sem isto, uma query quebrada devolve vazio e o veredicto lê o vazio como
# "sem amostra" — INCONCLUSIVO plausível em cima de instrumento morto.
[ "$(CH "SELECT 41 + 1")" = "42" ] || {
  echo "   ⛔ INSTRUMENTO QUEBRADO — clickhouse-client não respondeu 42 a 41+1"; exit 3; }
case "$(CH "SELECT count() FROM ${CH_DB}.segments")" in
  ''|*[!0-9]*) echo "   ⛔ INSTRUMENTO QUEBRADO — count() em segments não é inteiro"; exit 3 ;;
esac
_FAKE="agent_released published to lifecycle (human agent): session=sess_X instance=human-a pool=p tenant=t keep_slot_for_wrapup=False"
[ "$(echo "$_FAKE" | grep -oE 'session=[A-Za-z0-9_.:-]+' | sed 's/session=//')" = "sess_X" ] || {
  echo "   ⛔ INSTRUMENTO QUEBRADO — parser não extrai session da linha sintética"; exit 3; }
echo "   preflight ok (leitor CH + parser de log)"

LOGS=$($COMPOSE logs --since "${SINCE_MIN}m" --no-color orchestrator-bridge 2>/dev/null)

# ── A) o drop publica agent_released ─────────────────────────────────────────
REL_SESSIONS=$(printf '%s\n' "$LOGS" \
  | grep -F "agent_released published to lifecycle" \
  | grep -oE "session=[A-Za-z0-9_.:-]+" | sed 's/session=//' | sort -u)

if [ -z "$REL_SESSIONS" ]; then
  echo
  echo "   ⚠️  INCONCLUSIVO — nenhuma queda na janela de ${SINCE_MIN} min."
  echo "      Não é 'a Fase E funciona': é ausência de amostra. Provoque um F5"
  echo "      no Console sobre um item reivindicado e rode de novo."
  exit 2
fi
NREL=$(printf '%s\n' "$REL_SESSIONS" | wc -l | tr -d ' ')
ok "$NREL queda(s) publicaram agent_released"

# O nome antigo não pode aparecer PARA AS MESMAS sessões. Comparar só o total
# global daria falso vermelho num encerramento normal legítimo na mesma janela.
LEAK=0
while IFS= read -r SID; do
  [ -n "$SID" ] || continue
  if printf '%s\n' "$LOGS" | grep -F "agent_done published to lifecycle (human agent)" \
     | grep -qF "session=$SID"; then
    bad "session=$SID publicou agent_done TAMBÉM — a queda ainda se declara conclusão"
    LEAK=$((LEAK+1))
  fi
done <<< "$REL_SESSIONS"
[ "$LEAK" -eq 0 ] && ok "nenhuma dessas sessões publicou agent_done"

# ── B/C) o carimbo no segmento ───────────────────────────────────────────────
echo
echo "══ segmentos dessas sessões (ClickHouse) ══"
while IFS= read -r SID; do
  [ -n "$SID" ] || continue
  ROW=$(CH "SELECT ifNull(nullIf(close_reason,''),'AUSENTE')
            FROM ${CH_DB}.segments FINAL
            WHERE tenant_id='$TENANT' AND session_id='$SID' AND agent_type='human'
            ORDER BY started_at DESC LIMIT 1")
  # Veredicto RAMIFICA sobre o valor medido. Três ramos, e o terceiro
  # (segmento ausente) é INCONCLUSIVO — nunca verde, nunca vermelho.
  case "$ROW" in
    agent_disconnect)
      ok "session=${SID:0:12} close_reason=agent_disconnect (domínio de SEGMENTO)" ;;
    agent_hangup)
      bad "session=${SID:0:12} close_reason=agent_hangup — vocabulário de CONTATO numa queda. \
Alguém estendeu _TRANSPORT_TO_CLOSE_REASON: o segmento agora afirma que o atendente encerrou" ;;
    AUSENTE)
      bad "session=${SID:0:12} SEM close_reason — lacuna 6 aberta neste caminho" ;;
    '')
      echo "   ⚠️  session=${SID:0:12} sem segmento humano no ClickHouse — INCONCLUSIVO"
      echo "      (ingestão atrasada, ou o segmento não foi fechado. Reexecute em ~30 s"
      echo "       antes de concluir qualquer coisa.)" ;;
    *)
      bad "session=${SID:0:12} close_reason=$ROW — valor inesperado, conferir o mapa" ;;
  esac
done <<< "$REL_SESSIONS"

echo
echo "══════════════════════════════════════"
echo "  passou: $pass    falhou: $fail"
[ "$fail" -eq 0 ] || exit 1
echo "  ✅ Fase E: a queda carimba o segmento e não se declara conclusão"
