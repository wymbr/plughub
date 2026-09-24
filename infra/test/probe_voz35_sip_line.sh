#!/usr/bin/env bash
# probe_voz35_sip_line.sh — 2026-09-23  (VOZ-35)
#
# PERGUNTA: uma chamada telefonica para um pool SEM agente de IA de audio (pool HUMANO, fila sem
# ninguem) e ATENDIDA de imediato e fica com a linha VIVA (RTP continuo) enquanto espera?
#
# O ESTADO QUE O ORIGINOU (medido em 2026-09-23, antes do conserto): o servico SIP do SFU so atende
# quando ha trilha na sala para assinar, e so o agente de IA publicava. Chamada para pool humano
# ficava TOCANDO (180) por 60 s e caia com 486 — contada como `customer_hangup`, abandono do
# cliente, com a espera de 60 018 ms na fila. Esse mesmo alvo e o controle negativo deste probe: o
# codigo antigo sai VERMELHO em L1.
#
# O conserto: a LINHA (`linha-{sid}`), participante da plataforma que entra em toda chamada SIP no
# nascimento e publica uma trilha muda. Silencio e o piso (decisao do dono); conteudo na espera e
# de um agente de fila.
#
# RAMOS (pool de fixture; o endpoint e APAGADO no fim):
#   K0 o numero do tronco de demo nao tem endpoint `voice` apontando para pool real (senao NAO mexe)
#   C0 CONTROLE: nenhuma VOZ de IA entrou na sala — senao quem atendeu nao foi a linha (INCONCL)
#   L1 a chamada e ATENDIDA (200 OK)
#   L2 atendida em <= 3 s (antes: nunca; 486 aos 60 s)
#   L3 RTP continuo calado: >= 90% dos pacotes de 20 ms e nenhum buraco >= 1 s
#   L4 o gateway diz que a linha entrou, e o contato fecha como `customer_hangup` quando o chamador sai
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
POOL="probe_voz35_humano"
DNIS="${SIP_DNIS:-+551140000000}"
DURACAO="${DURACAO:-20}"
SEEDC="${SEED_CONTAINER:-plughub-demo-sip-seed-1}"
SIP_PASS="${SIP_TRUNK_PASSWORD_DEMO:-$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$SEEDC" 2>/dev/null | sed -n 's/^SIP_TRUNK_PASSWORD_DEMO=//p' | head -1)}"
ANI="+5511$(( 90000000 + RANDOM * 30 + RANDOM % 30 ))5"
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
echo " chamada para pool sem IA de audio e atendida, com a linha viva?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
if [ "$(docker inspect -f '{{.State.Running}}' "$SIPC" 2>/dev/null)" != true ]; then
  incon "servico SIP do SFU ($SIPC) fora do ar — nada medido"; fim
fi
[ -n "$SIP_PASS" ] || { incon "senha do tronco de demo nao lida do $SEEDC — nada medido"; fim; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

# ── K0 ──
EXIST=$(curl -s --max-time 10 "${H[@]}" "$REG/v1/channel-endpoints?channel=voice" \
  | jq -c --arg d "$DNIS" '[(.items // .endpoints // . // [])[] | select(.identifier == $d)] | .[0] // empty' 2>/dev/null)
if [ -n "$EXIST" ]; then
  DONO=$(printf '%s' "$EXIST" | jq -r '.pool_id')
  case "$DONO" in
    probe_*) curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$(printf '%s' "$EXIST" | jq -r '.id')"
             ok "K0 $DNIS livre (sobra de probe removida: $DONO)" ;;
    *) incon "K0 $DNIS ja tem endpoint voice -> $DONO — o probe nao sobrescreve config real"; fim ;;
  esac
else
  ok "K0 $DNIS livre de endpoint"
fi

