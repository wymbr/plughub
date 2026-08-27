#!/usr/bin/env bash
# probe_duration_definitions.sh — Fase 3 (D9): duas grandezas, dois nomes.
#
#   elapsed_time_ms  (tempo)          — quanto o caso levou para o cliente
#   agent_time_ms    (agente × tempo) — quanto RECURSO o atendimento consumiu
#
# ⚠️ Elas NÃO são comparáveis, e este probe existe para provar que o código parou de
# tratá-las como a mesma coisa. Segmentos se sobrepõem (@mention é sempre paralelo ao
# primary; especialista nasce dentro da janela do pai; hooks posatt são paralelos),
# então Σ ≥ wall-clock com sobreposição e Σ ≤ com lacunas. O que o probe julga é que
# as duas EXISTEM, são DISTINTAS onde têm de ser, e que os filtros pegaram.
#
# Uso:  bash infra/test/probe_duration_definitions.sh
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

TENANT="${TENANT:-tenant_demo}"
UI="${UI:-http://localhost:5173}"
API="${API:-$UI/analytics}"
DB="${CH_DB:-plughub_demo}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"

CH() { $COMPOSE exec -T clickhouse clickhouse-client -u plughub --password plughub \
         -d "$DB" --query "$1" < /dev/null 2>&1 | tr -d '\r'; }

num() {
  case "${1:-}" in
    ''|*[!0-9-]*) echo "   ⛔ INCONCLUSIVO — '$2' não devolveu número: ${1:-<vazio>}" >&2; exit 3 ;;
  esac
  echo "$1"
}

echo "══ D9 — duas grandezas, dois nomes — tenant=$TENANT ══"

[ "$(CH "SELECT 41 + 1")" = "42" ] || {
  echo "   ⛔ INCONCLUSIVO — clickhouse-client não respondeu 42"; exit 3; }

# ── Sessão de teste: uma webhook FECHADA, que é onde as duas mais divergem ────
SESSION="$(CH "SELECT session_id FROM $DB.sessions FINAL
               WHERE tenant_id='$TENANT' AND channel='webhook'
                 AND closed_at IS NOT NULL AND origin='live'
               ORDER BY opened_at DESC LIMIT 1")"
if [ -z "$SESSION" ] || [ "${#SESSION}" -lt 8 ]; then
  echo "   ⛔ INCONCLUSIVO — nenhuma sessão webhook fechada. Rode o gate/smoke antes."
  exit 3
fi
echo "   sessão: $SESSION"

# ── As duas grandezas, calculadas AQUI (independente do endpoint) ─────────────
echo
echo "── as duas grandezas, direto do substrato ────────────────────────────────"
CH "SELECT
      toString(min(g.started_at))  AS primeiro_segmento,
      toString(any(s.closed_at))   AS fechou_em,
      toInt64(dateDiff('millisecond', min(g.started_at), any(s.closed_at))) AS elapsed_ms,
      sumIf(g.duration_ms, g.agent_type != 'system'
            AND g.role IN ('primary','specialist') AND g.duration_ms IS NOT NULL) AS agent_ms,
      sum(g.duration_ms) AS soma_bruta_todos_segmentos,
      sumIf(g.duration_ms, g.role = 'queue') AS espera_ms
    FROM $DB.segments AS g FINAL
    INNER JOIN (SELECT session_id, closed_at FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND session_id='$SESSION') AS s
      ON s.session_id = g.session_id
    WHERE g.tenant_id='$TENANT' AND g.session_id='$SESSION'
    FORMAT Vertical"

ELAPSED="$(num "$(CH "SELECT toInt64(dateDiff('millisecond', min(g.started_at), any(s.closed_at)))
    FROM $DB.segments AS g FINAL
    INNER JOIN (SELECT session_id, closed_at FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND session_id='$SESSION') AS s
      ON s.session_id = g.session_id
    WHERE g.tenant_id='$TENANT' AND g.session_id='$SESSION'")" 'elapsed')"
AGENT="$(num "$(CH "SELECT toInt64(sumIf(duration_ms, agent_type != 'system'
            AND role IN ('primary','specialist') AND duration_ms IS NOT NULL))
    FROM $DB.segments FINAL
    WHERE tenant_id='$TENANT' AND session_id='$SESSION'")" 'agent')"
BRUTA="$(num "$(CH "SELECT toInt64(sum(duration_ms)) FROM $DB.segments FINAL
    WHERE tenant_id='$TENANT' AND session_id='$SESSION'")" 'soma bruta')"

echo
echo "   elapsed_time_ms=$ELAPSED · agent_time_ms=$AGENT · soma bruta (sem filtro)=$BRUTA"

