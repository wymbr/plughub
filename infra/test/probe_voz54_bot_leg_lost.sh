#!/usr/bin/env bash
# probe_voz54_bot_leg_lost.sh — VOZ-54: o bot leg que CAI no meio da chamada é DITO.
#
# PROPOSIÇÃO: com a config convertendo (STT e TTS de pé), o serviço de fala que passa a FALHAR numa
# chamada vira `media.degraded` com motivo `bot_leg_lost` (agentes) e aviso ao cliente
# (`system_notice`), e a chamada sem falha não diz nada.
#
# Como a falha é provocada SEM derrubar o `speaches` compartilhado: um perfil de fala do tenant
# (`probe-voz54`) aponta o STT para um modelo que o serviço não tem, e um endpoint webrtc próprio
# usa o perfil — só as chamadas por esse endereço falham (o serviço responde 404 a cada frase).
# A config do gateway segue dizendo que converte, que é exatamente o caso que a VOZ-11 não via.
# O perfil é gravado pela porta CRUA do config-api, que não confere o modelo (dívida VOZ-29).
#
# Fixture: a do probe_webrtc_speech_tuning (pool `probe_voz18_speech`, IA de áudio, menus por fala)
# e o exercício `_webrtc_speech_tuning_exercise.py` (cliente LiveKit falando de verdade).
#
#   K0 o tenant não tem perfil `probe-voz54` (não sobrescreve config real)
#   L1 chamada pelo endereço com modelo inexistente → `media.degraded` `bot_leg_lost`, áudio, para a IA
#   L2 … e o cliente foi avisado (`system_notice` no stream)
#   L3 o log do gateway diz que o serviço de fala CAIU, nomeando a sessão
#   C1 CONTROLE chamada pelo pool (modelo real) → nenhum `bot_leg_lost` — senão L1 não discrimina
#
# A VOLTA (`media.restored` + aviso) não é medida aqui: o perfil da chamada é resolvido uma vez por
# sessão, então o modelo não "volta" no meio dela. Ela é medida no unitário
# (`test_voz54_bot_leg_lost.py`), e a recuperação real do serviço é o mesmo caminho de código.
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq).
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz18_speech"
SKILL="skill_probe_speech_tuning_v1"
FIXTURE="infra/test/fixtures/skill_probe_speech_tuning_v1.json"
PERFIL="probe-voz54"
ALIAS="probe-voz54-$RANDOM$RANDOM"
MODELO_FALSO="probe/modelo-inexistente-voz54"
FALHA=0; INCONCL=0; EP_ID=""; MEXEU=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
fim() {
  echo "────────────────────────────────────────────────────────────────────"
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
  echo " VERDE"; exit 0
}

echo "════════════════════════════════════════════════════════════════════"
echo " o servico de fala que CAI no meio da chamada e dito?"
echo "════════════════════════════════════════════════════════════════════"
command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

PERFIS=$(curl -s --max-time 10 "$CFG/config/speech_profiles?tenant_id=$TENANT")
if [ "$(printf '%s' "$PERFIS" | jq --arg p "$PERFIL" '(.entries // {}) | has($p)')" = true ]; then
  incon "K0 ja existe um perfil $PERFIL no tenant — o probe nao sobrescreve config real"; fim
fi
ok "K0 sem perfil $PERFIL no tenant"

