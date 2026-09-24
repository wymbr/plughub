#!/usr/bin/env bash
# voz37_round.sh — 2026-09-24  (VOZ-37, segunda rodada assistida)
#
# Monta e desmonta o cenario da validacao com gente da PAUSA DE MIDIA no Console: uma chamada
# telefonica real (Twilio → borda SIP) cai num pool HUMANO de voz; o atendente, no Console,
# convida um especialista de IA (@pin) que pede o PIN pelo teclado. Durante o bloco mascarado o
# Console tem de mostrar "Audio pausado", a rota de token tem de responder 409, e ao fim o audio
# tem de voltar sozinho. Roteiro: docs/guias/roteiro-validacao-pausa-midia-console.md
#
#   bash infra/scripts/voz37_round.sh preparar            # skill, 2 pools, deploy, escopo do admin
#   bash infra/scripts/voz37_round.sh apontar             # o numero Twilio passa a tocar no pool humano
#   bash infra/scripts/voz37_round.sh devolver            # o numero volta ao pool de onde saiu
#   bash infra/scripts/voz37_round.sh evidencias <desde> [pin]   # le logs e stream da rodada
#
# `preparar` e idempotente e NAO mexe no numero; `apontar` guarda o pool de origem em
# /tmp/voz37_endpoint_origem para o `devolver` saber para onde voltar (nunca adivinha).
# Nao liga a borda SIP: isso e do dono (PLUGHUB_SIP_EDGE=true no .env.demo + infra/scripts/up.sh).

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202/auth}"
EMAIL="${VOZ37_EMAIL:-admin@plughub.local}"
PASS="${VOZ37_PASS:-changeme_admin}"
NUMERO="${VOZ37_NUMERO:-+15513241410}"
HUMANO="voz37_humano"
ESPEC="voz37_pin_ia"
ALIAS="pin"
SKILL="skill_voz37_pin_especialista_v1"
FIXTURE="infra/test/fixtures/$SKILL.json"
ORIGEM_F="/tmp/voz37_endpoint_origem"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
POLICY='{"customer_publish":["audio"],"agent_publish":["audio"]}'

ok()   { echo "  OK      $*"; }
erro() { echo "  ERRO    $*"; exit 1; }
info() { echo "  INFO    $*"; }

