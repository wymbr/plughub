#!/usr/bin/env bash
# probe_speech_check.sh — 2026-09-17  (VOZ-23, camada B da recalibragem de STT)
#
# PERGUNTA: a verificação ativa do caminho de fala RODA pelo caminho real, grava o resultado, e o
# relatório mostra REGRESSÃO quando o caminho piora — e nada quando não piora?
#
# O QUE ELA EXISTE PARA PEGAR: a cadeia (SFU, codec, reamostragem, modelo, perfil) move os números sem
# nada ficar vermelho — medido na VOZ-18 ("Fatura." 0,59 limpa → "Batura!" 0,45 pela chamada). Só a
# comparação com uma linha de base marcada por pessoa transforma isso em resposta.
#
# RAMOS
#   K0  o serviço está de pé e OCIOSO, o pool de calibração tem instância, e o tenant não tem o perfil
#       do probe (verificação em curso faria o disparo levar 409 e o probe medir a chamada de outro)
#   A1  sem credencial a rota RECUSA (401); perfil inexistente é recusado ANTES de abrir chamada (422)
#   D1  disparo pelo POOL WEBHOOK (tool MCP `speech_check_run`) → verificação corre e termina completed
#   R1  a linha ficou no ClickHouse: frases certas > 0, perfil aplicado = o pedido, sessão presente
#   R2  a verificação aparece no relatório do perfil para quem ALCANÇA o pool de calibração, não para
#       quem não alcança (o admin do demo nasce sem pools — por isso os usuários-sonda), e o endpoint
#       temporário NÃO ficou para trás
#   B1  sem linha de base marcada, a comparação diz `no_baseline` (nunca um delta zero)
#   B2  marcada a base, `latest_is_baseline`
#   M1  perfil trocado para um modelo INEXISTENTE → nova verificação → `compared` com items_regressed
#       não vazio e accuracy_delta < 0 (é a regressão que a camada B existe para ver)
#   C1  CONTROLE: perfil restaurado → a verificação seguinte não regride contra a base
#
# Cada chamada sintética leva ~1 min. Perfil, base e endpoints do probe são apagados na saída.
#
# EXIT: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
SC="${SPEECH_CHECK_CONTAINER:-plughub-demo-speech-check-1}"
CH="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
DB="${CH_DB:-plughub_demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
CFG="${CONFIG_API:-http://localhost:3600}"
AN="${ANALYTICS:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="speech_check"
TRIGGER="speech_check_trigger"
PERFIL="probe-voz23"
MODELO_FALSO="probe/modelo-inexistente-voz23"
GWPORT="${GW_PORT:-8010}"
FALHA=0
INCONCL=0
MEXEU=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
fim() {
  echo "────────────────────────────────────────────────────────────────────"
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
  echo " VERDE"; exit 0
}
chq() { docker exec "$CH" clickhouse-client -d "$DB" -q "$1" 2>&1; }

echo "════════════════════════════════════════════════════════════════════"
echo " a verificacao ativa da fala mede o caminho real e acusa regressao?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
[ -n "$TOKEN" ] || { incon "login do admin falhou — nada medido"; fim; }
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

