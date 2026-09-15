#!/usr/bin/env bash
# probe_agent_ws_credential.sh — 2026-09-15  (CAP-19)
#
# PERGUNTA: o WebSocket do agente humano (`/agent/ws`, e `/agent-ws` pela borda do platform-ui)
# só atende quem apresenta credencial de agente DAQUELE pool — e só lhe dá as sessões dele?
#
# O DEFEITO QUE O ORIGINOU (medido ao vivo em 2026-09-15, pela 5174, sem token nenhum)
#   · `?pool=&user_id=` inventados registravam instância `human-{user_id}` pronta, que RECEBIA
#     contato roteado;
#   · `?session_id=` de um contato alheio fazia o socket RECEBER os eventos de agente da sessão
#     e ESCREVER no stream canônico dela como `human_agent`/`primary`/`all` — falar ao cliente
#     como atendente sabendo só o id;
#   · e a 5174 é publicada em todas as interfaces: o `127.0.0.1:3100` da CAP-13 não cobria isto.
#
# RAMOS (no exercício): W1 sem credencial · W2 assinatura errada · W3 sem `agent_assist.atender`
#   · W4 atender em outro pool · W5 `sub` ≠ `user_id` · W6 CONTROLE POSITIVO (token do Console)
#   · W7 sessão alheia não lê nem escreve, com o controle W7b (sessão própria escreve) · W8 borda
#   sem credencial, com o controle W9 (borda COM credencial abre — o nginx repassa o subprotocolo).
#
# A credencial viaja no subprotocolo (`Sec-WebSocket-Protocol: plughub.bearer, <jwt>`): o browser
# não deixa pôr `Authorization` em WebSocket, e JWT em URL é proibido na casa (vai para log).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_agh01"
FALHA=0; INCONCL=0

echo "════════════════════════════════════════════════════════════════════"
echo " o /agent/ws so atende agente com credencial do pool?"
echo "════════════════════════════════════════════════════════════════════"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  echo "  INCONCL gateway fora do ar ou login do admin falhou"; echo " INCONCLUSIVO"; exit 2
fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
  curl -s -o /dev/null -X POST "${H[@]}" "$REG/v1/pools" \
    -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webchat\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture AGH-01 / CAP-19\"}"
  sleep 5
fi

ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(AUTH_JWT_SECRET|TENANT_ID)=' | sed 's/^/-e /' | tr '\n' ' ')
OUT=$(timeout "${EXERCISE_TIMEOUT_S:-240}" docker run --rm -i --network "$NET" --entrypoint python \
      $ENV -e POOL="$POOL" "$IMG" - < infra/test/_agent_ws_credential_exercise.py 2>&1)
[ $? -eq 124 ] && OUT="$OUT
FALHA TIMEOUT exercicio morto"

while IFS= read -r l; do
  case "$l" in
    OK\ *)      echo "  OK      ${l#OK }" ;;
    FALHA\ *)   echo "  FALHA   ${l#FALHA }"; FALHA=$((FALHA + 1)) ;;
    INCONCL\ *) echo "  INCONCL ${l#INCONCL }"; INCONCL=$((INCONCL + 1)) ;;
    "") ;;
    *)          echo "  INCONCL saida inesperada: $l"; INCONCL=$((INCONCL + 1)) ;;
  esac
done <<< "$OUT"
N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) (W[1-9]|W7b|LIMPEZA) ')
[ "$N" -ge 11 ] || { echo "  FALHA   exercicio emitiu $N de 11 veredictos"; FALHA=$((FALHA + 1)); }

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
