#!/usr/bin/env bash
# probe_voz06_recording.sh — 2026-09-18  (VOZ-06)
#
# PERGUNTA: a chamada é GRAVADA quando — e só quando — o pool de quem atende pede; o cliente é
# avisado antes; o trecho do dado protegido (NIV-07) NUNCA está na gravação; a recusa que o FLUXO
# grava é honrada; e a gravação fica guardada como `call_recording`, com a retenção da classe, fora
# da porta pública de anexos?
#
# O ESTADO QUE O ORIGINOU: havia código de gravação ("Phase D") que nunca rodou — nenhum serviço de
# egress no compose, gatilho lendo um campo sem produtor, vídeo MP4 com `sleep(5)` adivinhando o fim
# do arquivo, e o arquivo guardado com a retenção do anexo de webchat, servido pela porta que toma o
# file_id como credencial. Nada disso ficava vermelho, porque nada disso executava.
#
# O "telefone" é o cliente SIP de teste (`_sip_ua.py`); o conteúdo de cada parte é medido
# TRANSCREVENDO o arquivo guardado pelo mesmo serviço de fala da plataforma.
#
# RAMOS (pools, skill e endpoint de fixture; o endpoint é APAGADO no fim):
#   K0 o número do tronco de demo não tem endpoint `voice` de outro pool (senão NÃO mexe)
#   G1 cliente ACEITA: DUAS partes guardadas (antes e depois do PIN), nenhum evento de falha/descarte
#   G2 cada parte: classe `call_recording`, `audio/ogg`, expira pela retenção da CLASSE, pools certos
#   G3 cada parte tem FALA dentro (controle de que o arquivo não é silêncio)
#   G4 o pedido do PIN e o PIN não estão em parte nenhuma
#   G5 a porta pública de anexos responde 404 para cada parte
#   L1 no log: a parte 1 PARA antes da pausa de mídia; a parte 2 começa depois de a pausa sair
#   L2 no log: o aviso foi entregue ANTES de a parte 1 começar
#   R1 cliente RECUSA pelo fluxo: nenhuma parte guardada, a em curso DESCARTADA
#   N1 CONTROLE: pool sem `recording` — nenhum evento de gravação
#   Lg o PIN não aparece no log do gateway
# ~3 chamadas. Usa o MESMO número de tronco do probe_voz02 — não rodar os dois em paralelo.
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq).
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
SIPC="${SIP_CONTAINER:-plughub-demo-livekit-sip-1}"
EGC="${EGRESS_CONTAINER:-plughub-demo-livekit-egress-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL_REC="probe_voz06_rec"
POOL_NOREC="probe_voz06_norec"
SKILL="skill_probe_recording_v1"
FIXTURE="infra/test/fixtures/skill_probe_recording_v1.json"
DNIS="${SIP_DNIS:-+551140000000}"
# VOZ-32: a senha EM VIGOR e a com que o sip-seed criou o tronco — com a borda SIP ligada ela vem
# do .env.demo e a do repositorio e RECUSADA. Ler o compose aqui mediria a senha errada.
SEEDC="${SEED_CONTAINER:-plughub-demo-sip-seed-1}"
SIP_PASS="${SIP_TRUNK_PASSWORD_DEMO:-$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$SEEDC" 2>/dev/null | sed -n 's/^SIP_TRUNK_PASSWORD_DEMO=//p' | head -1)}"
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
echo " a chamada e gravada quando o pool pede, avisada, sem o dado protegido, com recusa honrada?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
for c in "$SIPC" "$EGC"; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != true ]; then
    incon "$c fora do ar — nada medido"; fim
  fi
done
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
RET=$(curl -s --max-time 10 "$CFG/config/storage?tenant_id=$TENANT" | jq -r '.entries.call_recording_retention_days // empty')
[ -n "$RET" ] || { incon "storage.call_recording_retention_days ausente no config-api — rode o config-seed"; fim; }

