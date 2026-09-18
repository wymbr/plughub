#!/usr/bin/env bash
# probe_voz02_sip_inbound.sh — 2026-09-18  (VOZ-02, fatia 1)
#
# PERGUNTA: uma chamada TELEFÔNICA que entra pelo tronco SIP vira contato `voice` da plataforma, é
# atendida pela IA do pool do número discado, a IA fala e ouve pelo telefone, e a chamada termina
# pelos DOIS lados — e número sem endpoint é RECUSADO, nunca mandado a um pool que ninguém escolheu?
#
# O ESTADO QUE O ORIGINOU: o canal `voice` só existia como TwiML (Twilio). Um tronco SIP — o que
# operadora brasileira entrega — não tinha por onde entrar, e a fala da plataforma (bot leg, STT,
# TTS, coleta por voz) só existia para a chamada de browser. A fatia 1 põe o serviço SIP do próprio
# SFU (livekit/sip) na frente: a chamada vira participante de uma sala, e o gateway a adota.
#
# O "telefone" é um cliente SIP mínimo (`_sip_ua.py`, G.711 + digest) — nenhum provedor de telecom
# é necessário para medir, e é isso que torna o gate repetível.
#
# RAMOS (pool, skill e endpoint de fixture; o endpoint é APAGADO no fim):
#   K0 o número do tronco de demo não tem endpoint `voice` apontando para outro pool (senão NÃO mexe)
#   S0 CONTROLE do instrumento: a frase do chamador é sintetizada
#   S1 a chamada é ATENDIDA (200 OK)
#   S2 nasce um contato `voice` do chamador (ANI), no pool do número (DNIS)
#   S3 o chamador OUVE a IA: energia no áudio G.711 que volta
#   S4 a fala do chamador chega ao fluxo: o menu por voz registra `sip-m0=atendente`
#   K1 (VOZ-31) o serviço SIP aceita `telephone-event` na negociação
#   K2 (VOZ-31) teclas FORA de banda (RFC 4733) respondem o menu de teclado (`sip-m1=<código>`)
#   K3 (NIV-07) PIN MASCARADO pelo telefone É coletado por tecla e chega ao fluxo, sem valor no
#      stream; K3p o gateway diz que pausou a mídia e quem tirou da sala; K3g o PIN não aparece
#      nos logs do gateway, do bridge, do motor nem do mcp-server
#   K4 (NIV-07) um "humano" na sala antes do bloco é tirado dela e não vê o PIN (a tecla SIP chega
#      a TODOS na sala — medido —, e é por isso que ele sai)
#   S5 o fluxo encerra e a PLATAFORMA derruba a chamada (BYE no telefone)
#   S6 o contato da chamada 1 fechou pela plataforma (log do gateway)
#   H1 2ª chamada, o CHAMADOR desliga: o contato fecha como `customer_hangup`
#   B1 (VOZ-31, INFO) 3ª chamada SEM `telephone-event`, tecla como TOM no áudio — caracterização do
#      conversor, entrada do controle (1) da NIV-07; não é veredicto
#   R1 endpoint apagado: a chamada NÃO é atendida e nenhum contato nasce
#   INFO codec da trilha do chamador na sala
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
SIPC="${SIP_CONTAINER:-plughub-demo-livekit-sip-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
# Pool SÓ `voice`: desde a NIV-07 `voice` declara `masked_input`, e o deploy do menu mascarado
# neste pool é a prova de que o registry passou a aceitar (na VOZ-31 ele recusava, medido:
# `masked_sem_canal_capaz`). O pool misto da VOZ-31 fica no banco, sem uso.
POOL="probe_voz02_sip"
SKILL="skill_probe_sip_inbound_v1"
FIXTURE="infra/test/fixtures/skill_probe_sip_inbound_v1.json"
DNIS="${SIP_DNIS:-+551140000000}"
SIP_PASS="${SIP_TRUNK_PASSWORD_DEMO:-changeme_sip_trunk_demo}"
ANI="+5511$(( 90000000 + RANDOM * 30 + RANDOM % 30 ))0"
FALHA=0
INCONCL=0
EP_ID=""

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
info()  { echo "  INFO    $*"; }
fim() {
  echo "────────────────────────────────────────────────────────────────────"
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
  echo " VERDE"; exit 0
}

