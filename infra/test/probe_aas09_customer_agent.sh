#!/usr/bin/env bash
# probe_aas09_customer_agent.sh — 2026-10-01  (AAS-09 · adr-a2a-server-binding D6, D9, D13)
#
# PERGUNTA: o cliente que provou a posse numa conversa gera um token para o assistente DELE — o
# titular sai da prova (nunca do fluxo), a credencial só nasce na retirada e uma vez, a sessão que
# o assistente abre é DO titular, a cota segura, outro titular não vê, e revogar derruba?
#
# RAMOS
#   G  EMISSÃO (`customer_agent_grant` real pelo /sse; prova de posse é fixture, como no PID-03)
#      G1 sem prova → no_fresh_proof · G2 prova de dois clientes → ambiguous_holder
#      G3 com prova de A e `customer_id` de B no INPUT → link (e nenhuma credencial na resposta)
#   R  RETIRADA  R1 o GET não retira · R2 o POST mostra `pha_…` uma vez, sem cache · R3 de novo → 410
#   S  A SESSÃO  S1 o assistente de A abre task (INPUT_REQUIRED) · S2 o cliente da sessão é A — não
#      o B do input — e `core.a2a.holder` diz A
#   X  OUTRO TITULAR  X1 o token de B não lê a task de A (-32001)
#   Q  COTA (política do pool: 1 ativa, 3/dia)  Q1 segunda ativa → 429 active · Q2 quarta do dia → 429 daily
#   A  ADMIN  A1 não rotaciona token de cliente (409) · A2 desliga o de B (200)
#   V  REVOGAÇÃO  V1 `customer_agent_revoke` pela prova de A → a porta recusa A em ≤ 31 s (cache)
#
# NÃO MEDIDO AO VIVO (declarado): a validade vencendo (dias) — `test_aas09_customer_agent.py` nos
# dois serviços, inclusive o cache de 30 s que não estica a validade.
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."
MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"

echo "════════════════════════════════════════════════════════════════════"
echo " o token do próprio cliente nasce da prova dele, e só fala por ele?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl docker; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

. infra/test/_a2a_fixture.sh