EXIST=$(curl -s --max-time 10 "${H[@]}" "$REG/v1/channel-endpoints?channel=voice" \
  | jq -c --arg d "$DNIS" '[(.items // .endpoints // . // [])[] | select(.identifier == $d)] | .[0] // empty' 2>/dev/null)
case "$(printf '%s' "$EXIST" | jq -r '.pool_id // empty' 2>/dev/null)" in
  ""|probe_voz02_sip|"$POOL_REC"|"$POOL_NOREC") ;;
  *) incon "K0 $DNIS ja tem endpoint voice -> $(printf '%s' "$EXIST" | jq -r '.pool_id') — o probe nao sobrescreve config real"; fim ;;
esac
[ -n "$EXIST" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$(printf '%s' "$EXIST" | jq -r '.id')"
ok "K0 $DNIS livre de endpoint real"

limpa() {
  [ -n "$EP_ID" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
  EP_ID=""
}
trap limpa EXIT INT TERM

pool_fixture() {  # $1 = pool, $2 = politica (JSON)
  local st
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$1")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$1\",\"agent_kind\":\"ai\",\"channel_types\":[\"voice\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_voz06_recording (VOZ-06)\",\"media_policy\":$2}")
    [ "$st" = 201 ] || { incon "pool $1 nao criado (http $st)"; fim; }
  else
    st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$1" -d "{\"media_policy\":$2}")
    [ "$st" = 200 ] || { incon "politica do pool $1 nao gravada (http $st)"; fim; }
  fi
  local sn pm
  sn=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$1/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  pm=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$1/promote" -d '{}')
  { [ "$sn" = 200 ] && [ "$pm" = 200 ]; } || { incon "fixture nao implantada em $1 (set-next=$sn promote=$pm)"; fim; }
}
PUB=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
{ [ "$PUB" = 200 ] || [ "$PUB" = 201 ]; } || { incon "skill de fixture nao publicada (http $PUB)"; fim; }
pool_fixture "$POOL_REC"   '{"customer_publish":["audio"],"agent_publish":["audio"],"recording":true}'
pool_fixture "$POOL_NOREC" '{"customer_publish":["audio"],"agent_publish":["audio"]}'
VIVO=$(curl -s "${H[@]}" "$REG/v1/pools/$POOL_REC" | jq -c '.media_policy')
case "$VIVO" in *'"recording":true'*) ok "K1 o registry guarda a politica com recording: $VIVO" ;;
  *) falha "K1 o registry NAO guardou recording no pool $POOL_REC: $VIVO"; fim ;; esac

endpoint() {  # $1 = pool
  limpa
  local resp
  resp=$(curl -s -w '\n%{http_code}' -X POST "${H[@]}" "$REG/v1/channel-endpoints" \
    -d "{\"channel\":\"voice\",\"identifier\":\"$DNIS\",\"pool_id\":\"$1\",\"display_name\":\"probe VOZ-06\"}")
  EP_ID=$(printf '%s' "$resp" | sed '$d' | jq -r '.id // empty' 2>/dev/null)
  [ -n "$EP_ID" ] || { incon "endpoint voice $DNIS -> $1 nao cadastrado: $(printf '%s' "$resp" | tr '\n' ' ' | cut -c1-200)"; fim; }
  for _ in $(seq 1 40); do
    [ -n "$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$1:instances" | head -1)" ] && break
    sleep 1
  done
}
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
exercicio() {  # $1 = MODE, $2 = ANI, $3 = POOL
  timeout 600 docker run --rm -i --name "probe_voz06_$$_$RANDOM" --network "$NET" --entrypoint python \
    -v "$PWD/infra/test:/t:ro" -w /t $ENV -e MODE="$1" -e DNIS="$DNIS" -e ANI="$2" -e POOL="$3" \
    -e RETENCAO_DIAS="$RET" -e SIP_PASS="$SIP_PASS" "$IMG" /t/_recording_exercise.py 2>&1
}
mostra() {  # $1 = saida → imprime e soma
  printf '%s\n' "$1" | grep -E '^(OK|FALHA|INCONCL|INFO) ' | while read -r st ramo resto; do
    case "$st" in
      OK) echo "  OK      $ramo $resto" ;; FALHA) echo "  FALHA   $ramo $resto" ;;
      INCONCL) echo "  INCONCL $ramo $resto" ;; *) echo "  INFO    $ramo $resto" ;;
    esac
  done
  FALHA=$((FALHA + $(printf '%s\n' "$1" | grep -c '^FALHA ')))
  INCONCL=$((INCONCL + $(printf '%s\n' "$1" | grep -c '^INCONCL ')))
  if [ "$(printf '%s\n' "$1" | grep -c '^\(OK\|FALHA\|INCONCL\) ')" = 0 ]; then
    incon "o exercicio nao produziu veredicto: $(printf '%s\n' "$1" | tail -5 | tr '\n' ' ' | cut -c1-400)"
  fi
}