echo "════════════════════════════════════════════════════════════════════"
echo " a chamada telefonica pelo tronco SIP vira contato voice, e termina?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
if [ "$(docker inspect -f '{{.State.Running}}' "$SIPC" 2>/dev/null)" != true ]; then
  incon "servico SIP do SFU ($SIPC) fora do ar — nada medido"; fim
fi
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

# ── K0 ──
EXIST=$(curl -s --max-time 10 "${H[@]}" "$REG/v1/channel-endpoints?channel=voice" \
  | jq -c --arg d "$DNIS" '[(.items // .endpoints // . // [])[] | select(.identifier == $d)] | .[0] // empty' 2>/dev/null)
if [ -n "$EXIST" ] && [ "$(printf '%s' "$EXIST" | jq -r '.pool_id')" != "$POOL" ]; then
  incon "K0 $DNIS ja tem endpoint voice -> $(printf '%s' "$EXIST" | jq -r '.pool_id') — o probe nao sobrescreve config real"; fim
fi
[ -n "$EXIST" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$(printf '%s' "$EXIST" | jq -r '.id')"
ok "K0 $DNIS livre de endpoint real (sobra de probe anterior removida: $([ -n "$EXIST" ] && echo sim || echo nao))"

limpa() {
  [ -n "$EP_ID" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
  EP_ID=""
}
trap limpa EXIT INT TERM

# ── fixture ──
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
  st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"voice\",\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_voz02_sip_inbound (VOZ-02/VOZ-31): pool misto browser+telefone\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
  [ "$st" = 201 ] || { incon "pool de fixture nao criado (http $st)"; fim; }
fi
PUB=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
SN=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$POOL/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
PM=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$POOL/promote" -d '{}')
if { [ "$PUB" != 200 ] && [ "$PUB" != 201 ]; } || [ "$SN" != 200 ] || [ "$PM" != 200 ]; then
  incon "fixture nao implantada (publish=$PUB set-next=$SN promote=$PM)"; fim
fi
RESP=$(curl -s -w '\n%{http_code}' -X POST "${H[@]}" "$REG/v1/channel-endpoints" \
  -d "{\"channel\":\"voice\",\"identifier\":\"$DNIS\",\"pool_id\":\"$POOL\",\"display_name\":\"probe VOZ-02\"}")
EP_ID=$(printf '%s' "$RESP" | sed '$d' | jq -r '.id // empty' 2>/dev/null)
[ -n "$EP_ID" ] || { incon "endpoint voice $DNIS nao cadastrado: $(printf '%s' "$RESP" | tr '\n' ' ' | cut -c1-200)"; fim; }
for _ in $(seq 1 40); do
  [ -n "$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)" ] && break
  sleep 1
done
sleep 3
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')

exercicio() {  # $1 = MODE, $2 = ANI → saida crua do exercicio
  timeout 720 docker run --rm -i --name "probe_voz02_$$_$RANDOM" --network "$NET" --entrypoint python \
    -v "$PWD/infra/test:/t:ro" -w /t $ENV -e MODE="$1" -e DNIS="$DNIS" -e ANI="$2" -e POOL="$POOL" \
    -e SIP_PASS="$SIP_PASS" "$IMG" /t/_sip_inbound_exercise.py 2>&1
}

# ── chamada 1 e 2 ──
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
OUT=$(exercicio atende "$ANI")
printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL|INFO) ' | grep -v '^INFO UA ' | while read -r st ramo resto; do
  case "$st" in
    OK) echo "  OK      $ramo $resto" ;; FALHA) echo "  FALHA   $ramo $resto" ;;
    INCONCL) echo "  INCONCL $ramo $resto" ;; *) echo "  INFO    $ramo $resto" ;;
  esac
done
FALHA=$((FALHA + $(printf '%s\n' "$OUT" | grep -c '^FALHA ')))
INCONCL=$((INCONCL + $(printf '%s\n' "$OUT" | grep -c '^INCONCL ')))
if [ "$(printf '%s\n' "$OUT" | grep -c '^\(OK\|FALHA\|INCONCL\) ')" = 0 ]; then
  incon "o exercicio nao produziu veredicto: $(printf '%s\n' "$OUT" | tail -5 | tr '\n' ' ' | cut -c1-400)"
