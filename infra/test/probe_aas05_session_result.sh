#!/usr/bin/env bash
# probe_aas05_session_result.sh — 2026-10-01  (AAS-05 · adr-a2a-server-binding D5)
#
# PERGUNTA: quando o fluxo termina num `complete`, o resultado que ele declara fica LEGÍVEL, com o
# veredicto contra o `output_schema` do contrato do pool — e o status da sessão é deduzido de
# fatos, com `unknown` onde não há fato (antes: chave ausente respondia `closed`)?
#
# Três sessões REAIS pelo mesmo pool webhook, cada uma com o contrato num estado:
#   R1  contrato que o resultado cumpre        → closed · complete · result · contract valid
#   R2  contrato que exige campo ausente       → closed · result PRESERVADO · contract inválido nomeando o campo
#   R3  pool sem contrato A2A                  → closed · result · contract no_contract
#   S1  sessão inventada                       → unknown (nunca closed)
#   S2  sessão real consultada por OUTRO tenant → unknown
#   S3  a chave de resultado tem TTL (nunca eterna) e ≤ o TTL da sessão
#   S4  nenhuma chave de status "active" eterna sobrou
#
# O fluxo de fixture não chama LLM: `invoke pool_status_get` (sempre devolve `pool_id`) e
# `complete` com `result_from`. Fixtures: skill `skill_probe_aas05`, pool `probe_aas05`
# (webhook + a2a). A cada rodada o contrato volta ao estado de R1.
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

REG="${REGISTRY:-http://localhost:3300}"
GW="${GATEWAY:-http://localhost:8010}"
AUTHB="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS_C="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_aas05"; SKILL="skill_probe_aas05"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " o fim do fluxo deixa o resultado legível — e o status diz o que sabe?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl docker; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

C="curl -s --max-time 20"
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
body() { cat "$BODY"; }
TOK=$($C -X POST "$AUTHB/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOK" ] || { echo "INCONCLUSIVO — login do admin falhou"; exit 2; }
req() { local extra=(); [ -n "${3:-}" ] && extra=(-d "$3")
  $C -o "$BODY" -w '%{http_code}' -X "$1" -H "Authorization: Bearer $TOK" -H "x-tenant-id: $TENANT" \
     -H 'Content-Type: application/json' "${extra[@]}" "$REG$2"; }
rcli() { docker exec "$REDIS_C" redis-cli "$@" 2>/dev/null; }

SCHEMA_OK='{"type":"object","required":["pool_id"],"properties":{"pool_id":{"type":"string"}}}'
SCHEMA_RUIM='{"type":"object","required":["pool_id","campo_que_nao_existe"]}'
descr() { echo "{\"display_name\":\"probe AAS-05\",\"description\":\"Fixture do probe AAS-05.\",\"input_schema\":{\"type\":\"object\"},\"output_schema\":$1,\"skills\":[{\"id\":\"probe\",\"name\":\"probe\",\"description\":\"probe\"}],\"principal_kinds\":[\"partner\"]}"; }

# ── fixtures ─────────────────────────────────────────────────────────────────
FLOW='{"entry":"consultar","steps":[
 {"id":"consultar","type":"invoke","target":{"mcp_server":"mcp-server-plughub","tool":"pool_status_get"},
  "input":{"tenant_id":"$.session.tenant_id","pool_id":"probe_aas05"},"output_as":"status_pool",
  "on_success":"fim","on_failure":"falha"},
 {"id":"fim","type":"complete","outcome":"resolved","result_from":"status_pool"},
 {"id":"falha","type":"complete","outcome":"failed","issue_status":"pool_status_get falhou"}]}'
st=$(req PUT "/v1/skills/$SKILL" "{\"skill_id\":\"$SKILL\",\"name\":\"probe AAS-05\",\"version\":\"1.0\",\"description\":\"Fixture do probe AAS-05: termina com result_from.\",\"classification\":{\"type\":\"orchestrator\"},\"flow\":$FLOW}")
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — skill de fixture -> $st $(body | head -c 300)"; exit 2; }
contrato() {  # $1 = schema JSON ou "nenhum"
  if [ "$1" = nenhum ]; then req PUT "/v1/pools/$POOL" '{"channel_types":["webhook"],"a2a":null}'
  else req PUT "/v1/pools/$POOL" "{\"channel_types\":[\"webhook\",\"a2a\"],\"a2a\":$(descr "$1")}"; fi; }
if [ "$(req GET "/v1/pools/$POOL")" = 404 ]; then
  st=$(req POST /v1/pools "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webhook\",\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":1,\"description\":\"fixture do probe AAS-05\",\"a2a\":$(descr "$SCHEMA_OK")}")
