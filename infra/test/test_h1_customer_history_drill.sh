#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# H1 — Customer History drill (lista → transcrição) na HistoricoTab (Agent Assist)
#
# Valida as peças alteradas nesta fase:
#   A) Endpoint de transcrição alcançável e MASKED:
#        GET /v1/transcript/sessions/{id}?scope=contact  (analytics-api :3500)
#   B) NOVO proxy /analytics/* do platform-ui (nginx) — o que o drill do browser usa:
#        GET /analytics/v1/transcript/sessions/{id}?scope=contact  (platform-ui :5174)
#        GET /analytics/sessions/customer/{id}                      (platform-ui :5174)
#      (só roda se o platform-ui foi rebuildado; senão avisa e pula.)
#   C) Integração real (quando há dados de demo): descobre um (customer, session)
#        real e confirma que a lista traz a sessão e a transcrição dela carrega.
#
# Seed A/B é auto-contido (só messages, como test_t9c1_transcript_window.sh) — não
# depende do schema de `sessions`. Idempotente.
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
SESS="${SESS:-h1_drill_sess_1}"
CURL="curl -s --max-time 15"
FAIL=0
assert()    { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_ge() { if [ "$2" -ge "$3" ] 2>/dev/null; then echo "  ✓ $1 = $2 (>= $3)"; else echo "  ✗ $1: esperado >= $3, veio [$2]"; FAIL=1; fi; }
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ aguardando analytics-api ══"
for i in $(seq 1 30); do $CURL "$ANALYTICS/v1/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# ── A) seed messages + transcript direto ──────────────────────────────────────
echo "══ A) seed 3 mensagens (mascaradas) p/ $SESS ══"
ch "ALTER TABLE $DB.messages DELETE WHERE session_id='$SESS'" >/dev/null 2>&1 || true
sleep 1
ch "INSERT INTO $DB.messages
    (message_id,tenant_id,session_id,author_id,author_role,channel,content_type,visibility,content,timestamp,date)
    FORMAT JSONEachRow
{\"message_id\":\"h1m0\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"cust\",\"author_role\":\"customer\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"meu cpf e [cpf:tk_ab12:***-00]\",\"timestamp\":\"2026-06-20 09:00:00.000\",\"date\":\"2026-06-20\"}
{\"message_id\":\"h1m1\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"all\",\"content\":\"obrigado, confirmado\",\"timestamp\":\"2026-06-20 09:01:00.000\",\"date\":\"2026-06-20\"}
{\"message_id\":\"h1m2\",\"tenant_id\":\"$TENANT\",\"session_id\":\"$SESS\",\"author_id\":\"ag1\",\"author_role\":\"primary\",\"channel\":\"webchat\",\"content_type\":\"message\",\"visibility\":\"agents_only\",\"content\":\"nota interna: cliente VIP\",\"timestamp\":\"2026-06-20 09:02:00.000\",\"date\":\"2026-06-20\"}" >/dev/null
sleep 1
assert "msgs semeadas" "3" "$(ch "SELECT count() FROM $DB.messages WHERE session_id='$SESS'")"

echo "══ A) GET /v1/transcript (direto :3500, scope=contact) ══"
R=$($CURL "$ANALYTICS/v1/transcript/sessions/$SESS?tenant_id=$TENANT&scope=contact")
assert "masked"            "true"    "$(echo "$R" | jq -r '.masked')"
assert "scope"             "contact" "$(echo "$R" | jq -r '.scope')"
assert "n_messages"        "3"       "$(echo "$R" | jq -r '.messages|length')"
assert "1a msg mascarada"  "meu cpf e [cpf:tk_ab12:***-00]" "$(echo "$R" | jq -r '.messages[0].content')"
assert "visibility interna" "agents_only" "$(echo "$R" | jq -r '.messages[-1].visibility')"

# ── B) NOVO proxy /analytics do platform-ui ───────────────────────────────────
echo "══ B) proxy /analytics/* do platform-ui (:5174) ══"
if $CURL "$UI/" >/dev/null 2>&1; then
  RP=$($CURL "$UI/analytics/v1/transcript/sessions/$SESS?tenant_id=$TENANT&scope=contact")
  # se o proxy não existir, nginx devolve index.html (não-JSON) → jq falha → "null"
  assert "proxy transcript masked"  "true" "$(echo "$RP" | jq -r '.masked' 2>/dev/null)"
  assert "proxy transcript n_msgs"  "3"    "$(echo "$RP" | jq -r '.messages|length' 2>/dev/null)"
  # lista via proxy (customer inexistente → 200 []; valida que a rota chega ao analytics-api, não à SPA)
  RL=$($CURL "$UI/analytics/sessions/customer/__nao_existe__?tenant_id=$TENANT")
  assert "proxy lista → JSON array" "array" "$(echo "$RL" | jq -r 'type' 2>/dev/null)"
else
  echo "  ⚠ platform-ui :5174 indisponível — pule B (rebuild platform-ui p/ testar o novo proxy nginx)"
fi

# ── C) integração com dado real (se houver) ───────────────────────────────────
echo "══ C) drill com dado real (descoberta) ══"
REAL=$(ch "SELECT customer_id,'|',session_id,'|',tenant_id FROM $DB.sessions FINAL
           WHERE customer_id != '' AND closed_at IS NOT NULL
             AND session_id IN (SELECT DISTINCT session_id FROM $DB.messages)
           ORDER BY opened_at DESC LIMIT 1" 2>/dev/null | tr -d '\t' )
RCID=$(echo "$REAL" | awk -F'|' '{gsub(/^ +| +$/,"",$1); print $1}')
RSID=$(echo "$REAL" | awk -F'|' '{gsub(/^ +| +$/,"",$2); print $2}')
RTEN=$(echo "$REAL" | awk -F'|' '{gsub(/^ +| +$/,"",$3); print $3}')
if [ -n "$RCID" ] && [ -n "$RSID" ]; then
  echo "  • real: customer=$RCID session=$RSID tenant=$RTEN"
  RLIST=$($CURL "$ANALYTICS/sessions/customer/$RCID?tenant_id=$RTEN")
  assert "lista traz a sessão real" "$RSID" \
    "$(echo "$RLIST" | jq -r --arg s "$RSID" '[.[]|select(.session_id==$s)]|.[0].session_id // "MISSING"')"
  RTR=$($CURL "$ANALYTICS/v1/transcript/sessions/$RSID?tenant_id=$RTEN&scope=contact")
  assert "transcrição real masked" "true" "$(echo "$RTR" | jq -r '.masked')"
  assert_ge "transcrição real n_msgs" "$(echo "$RTR" | jq -r '.messages|length')" "1"
else
  echo "  ⚠ sem (customer, session) real com mensagens no demo — pule C (rode o e2e/seed de demo p/ cobrir integração real)"
fi

echo
[ "$FAIL" = 0 ] && echo "✅ H1 OK — transcrição MASKED alcançável (direto + novo proxy /analytics); drill validado" \
                || { echo "❌ H1 com falhas"; exit 1; }