POOL9="probe_aas09_a2a"; SLUG9="probe-aas09"
DESC9=$(echo "$DESC" | jq -c '.principal_kinds = ["customer_agent"]
  | .customer_agent = {validity_days: 1, max_active_tasks: 1, max_tasks_per_day: 3}
  | .display_name = "probe AAS-09"')
if [ "$(req "$ADMIN" GET "$REG/v1/pools/$POOL9")" = 404 ]; then
  st=$(req "$ADMIN" POST "$REG/v1/pools" "{\"pool_id\":\"$POOL9\",\"agent_kind\":\"ai\",\"channel_types\":[\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":10,\"description\":\"fixture do probe AAS-09\",\"a2a\":$DESC9}")
else st=$(req "$ADMIN" PUT "$REG/v1/pools/$POOL9" "{\"channel_types\":[\"a2a\"],\"max_concurrent_sessions\":10,\"a2a\":$DESC9}"); fi
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — pool de fixture -> $st $(body | head -c 300)"; exit 2; }
req "$ADMIN" GET "$REG/v1/pools/$POOL9/slots" >/dev/null
if [ "$(body | jq -r '.slots.current.skill_id // empty')" != "$SKILL" ]; then
  s1=$(req "$ADMIN" PUT "$REG/v1/pools/$POOL9/slots/next" "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  s2=$(req "$ADMIN" POST "$REG/v1/pools/$POOL9/promote" '{}')
  [ "${s1:0:1}" = 2 ] && [ "${s2:0:1}" = 2 ] || { echo "INCONCLUSIVO — deploy $s1/$s2 $(body | head -c 200)"; exit 2; }
fi
req "$ADMIN" GET "$REG/v1/channel-endpoints?channel=a2a" >/dev/null
if [ -z "$(body | jq -r --arg s "$SLUG9" '(.endpoints // .)[]? | select(.identifier==$s) | .id' | head -1)" ]; then
  st=$(req "$ADMIN" POST "$REG/v1/channel-endpoints" "{\"channel\":\"a2a\",\"identifier\":\"$SLUG9\",\"pool_id\":\"$POOL9\",\"display_name\":\"probe AAS-09\"}")
  [ "$st" = 201 ] || { echo "INCONCLUSIVO — endpoint -> $st $(body | head -c 200)"; exit 2; }
fi

RUN=$(date +%s)
CLI_A="probe-aas09-a-$RUN"; CLI_B="probe-aas09-b-$RUN"
rpc9() {  # $1 credencial  $2 método  $3 params
  $C -o "$BODY" -D "$BODY.h" -w '%{http_code}' -X POST "$GW/a2a/$SLUG9" -H 'Content-Type: application/json' \
    -H 'A2A-Version: 1.0' -H "Authorization: Bearer $1" -d "{\"jsonrpc\":\"2.0\",\"id\":\"p9\",\"method\":\"$2\",\"params\":$3}"; }
nova9() { rpc9 "$1" SendMessage "{\"message\":{\"role\":\"ROLE_USER\",\"messageId\":\"m-$RANDOM\",\"parts\":[{\"data\":{\"linha\":\"1\"}}]}}"; }
cancela9() { rpc9 "$1" CancelTask "{\"id\":\"$2\"}" >/dev/null
  for _ in $(seq 1 40); do rpc9 "$1" GetTask "{\"id\":\"$2\"}" >/dev/null
    [ "$(body | jq -r '.result.status.state')" = TASK_STATE_CANCELED ] && return 0; sleep 0.5; done; return 1; }
exercicio() { docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - $TENANT $1 $POOL9 $CLI_A $CLI_B" \
  < infra/test/_customer_agent_exercise.cjs 2>/dev/null | tail -1; }

# ── G ────────────────────────────────────────────────────────────────────────
echo ""; echo "── G · EMISSÃO ────────────────────────────────────────────────────────"
J=$(exercicio grant)
echo "$J" | jq -e . >/dev/null 2>&1 || { echo "INCONCLUSIVO — exercício não rodou: $J"; exit 2; }
[ "$(echo "$J" | jq -r .sem_prova.body.error)" = no_fresh_proof ] && ok "G1 sem prova → no_fresh_proof" || falha "G1 $(echo "$J" | jq -c .sem_prova)"
[ "$(echo "$J" | jq -r .dois_clientes.body.error)" = ambiguous_holder ] && ok "G2 prova de dois clientes → ambiguous_holder" || falha "G2 $(echo "$J" | jq -c .dois_clientes)"
LA=$(echo "$J" | jq -r '.link_a.body.link // empty'); LB=$(echo "$J" | jq -r '.link_b.body.link // empty')
if [ -n "$LA" ] && ! echo "$J" | grep -q 'pha_'; then ok "G3 link de retirada, nenhuma credencial na resposta da tool"
else falha "G3 $(echo "$J" | jq -c .link_a)"; fi
[ -n "$LA" ] && [ -n "$LB" ] || { echo "INCONCLUSIVO — sem links, nada mais a medir"; exit 2; }

# ── R ────────────────────────────────────────────────────────────────────────
echo ""; echo "── R · RETIRADA ───────────────────────────────────────────────────────"
st=$($C -o "$BODY" -w '%{http_code}' "$LA")
[ "$st" = 200 ] && grep -q '<form method="post">' "$BODY" && ! grep -q 'pha_' "$BODY" \
  && ok "R1 o GET mostra o botão e não retira" || falha "R1 -> $st"
st=$($C -o "$BODY" -D "$BODY.h" -w '%{http_code}' -X POST "$LA")
CA=$(grep -oE 'pha_[A-Za-z0-9_-]+' "$BODY" | head -1)
[ "$st" = 200 ] && [ -n "$CA" ] && grep -qi '^cache-control: no-store' "$BODY.h" \
  && ok "R2 o POST mostra a credencial uma vez, sem cache" || falha "R2 -> $st"
st=$($C -o "$BODY" -w '%{http_code}' -X POST "$LA")
[ "$st" = 410 ] && ! grep -q 'pha_' "$BODY" && ok "R3 segunda retirada → 410, sem credencial" || falha "R3 -> $st"
$C -o "$BODY" -X POST "$LB" >/dev/null; CB=$(grep -oE 'pha_[A-Za-z0-9_-]+' "$BODY" | head -1)
[ -n "$CA" ] && [ -n "$CB" ] || { echo "INCONCLUSIVO — sem as duas credenciais"; exit 2; }

# ── S ────────────────────────────────────────────────────────────────────────
echo ""; echo "── S · A SESSÃO É DO TITULAR ──────────────────────────────────────────"
st=$(nova9 "$CA"); T1=$(body | jq -r '.result.task.id // empty'); E1=$(body | jq -r '.result.task.status.state // .error.code')
[ "$st" = 200 ] && [ -n "$T1" ] && ok "S1 o assistente de A abre a task ($E1)" || falha "S1 -> $st $(body | head -c 200)"
cid=$(rcli GET "session:$T1:meta" | jq -r '.customer_id // empty')
hol=$(rcli HGET "$TENANT:ctx:$T1" core.a2a.holder | jq -r '.value.customer_id // empty' 2>/dev/null)
[ "$cid" = "$CLI_A" ] && [ "$hol" = "$CLI_A" ] \
  && ok "S2 cliente da sessão = A (o B do input foi ignorado), e core.a2a.holder diz A" \
  || falha "S2 meta.customer_id=$cid holder=$hol (A=$CLI_A)"

# ── X ────────────────────────────────────────────────────────────────────────
echo ""; echo "── X · OUTRO TITULAR ──────────────────────────────────────────────────"
rpc9 "$CB" GetTask "{\"id\":\"$T1\"}" >/dev/null
[ "$(body | jq -r .error.code)" = -32001 ] && ok "X1 o token de B não lê a task de A (-32001)" || falha "X1 $(body | head -c 200)"

# ── Q ────────────────────────────────────────────────────────────────────────
echo ""; echo "── Q · COTA (1 ativa, 3/dia) ──────────────────────────────────────────"
st=$(nova9 "$CA")
[ "$st" = 429 ] && [ "$(body | jq -r .quota)" = active ] && grep -qi '^retry-after:' "$BODY.h" \
  && ok "Q1 segunda task ativa → 429 active, com Retry-After" || falha "Q1 -> $st $(body | head -c 200)"
cancela9 "$CA" "$T1" || falha "Q* não consegui cancelar $T1"
for n in 2 3; do
  st=$(nova9 "$CA"); tid=$(body | jq -r '.result.task.id // empty')
  [ "$st" = 200 ] && [ -n "$tid" ] || falha "Q* a task $n do dia -> $st $(body | head -c 160)"
  [ -n "$tid" ] && cancela9 "$CA" "$tid"
done
st=$(nova9 "$CA")
[ "$st" = 429 ] && [ "$(body | jq -r .quota)" = daily ] && ok "Q2 quarta do dia → 429 daily" || falha "Q2 -> $st $(body | head -c 200)"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""; echo "── A · O ADMIN SÓ DESLIGA ─────────────────────────────────────────────"
req "$MASTER" GET "$P" >/dev/null
PA=$(body | jq -r --arg c "$CLI_A" '.[]? | select(.customer_id==$c) | .agent_principal_id' | head -1)
PB=$(body | jq -r --arg c "$CLI_B" '.[]? | select(.customer_id==$c) | .agent_principal_id' | head -1)
st=$(req "$MASTER" POST "$P/$PA/credential"); [ "$st" = 409 ] && ok "A1 rotacionar token de cliente → 409" || falha "A1 -> $st"
st=$(req "$MASTER" PUT "$P/$PB" '{"active":false}'); [ "$st" = 200 ] && ok "A2 desligar o de B → 200" || falha "A2 -> $st"

# ── V ────────────────────────────────────────────────────────────────────────
echo ""; echo "── V · REVOGAÇÃO PELO TITULAR ─────────────────────────────────────────"
J=$(exercicio revoke)
n=$(echo "$J" | jq '.revoke.body.revoked | length' 2>/dev/null)
st=""; for _ in $(seq 1 35); do st=$(rpc9 "$CA" GetTask "{\"id\":\"$T1\"}"); [ "$st" = 401 ] && break; sleep 1; done
[ "${n:-0}" -ge 1 ] && [ "$st" = 401 ] && ok "V1 revogado pela prova de A ($n) e a porta recusa A (401)" \
  || falha "V1 revogados=$n porta=$st $(echo "$J" | head -c 200)"

limpa
echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — o token do cliente nasce da prova dele, uma vez, fala só por ele, tem cota e cai quando ele revoga."
exit 0