else st=$(contrato "$SCHEMA_OK"); fi
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — pool de fixture -> $st $(body | head -c 300)"; exit 2; }
req GET "/v1/pools/$POOL/slots" >/dev/null
if [ "$(body | jq -r '.slots.current.skill_id // empty')" != "$SKILL" ] || [ "${AAS05_REDEPLOY:-}" = 1 ]; then
  s1=$(req PUT "/v1/pools/$POOL/slots/next" "{\"skill_id\":\"$SKILL\",\"config_json\":{}}"); s2=$(req POST "/v1/pools/$POOL/promote" '{}')
  [ "${s1:0:1}" = 2 ] && [ "${s2:0:1}" = 2 ] || { echo "INCONCLUSIVO — deploy: set-next $s1 promote $s2 $(body | head -c 300)"; exit 2; }
fi

status() {  # $1 sessão  $2 tenant
  $C "$GW/v1/channels/webhook/$1/status?tenant_id=${2:-$TENANT}"; }
roda() {  # dispara e espera o fim; ecoa o session_id
  local sid
  sid=$($C -X POST "$GW/v1/channels/webhook/pool/$POOL" -H 'Content-Type: application/json' \
        -d "{\"tenant_id\":\"$TENANT\",\"trigger_type\":\"task\",\"context\":{}}" | jq -r '.session_id // empty')
  [ -n "$sid" ] || return 1
  for _ in $(seq 1 45); do
    [ "$(status "$sid" | jq -r .status)" = closed ] && { echo "$sid"; return 0; }
    sleep 2
  done
  echo "$sid"; return 1
}

# ── R ────────────────────────────────────────────────────────────────────────
echo ""; echo "── R · TRÊS SESSÕES REAIS ─────────────────────────────────────────────"
if SID1=$(roda); then
  s=$(status "$SID1")
  r=$(echo "$s" | jq -c '{st:.status, by:.closed_by, o:.outcome, f:.result.from, p:.result.value.pool_id, c:.contract}')
  [ "$r" = '{"st":"closed","by":"complete","o":"resolved","f":"status_pool","p":"probe_aas05","c":{"checked":true,"valid":true}}' ] \
    && ok "R1 contrato cumprido: closed por complete, resultado legível, contrato válido" || falha "R1 -> $r"
else incon "R1 a sessão $SID1 não fechou em 90 s ($(status "$SID1" | head -c 200))"; fi

contrato "$SCHEMA_RUIM" >/dev/null
if SID2=$(roda); then
  s=$(status "$SID2")
  c=$(echo "$s" | jq -c '.contract'); p=$(echo "$s" | jq -r '.result.value.pool_id // empty')
  [ "$(echo "$c" | jq -r '.checked,.valid' | tr '\n' ' ')" = "true false " ] && echo "$c" | grep -q campo_que_nao_existe && [ "$p" = "$POOL" ] \
    && ok "R2 contrato violado: veredicto inválido NOMEANDO o campo, e o resultado preservado" || falha "R2 -> $c pool=$p"
else incon "R2 a sessão $SID2 não fechou em 90 s"; fi

contrato nenhum >/dev/null
if SID3=$(roda); then
  c=$(status "$SID3" | jq -c '.contract')
  [ "$c" = '{"checked":false,"reason":"no_contract"}' ] && ok "R3 pool sem contrato: resultado gravado, contrato no_contract" || falha "R3 -> $c"
else incon "R3 a sessão $SID3 não fechou em 90 s"; fi
contrato "$SCHEMA_OK" >/dev/null

# ── S ────────────────────────────────────────────────────────────────────────
echo ""; echo "── S · STATUS HONESTO ─────────────────────────────────────────────────"
s=$(status "sessao-que-nunca-existiu-$(date +%s)" | jq -r .status)
[ "$s" = unknown ] && ok "S1 sessão inventada -> unknown (antes: closed)" || falha "S1 -> $s"
if [ -n "${SID1:-}" ]; then
  s=$(status "$SID1" tenant_alheio | jq -r .status)
  [ "$s" = unknown ] && ok "S2 sessão real consultada por outro tenant -> unknown" || falha "S2 -> $s"
  ttl=$(rcli TTL "$TENANT:session:$SID1:result")
  [ -n "$ttl" ] && [ "$ttl" -gt 0 ] && [ "$ttl" -le 14400 ] && ok "S3 resultado com TTL da sessão (${ttl}s restantes, ≤ 14400)" || falha "S3 TTL=$ttl"
  st=$(rcli GET "$TENANT:session:$SID1:status")
  [ -z "$st" ] && ok "S4 nenhuma chave de status 'active' eterna" || falha "S4 chave de status = '$st'"
else incon "S2–S4 sem sessão real (R1 não rodou)"; fi

echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — o resultado do fim é legível e conferido, e o status só afirma o que sabe."
exit 0
