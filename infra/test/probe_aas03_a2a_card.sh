#!/usr/bin/env bash
# probe_aas03_a2a_card.sh — 2026-10-01  (AAS-03 · adr-a2a-server-binding D2, D14.3)
#
# PERGUNTA: o AgentCard público é PROJEÇÃO do pool — endereçado por ChannelEndpoint `a2a`,
# servido pela borda só quando o pool pode atender e o contrato é descobrível, com a versão
# do deploy em produção — e a recusa é MUDA para fora e DITA no log?
#
# Decisão do dono (2026-10-01): um card por POOL em {base}/a2a/{slug}/.well-known/agent-card.json.
# A forma do card é a do `a2a.proto` v1.0, conferida pelo `AgentCardSchema` de @plughub/schemas
# rodando no container do registry (outro caminho que não o do montador).
#
# RAMOS
#   C  CARD AO VIVO pela borda (gateway :8010): 200, forma do proto, versão = set_at do slot
#      `current` lido no registry, URL da interface = env pública + slug, skills e schemas do
#      contrato, cabeçalho de cache.
#   R  RECUSAS no registry, cada uma com o MOTIVO: slug inexistente, pool sem deploy, contrato
#      não descobrível, endpoint desligado — e o controle de que, desfeito, o card volta.
#   G  BORDA MUDA: 404 do gateway é `{"error":"not_found"}` sem motivo, e o motivo aparece no
#      LOG do gateway; o cache cai em ≤ 30 s (tirar `discoverable` some com o card).
#   E  CADASTRO: endpoint a2a para pool que não expõe A2A e slug fora de forma → 422.
#
# Fixtures fixas e idempotentes: pools `probe_aas03_a2a` (com deploy de skill_nps_v1) e
# `probe_aas03_sem_deploy`; endpoints `probe-aas03` e `probe-aas03-sem-deploy`.
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

REG="${REGISTRY:-http://localhost:3300}"
GW="${GATEWAY:-http://localhost:8010}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
GW_C="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
REG_C="${REGISTRY_CONTAINER:-plughub-demo-agent-registry-1}"
POOL="probe_aas03_a2a"; SLUG="probe-aas03"
POOL_SD="probe_aas03_sem_deploy"; SLUG_SD="probe-aas03-sem-deploy"
SKILL="skill_nps_v1"
D='{"display_name":"Segunda via (probe AAS-03)","description":"Emite a segunda via de um boleto em aberto.","input_schema":{"type":"object","properties":{"cpf":{"type":"string"}},"required":["cpf"]},"output_schema":{"type":"object","properties":{"linha_digitavel":{"type":"string"}}},"skills":[{"id":"segunda_via","name":"Segunda via","description":"Emite a segunda via.","tags":["boleto"],"examples":["quero a segunda via"]}],"discoverable":true,"principal_kinds":["partner","customer_agent"]}'
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " o AgentCard é projeção do pool — e a recusa é muda para fora?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl python3 docker; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

