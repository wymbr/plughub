#!/usr/bin/env bash
# probe_webrtc_tts_spoken.sh — 2026-09-15  (VOZ-05, fatia 3)
#
# PERGUNTA: numa chamada WebRTC atendida por agente de IA, o cliente OUVE o que o agente diz —
# avisos e prompts de menu, na ordem —, e pode interromper falando (barge-in)?
#
# O QUE HAVIA (medido em 2026-09-15, antes de mexer)
#   · `webrtc_tts_injection_enabled` = false por default: a IA ganhava teto de áudio (VOZ-05
#     fatia 1 exige STT e TTS) e ficava MUDA, sem uma linha de log;
#   · ligado o flag, continuaria muda: o bot entra na sala OCULTO, e um participante oculto não
#     entrega trilha a ninguém — medido com o SDK contra o SFU: 0,00 s de áudio recebido, e o
#     cliente loga "received track from an unknown participant";
#   · a fala inteira ia num quadro só, sem fila: duas mensagens seguidas se sobreporiam, e não
#     havia como parar no meio;
#   · só `deliver_text` falava — prompt de menu nunca —, e falava QUALQUER texto, inclusive o
#     digitado por humano.
#
# RAMOS (exercício ao vivo — ver `_webrtc_tts_spoken_exercise.py`)
#   T1 ouve o agente · T2 prompt de menu falado · T3 ordem · C controle (fala inteira sem
#   interrupção) · T4 barge-in (para em ≤ 1,5 s, N2 < 60%, fim não ouvido) · T5 a fala volta
#   V  o gateway registrou a interrupção da sessão (a ausência de fala poderia ter outra causa)
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz05_tts"
SKILL="skill_probe_tts_v1"
FIXTURE="infra/test/fixtures/skill_probe_tts_v1.json"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " o cliente WebRTC ouve o agente de IA, e pode interrompe-lo?"
echo "════════════════════════════════════════════════════════════════════"

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then
  incon "login do admin falhou ou gateway fora do ar — nada medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_webrtc_tts_spoken (VOZ-05 fatia 3)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
  fi
  BODY=$(mktemp)
  PUB=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
  SN=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$POOL/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  PM=$(curl -s -o "$BODY" -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$POOL/promote" -d '{}')
  rm -f "$BODY"
  if [ "$PUB" != 200 ] && [ "$PUB" != 201 ] || [ "$SN" != 200 ] || [ "$PM" != 200 ]; then
    incon "fixture nao implantada (publish=$PUB set-next=$SN promote=$PM)"
  else
    INST=""
    for _ in $(seq 1 40); do
      INST=$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)
      [ -n "$INST" ] && break
      sleep 1
    done
    if [ -z "$INST" ]; then
      incon "nenhuma instancia de IA em $POOL 40 s depois do promote"
    else
      sleep 3
      ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
      T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      name="probe_voz05t_$$_$RANDOM"
      OUT=$(timeout "${EXERCISE_TIMEOUT_S:-300}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            -v "$PWD/$FIXTURE:/fixture.json:ro" $ENV -e POOL="$POOL" \
            "$IMG" - < infra/test/_webrtc_tts_spoken_exercise.py 2>&1)
      [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
      while IFS= read -r l; do
        case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
          INFO\ *) echo "  INFO    ${l#INFO }";; esac
      done <<< "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL|INFO) ')"
      N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) (T[1-5]|C) ')
      [ "$N" -ge 9 ] || falha "exercicio emitiu $N de 9 veredictos: $(printf '%s' "$OUT" | grep -vE '^(OK|FALHA|INCONCL|INFO|SID) ' | tail -3 | tr '\n' ' ' | cut -c1-300)"
      SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p')
      if [ -n "$SID" ]; then
        if docker logs --since "$T0" "$GW" 2>&1 | grep -q "fala INTERROMPIDA pelo cliente session=$SID"; then
          ok "V o gateway registrou o barge-in da sessao $SID"
        else
          falha "V nenhum barge-in registrado pelo gateway para $SID"
        fi
      fi
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