# ── 1 · aceita: duas partes, sem o PIN ──
endpoint "$POOL_REC"
sleep 35   # o cache de endpoint do gateway vive 30 s
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
OUT=$(exercicio grava "$ANI" "$POOL_REC")
mostra "$OUT"
SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
if [ -n "$SID" ]; then
  LOG=$(docker logs --since "$T0" "$GW" 2>&1 | grep -n "$SID")
  n_aviso=$(printf '%s\n' "$LOG" | grep "gravacao: aviso entregue" | head -1 | cut -d: -f1)
  n_p1=$(printf '%s\n' "$LOG" | grep "parte 1 INICIADA" | head -1 | cut -d: -f1)
  n_p1s=$(printf '%s\n' "$LOG" | grep "parte 1 PARADA — bloco mascarado" | head -1 | cut -d: -f1)
  n_hold=$(printf '%s\n' "$LOG" | grep "pausa de midia no menu" | head -1 | cut -d: -f1)
  n_rel=$(printf '%s\n' "$LOG" | grep "pausa de midia do menu .* liberada" | head -1 | cut -d: -f1)
  n_p2=$(printf '%s\n' "$LOG" | grep "parte 2 INICIADA" | head -1 | cut -d: -f1)
  if [ -n "$n_p1s" ] && [ -n "$n_hold" ] && [ -n "$n_rel" ] && [ -n "$n_p2" ] \
     && [ "$n_p1s" -lt "$n_hold" ] && [ "$n_p2" -gt "$n_rel" ]; then
    ok "L1 a parte 1 parou ANTES da pausa de midia e a parte 2 comecou DEPOIS de ela sair"
  else
    falha "L1 ordem no log: parte1-parada=${n_p1s:-?} pausa=${n_hold:-?} liberada=${n_rel:-?} parte2=${n_p2:-?}"
  fi
  if [ -n "$n_aviso" ] && [ -n "$n_p1" ] && [ "$n_aviso" -lt "$n_p1" ]; then
    ok "L2 o aviso foi entregue antes da parte 1: $(printf '%s\n' "$LOG" | grep 'aviso entregue' | head -1 | sed 's/.*aviso entregue/aviso entregue/' | cut -c1-90)"
  else
    falha "L2 aviso=${n_aviso:-?} parte1=${n_p1:-?} — a gravacao comecou sem aviso antes"
  fi
  VAZ=$(docker logs --since "$T0" "$GW" 2>&1 | grep -cE "(^|[^0-9])7391([^0-9]|$)")
  [ "${VAZ:-0}" = 0 ] && ok "Lg o PIN nao aparece no log do gateway" || falha "Lg o PIN aparece $VAZ vez(es) no log do gateway"
fi

# ── 2 · recusa pelo fluxo ──
OUTR=$(exercicio recusa "${ANI%?}3" "$POOL_REC")
mostra "$OUTR"

# ── 3 · controle: pool sem recording ──
endpoint "$POOL_NOREC"
sleep 35
OUTN=$(exercicio controle "${ANI%?}5" "$POOL_NOREC")
mostra "$OUTN"

limpa
fim