limpa() {
  [ -n "$EP_ID" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
  EP_ID=""
  [ "$MEXEU" = 1 ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$CFG/config/speech_profiles/$PERFIL?tenant_id=$TENANT"
  MEXEU=0
}
trap limpa EXIT INT TERM

if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
  curl -s -o /dev/null -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_webrtc_speech_tuning (VOZ-18)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}"
fi
PUB=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
SN=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$POOL/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
PM=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$POOL/promote" -d '{}')
if { [ "$PUB" != 200 ] && [ "$PUB" != 201 ]; } || [ "$SN" != 200 ] || [ "$PM" != 200 ]; then
  incon "fixture nao implantada (publish=$PUB set-next=$SN promote=$PM)"; fim
fi
for _ in $(seq 1 40); do
  [ -n "$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)" ] && break
  sleep 1
done
sleep 3
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')

chamada() {  # $1 = endereco; imprime o SID
  local out
  out=$(timeout 400 docker run --rm -i --name "probe_voz54_$$_$RANDOM" --network "$NET" --entrypoint python \
        $ENV -e POOL="$1" "$IMG" - < infra/test/_webrtc_speech_tuning_exercise.py 2>&1)
  printf '%s\n' "$out" | sed -n 's/^SID //p' | head -1
}
eventos() {  # $1 = SID, $2 = tipo → payloads JSON, um por linha
  docker exec "$REDIS" redis-cli --raw XRANGE "session:$1:stream" - + 2>/dev/null \
    | awk -v t="$2" 'prev=="type" && $0==t {achou=1} {if (achou && prev=="payload") {print; achou=0}} {prev=$0}'
}

# ── L: chamada pelo endereço com o modelo inexistente ──
MEXEU=1
st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$CFG/config/speech_profiles/$PERFIL" \
  -d "{\"value\":{\"description\":\"probe VOZ-54 — STT com modelo que o servico nao tem\",\"stt_model\":\"$MODELO_FALSO\"},\"tenant_id\":\"$TENANT\"}")
[ "$st" = 200 ] || { incon "perfil nao gravado (http $st)"; fim; }
RESP=$(curl -s -w '\n%{http_code}' -X POST "${H[@]}" "$REG/v1/channel-endpoints" \
  -d "{\"channel\":\"webrtc\",\"identifier\":\"$ALIAS\",\"pool_id\":\"$POOL\",\"display_name\":\"probe VOZ-54\",\"settings\":{\"speech_profile_id\":\"$PERFIL\"}}")
EP_ID=$(printf '%s' "$RESP" | sed '$d' | jq -r '.id // empty' 2>/dev/null)
[ -n "$EP_ID" ] || { incon "endpoint $ALIAS nao cadastrado: $(printf '%s' "$RESP" | tr '\n' ' ' | cut -c1-200)"; fim; }
sleep 4   # o config.changed do perfil chega ao gateway

SID1=$(chamada "$ALIAS")
[ -n "$SID1" ] || { incon "L a chamada pelo alias nao abriu sessao"; fim; }
echo "  INFO    chamada com modelo inexistente: $SID1"
D1=$(eventos "$SID1" media.degraded | jq -c 'select(.reason == "bot_leg_lost")' 2>/dev/null)
if [ -z "$D1" ]; then
  n_err=$(docker logs --since 10m "$GW" 2>&1 | grep -c "speaches STT: http .*$MODELO_FALSO")
  if [ "${n_err:-0}" -lt 2 ]; then
    incon "L1 o STT nao falhou ao menos 2 vezes na chamada ($n_err) — o servico nem foi chamado"
  else
    falha "L1 o STT falhou $n_err vezes e nenhum media.degraded bot_leg_lost foi ao stream"
  fi
else
  printf '%s' "$D1" | jq -e 'select(.kind == "audio" and .direction == "to_attendant")' >/dev/null \
    && ok "L1 media.degraded bot_leg_lost no stream: $(printf '%s' "$D1" | head -1)" \
    || falha "L1 bot_leg_lost com forma inesperada: $D1"
fi
N1=$(eventos "$SID1" system_notice | grep -c "Sua voz n")
[ "${N1:-0}" -ge 1 ] && ok "L2 o cliente foi avisado (system_notice: sua voz nao e recebida)" \
                     || falha "L2 nenhum aviso ao cliente no stream"
docker logs --since 10m "$GW" 2>&1 | grep -q "servico de fala CAIU .*session=$SID1" \
  && ok "L3 o log diz que o servico de fala caiu, com a sessao" \
  || falha "L3 nenhuma linha 'servico de fala CAIU' para $SID1"

limpa

# ── C1: CONTROLE — chamada pelo pool, modelo real ──
SID2=$(chamada "$POOL")
if [ -z "$SID2" ]; then incon "C1 a chamada de controle nao abriu sessao"
else
  D2=$(eventos "$SID2" media.degraded | jq -c 'select(.reason == "bot_leg_lost")' 2>/dev/null)
  T2=$(docker exec "$REDIS" redis-cli --raw XRANGE "session:$SID2:stream" - + 2>/dev/null | grep -c '"speech"')
  if [ "${T2:-0}" -lt 1 ]; then incon "C1 a chamada de controle nao transcreveu nada — nao discrimina"
  elif [ -n "$D2" ]; then falha "C1 a chamada com modelo real disse bot_leg_lost: $D2"
  else ok "C1 CONTROLE a chamada pelo pool transcreveu ($T2) e nao disse bot_leg_lost"; fi
fi
fim
