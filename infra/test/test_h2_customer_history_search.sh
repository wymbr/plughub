#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# H2 — busca no histórico do cliente (Customer History search)
#
# Valida GET /sessions/customer/{id}/search (analytics-api :3500) + o proxy
# /analytics/* do platform-ui (:5174):
#   - substring case-insensitive sobre conteúdo MASKED (positionCaseInsensitiveUTF8)
#   - 1 hit por sessão, score = nº de mensagens que casaram, snippet mascarado
#   - filtros estruturados (channel/outcome) espelham a lista
#   - escopo por customer_id (join sessions) + tenant; só sessões fechadas
#   - LGPD: só content mascarado (analytics.messages não tem original_content)
#
# Seed direto no ClickHouse (8123, db plughub_demo): 2 sessões do mesmo cliente
#   + mensagens (A: 2 com "cobranca"; B: 1 com "cobranca"; 1 sem match). Idempotente.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim
ANALYTICS="${ANALYTICS:-http://localhost:3500}"
UI="${UI:-http://localhost:5174}"
CH="${CH:-http://localhost:8123}"
CH_USER="${CH_USER:-plughub}"
CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
CID="${CID:-cust_h2_demo}"
SA="h2_sess_A"; SB="h2_sess_B"
CURL="curl -s --max-time 15"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ aguardando analytics-api ══"
for i in $(seq 1 30); do $CURL "$ANALYTICS/v1/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ limpeza idempotente ══"
ch "ALTER TABLE $DB.messages DELETE WHERE session_id IN ('$SA','$SB')" >/dev/null 2>&1 || true
ch "ALTER TABLE $DB.sessions DELETE WHERE session_id IN ('$SA','$SB')" >/dev/null 2>&1 || true
sleep 1

