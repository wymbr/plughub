#!/usr/bin/env bash
# probe_webrtc_human_transcript.sh — 2026-09-16  (VOZ-05 fatia 4 · ADR adr-voice-media-plane V-F2)
#
# PERGUNTA: numa chamada WebRTC atendida por HUMANO, a conversa FALADA chega à sessão — cliente e
# atendente, cada um no seu canal, como mensagem do falante marcada como fala — sem aparecer como
# texto digitado para quem já a ouviu?
#
# O QUE HAVIA: o bot leg só entrava com agente de IA; chamada de humano não era transcrita, e o
# estado de mídia dizia "transcricao de chamada com humano ainda sem destino". O único destino que
# o pipeline conhecia era a mensagem de chat do cliente, e o bridge descartava inbound de qualquer
# outro autor e gravava `content_type: "text"` fixo — a marca `audio_transcript` se perdia.
#
# DECISÕES DO DONO (2026-09-16): transcrição = mensagem de texto dos participantes reais, com marca
# de origem; só a frase final sai do gateway; a fala do cliente não vai ao Console do humano; a
# visibilidade é `all`, nunca reenviada ao cliente; a fala do humano segue o caminho da mensagem
# digitada (`message_sent`), que acorda os steps `receive`.
#
# RAMOS (no exercício): H0 controle do speaches · H1 ouvinte oculto e mudo, sem voz · H2 fala do
# cliente no stream · H3 fala do humano no stream · H4 canais separados · H5 Console sem a fala e
# com o digitado · H6 cliente sem a fala do humano e com o digitado · H7 digitado fica `text` ·
# H8 ClickHouse com as duas marcas e o digitado `text`.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_voz05_humano"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
tally() {
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      SID\ *) ;; INFO\ *) echo "  INFO    ${l#INFO }";; *) [ -n "$l" ] && incon "saida inesperada: $l";; esac
  done <<< "$1"
}

echo "════════════════════════════════════════════════════════════════════"
echo " chamada de humano: a conversa falada chega a sessao, por falante?"
echo "════════════════════════════════════════════════════════════════════"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  incon "gateway fora do ar ou login do admin falhou — nao medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  POLICY='{"customer_publish":["audio"],"agent_publish":["audio"]}'
  st=$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")
  if [ "$st" = 404 ]; then
    curl -s -o /dev/null -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture VOZ-05 fatia 4\",\"media_policy\":$POLICY}"
    sleep 5
  fi
  curl -s -o /dev/null -X PUT "${H[@]}" "$REG/v1/pools/$POOL" -d "{\"media_policy\":$POLICY}"
  ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(JWT_SECRET|AUTH_JWT_SECRET|TENANT_ID|WEBRTC_LIVEKIT_URL|WEBRTC_LIVEKIT_API_KEY|WEBRTC_LIVEKIT_API_SECRET|WEBRTC_SPEACHES_URL|WEBRTC_STT_MODEL|WEBRTC_TTS_MODEL|WEBRTC_TTS_VOICE)=' | sed 's/^/-e /' | tr '\n' ' ')
  name="probe_voz05h_$$_$RANDOM"
  OUT=$(timeout "${EXERCISE_TIMEOUT_S:-300}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
          $ENV -e POOL="$POOL" "$IMG" - < infra/test/_webrtc_human_transcript_exercise.py 2>&1)
  [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto apos ${EXERCISE_TIMEOUT_S:-300}s"; }
  tally "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL|SID|INFO) ')"
  N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA|INCONCL) (H[0-8]|LIMPEZA) ')
  [ "$N" -ge 10 ] || falha "exercicio emitiu $N de 10 veredictos: $(printf '%s' "$OUT" | tail -4 | tr '\n' ' ' | cut -c1-300)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
