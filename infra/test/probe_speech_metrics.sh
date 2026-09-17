#!/usr/bin/env bash
# probe_speech_metrics.sh — 2026-09-17  (VOZ-22)
#
# PERGUNTA: uma chamada WebRTC com coleta por voz deixa, no ClickHouse e no relatório, a telemetria
# passiva da fala — só números, certos para o que aconteceu na chamada, e nenhum texto?
#
# O ESTADO QUE O ORIGINOU: a recalibragem de STT por instalação (ADR voice-media-plane V13) precisa
# saber como a fala dos clientes DESTA instalação se comporta — chão de ruído, confiança, descartes do
# VAD, desfechos da coleta —, e nada disso era medido fora do log.
#
# A CHAMADA é o exercício do probe_webrtc_speech_tuning (5 menus só por voz, cada um com o que se sabe
# que acontece): m0 ruído + "Cancelar." → valor · m1 min_confidence 0.99 → inválido POR CONFIANÇA ·
# m2 → valor · m3 end_silence_ms 2500 → inválido (uma fala, duas opções) · m4 → valor.
#
# RAMOS:
#   S1 UMA linha em speech_stream_summaries para a sessão, com quadros, >= 5 falas transcritas, o ruído
#      do m0 descartado pelo VAD (discarded_vad >= 1) e confiança medida em (0,1)
#   S2 chão de ruído medido (noise_rms_p50 presente) e segmentação em vigor gravada (700, global)
#   C1 CINCO linhas em speech_collect_outcomes, na ordem: value, invalid, value, invalid, value
#   C2 só o m1 foi recusado por confiança (invalid_low_confidence = 0,1,0,0,0) e declara 0.99
#   C3 o m3 carrega o end_silence_ms que declarou (2500)
#   T1 nenhum texto da chamada nas duas tabelas ("Cancelar"/"Atendente" contados nas linhas = 0)
#   R1 o relatório /reports/speech/quality devolve o pool a um usuário-sonda que o ALCANÇA, com
#      sample_sufficient=false no default (30)
#   R2 CONTROLE com min_sample=1 o mesmo pool sai sample_sufficient=true
#   R3 um usuário-sonda com OUTRO pool no escopo não vê este, nem pedindo `?pool_id=` dele
#   (o admin do demo nasce sem pools — `accessible_pools=[]` é NENHUM —, por isso os usuários-sonda;
#    são criados e apagados aqui)
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CH="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
DB="${CH_DB:-plughub_demo}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
AN="${ANALYTICS:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz18_speech"
SKILL="skill_probe_speech_tuning_v1"
FIXTURE="infra/test/fixtures/skill_probe_speech_tuning_v1.json"
FALHA=0
INCONCL=0

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
echo " a chamada deixa telemetria da fala, certa e sem texto?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
[ "$(chq "EXISTS TABLE speech_collect_outcomes")" = 1 ] || { incon "tabelas speech_* ausentes no ClickHouse $DB"; fim; }
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

# fixture (a mesma do probe_webrtc_speech_tuning)
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
OUT=$(timeout 400 docker run --rm -i --name "probe_voz22_$$_$RANDOM" --network "$NET" --entrypoint python \
      $ENV -e POOL="$POOL" "$IMG" - < infra/test/_webrtc_speech_tuning_exercise.py 2>&1)
printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL|INFO) ' | sed 's/^/  INFO    exercicio: /'
SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
[ -n "$SID" ] || { incon "a chamada nao abriu sessao"; fim; }
if [ "$(printf '%s\n' "$OUT" | grep -cE '^OK (N1|C1|C2|S2) ')" -lt 4 ]; then
  incon "a chamada nao seguiu o roteiro (m0..m4) — os numeros esperados nao valem"; fim
fi