echo "══ seed: 2 sessões fechadas do cliente $CID ══"
ch "INSERT INTO $DB.sessions
    (session_id,tenant_id,channel,pool_id,customer_id,opened_at,closed_at,close_reason,outcome,date)
    FORMAT JSONEachRow
{\"session_id\":\"$SA\",\"tenant_id\":\"$TENANT\",\"channel\":\"webchat\",\"pool_id\":\"pool_a\",\"customer_id\":\"$CID\",\"opened_at\":\"2026-06-10 10:00:00.000\",\"closed_at\":\"2026-06-10 10:05:00.000\",\"close_reason\":\"flow_complete\",\"outcome\":\"resolved\",\"date\":\"2026-06-10\"}
{\"session_id\":\"$SB\",\"tenant_id\":\"$TENANT\",\"channel\":\"voice\",\"pool_id\":\"pool_a\",\"customer_id\":\"$CID\",\"opened_at\":\"2026-06-12 14:00:00.000\",\"closed_at\":\"2026-06-12 14:03:00.000\",\"close_reason\":\"customer_hangup\",\"outcome\":\"escalated\",\"date\":\"2026-06-12\"}" >/dev/null

echo "══ seed: mensagens mascaradas (A: 2 match, B: 1 match, +1 sem match) ══"
ch "INSERT INTO $DB.messages
    (message_id,tenant_id,session_id,author_id,author_role,channel,content_type,visibility,content,timestamp,date)
    FORMAT JSONEachRow
{\"message_id\":\"h2a0\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SA\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"tenho uma COBRANCA indevida no cartao\",\"timestamp\":\"2026-06-10 10:00:30.000\",\"date\":\"2026-06-10\"}
{\"message_id\":\"h2a1\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SA\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"vou verificar a cobranca agora\",\"timestamp\":\"2026-06-10 10:01:00.000\",\"date\":\"2026-06-10\"}
{\"message_id\":\"h2a2\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SA\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"obrigado\",\"timestamp\":\"2026-06-10 10:02:00.000\",\"date\":\"2026-06-10\"}
{\"message_id\":\"h2b0\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SB\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"voice\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"de novo a cobranca errada\",\"timestamp\":\"2026-06-12 14:00:30.000\",\"date\":\"2026-06-12\"}" >/dev/null
sleep 1
assert "sessões semeadas" "2" "$(ch "SELECT count() FROM $DB.sessions WHERE session_id IN ('$SA','$SB')")"
assert "mensagens semeadas" "4" "$(ch "SELECT count() FROM $DB.messages WHERE session_id IN ('$SA','$SB')")"

echo "══ busca q=cobranca (direto :3500) ══"
# ordenação = opened_at DESC (mais recente primeiro), consistente com a lista (H1):
# sess_B (2026-06-12) vem antes de sess_A (2026-06-10).
R=$($CURL "$ANALYTICS/sessions/customer/$CID/search?tenant_id=$TENANT&q=cobranca")
echo "    $(echo "$R" | jq -c '[.[]|{session_id,score,ch:.channel,out:.outcome}]')"
assert "n_hits"           "2"   "$(echo "$R" | jq -r 'length')"
assert "1a sessão (mais recente)" "$SB" "$(echo "$R" | jq -r '.[0].session_id')"
assert "score B (1 msg)"  "1"   "$(echo "$R" | jq -r '.[0].score')"
assert "2a sessão"        "$SA" "$(echo "$R" | jq -r '.[1].session_id')"
assert "score A (2 msgs)" "2"   "$(echo "$R" | jq -r '.[1].score')"
# score por sessão casado corretamente com o session_id (independente da ordem)
assert "score de A via lookup" "2" "$(echo "$R" | jq -r --arg s "$SA" '.[]|select(.session_id==$s)|.score')"
assert "score de B via lookup" "1" "$(echo "$R" | jq -r --arg s "$SB" '.[]|select(.session_id==$s)|.score')"
assert "snippet tem termo" "true" "$(echo "$R" | jq -r '.[0].snippet|ascii_downcase|contains("cobranca")')"

echo "══ case-insensitive (q=COBRANCA) ══"
R=$($CURL "$ANALYTICS/sessions/customer/$CID/search?tenant_id=$TENANT&q=COBRANCA")
assert "n_hits (upper)" "2" "$(echo "$R" | jq -r 'length')"

echo "══ filtro channel=voice → só sessão B ══"
R=$($CURL "$ANALYTICS/sessions/customer/$CID/search?tenant_id=$TENANT&q=cobranca&channel=voice")
assert "n_hits (voice)" "1"   "$(echo "$R" | jq -r 'length')"
assert "sessão (voice)" "$SB" "$(echo "$R" | jq -r '.[0].session_id')"

echo "══ filtro outcome=resolved → só sessão A ══"
R=$($CURL "$ANALYTICS/sessions/customer/$CID/search?tenant_id=$TENANT&q=cobranca&outcome=resolved")
assert "n_hits (resolved)" "1"   "$(echo "$R" | jq -r 'length')"
assert "sessão (resolved)" "$SA" "$(echo "$R" | jq -r '.[0].session_id')"

echo "══ termo inexistente → 0 hits ══"
R=$($CURL "$ANALYTICS/sessions/customer/$CID/search?tenant_id=$TENANT&q=xyznaoexiste")
assert "n_hits (none)" "0" "$(echo "$R" | jq -r 'length')"

echo "══ proxy /analytics do platform-ui (:5174) ══"
if $CURL "$UI/" >/dev/null 2>&1; then
  RP=$($CURL "$UI/analytics/sessions/customer/$CID/search?tenant_id=$TENANT&q=cobranca")
  assert "proxy n_hits" "2" "$(echo "$RP" | jq -r 'length' 2>/dev/null)"
else
  echo "  ⚠ platform-ui :5174 indisponível — pule (rebuild platform-ui p/ testar o proxy)"
fi

echo
[ "$FAIL" = 0 ] && echo "✅ H2 OK — busca por termo MASKED, score por sessão, filtros, escopo por cliente, proxy" \
                || { echo "❌ H2 com falhas"; exit 1; }
