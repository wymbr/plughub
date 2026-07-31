#!/usr/bin/env bash
# check_close_reason_persisted.sh — SONDA (lê, não cria) do fix de 2026-07-30:
# o `close_reason` do CONTATO passou a viajar no fechamento canônico, em vez de
# depender do republish disparado pelo SUBMIT do wrap-up.
#
# Não é um smoke: a condição só nasce de um atendimento humano real. O script
# afirma sobre a janela recente e se declara INCONCLUSIVO quando não há amostra —
# nunca passa por ausência de dado.
#
# COMO CRIAR A CONDIÇÃO (o teste que importa são os DOIS casos):
#   1. atenda um contato no Console e ENCERRE, SUBMETENDO o wrap-up;
#   2. atenda outro e ENCERRE SEM submeter (deixe a pendência vencer/ignore).
#   Antes do fix, o 2º gravava close_reason NULL. Depois, ambos gravam.
#
# O discriminador de "houve wrap-up" é o `issue_status`: ele recebe a classificação
# CRUA do formulário e não tem outro produtor (achado da Camada F). O `outcome` NÃO
# serve — vale `resolved` nos dois casos, e é esse valor plausível que escondeu o
# defeito por tanto tempo.
#
# Uso (raiz do repo):
#   bash infra/test/check_close_reason_persisted.sh
#   SINCE_MIN=120 bash infra/test/check_close_reason_persisted.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
CH="$COMPOSE exec -T clickhouse clickhouse-client"
SINCE_MIN="${SINCE_MIN:-60}"

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

# Segmentos HUMANOS de pool de CONTATO (exclui `-int`, que é a fila interna de
# wrap-up: aquele segmento tem domínio próprio de close_reason — task_submitted /
# acw_expired / acw_supervisor_closed — e confundi-los seria comparar coisas
# diferentes).
WHERE="tenant_id='$TENANT' AND agent_type='human' \
       AND NOT endsWith(pool_id,'-int') \
       AND started_at >= now() - INTERVAL $SINCE_MIN MINUTE \
       AND ended_at IS NOT NULL"

echo "══ janela: últimos $SINCE_MIN min ══"
TOTAL=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL WHERE $WHERE" | tr -d '\r')
echo "   segmentos humanos de contato encerrados: $TOTAL"

if [ "${TOTAL:-0}" -eq 0 ]; then
  echo
  echo "   ⚠️  NENHUMA amostra na janela — resultado INCONCLUSIVO."
  echo "      Atenda e encerre um contato no Console (um COM e um SEM wrap-up) e"
  echo "      rode de novo, ou aumente a janela com SINCE_MIN=."
  exit 2
fi

echo "══ detalhe (issue_status vazio = wrap-up NÃO submetido) ══"
$CH -q "SELECT substring(session_id,1,8) AS sess, pool_id, \
        ifNull(close_reason,'∅') AS close_reason, \
        ifNull(outcome,'∅')      AS outcome, \
        if(ifNull(issue_status,'')='','sem wrap-up','com wrap-up') AS wrapup \
        FROM ${CH_DB}.segments FINAL WHERE $WHERE \
        ORDER BY started_at DESC LIMIT 20 FORMAT PrettyCompact"

echo "══ A) todo segmento humano de contato tem close_reason ══"
MISSING=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
          WHERE $WHERE AND (close_reason IS NULL OR close_reason='')" | tr -d '\r')
[ "${MISSING:-1}" -eq 0 ] \
  && ok "0 de $TOTAL sem close_reason" \
  || bad "$MISSING de $TOTAL AINDA sem close_reason — o fechamento canônico não o carimbou"

echo "══ B) o caso SEM wrap-up é o que o fix endereça — precisa existir na amostra ══"
NOWRAP=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
         WHERE $WHERE AND ifNull(issue_status,'')=''" | tr -d '\r')
if [ "${NOWRAP:-0}" -eq 0 ]; then
  echo "   ⚠️  a janela só tem contatos COM wrap-up submetido — o caminho que"
  echo "      falhava NÃO foi exercitado. Encerre um contato sem preencher o"
  echo "      formulário e rode de novo; sem isso, o ✅ acima não prova o fix."
else
  NOWRAP_MISSING=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
                   WHERE $WHERE AND ifNull(issue_status,'')='' \
                   AND (close_reason IS NULL OR close_reason='')" | tr -d '\r')
  [ "${NOWRAP_MISSING:-1}" -eq 0 ] \
    && ok "$NOWRAP contato(s) SEM wrap-up, todos com close_reason — é exatamente o caso que gravava NULL" \
    || bad "$NOWRAP_MISSING de $NOWRAP contatos sem wrap-up seguem com close_reason NULL"
fi

echo "══ C) nenhum transporte não mapeado silenciou o campo ══"
# `_close_reason_from_transport` loga em WARNING quando o transporte é desconhecido —
# a ausência do campo passou a ter rastro em vez de virar `agent_hangup` inventado.
UNMAPPED=$($COMPOSE logs --since "${SINCE_MIN}m" orchestrator-bridge 2>/dev/null \
           | grep -c "transporte .* não mapeado" || true)
[ "${UNMAPPED:-0}" -eq 0 ] \
  && ok "nenhum WARNING de transporte não mapeado" \
  || echo "   ⚠️  $UNMAPPED WARNING(s) de transporte não mapeado — ver logs do bridge e completar _TRANSPORT_TO_CLOSE_REASON"

echo
echo "══════════════════════════════════════"
echo "  passou: $pass    falhou: $fail"
[ "$fail" -eq 0 ] || exit 1
echo "  ✅ close_reason do contato persiste independente do wrap-up"