limpa() {
  [ -n "$EP_ID" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
  EP_ID=""
}
trap limpa EXIT INT TERM

# ── fixture: pool HUMANO de voz, sem fila e sem ninguem ──
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
  st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"human\",\"channel_types\":[\"voice\"],\"sla_target_ms\":60000,\"description\":\"fixture do probe_voz35_sip_line: pool HUMANO de voz sem atendente e sem fila\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
  [ "$st" = 201 ] || { incon "pool de fixture nao criado (http $st)"; fim; }
fi
RESP=$(curl -s -w '\n%{http_code}' -X POST "${H[@]}" "$REG/v1/channel-endpoints" \
  -d "{\"channel\":\"voice\",\"identifier\":\"$DNIS\",\"pool_id\":\"$POOL\",\"display_name\":\"probe VOZ-35\"}")
EP_ID=$(printf '%s' "$RESP" | sed '$d' | jq -r '.id // empty' 2>/dev/null)
[ -n "$EP_ID" ] || { incon "endpoint voice $DNIS nao cadastrado: $(printf '%s' "$RESP" | tr '\n' ' ' | cut -c1-200)"; fim; }
# o gateway guarda o endpoint em cache; a invalidacao chega por registry.changed
sleep "${ESPERA_CACHE:-35}"

# ── a chamada: fica calada DURACAO s e mede o que o "telefone" recebe ──
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
OUT=$(timeout 180 docker run --rm --name "probe_voz35_$$_$RANDOM" --network "$NET" --entrypoint python \
  -e ESPERA=70 -e DURACAO="$DURACAO" -e ANI="$ANI" -v "$PWD/infra/test:/t:ro" -w /t "$IMG" \
  /t/_sip_rtp_gaps.py "$DNIS" plughub_demo "$SIP_PASS" 2>&1 </dev/null)
sleep 2
LOG=$(docker logs --since "$T0" "$GW" 2>&1)
SID=$(printf '%s\n' "$LOG" | grep -F "de $ANI para $DNIS virou contato" | sed -n 's/.*session=\([0-9a-f-]*\).*/\1/p' | head -1)
# a chave pode vir no meio da linha (`INFO pacotes=N esperados_20ms=M`): lê o token, não o resto
val() { printf '%s\n' "$OUT" | grep '^INFO ' | tr ' ' '\n' | sed -n "s/^$1=//p" | head -1; }
FINAL=$(printf '%s\n' "$OUT" | sed -n 's/^FINAL //p' | head -1)

if [ -z "$FINAL" ]; then
  incon "o telefone de teste nao reportou desfecho: $(printf '%s' "$OUT" | tail -3 | tr '\n' ' ' | cut -c1-240)"; fim
fi
if [ -z "$SID" ]; then
  incon "nenhum contato nasceu de $ANI para $DNIS — a chamada nem chegou ao gateway (endpoint em cache?)"; fim
fi
info "session=$SID"

# ── C0 ──
if printf '%s\n' "$LOG" | grep -F "webrtc voz: entrou na sala session=$SID" >/dev/null; then
  incon "C0 uma VOZ de IA entrou na sala — o atendimento nao prova a linha (pool $POOL tem IA?)"
else
  ok "C0 nenhuma voz de IA na sala: quem atende, se atender, e a linha"
fi

# ── L1 · L2 ──
if [ "$FINAL" = 200 ]; then
  ok "L1 chamada atendida (200)"
  AT=$(val atendida_em_s)
  if awk -v a="$AT" 'BEGIN{exit !(a != "" && a <= 3.0)}'; then ok "L2 atendida em ${AT} s"
  else falha "L2 atendida em ${AT:-?} s — mais que 3 s"; fi
else
  falha "L1 chamada NAO atendida (final=$FINAL $(printf '%s\n' "$OUT" | sed -n 's/^INFO respostas=//p' | head -1)) — o defeito da VOZ-35: sem IA, ninguem publica"
  info "L2/L3 nao medidos (sem atendimento)"
fi

# ── L3 ──
if [ "$FINAL" = 200 ]; then
  PK=$(val pacotes); ESP=$(val esperados_20ms); GAP=$(val maior_intervalo_s)
  if [ -z "$PK" ] || [ -z "$ESP" ] || [ "$ESP" = 0 ]; then
    incon "L3 contagem de RTP ausente na saida do telefone"
  elif [ "$GAP" = sem_pacotes ] || [ "$PK" = 0 ]; then
    falha "L3 atendida mas NENHUM pacote RTP em ${DURACAO} s — operadora derruba chamada assim"
  elif awk -v p="$PK" -v e="$ESP" -v g="$GAP" 'BEGIN{exit !(p >= 0.9 * e && g < 1.0)}'; then
    ok "L3 RTP continuo: $PK de $ESP pacotes, maior intervalo ${GAP} s"
  else
    falha "L3 RTP com buraco: $PK de $ESP pacotes, maior intervalo ${GAP} s ($(val intervalos_maiores_que_1s))"
  fi
fi

# ── L4 ──
if printf '%s\n' "$LOG" | grep -F "linha na sala — chamada atendida (session=$SID" >/dev/null; then
  ok "L4 o gateway registrou a linha na sala"
else
  falha "L4 o gateway NAO registrou a linha na sala para $SID"
  printf '%s\n' "$LOG" | grep -F "$SID" | grep -iE 'linha|NAO' | tail -3 | sed 's/^/          /'
fi
if [ "$FINAL" = 200 ]; then
  if printf '%s\n' "$LOG" | grep -F "o chamador desligou (session=$SID)" >/dev/null; then
    ok "L4 o chamador desligou e o contato fechou pelo lado dele"
  else
    falha "L4 o chamador desligou e o gateway nao fechou o contato $SID"
  fi
fi

fim
