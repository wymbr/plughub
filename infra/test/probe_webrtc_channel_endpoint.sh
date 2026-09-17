#!/usr/bin/env bash
# probe_webrtc_channel_endpoint.sh — 2026-09-17  (VOZ-26)
#
# PERGUNTA: um endpoint WebRTC cadastrado no registro ENDEREÇA a chamada — o contato que entra
# por `/ws/webrtc/{identificador}` cai no pool do endpoint, e trocar o pool atrás do endereço
# vale para a próxima chamada?
#
# O DEFEITO QUE O ORIGINOU
#   O gateway resolvia o identificador pela tabela `ChannelEndpoint` (`channel=webrtc`) antes de
#   tratá-lo como `pool_id`, mas o agent-registry RECUSAVA `channel=webrtc` no cadastro (400) e a
#   tela não oferecia o canal. A resolução era ramo morto e nada ficava vermelho: o lookup não
#   achava e o fallback usava o identificador como pool — a URL do widget carregava o pool cru.
#
# RAMOS
#   A  PARIDADE — os canais que a tela oferece (`ChannelEndpointChannel`) são os que o registro
#      aceita (`VALID_CHANNELS`). Tela que oferece o que o backend recusa é o defeito de origem.
#   B  AO VIVO, lido no LEITOR (`session:{sid}:meta.pool_id`, que bridge e routing leem):
#      R1 o registro aceita `channel=webrtc`
#      E1 o endereço cadastrado cai no pool do endpoint (identificador ≠ pool, logo só a
#         resolução explica o acerto)
#      E2 controle: o id do pool direto continua valendo (o fallback não foi quebrado)
#      E3 trocar o pool do endpoint vale na chamada seguinte ANTES do TTL do cache (30 s) — prova
#         que o `registry.changed` invalida; sem isso a troca só valeria depois do TTL
#
# FIXTURES: pools `probe_voz04_webrtc` (do probe de entrada) e `probe_voz26_webrtc_b`, criados se
# ausentes (pool não se apaga). O endpoint é criado e APAGADO pelo probe.
#
# EXIT: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL_A="probe_voz04_webrtc"
POOL_B="probe_voz26_webrtc_b"
ALIAS="probe-voz26-$RANDOM$RANDOM"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " um endpoint WebRTC cadastrado enderessa a chamada?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · PARIDADE tela × registro ───────────────────────────────────────"
A_OUT=$(python3 - <<'PYEOF'
import io, re
def src(p): return io.open(p, encoding="utf-8").read()
reg = re.search(r'const VALID_CHANNELS = new Set\(\[([^\]]*)\]\)', src("packages/agent-registry/src/routes/channel-endpoints.ts"))
ui = re.search(r"export type ChannelEndpointChannel = ([^\n]*)", src("packages/platform-ui/src/types/index.ts"))
if not reg or not ui:
    print("INCONCL A1 nao achei VALID_CHANNELS ou ChannelEndpointChannel")
else:
    r = set(re.findall(r'"(\w+)"', reg.group(1)))
    u = set(re.findall(r"'(\w+)'", ui.group(1)))
    print(("OK" if r == u and "webrtc" in r else "FALHA") +
          " A1 registro=%s tela=%s (so no registro: %s, so na tela: %s)" % (sorted(r), sorted(u), sorted(r - u), sorted(u - r)))
PYEOF
)
while IFS= read -r l; do
  case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; *) incon "${l#INCONCL }";; esac