RC=0

# 1. O FILTRO pegou? Se agent == soma bruta, nenhum segmento foi excluído — e numa
#    sessão com fila isso significa que a ESPERA entrou no tempo-agente.
if [ "$AGENT" -eq "$BRUTA" ]; then
  QUEUE_N="$(num "$(CH "SELECT count() FROM $DB.segments FINAL
      WHERE tenant_id='$TENANT' AND session_id='$SESSION'
        AND (role='queue' OR agent_type='system')")" 'segmentos excluíveis')"
  if [ "$QUEUE_N" -gt 0 ]; then
    echo "   ❌ REPROVOU — há $QUEUE_N segmento(s) de fila/sintético e o tempo-agente"
    echo "      é IGUAL à soma bruta: o filtro não foi aplicado. Espera não é trabalho."
    RC=1
  else
    echo "   ⚠️  agent == soma bruta, mas não há segmento excluível nesta sessão —"
    echo "      o filtro não foi EXERCITADO. Verde aqui não cobre o filtro."
    FILTER_UNTESTED=1
  fi
fi

# 2. As duas têm de ser DISTINTAS numa sessão webhook com suspensão. Iguais = alguém
#    voltou a derivar uma da outra.
if [ "$ELAPSED" -eq "$AGENT" ]; then
  echo "   ❌ REPROVOU — elapsed == agent ($ELAPSED ms). São unidades diferentes;"
  echo "      igualdade exata denuncia que uma está sendo derivada da outra."
  RC=1
fi

# 3. O endpoint expõe os DOIS nomes? (o substrato pode estar certo e a API velha)
echo
echo "── o endpoint /reports/sessions expõe os dois nomes? ─────────────────────"
# ⚠️ O STATUS é julgado ANTES do corpo, e um não-200 é INCONCLUSIVO — nunca
# reprovação. A v1 deste bloco chamava o endpoint sem `tenant_id` (obrigatório,
# `reports.py:110`), levava 422, não achava os campos no corpo de erro e declarava
# "campo AUSENTE" — requisição malformada lida como feature faltando, que é o
# portão julgando a própria montagem. Custou uma execução em 2026-08-11.
URL="$API/reports/sessions?tenant_id=$TENANT&page_size=1"
HTTP="$(curl -s -o /tmp/_d9body.$$ -w '%{http_code}' --max-time 20 "$URL" 2>/dev/null)"
BODY="$(cat /tmp/_d9body.$$ 2>/dev/null)"; rm -f "/tmp/_d9body.$$"
if [ "$HTTP" != "200" ]; then
  echo "   ⚠️  INCONCLUSIVO (parcial) — endpoint devolveu HTTP $HTTP em $URL"
  printf '      %s\n' "$(printf '%s' "$BODY" | head -c 300)"
  echo "      O substrato foi julgado acima; a EXPOSIÇÃO não foi medida."
else
  for f in elapsed_time_ms agent_time_ms; do
    if printf '%s' "$BODY" | grep -q "\"$f\""; then
      echo "   ✅ $f presente"
    else
      echo "   ❌ REPROVOU — $f AUSENTE na resposta do endpoint"
      RC=1
    fi
  done
  printf '%s' "$BODY" | grep -q '"handle_time_ms"' \
    && echo "   ℹ️  handle_time_ms ainda presente — alias de compat, esperado nesta fase"
fi

echo
if [ "$RC" -eq 0 ]; then
  # A frase de sucesso NÃO pode afirmar mais do que foi medido. A v1 dizia "e o
  # filtro pegou" logo abaixo do aviso de que o filtro não fora exercitado —
  # contradição impressa na mesma tela, e é assim que um verde vira crença.
  if [ "${FILTER_UNTESTED:-0}" -eq 1 ]; then
    echo "✅ OK PARCIAL — as duas grandezas existem, são distintas e estão expostas."
    echo "   ⚠️  O FILTRO do tempo-agente NÃO foi coberto: esta sessão não tem"
    echo "      segmento de fila nem sintético para excluir. Para cobri-lo, medir uma"
    echo "      sessão que tenha passado por FILA (push, não pull)."
  else
    echo "✅ OK — as duas grandezas existem, são distintas, expostas, e o filtro do"
    echo "   tempo-agente excluiu espera/sintético nesta sessão."
  fi
  echo "   Lembrete: elas NÃO são comparáveis. Σ ≥ wall-clock com sobreposição"
  echo "   (@mention, conferência, posatt) e ≤ com lacunas."
else
  echo "❌ REPROVOU."
fi
exit "$RC"