command -v jq >/dev/null || { echo "jq ausente — rode de dentro do WSL"; exit 2; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOKEN" ] || erro "login de $EMAIL falhou em $AUTH (auth-api no ar?)"
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

endpoint() {  # o endpoint voice do numero, ou vazio
  curl -s --max-time 10 "${H[@]}" "$REG/v1/channel-endpoints?channel=voice" \
    | jq -c --arg d "$NUMERO" '[(.items // .endpoints // . // [])[] | select(.identifier == $d)] | .[0] // empty'
}

preparar() {
  echo "── preparar"
  st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
  { [ "$st" = 200 ] || [ "$st" = 201 ]; } || erro "skill $SKILL nao publicada (http $st)"
  ok "skill $SKILL gravada"

  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$ESPEC")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$ESPEC\",\"agent_kind\":\"ai\",\"channel_types\":[\"voice\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"VOZ-37: especialista de IA de voz que coleta o PIN mascarado, convidado pelo atendente humano (@$ALIAS)\",\"media_policy\":$POLICY}")
    [ "$st" = 201 ] || erro "pool $ESPEC nao criado (http $st)"
    ok "pool $ESPEC criado"
  else
    ok "pool $ESPEC ja existe"
  fi
  sn=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$ESPEC/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{\"max_concurrent_sessions\":2}}")
  pm=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$ESPEC/promote" -d '{}')
  { [ "$sn" = 200 ] && [ "$pm" = 200 ]; } || erro "deploy do $SKILL em $ESPEC falhou (set-next=$sn promote=$pm)"
  ok "$SKILL em producao no $ESPEC"

  mention="{\"$ALIAS\":\"$ESPEC\"}"
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$HUMANO")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$HUMANO\",\"agent_kind\":\"human\",\"channel_types\":[\"voice\"],\"sla_target_ms\":60000,\"description\":\"VOZ-37: pool HUMANO de voz; o atendente convida @$ALIAS para coletar o PIN\",\"media_policy\":$POLICY,\"mentionable_pools\":$mention}")
    [ "$st" = 201 ] || erro "pool $HUMANO nao criado (http $st)"
    ok "pool $HUMANO criado"
  else
    st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$HUMANO" -d "{\"mentionable_pools\":$mention}")
    [ "$st" = 200 ] || erro "mentionable_pools do $HUMANO nao gravado (http $st)"
    ok "pool $HUMANO ja existe (mentionable_pools regravado)"
  fi
  got=$(curl -s "${H[@]}" "$REG/v1/pools/$HUMANO" | jq -r --arg a "$ALIAS" '.mentionable_pools[$a] // empty')
  [ "$got" = "$ESPEC" ] || erro "o registry diz @$ALIAS -> '${got:-nada}', esperado $ESPEC"
  ok "o registry confirma @$ALIAS -> $ESPEC no $HUMANO"

  for _ in $(seq 1 40); do
    [ -n "$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$ESPEC:instances" | head -1)" ] && break
    sleep 1
  done
  n=$(docker exec "$REDIS" redis-cli scard "$TENANT:pool:$ESPEC:instances")
  [ "${n:-0}" -gt 0 ] || erro "nenhuma instancia do $ESPEC em 40 s — o bridge nao reconciliou o pool"
  ok "$n instancia(s) do especialista prontas"

  uid=$(curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOKEN" | jq -r --arg e "$EMAIL" '.[] | select(.email==$e) | .id' | head -1)
  [ -n "$uid" ] || erro "usuario $EMAIL nao encontrado na auth-api"
  atual=$(curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOKEN" | jq -c --arg e "$EMAIL" '.[] | select(.email==$e) | (.accessible_pools // [])' | head -1)
  novo=$(printf '%s' "$atual" | jq -c --arg a "$HUMANO" --arg b "$ESPEC" '(. + [$a,$b]) | unique')
  if [ "$novo" != "$(printf '%s' "$atual" | jq -c 'unique')" ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$AUTH/users/$uid" -H 'content-type: application/json' \
      -H "Authorization: Bearer $TOKEN" -d "{\"accessible_pools\":$novo}")
    [ "$st" = 200 ] || erro "escopo de $EMAIL nao atualizado (http $st)"
    ok "$HUMANO e $ESPEC no escopo de $EMAIL — SAIA E ENTRE DE NOVO no Console para o token levar"
  else
    ok "$HUMANO e $ESPEC ja estavam no escopo de $EMAIL"
  fi

  ep=$(endpoint)
  info "numero $NUMERO hoje toca em: $(printf '%s' "$ep" | jq -r '.pool_id // "(sem endpoint)"') — o 'apontar' troca na hora da chamada"
  info "borda SIP: $(grep -E '^PLUGHUB_SIP_EDGE=' .env.demo 2>/dev/null || echo 'PLUGHUB_SIP_EDGE ausente')"
}

apontar() {
  echo "── apontar"
  ep=$(endpoint)
  [ -n "$ep" ] || erro "o numero $NUMERO nao tem endpoint voice — cadastre-o antes (Configuracao > Canais > Voz)"
  id=$(printf '%s' "$ep" | jq -r '.id'); de=$(printf '%s' "$ep" | jq -r '.pool_id')
  if [ "$de" = "$HUMANO" ]; then ok "$NUMERO ja toca no $HUMANO"; return; fi
  printf '%s %s\n' "$id" "$de" > "$ORIGEM_F"
  st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/channel-endpoints/$id" -d "{\"pool_id\":\"$HUMANO\"}")
  [ "$st" = 200 ] || erro "endpoint nao trocado (http $st)"
  ok "$NUMERO: $de -> $HUMANO (origem guardada em $ORIGEM_F)"
  info "o gateway guarda o endpoint em cache; espere ~40 s antes de ligar"
}

devolver() {
  echo "── devolver"
  [ -f "$ORIGEM_F" ] || { info "nada guardado em $ORIGEM_F — o numero nao foi apontado por este script"; return; }
  read -r id de < "$ORIGEM_F"
  st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/channel-endpoints/$id" -d "{\"pool_id\":\"$de\"}")
  [ "$st" = 200 ] || erro "endpoint nao devolvido (http $st) — continua no $HUMANO"
  rm -f "$ORIGEM_F"
  ok "$NUMERO de volta ao $de"
}

evidencias() {
  desde="${1:?uso: evidencias <desde, ex. 2026-09-24T14:05:00Z> [pin]}"
  pin="${2:-}"
  echo "── evidencias desde $desde"
  LOG=$(docker logs --since "$desde" "$GW" 2>&1)
  sid=$(printf '%s\n' "$LOG" | grep -F "para $NUMERO virou contato" | sed -n 's/.*session=\([0-9a-f-]*\).*/\1/p' | tail -1)
  [ -n "$sid" ] || erro "nenhuma chamada para $NUMERO virou contato desde $desde"
  info "session=$sid"
  printf '%s\n' "$LOG" | grep -F "$sid" | grep -E "virou contato|linha na sala|voz: entrou|coleta mascarada|DESFEITO|chamador desligou|encerrada pela plataforma" \
    | sed -E 's/^([0-9-]+ [0-9:,]+) [A-Z]+ [^ ]+ — /\1  /' | cut -c1-220
  n409=$(printf '%s\n' "$LOG" | grep -E "/webrtc/token/$sid[^ ]* HTTP/[0-9.]+\" 409" | wc -l)
  n200=$(printf '%s\n' "$LOG" | grep -E "/webrtc/token/$sid[^ ]* HTTP/[0-9.]+\" 200" | wc -l)
  info "rota de token: $n409 resposta(s) 409 · $n200 resposta(s) 200"
  if [ -n "$pin" ]; then
    achou=""
    for c in "$GW" plughub-demo-orchestrator-bridge-1 plughub-demo-skill-flow-service-1 plughub-demo-mcp-server-plughub-1; do
      docker logs --since "$desde" "$c" 2>&1 | grep -qF "$pin" && achou="$achou $c"
    done
    no_stream=$(docker exec "$REDIS" redis-cli --raw xrange "session:$sid:stream" - + 2>/dev/null | grep -cF "$pin")
    [ -z "$achou" ] && ok "PIN ausente dos logs de gateway, bridge, motor e mcp-server" || echo "  ALERTA  PIN aparece em:$achou"
    [ "${no_stream:-0}" = 0 ] && ok "PIN ausente do stream da sessao" || echo "  ALERTA  PIN aparece $no_stream vez(es) no stream"
  fi
}

case "${1:-}" in
  preparar)   preparar ;;
  apontar)    apontar ;;
  devolver)   devolver ;;
  evidencias) shift; evidencias "$@" ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