done <<< "$A_OUT"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO (registro + gateway) ───────────────────────────────────"
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  incon "B: gateway fora do ar ou login do admin falhou — nao medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  for P in "$POOL_A" "$POOL_B"; do
    st=$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$P")
    if [ "$st" = 404 ]; then
      st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$P\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture de probe WebRTC (VOZ-04/VOZ-26)\",\"media_policy\":{\"customer_publish\":[\"audio\",\"video\"],\"agent_publish\":[\"audio\",\"video\"]}}")
      [ "$st" = 201 ] || incon "fixture $P nao criada (http $st)"
      sleep 5
    fi
  done

  EP_ID=""
  limpa() {
    [ -n "$EP_ID" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
    EP_ID=""
  }
  trap limpa EXIT INT TERM

  RESP=$(curl -s -w '\n%{http_code}' -X POST "${H[@]}" "$REG/v1/channel-endpoints" \
    -d "{\"channel\":\"webrtc\",\"identifier\":\"$ALIAS\",\"pool_id\":\"$POOL_A\",\"display_name\":\"probe VOZ-26\"}")
  st=$(printf '%s' "$RESP" | tail -1)
  EP_ID=$(printf '%s' "$RESP" | sed '$d' | jq -r '.id // empty' 2>/dev/null)
  if [ "$st" = 201 ] && [ -n "$EP_ID" ]; then
    ok "R1 registro aceitou channel=webrtc ($ALIAS → $POOL_A)"
  else
    falha "R1 registro recusou channel=webrtc: http $st $(printf '%s' "$RESP" | sed '$d' | cut -c1-200)"
  fi

  ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(JWT_SECRET|TENANT_ID|REDIS_URL)=' | sed 's/^/-e /' | tr '\n' ' ')
  chamada() {   # $1 = identificador → imprime o pool gravado, ou "?<motivo>"
    local out
    out=$(timeout 60 docker run --rm -i --network "$NET" --entrypoint python $ENV -e IDENT="$1" "$IMG" - \
          < infra/test/_webrtc_channel_endpoint_exercise.py 2>&1)
    local p; p=$(printf '%s\n' "$out" | sed -n "s/^POOL $1 //p" | head -1)
    if [ -n "$p" ]; then echo "$p"; else echo "?$(printf '%s' "$out" | tail -2 | tr '\n' ' ' | cut -c1-200)"; fi
  }

  if [ -z "$EP_ID" ]; then
    incon "E1-E3: sem endpoint cadastrado, a resolucao nao pode ser medida"
  else
    sleep 3   # registry.changed(created) chega ao gateway
    got=$(chamada "$ALIAS"); T1=$(date +%s)
    case "$got" in
      "$POOL_A") ok "E1 /ws/webrtc/$ALIAS caiu no pool do endpoint ($got)";;
      \?*)       incon "E1 chamada nao mediu: ${got#?}";;
      *)         falha "E1 /ws/webrtc/$ALIAS caiu em '$got', esperado '$POOL_A' — o endpoint nao endereca";;
    esac

    got=$(chamada "$POOL_A")
    case "$got" in
      "$POOL_A") ok "E2 controle: o id do pool direto continua valendo ($got)";;
      \?*)       incon "E2 chamada nao mediu: ${got#?}";;
      *)         falha "E2 /ws/webrtc/$POOL_A caiu em '$got' — o fallback por pool quebrou";;
    esac

    st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID" -d "{\"pool_id\":\"$POOL_B\"}")
    sleep 3
    got=$(chamada "$ALIAS"); DT=$(( $(date +%s) - T1 ))
    if [ "$st" != 200 ]; then
      incon "E3 troca de pool recusada (http $st)"
    elif [ "$DT" -ge 28 ]; then
      incon "E3 a chamada saiu ${DT}s depois da primeira — alem do TTL do cache, nao discrimina a invalidacao"
    else
      case "$got" in
        "$POOL_B") ok "E3 trocar o pool vale na chamada seguinte, ${DT}s depois (antes do TTL): $got";;
        \?*)       incon "E3 chamada nao mediu: ${got#?}";;
        *)         falha "E3 depois da troca o endereco ainda caiu em '$got' (esperado '$POOL_B', ${DT}s) — registry.changed nao invalidou";;
      esac
    fi
  fi
  limpa
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