# a telemetria atravessa gateway → Kafka → consumidor → ClickHouse
N=0; M=0
for _ in $(seq 1 45); do
  N=$(chq "SELECT count() FROM speech_stream_summaries FINAL WHERE session_id = '$SID'")
  M=$(chq "SELECT count() FROM speech_collect_outcomes FINAL WHERE session_id = '$SID'")
  [ "${N:-0}" -ge 1 ] && [ "${M:-0}" -ge 5 ] && break
  sleep 2
done

# ── S1/S2 ──
LINHA=$(chq "SELECT frames, utterances_transcribed, discarded_vad, confidence_count, confidence_p50, noise_rms_p50, seg_end_silence_ms, segmentation_scope FROM speech_stream_summaries FINAL WHERE session_id = '$SID' FORMAT JSONEachRow")
if [ "${N:-0}" != 1 ]; then
  falha "S1 $N linha(s) em speech_stream_summaries para a sessao $SID (esperada 1)"
else
  fr=$(printf '%s' "$LINHA" | jq -r .frames); ut=$(printf '%s' "$LINHA" | jq -r .utterances_transcribed)
  dv=$(printf '%s' "$LINHA" | jq -r .discarded_vad); cp=$(printf '%s' "$LINHA" | jq -r .confidence_p50)
  if [ "$fr" -gt 0 ] && [ "$ut" -ge 5 ] && [ "$dv" -ge 1 ] && awk -v c="$cp" 'BEGIN{exit !(c>0 && c<1)}'; then
    ok "S1 resumo do fluxo do cliente: $fr quadros, $ut falas transcritas, $dv descartada(s) pelo VAD, confianca p50 $cp"
  else
    falha "S1 resumo nao bate com a chamada: $LINHA"
  fi
  nr=$(printf '%s' "$LINHA" | jq -r .noise_rms_p50); se=$(printf '%s' "$LINHA" | jq -r .seg_end_silence_ms)
  sc=$(printf '%s' "$LINHA" | jq -r .segmentation_scope | jq -r .end_silence_ms)
  [ "$nr" != null ] && [ "$se" = 700 ] && [ "$sc" = global ] \
    && ok "S2 chao de ruido medido (p50 $nr) e segmentacao em vigor gravada (end_silence_ms 700, global)" \
    || falha "S2 ruido ou segmentacao ausentes: noise_p50=$nr end_silence=$se escopo=$sc"
fi

# ── C1/C2/C3 ──
DES=$(chq "SELECT outcome, invalid_low_confidence, min_confidence, end_silence_ms FROM speech_collect_outcomes FINAL WHERE session_id = '$SID' ORDER BY timestamp FORMAT TSV")
ORD=$(printf '%s\n' "$DES" | cut -f1 | paste -sd, -)
[ "$ORD" = "value,invalid,value,invalid,value" ] && ok "C1 cinco desfechos na ordem da chamada: $ORD" \
  || falha "C1 desfechos da sessao $SID: '$ORD' (esperado value,invalid,value,invalid,value)"
LC=$(printf '%s\n' "$DES" | cut -f2 | paste -sd, -)
MC=$(printf '%s\n' "$DES" | sed -n 2p | cut -f3)
[ "$LC" = "0,1,0,0,0" ] && awk -v m="$MC" 'BEGIN{exit !(m>0.98 && m<1)}' \
  && ok "C2 so o m1 recusado por confianca (0,1,0,0,0), declarando min_confidence $MC" \
  || falha "C2 recusas por confianca '$LC', min_confidence do m1 '$MC'"
ES=$(printf '%s\n' "$DES" | sed -n 4p | cut -f4)
[ "$ES" = 2500 ] && ok "C3 o m3 carrega o end_silence_ms que declarou (2500)" || falha "C3 end_silence_ms do m3: '$ES'"

# ── T1 ──
TX=$(chq "SELECT count() FROM (SELECT toJSONString(tuple(*)) AS j FROM speech_collect_outcomes FINAL WHERE session_id = '$SID' UNION ALL SELECT toJSONString(tuple(*)) FROM speech_stream_summaries FINAL WHERE session_id = '$SID') WHERE positionCaseInsensitive(j, 'cancelar') > 0 OR positionCaseInsensitive(j, 'atendente') > 0")
[ "$TX" = 0 ] && ok "T1 nenhum texto da chamada nas linhas de telemetria" || falha "T1 $TX linha(s) com texto da chamada: '$TX'"