fi
SID1=$(printf '%s\n' "$OUT" | sed -n 's/^SID1 //p' | head -1)
PIN=$(printf '%s\n' "$OUT" | sed -n 's/^PIN //p' | head -1)
if [ -n "$SID1" ]; then
  PAUSA=$(docker logs --since "$T0" "$GW" 2>&1 | grep "pausa de midia no menu" | grep "session=$SID1" | tail -1)
  case "$PAUSA" in
    *agent-probe-humano*) ok "K3p o gateway pausou a midia e nomeou quem tirou da sala: ${PAUSA##*— }" ;;
    "") falha "K3p nenhuma linha de pausa de midia para $SID1 no gateway" ;;
    *) falha "K3p a pausa nao tirou o 'humano' de teste: ${PAUSA##*— }" ;;
  esac
fi
if [ -n "$PIN" ]; then
  # fronteira de dígito: o ANI do chamador, que o log cita, pode conter a mesma sequência
  VAZ=""
  for c in "$GW" plughub-demo-orchestrator-bridge-1 plughub-demo-skill-flow-service-1 plughub-demo-mcp-server-plughub-1; do
    n=$(docker logs --since "$T0" "$c" 2>&1 | grep -cE "(^|[^0-9])$PIN([^0-9]|$)")
    [ "${n:-0}" != 0 ] && VAZ="$VAZ ${c#plughub-demo-}=$n"
  done
  [ -z "$VAZ" ] && ok "K3g o PIN nao aparece nos logs do gateway, bridge, motor e mcp-server" \
                || falha "K3g o PIN aparece em log:$VAZ — VAZOU"
fi
SID2=$(printf '%s\n' "$OUT" | sed -n 's/^SID2 //p' | head -1)

fechou() {  # $1 = SID, $2 = reason → espera a linha de fechamento no log do gateway
  for _ in $(seq 1 20); do
    docker logs --since "$T0" "$GW" 2>&1 | grep "contact_closed" | grep "$1" | grep -q "$2" && return 0
    docker logs --since "$T0" "$GW" 2>&1 | grep "session=$1" | grep -qi "fechad.*$2\|$2.*session" && return 0
    sleep 1
  done
  return 1
}
if [ -n "$SID1" ]; then
  if fechou "$SID1" agent_done; then ok "S6 o contato $SID1 fechou pela plataforma (agent_done)"
  else falha "S6 nenhuma linha de fechamento agent_done para $SID1 no log do gateway"; fi
fi
if [ -n "$SID2" ]; then
  if fechou "$SID2" customer_hangup; then ok "H1 o chamador desligou e o contato $SID2 fechou como customer_hangup"
  else falha "H1 o chamador desligou e $SID2 nao fechou como customer_hangup: $(docker logs --since "$T0" "$GW" 2>&1 | grep "$SID2" | tail -2 | tr '\n' ' ' | cut -c1-300)"; fi
elif printf '%s\n' "$OUT" | grep -q '^INFO H1 '; then
  falha "H1 a 2a chamada foi atendida mas nenhum contato do chamador apareceu"
fi

# ── recusa ──
curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
EP_ID=""
sleep 35   # o cache de endpoint do gateway vive 30 s — a recusa tem de ser lida do REGISTRO
T1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
OUTR=$(exercicio recusa "${ANI%?}7")
printf '%s\n' "$OUTR" | grep -E '^(OK|FALHA|INCONCL) ' | sed 's/^OK /  OK      /; s/^FALHA /  FALHA   /; s/^INCONCL /  INCONCL /'
FALHA=$((FALHA + $(printf '%s\n' "$OUTR" | grep -c '^FALHA ')))
[ "$(printf '%s\n' "$OUTR" | grep -c '^\(OK\|FALHA\) R1')" = 1 ] || incon "R1 sem veredicto: $(printf '%s\n' "$OUTR" | tail -3 | tr '\n' ' ' | cut -c1-300)"
MOT=$(docker logs --since "$T1" "$GW" 2>&1 | grep -c "RECUSADA")
[ "$MOT" -ge 1 ] && info "R1 o gateway nomeou a recusa: $(docker logs --since "$T1" "$GW" 2>&1 | grep RECUSADA | tail -1 | sed 's/.*RECUSADA/RECUSADA/' | cut -c1-160)" \
                 || falha "R1 a chamada sem endpoint nao deixou linha RECUSADA no gateway — recusa muda"
fim