# ── K0 ──
docker inspect -f '{{.State.Running}}' "$SC" 2>/dev/null | grep -qx true || { incon "K0 servico speech-check fora do ar"; fim; }
INST=$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)
[ -n "$INST" ] || { incon "K0 pool de calibracao $POOL sem instancia — o RegistrySyncer semeou? (reinicie o orchestrator-bridge)"; fim; }
EMCURSO=$(docker exec "$SC" python -c "
import json,urllib.request,os
req=urllib.request.Request('http://localhost:3870/v1/speech-checks',
    headers={'x-service-token':os.environ['PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN']})
print(json.load(urllib.request.urlopen(req))['running'] or '-')
" 2>&1 | tail -1)
[ "$EMCURSO" = "-" ] || { incon "K0 ja ha verificacao em curso ($EMCURSO) — o disparo levaria 409 e o probe mediria a chamada de outro"; fim; }
PERFIS=$(curl -s --max-time 10 "$CFG/config/speech_profiles?tenant_id=$TENANT")
[ "$(printf '%s' "$PERFIS" | jq --arg p "$PERFIL" '(.entries // {}) | has($p)')" = false ] \
  || { incon "K0 ja existe um perfil $PERFIL no tenant — o probe nao sobrescreve config real"; fim; }
BASES=$(curl -s --max-time 10 "$CFG/config/speech_check_baselines?tenant_id=$TENANT")
[ "$(printf '%s' "$BASES" | jq --arg p "$PERFIL" '(.entries // {}) | has($p)')" = false ] \
  || { incon "K0 ja existe linha de base para $PERFIL"; fim; }
ok "K0 speech-check de pe e ocioso, $POOL com instancia, tenant sem o perfil nem a base do probe"

limpa() {
  apaga_sondas
  [ "$MEXEU" = 1 ] || return 0
  curl -s -o /dev/null -X DELETE "${H[@]}" "$CFG/config/speech_profiles/$PERFIL?tenant_id=$TENANT"
  curl -s -o /dev/null -X DELETE "${H[@]}" "$CFG/config/speech_check_baselines/$PERFIL?tenant_id=$TENANT"
  for id in $(curl -s "${H[@]}" "$REG/v1/channel-endpoints?channel=webrtc" | jq -r '.endpoints[] | select(.identifier | startswith("speech-check-")) | .id'); do
    curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$id"
  done
  MEXEU=0
}
trap limpa EXIT INT TERM

put_perfil() { curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$CFG/config/speech_profiles/$PERFIL" -d "{\"value\":$1,\"tenant_id\":\"$TENANT\"}"; }
# ⚠️ O relatório recorta por pool ALCANÇADO, e o admin do demo nasce com `accessible_pools` sem o pool
# de calibração (criado agora): medir com o token do admin mediria o escopo, não o relatório. Daí as
# sondas — uma com o pool, uma sem (o controle que prova que o recorte existe).
SONDAS="probe_voz23_in@plughub.local probe_voz23_out@plughub.local"
uid() { curl -s -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users?tenant_id=$TENANT&limit=500" \
          | jq -r --arg e "$1" '.[] | select(.email==$e) | .id' | head -1; }
apaga_sondas() { for e in $SONDAS; do i=$(uid "$e"); [ -n "$i" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users/$i"; done; }
cria_sonda() {  # email, pool
  curl -s -o /dev/null -X POST "$AUTH/auth/users" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$1\",\"name\":\"Probe VOZ-23\",\"password\":\"probe_voz23_123\",\"roles\":[\"supervisor\"],\"accessible_pools\":[\"$2\"]}"
  curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"probe_voz23_123\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'
}
apaga_sondas
T_IN=$(cria_sonda probe_voz23_in@plughub.local "$POOL")
T_OUT=$(cria_sonda probe_voz23_out@plughub.local "probe_voz23_outro_pool")
[ -n "$T_IN" ] && [ -n "$T_OUT" ] || { incon "usuarios-sonda nao criados — o relatorio nao pode ser medido"; fim; }
compara()    { curl -s -H "Authorization: Bearer $T_IN" "$AN/reports/speech/checks/compare?tenant_id=$TENANT&speech_profile_id=$PERFIL"; }

# espera a verificacao sair de running; imprime o status final ou "?"
espera() {  # $1 = check_id
  for _ in $(seq 1 90); do
    st=$(chq "SELECT status FROM speech_checks FINAL WHERE check_id = '$1'")
    [ -n "$st" ] && { echo "$st"; return; }
    sleep 4
  done
  echo "?"
}

MEXEU=1
st=$(put_perfil '{"description":"probe VOZ-23"}')
[ "$st" = 200 ] || { incon "perfil do probe nao gravado (http $st)"; fim; }

# ── A1 ──
SEM=$(docker exec "$SC" python -c "
import json,urllib.request
req=urllib.request.Request('http://localhost:3870/v1/speech-checks', method='POST',
    data=json.dumps({'requested_by':'probe'}).encode(), headers={'Content-Type':'application/json'})
try:
    urllib.request.urlopen(req)
    print(200)
except urllib.error.HTTPError as e:
    print(e.code)
")
RUIM=$(docker exec "$SC" python -c "
import json,urllib.request,os
req=urllib.request.Request('http://localhost:3870/v1/speech-checks', method='POST',
    data=json.dumps({'requested_by':'probe','speech_profile_id':'nao-existe-voz23'}).encode(),
    headers={'Content-Type':'application/json','x-service-token':os.environ['PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN']})
try:
    urllib.request.urlopen(req)
    print(200)
except urllib.error.HTTPError as e:
    print(e.code)
")
[ "$SEM" = 401 ] && [ "$RUIM" = 422 ] && ok "A1 sem credencial 401; perfil inexistente 422 antes de abrir chamada" \
  || falha "A1 recusas erradas: sem credencial=$SEM (esperado 401), perfil inexistente=$RUIM (esperado 422)"

# ── D1/R1/R2 — disparo pelo pool webhook (caminho da tool MCP) ──
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
DISP=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" \
  "http://localhost:$GWPORT/v1/channels/webhook/pool/$TRIGGER" \
  -d "{\"context\":{\"speech_profile_id\":\"$PERFIL\",\"requested_by\":\"probe_speech_check\"}}")
if [ "$DISP" != 200 ] && [ "$DISP" != 201 ] && [ "$DISP" != 202 ]; then
  incon "D1 disparo pelo pool webhook respondeu http $DISP"; fim
fi
CID=""
for _ in $(seq 1 60); do
  # `started_at >= T0`: linha de uma verificacao ANTERIOR nao vale como prova deste disparo
  CID=$(chq "SELECT check_id FROM speech_checks FINAL WHERE speech_profile_id = '$PERFIL' AND started_at >= parseDateTimeBestEffort('$T0') ORDER BY started_at DESC LIMIT 1")
  [ -n "$CID" ] && break
  sleep 5
done
if [ -z "$CID" ]; then falha "D1 nenhuma verificacao gravada depois do disparo pelo pool webhook"; fim; fi
LINHA=$(chq "SELECT status, ifNull(failure_reason,'-'), phrases_correct, phrases_total, ifNull(profile_in_effect,'-'), ifNull(session_id,'-'), hallucinations FROM speech_checks FINAL WHERE check_id = '$CID' FORMAT TSV")
IFS=$'\t' read -r ST RAZAO CERTAS TOTAL PEFETIVO SID HAL <<< "$LINHA"
[ "$ST" = completed ] && ok "D1 a verificacao pedida pelo pool webhook terminou completed ($CID)" \
                      || falha "D1 verificacao $CID terminou $ST ($RAZAO)"
if [ "$ST" = completed ]; then
  { [ "${CERTAS:-0}" -gt 0 ] && [ "$PEFETIVO" = "$PERFIL" ] && [ "$SID" != "-" ]; } \
    && ok "R1 $CERTAS/$TOTAL frases certas, perfil aplicado=$PEFETIVO, sessao=$SID, alucinacoes=$HAL" \
    || falha "R1 linha do ClickHouse nao fecha: certas=$CERTAS/$TOTAL perfil_aplicado=$PEFETIVO sessao=$SID"
fi
N=$(curl -s -H "Authorization: Bearer $T_IN" "$AN/reports/speech/checks?tenant_id=$TENANT&speech_profile_id=$PERFIL" | jq --arg c "$CID" '[.data[] | select(.check_id == $c)] | length')
NX=$(curl -s -H "Authorization: Bearer $T_OUT" "$AN/reports/speech/checks?tenant_id=$TENANT&speech_profile_id=$PERFIL" | jq '.data | length')
ORF=$(curl -s "${H[@]}" "$REG/v1/channel-endpoints?channel=webrtc" | jq '[.endpoints[] | select(.identifier | startswith("speech-check-"))] | length')
{ [ "$N" = 1 ] && [ "$NX" = 0 ] && [ "$ORF" = 0 ]; } && ok "R2 quem alcanca o pool ve a verificacao (1), quem nao alcanca nao ve (0), e nenhum endpoint temporario ficou" \
  || falha "R2 sonda com o pool viu $N linha(s) para $CID, sonda sem o pool viu $NX, e sobraram $ORF endpoint(s) speech-check-*"

# ── B1/B2 — linha de base ──
[ "$(compara | jq -r .status)" = no_baseline ] && ok "B1 sem base marcada, a comparacao diz no_baseline" \
  || falha "B1 comparacao sem base disse $(compara | jq -r .status)"
st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$CFG/config/speech_check_baselines/$PERFIL" \
  -d "{\"value\":{\"check_id\":\"$CID\",\"marked_by\":\"probe\"},\"tenant_id\":\"$TENANT\"}")
[ "$st" = 200 ] || { incon "linha de base nao gravada (http $st)"; fim; }
[ "$(compara | jq -r .status)" = latest_is_baseline ] && ok "B2 marcada a base, a comparacao diz latest_is_baseline" \
  || falha "B2 depois de marcar a base a comparacao disse $(compara | jq -r .status)"

# ── M1 — regressao provocada ──
st=$(put_perfil "{\"description\":\"probe VOZ-23\",\"stt_model\":\"$MODELO_FALSO\"}")
[ "$st" = 200 ] || { incon "M1 perfil com modelo falso nao gravado (http $st)"; fim; }
sleep 4
CID2=$(docker exec "$SC" python -c "
import json,urllib.request,os
req=urllib.request.Request('http://localhost:3870/v1/speech-checks', method='POST',
    data=json.dumps({'requested_by':'probe','speech_profile_id':'$PERFIL'}).encode(),
    headers={'Content-Type':'application/json','x-service-token':os.environ['PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN']})
print(json.load(urllib.request.urlopen(req))['check_id'])
")
ST2=$(espera "$CID2")
CMP=$(compara)
if [ "$ST2" != completed ]; then
  falha "M1 a verificacao com modelo inexistente terminou $ST2 (esperado completed com frases erradas)"
else
  S=$(printf '%s' "$CMP" | jq -r .status)
  REG_N=$(printf '%s' "$CMP" | jq '.comparison.items_regressed | length')
  DELTA=$(printf '%s' "$CMP" | jq -r '.comparison.accuracy_delta')
  MUD=$(printf '%s' "$CMP" | jq -r '.comparison.config_changes.stt_model.latest // "-"')
  { [ "$S" = compared ] && [ "${REG_N:-0}" -gt 0 ] && [ "$MUD" = "$MODELO_FALSO" ]; } \
    && ok "M1 a comparacao acusou regressao: $REG_N item(ns), accuracy_delta=$DELTA, modelo $MUD" \
    || falha "M1 comparacao nao acusou a regressao: status=$S regredidos=$REG_N delta=$DELTA modelo=$MUD"
fi

# ── C1 — CONTROLE ──
st=$(put_perfil '{"description":"probe VOZ-23"}')
sleep 4
CID3=$(docker exec "$SC" python -c "
import json,urllib.request,os
req=urllib.request.Request('http://localhost:3870/v1/speech-checks', method='POST',
    data=json.dumps({'requested_by':'probe','speech_profile_id':'$PERFIL'}).encode(),
    headers={'Content-Type':'application/json','x-service-token':os.environ['PLUGHUB_SPEECH_CHECK_SERVICE_TOKEN']})
print(json.load(urllib.request.urlopen(req))['check_id'])
")
ST3=$(espera "$CID3")
CMP3=$(compara)
if [ "$ST3" != completed ]; then
  incon "C1 a verificacao de controle terminou $ST3"
else
  S3=$(printf '%s' "$CMP3" | jq -r .status)
  R3=$(printf '%s' "$CMP3" | jq '.comparison.items_regressed | length')
  { [ "$S3" = compared ] && [ "${R3:-9}" -le 1 ]; } \
    && ok "C1 CONTROLE restaurado o perfil, a verificacao seguinte nao regride ($R3 item(ns))" \
    || falha "C1 o controle acusou $R3 regressao(oes) (status=$S3) — o instrumento acusa sem defeito"
fi

limpa
fim