# ── R1/R2/R3 ──
SONDAS="probe_speech_in@plughub.local probe_speech_out@plughub.local"
uid() { curl -s -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users?tenant_id=$TENANT&limit=500" \
          | jq -r --arg e "$1" '.[] | select(.email==$e) | .id' | head -1; }
apaga_sondas() { for e in $SONDAS; do i=$(uid "$e"); [ -n "$i" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $TOKEN" "$AUTH/auth/users/$i"; done; }
trap apaga_sondas EXIT INT TERM
apaga_sondas
cria() {  # email, pool
  curl -s -o /dev/null -X POST "$AUTH/auth/users" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$1\",\"name\":\"Probe Speech\",\"password\":\"probe_speech_123\",\"roles\":[\"supervisor\"],\"accessible_pools\":[\"$2\"]}"
  curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"probe_speech_123\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'
}
T_IN=$(cria probe_speech_in@plughub.local "$POOL")
T_OUT=$(cria probe_speech_out@plughub.local "probe_speech_outro_pool")
if [ -z "$T_IN" ] || [ -z "$T_OUT" ]; then incon "R1/R2/R3 usuarios-sonda nao criados"; fim; fi
REP=$(curl -s --max-time 20 -H "Authorization: Bearer $T_IN" "$AN/reports/speech/quality?tenant_id=$TENANT&pool_id=$POOL")
REP1=$(curl -s --max-time 20 -H "Authorization: Bearer $T_IN" "$AN/reports/speech/quality?tenant_id=$TENANT&pool_id=$POOL&min_sample=1")
REPX=$(curl -s --max-time 20 -H "Authorization: Bearer $T_OUT" "$AN/reports/speech/quality?tenant_id=$TENANT&pool_id=$POOL")
C=$(printf '%s' "$REP" | jq -r --arg p "$POOL" '.data[] | select(.pool_id==$p) | .calls // empty')
S=$(printf '%s' "$REP" | jq -r --arg p "$POOL" '.data[] | select(.pool_id==$p) | .sample_sufficient')
CO=$(printf '%s' "$REP" | jq -r --arg p "$POOL" '.data[] | select(.pool_id==$p) | .collects // empty')
S1=$(printf '%s' "$REP1" | jq -r --arg p "$POOL" '.data[] | select(.pool_id==$p) | .sample_sufficient')
if [ -z "$C" ]; then
  falha "R1 o relatorio nao traz o pool $POOL: $(printf '%s' "$REP" | head -c 200)"
else
  # o pool so recebe chamadas de probe: menos de 30 ate aqui e o esperado, e a amostra e dita pequena
  if [ "$C" -lt 30 ]; then
    [ "$S" = false ] && ok "R1 o relatorio traz o pool ($C chamadas, $CO coletas) com sample_sufficient=false" \
                     || falha "R1 $C chamadas marcadas sample_sufficient=$S no minimo 30"
  else
    incon "R1 o pool ja tem $C chamadas — o ramo da amostra pequena nao se mede aqui"
  fi
  [ "$S1" = true ] && ok "R2 CONTROLE com min_sample=1 o mesmo pool sai sample_sufficient=true" \
                   || falha "R2 com min_sample=1 veio sample_sufficient=$S1"
  NX=$(printf '%s' "$REPX" | jq -r --arg p "$POOL" '[.data[]? | select(.pool_id==$p)] | length')
  [ "$NX" = 0 ] && ok "R3 quem alcanca OUTRO pool nao ve este, nem pedindo ?pool_id= dele" \
                || falha "R3 usuario sem o pool no escopo viu $NX linha(s) dele: $(printf '%s' "$REPX" | head -c 200)"
fi
fim
