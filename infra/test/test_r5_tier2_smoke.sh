#!/usr/bin/env bash
#
# test_r5_tier2_smoke.sh — R5 smoke (serviços de pé).
#
# Verifica a fiação HTTP do tier-2:
#   1. analytics-api GET /v1/audit/mcp-calls aceita o filtro session_id e responde
#      {calls, total} (escopo por sessão — base do tool_trace).
#   2. (informativo) lembra o caminho completo do evaluation_context_get.
#
# NÃO valida o LLM nem a nota — só a disponibilidade/escopo do dado.
#
# Uso:
#   ANALYTICS_API=http://localhost:3500 TENANT=tenant_demo ./infra/test/test_r5_tier2_smoke.sh
#
# Requer: curl, jq.
set -euo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

ANALYTICS_API="${ANALYTICS_API:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"
SID="${SID:-smoke-session-$(date +%s)}"

fail() { echo "✗ $1"; exit 1; }

echo "→ 1. /v1/audit/mcp-calls com session_id (escopo de sessão)"
RESP="$(curl -fsS "${ANALYTICS_API}/v1/audit/mcp-calls?tenant_id=${TENANT}&session_id=${SID}&limit=10")" \
  || fail "endpoint não respondeu (analytics-api de pé? porta 3500?)"

echo "${RESP}" | jq -e 'has("calls") and has("total")' >/dev/null \
  || fail "resposta sem {calls,total}: ${RESP}"
TOTAL="$(echo "${RESP}" | jq -r '.total')"
echo "✓ aceitou session_id — total=${TOTAL} (sessão inexistente → 0 esperado)"

echo "→ 2. /v1/audit/mcp-calls sem session_id (escopo de tenant, compat)"
RESP2="$(curl -fsS "${ANALYTICS_API}/v1/audit/mcp-calls?tenant_id=${TENANT}&limit=5")" \
  || fail "endpoint sem session_id falhou (regressão de compat)"
echo "${RESP2}" | jq -e 'has("calls") and has("total")' >/dev/null \
  || fail "resposta sem {calls,total}: ${RESP2}"
echo "✓ compat mantida (sem session_id ainda funciona)"

echo
echo "ALL PASS (smoke R5)"
echo "Nota: o caminho completo (tool_trace + flow_definition no ReplayContext) é"
echo "exercido por evaluation_context_get — requer sessão MCP + role evaluator;"
echo "cubra-o no e2e do avaliador (test_t7b2_evaluator_e2e.sh) após o deploy."
