#!/usr/bin/env bash
# probe_agent_ws_ghost_instance.sh — 2026-09-15  (AGH-01)
#
# PERGUNTA: quando um agente humano sai do Console, a instância dele sai do pool — mesmo que
# OUTRO agente entre no mesmo pool logo em seguida?
#
# O DEFEITO QUE O ORIGINOU (vermelho ao vivo antes do conserto)
#   Ao fechar o `/agent/ws`, o mcp-server agenda o `unregisterHumanAgent` para dali a 2,5 s e
#   guardava o timer num mapa chaveado SÓ pelo pool. Qualquer conexão nova ao mesmo pool dentro
#   da janela fazia `clearTimeout` nele — inclusive de outro usuário. O agente que saiu ficava
#   `ready` no pool, sem socket e sem TTL, e RECEBIA CONTATO. Achado quando um contato do widget
#   foi atribuído a uma instância assim, com o agente conectado sem receber nada. Em produção é a
#   troca de turno.
#
# RAMOS (no exercício): C1 controle — sair sozinho desregistra · G1 A sai e B entra na janela →
#   A sai, B fica · G2 o próprio A volta na janela (F5) → continua · LIMPEZA pelo produto.
#
# Pool de fixture próprio (`probe_agh01`), para não disputar contato com um Console aberto.
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
echo " a saida de um agente sobrevive a entrada de outro no mesmo pool?"
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
    -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webchat\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture AGH-01\"}"
  sleep 5
fi

# O exercício cunha, por usuário, a mesma credencial do Console (CAP-19).
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_AUTH_JWT_SECRET=' | sed 's/^/-e /' | tr '\n' ' ')
OUT=$(timeout "${EXERCISE_TIMEOUT_S:-150}" docker run --rm -i --network "$NET" --entrypoint python \
      $ENV -e POOL="$POOL" -e TENANT="$TENANT" "$IMG" - < infra/test/_agent_ws_ghost_exercise.py 2>&1)
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
N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) (C1|G1|G2|LIMPEZA) ')
[ "$N" -ge 4 ] || { echo "  FALHA   exercicio emitiu $N de 4 veredictos"; FALHA=$((FALHA + 1)); }

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