C="curl -s --max-time 20"
TOKEN=$($C -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOKEN" ] || { echo "INCONCLUSIVO — login do admin falhou em $AUTH"; exit 2; }
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
req() { local m=$1 p=$2 extra=(); [ -n "${3:-}" ] && extra=(-d "$3"); $C -o "$BODY" -w '%{http_code}' -X "$m" "${H[@]}" "${extra[@]}" "$REG$p"; }
body() { cat "$BODY"; }
motivo() { $C "$REG/v1/a2a-cards/$1?base_url=https://probe.invalid" -H "x-tenant-id: $TENANT" | jq -r '.reason // "CARD"'; }

# ── fixtures ─────────────────────────────────────────────────────────────────
garante_pool() {  # $1 pool
  local st; st=$(req GET "/v1/pools/$1")
  if [ "$st" = 404 ]; then
    st=$(req POST /v1/pools "{\"pool_id\":\"$1\",\"agent_kind\":\"ai\",\"channel_types\":[\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":1,\"description\":\"fixture do probe AAS-03\",\"a2a\":$D}")
  else
    st=$(req PUT "/v1/pools/$1" "{\"channel_types\":[\"a2a\"],\"a2a\":$D}")
  fi
  [ "${st:0:1}" = 2 ] || { echo "    pool $1 -> $st $(body | head -c 300)"; return 1; }
}
garante_endpoint() {  # $1 slug $2 pool
  req GET "/v1/channel-endpoints?channel=a2a" >/dev/null
  local id; id=$(body | jq -r --arg s "$1" '(.endpoints // .)[]? | select(.identifier==$s) | .id' 2>/dev/null | head -1)
  if [ -z "$id" ]; then
    local st; st=$(req POST /v1/channel-endpoints "{\"channel\":\"a2a\",\"identifier\":\"$1\",\"pool_id\":\"$2\",\"display_name\":\"probe AAS-03\"}")
    [ "$st" = 201 ] || { echo "    endpoint $1 -> $st $(body | head -c 300)"; return 1; }
    body | jq -r '.id'
  else
    req PUT "/v1/channel-endpoints/$id" '{"active":true}' >/dev/null; echo "$id"
  fi
}
garante_deploy() {  # $1 pool
  req GET "/v1/pools/$1/slots" >/dev/null
  [ "$(body | jq -r '.slots.current.set // false')" = true ] && return 0
  local s1 s2
  s1=$(req PUT "/v1/pools/$1/slots/next" "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  s2=$(req POST "/v1/pools/$1/promote" '{}')
  [ "${s1:0:1}" = 2 ] && [ "${s2:0:1}" = 2 ] || { echo "    deploy $1: set-next $s1, promote $s2 $(body | head -c 300)"; return 1; }
}
if ! garante_pool "$POOL" || ! garante_pool "$POOL_SD" || ! garante_deploy "$POOL"; then
  echo "INCONCLUSIVO — fixtures não montadas"; exit 2
fi
EP=$(garante_endpoint "$SLUG" "$POOL") && EP_SD=$(garante_endpoint "$SLUG_SD" "$POOL_SD") \
  || { echo "INCONCLUSIVO — endpoints não montados"; exit 2; }
req GET "/v1/pools/$POOL_SD/slots" >/dev/null
[ "$(body | jq -r '.slots.current.set // false')" = false ] \
  || { echo "INCONCLUSIVO — $POOL_SD ganhou deploy; o ramo de 'sem deploy' não mede nada"; exit 2; }
req GET "/v1/pools/$POOL/slots" >/dev/null
SET_AT=$(body | jq -r '.slots.current.set_at')
PUBLIC=$(docker exec "$GW_C" printenv PLUGHUB_A2A_PUBLIC_BASE_URL 2>/dev/null)
[ -n "$PUBLIC" ] || { echo "INCONCLUSIVO — PLUGHUB_A2A_PUBLIC_BASE_URL vazio no gateway"; exit 2; }

# ── C ────────────────────────────────────────────────────────────────────────
echo ""; echo "── C · CARD AO VIVO PELA BORDA ────────────────────────────────────────"
HDR=$(mktemp); CARD=$(mktemp)
st=$($C -D "$HDR" -o "$CARD" -w '%{http_code}' "$GW/a2a/$SLUG/.well-known/agent-card.json")
if [ "$st" != 200 ]; then
  falha "C1 card -> $st $(head -c 300 "$CARD")"
else
  ok "C1 GET $GW/a2a/$SLUG/.well-known/agent-card.json -> 200"
  # forma do proto, conferida pelo schema de OUTRA casa (o container do registry, @plughub/schemas)
  forma=$(docker exec -i "$REG_C" node -e '
    const { AgentCardSchema } = require("@plughub/schemas")
    let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
      const r = AgentCardSchema.safeParse(JSON.parse(s))
      console.log(r.success ? "OK" : "FALHA " + JSON.stringify(r.error.issues.slice(0, 2)))
    })' < "$CARD" 2>&1)
  [ "$forma" = OK ] && ok "C2 forma do AgentCard v1.0 (AgentCardSchema, no container do registry)" || falha "C2 forma: $forma"
  [ "$(jq -r .version "$CARD")" = "$SET_AT" ] && ok "C3 version = set_at do slot current ($SET_AT)" \
    || falha "C3 version=$(jq -r .version "$CARD") esperado set_at=$SET_AT"
  [ "$(jq -r '.supportedInterfaces[0].url' "$CARD")" = "${PUBLIC%/}/a2a/$SLUG" ] && ok "C4 interface = env pública + slug (${PUBLIC%/}/a2a/$SLUG)" \
    || falha "C4 interface=$(jq -c .supportedInterfaces "$CARD")"
  exp=$(echo "$D" | jq -cS '{skills: [.skills[].id], io: {input_schema, output_schema}, sec: (.principal_kinds|sort)}')
  got=$(jq -cS '{skills: [.skills[].id], io: .capabilities.extensions[0].params, sec: (.securitySchemes|keys|sort)}' "$CARD")
  [ "$exp" = "$got" ] && ok "C5 skills, schemas de entrada/saída e esquemas de segurança vêm do contrato" || falha "C5 contrato: esperado $exp, card $got"
  [ "$(jq -r '.capabilities.extendedAgentCard' "$CARD")" = false ] && ok "C6 extendedAgentCard=false até o principal existir (AAS-04)" \
    || falha "C6 extendedAgentCard=$(jq -r .capabilities.extendedAgentCard "$CARD")"
  grep -qi '^cache-control: public, max-age=30' "$HDR" && ok "C7 Cache-Control: public, max-age=30" || falha "C7 sem o cache declarado"
fi
rm -f "$HDR"

# ── R ────────────────────────────────────────────────────────────────────────
echo ""; echo "── R · RECUSAS NO REGISTRY, COM MOTIVO ────────────────────────────────"
m=$(motivo "probe-aas03-nunca-$(date +%s)"); [ "$m" = endpoint_not_found ] && ok "R1 slug inexistente -> endpoint_not_found" || falha "R1 -> $m"
m=$(motivo "$SLUG_SD"); [ "$m" = no_current_deploy ] && ok "R2 pool sem deploy -> no_current_deploy" || falha "R2 -> $m"
req PUT "/v1/pools/$POOL" "{\"a2a\":$(echo "$D" | jq -c '.discoverable=false')}" >/dev/null
m=$(motivo "$SLUG"); [ "$m" = not_discoverable ] && ok "R3 contrato não descobrível -> not_discoverable" || falha "R3 -> $m"
req PUT "/v1/pools/$POOL" "{\"a2a\":$D}" >/dev/null
req PUT "/v1/channel-endpoints/$EP" '{"active":false}' >/dev/null
m=$(motivo "$SLUG"); [ "$m" = endpoint_inactive ] && ok "R4 endpoint desligado -> endpoint_inactive" || falha "R4 -> $m"
req PUT "/v1/channel-endpoints/$EP" '{"active":true}' >/dev/null
m=$(motivo "$SLUG"); [ "$m" = CARD ] && ok "R5 controle: desfeito, o card volta" || falha "R5 controle -> $m"

# ── G ────────────────────────────────────────────────────────────────────────
echo ""; echo "── G · BORDA MUDA E CACHE CURTO ───────────────────────────────────────"
DESDE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
st=$($C -o "$BODY" -w '%{http_code}' "$GW/a2a/$SLUG_SD/.well-known/agent-card.json")
[ "$st" = 404 ] && [ "$(body | jq -cS .)" = '{"error":"not_found"}' ] \
  && ok "G1 recusa pela borda -> 404 {\"error\":\"not_found\"}, sem motivo" || falha "G1 -> $st $(body)"
sleep 1
docker logs --since "$DESDE" "$GW_C" 2>&1 | grep -q "slug=$SLUG_SD.*motivo=no_current_deploy" \
  && ok "G2 o motivo (no_current_deploy) está no LOG do gateway" || falha "G2 log do gateway sem 'slug=$SLUG_SD … motivo=no_current_deploy'"
req PUT "/v1/pools/$POOL" "{\"a2a\":$(echo "$D" | jq -c '.discoverable=false')}" >/dev/null
echo "    (esperando 31 s pelo cache da borda)"; sleep 31
st=$($C -o /dev/null -w '%{http_code}' "$GW/a2a/$SLUG/.well-known/agent-card.json")
[ "$st" = 404 ] && ok "G3 tirar discoverable some com o card em ≤ 30 s" || falha "G3 card ainda servido depois do cache -> $st"
req PUT "/v1/pools/$POOL" "{\"a2a\":$D}" >/dev/null

# ── E ────────────────────────────────────────────────────────────────────────
echo ""; echo "── E · CADASTRO DO ENDEREÇO ───────────────────────────────────────────"
# resíduo de rodada da bateria: com o portão desligado (M4), E1/E2 CRIAM os endereços, e a
# rodada seguinte receberia 409 em vez do 422 que mede. Endpoint se apaga pela API.
req GET "/v1/channel-endpoints?channel=a2a" >/dev/null
for id in $(body | jq -r '(.endpoints // .)[]? | select(.identifier=="probe-aas03-recusa" or .identifier=="Probe AAS03") | .id' 2>/dev/null); do
  req DELETE "/v1/channel-endpoints/$id" >/dev/null
done
[ "$(req GET /v1/pools/probe_aas01_texto)" = 200 ] || req POST /v1/pools '{"pool_id":"probe_aas01_texto","channel_types":["webchat"],"sla_target_ms":60000,"agent_kind":"ai"}' >/dev/null
st=$(req POST /v1/channel-endpoints '{"channel":"a2a","identifier":"probe-aas03-recusa","pool_id":"probe_aas01_texto","display_name":"x"}'); r=$(body | jq -r '.reason // empty')
[ "$st" = 422 ] && [ "$r" = a2a_pool_not_exposed ] && ok "E1 endpoint a2a para pool sem A2A -> 422 a2a_pool_not_exposed" || falha "E1 -> $st $r"
st=$(req POST /v1/channel-endpoints "{\"channel\":\"a2a\",\"identifier\":\"Probe AAS03\",\"pool_id\":\"$POOL\",\"display_name\":\"x\"}"); r=$(body | jq -r '.reason // empty')
[ "$st" = 422 ] && [ "$r" = a2a_slug_invalid ] && ok "E2 slug fora de forma -> 422 a2a_slug_invalid" || falha "E2 -> $st $r"

echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — o card é projeção do pool, e a recusa é muda para fora e dita no log."
exit 0
