#!/usr/bin/env bash
# probe_webrtc_speech_profile.sh — 2026-09-17  (VOZ-25)
#
# PERGUNTA: o perfil de fala apontado por um endpoint WebRTC VALE na chamada que entra por ele — a
# segmentação e a língua do perfil chegam ao STT (não só ao log), o modelo do perfil chega ao SERVIÇO,
# a telemetria grava o perfil em vigor — e a chamada pelo pool direto segue a config do tenant?
#
# O ESTADO QUE O ORIGINOU: a calibragem da fala era por TENANT (VOZ-21) e modelo/língua por
# instalação do gateway (env), enquanto a acústica varia pelo caminho da mídia (browser × tronco SIP)
# e pela língua. Dois pontos de entrada no mesmo tenant não podiam ter calibragem nem modelo próprios.
#
# RAMOS (fixture e exercício do probe_webrtc_speech_tuning; perfil e endpoint criados e APAGADOS):
#   K0 o tenant não tem override de stt_end_silence_ms nem perfil com o id do probe (senão NÃO mexe)
#   FASE 1 — perfil {stt_end_silence_ms: 2500, stt_language: "pt"}, endpoint alias → pool com ele
#     P1 o gateway recebeu o config.changed de speech_profiles e invalidou (linha do próprio invalidate)
#     F1 a chamada pelo alias abriu o STT com end_silence_ms=2500 (profile:<id>) e a voz com perfil=<id>
#        e stt_language=pt (profile:<id>)
#     F2 a fala do m4 (menu sem ajuste próprio) juntou as duas palavras: janela >= 3 s — o perfil
#        mudou o COMPORTAMENTO, não só a linha de log
#     F3 a telemetria da chamada grava speech_profile_id=<id> e o escopo `profile` no resumo, e o
#        perfil nos desfechos de coleta
#   FASE 2 — CONTROLE: a mesma chamada pelo POOL direto
#     C1 end_silence_ms=700 (global) e perfil=-
#     C2 a mesma fala do m4 separou: janela < 2,5 s
#     C3 a telemetria grava speech_profile_id NULL
#   FASE 3 — o modelo do perfil chega ao SERVIÇO
#     M1 perfil trocado para stt_model inexistente: o erro do speaches no log do gateway nomeia ESSE
#        modelo (só o pedido HTTP carrega o nome até lá)
#   FASE 4 — o DEFAULT do tenant, sem perfil nenhum (VOZ-17)
#     T1 com `webrtc.stt_model` do tenant, a chamada pelo POOL abre com esse modelo e procedência
#        `tenant` — antes da VOZ-17 os quatro campos de voz só podiam dizer `env`
#     T2 e a chamada transcreve com ele: o modelo escolhido pelo tenant é o que o serviço recebeu
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
CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz18_speech"
SKILL="skill_probe_speech_tuning_v1"
FIXTURE="infra/test/fixtures/skill_probe_speech_tuning_v1.json"
PERFIL="probe-voz25"
ALIAS="probe-voz25-$RANDOM$RANDOM"
MODELO_FALSO="probe/modelo-inexistente-voz25"
FALHA=0
INCONCL=0
EP_ID=""
MEXEU=0
MEXEU_NS=0

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
echo " o perfil de fala do endpoint vale na chamada, e so nela?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

# ── K0 ──
PROV=$(curl -s --max-time 10 "$CFG/config/webrtc/_provenance?tenant_id=$TENANT")
PERFIS=$(curl -s --max-time 10 "$CFG/config/speech_profiles?tenant_id=$TENANT")
if [ "$(printf '%s' "$PROV" | jq -r '.keys.stt_end_silence_ms.tenant_present // false')" = true ]; then
  incon "K0 $TENANT tem override proprio de stt_end_silence_ms — o controle nao discrimina; nada mexido"; fim
fi
if [ "$(printf '%s' "$PERFIS" | jq --arg p "$PERFIL" '(.entries // {}) | has($p)')" = true ]; then
  incon "K0 ja existe um perfil $PERFIL no tenant — o probe nao sobrescreve config real"; fim
fi
if [ "$(printf '%s' "$PROV" | jq -r '.keys.stt_model.tenant_present // false')" = true ]; then
  incon "K0 $TENANT ja escolhe stt_model proprio — a FASE 4 sobrescreveria config real; nada mexido"; fim
fi
ok "K0 sem override de stt_end_silence_ms nem de stt_model, e sem perfil $PERFIL no tenant"

limpa() {
  [ -n "$EP_ID" ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$REG/v1/channel-endpoints/$EP_ID"
  EP_ID=""
  [ "$MEXEU" = 1 ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$CFG/config/speech_profiles/$PERFIL?tenant_id=$TENANT"
  [ "$MEXEU_NS" = 1 ] && curl -s -o /dev/null -X DELETE "${H[@]}" "$CFG/config/webrtc/stt_model?tenant_id=$TENANT"
  MEXEU=0; MEXEU_NS=0
}
trap limpa EXIT INT TERM

put_perfil() {  # valor JSON
  curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$CFG/config/speech_profiles/$PERFIL" \
    -d "{\"value\":$1,\"tenant_id\":\"$TENANT\"}"
}

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

chamada() {  # $1 = endereco; imprime o SID (os veredictos do exercicio vao para stderr como INFO)
  local out
  out=$(timeout 400 docker run --rm -i --name "probe_voz25_$$_$RANDOM" --network "$NET" --entrypoint python \
        $ENV -e POOL="$1" "$IMG" - < infra/test/_webrtc_speech_tuning_exercise.py 2>&1)
  printf '%s\n' "$out" | grep -E '^(OK|FALHA|INCONCL|INFO) ' | sed 's/^/  INFO    exercicio: /' >&2
  printf '%s\n' "$out" | sed -n 's/^SID //p' | head -1
}
janela5() {
  docker exec "$REDIS" redis-cli --raw XRANGE "session:$1:stream" - + 2>/dev/null | grep '"speech"' \
    | jq -r '((.content.speech.end_ms // 0) - (.content.speech.start_ms // 0))' 2>/dev/null | sed -n 5p
}
linha() { docker logs --since 20m "$GW" 2>&1 | grep "$1 session=$2" | tail -1; }
invalidacoes() { docker logs --since "$1" "$GW" 2>&1 | grep -c "perfis de fala invalidados"; }
telemetria() {  # $1 = SID → espera o resumo e 5 desfechos; imprime "N M"
  local n=0 m=0
  for _ in $(seq 1 45); do
    n=$(chq "SELECT count() FROM speech_stream_summaries FINAL WHERE session_id = '$1'")
    m=$(chq "SELECT count() FROM speech_collect_outcomes FINAL WHERE session_id = '$1'")
    [ "${n:-0}" -ge 1 ] && [ "${m:-0}" -ge 5 ] && break
    sleep 2
  done
  echo "${n:-0} ${m:-0}"
}

# ── FASE 1 ──
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MEXEU=1
st=$(put_perfil "{\"description\":\"probe VOZ-25\",\"stt_end_silence_ms\":2500,\"stt_language\":\"pt\"}")
[ "$st" = 200 ] || { incon "perfil nao gravado (http $st)"; fim; }
RESP=$(curl -s -w '\n%{http_code}' -X POST "${H[@]}" "$REG/v1/channel-endpoints" \
  -d "{\"channel\":\"webrtc\",\"identifier\":\"$ALIAS\",\"pool_id\":\"$POOL\",\"display_name\":\"probe VOZ-25\",\"settings\":{\"speech_profile_id\":\"$PERFIL\"}}")
EP_ID=$(printf '%s' "$RESP" | sed '$d' | jq -r '.id // empty' 2>/dev/null)
[ -n "$EP_ID" ] || { incon "endpoint $ALIAS nao cadastrado: $(printf '%s' "$RESP" | tr '\n' ' ' | cut -c1-200)"; fim; }
n=0; for _ in $(seq 1 15); do n=$(invalidacoes "$T0"); [ "${n:-0}" -ge 1 ] && break; sleep 1; done
[ "${n:-0}" -ge 1 ] && ok "P1 o gateway recebeu o config.changed de speech_profiles e invalidou ($n)" \
                    || falha "P1 nenhuma invalidacao de perfis no gateway desde $T0 — perfil so valeria no boot"
sleep 3

SID1=$(chamada "$ALIAS")
[ -n "$SID1" ] || { incon "FASE 1 a chamada pelo alias nao abriu sessao"; fim; }
SEG1=$(linha "segmentacao da fala" "$SID1"); VOZ1=$(linha "voz da chamada" "$SID1")
case "$SEG1|$VOZ1" in
  *"end_silence_ms=2500 (profile:$PERFIL)"*"|"*"perfil=$PERFIL "*"stt_language=pt (profile:$PERFIL)"*)
    ok "F1 a chamada pelo alias abriu com end_silence_ms=2500 e stt_language=pt do perfil $PERFIL" ;;
  *) falha "F1 a chamada pelo alias nao trouxe o perfil: seg=[${SEG1:-<sem linha>}] voz=[${VOZ1:-<sem linha>}]" ;;
esac
W1=$(janela5 "$SID1")
[ "${W1:-0}" -ge 3000 ] && ok "F2 a fala do m4 juntou as duas palavras pelo perfil: ${W1} ms" \
                        || falha "F2 a fala do m4 fechou em ${W1:-?} ms — o silencio de fim do perfil nao valeu"
read -r N1 M1C <<< "$(telemetria "$SID1")"
R1=$(chq "SELECT speech_profile_id, JSONExtractString(segmentation_scope, 'end_silence_ms') FROM speech_stream_summaries FINAL WHERE session_id = '$SID1' FORMAT TSV")
D1=$(chq "SELECT countIf(speech_profile_id = '$PERFIL'), count() FROM speech_collect_outcomes FINAL WHERE session_id = '$SID1' FORMAT TSV")
if [ "$N1" != 1 ]; then
  incon "F3 $N1 resumo(s) da sessao $SID1 no ClickHouse — telemetria nao chegou"
elif [ "$R1" = "$PERFIL	profile" ] && [ "$(printf '%s' "$D1" | cut -f1)" = "$(printf '%s' "$D1" | cut -f2)" ] && [ "$(printf '%s' "$D1" | cut -f2)" -ge 1 ]; then
  ok "F3 telemetria grava o perfil: resumo=[$R1] desfechos com perfil=[$D1]"
else
  falha "F3 telemetria da chamada pelo alias sem o perfil: resumo=[$R1] desfechos(com perfil, total)=[$D1]"
fi

# ── FASE 2 (CONTROLE) ──
SID2=$(chamada "$POOL")
[ -n "$SID2" ] || { incon "FASE 2 a chamada pelo pool nao abriu sessao"; fim; }
SEG2=$(linha "segmentacao da fala" "$SID2"); VOZ2=$(linha "voz da chamada" "$SID2")
case "$SEG2|$VOZ2" in
  *"end_silence_ms=700 (global)"*"|"*"perfil=- "*) ok "C1 CONTROLE pelo pool direto: end_silence_ms=700 (global) e sem perfil" ;;
  *) falha "C1 a chamada pelo pool nao ficou na config do tenant: seg=[${SEG2:-<sem linha>}] voz=[${VOZ2:-<sem linha>}]" ;;
esac
W2=$(janela5 "$SID2")
[ -n "$W2" ] && [ "$W2" -lt 2500 ] && ok "C2 CONTROLE a mesma fala do m4 separou: ${W2} ms" \
                                   || falha "C2 a fala do m4 durou ${W2:-?} ms sem perfil"
read -r N2 _ <<< "$(telemetria "$SID2")"
R2=$(chq "SELECT isNull(speech_profile_id) FROM speech_stream_summaries FINAL WHERE session_id = '$SID2' FORMAT TSV")
if [ "$N2" != 1 ]; then incon "C3 $N2 resumo(s) da sessao $SID2 — telemetria nao chegou"
elif [ "$R2" = 1 ]; then ok "C3 CONTROLE a telemetria da chamada pelo pool grava speech_profile_id NULL"
else falha "C3 a chamada sem perfil gravou speech_profile_id nao-nulo"; fi

# ── FASE 3 ──
T2=$(date -u +%Y-%m-%dT%H:%M:%SZ)
st=$(put_perfil "{\"description\":\"probe VOZ-25\",\"stt_model\":\"$MODELO_FALSO\"}")
n=0; for _ in $(seq 1 15); do n=$(invalidacoes "$T2"); [ "${n:-0}" -ge 1 ] && break; sleep 1; done
if [ "$st" != 200 ] || [ "${n:-0}" -lt 1 ]; then
  incon "M1 perfil com modelo falso nao chegou ao gateway (http $st, invalidacoes $n)"
else
  sleep 3
  SID3=$(chamada "$ALIAS")
  E3=$(docker logs --since "$T2" "$GW" 2>&1 | grep -c "speaches STT: .*modelo $MODELO_FALSO)")
  if [ -z "$SID3" ]; then incon "M1 a chamada nao abriu sessao"
  elif [ "${E3:-0}" -ge 1 ]; then ok "M1 o speaches recebeu o modelo do perfil: $E3 erro(s) nomeando $MODELO_FALSO"
  else falha "M1 nenhum pedido ao speaches com o modelo do perfil ($MODELO_FALSO) — o modelo nao chegou ao servico"; fi
fi

# ── FASE 4 — o default do TENANT, sem perfil (VOZ-17) ──
# Até aqui modelo/língua/voz sem perfil só podiam vir do env do gateway: um tenant não tinha onde
# escolher, e a chamada pelo pool direto (C1) imprimia `(env)` nos quatro campos. A prova não é a
# chave no config-api — é a CHAMADA abrir com o modelo do tenant e transcrever com ele.
STT_ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | sed -n 's/^PLUGHUB_WEBRTC_STT_MODEL=//p')
STT_ALT=$(docker exec "$GW" python3 -c "
import json, urllib.request, os
url = os.environ.get('PLUGHUB_WEBRTC_SPEACHES_URL', '')
d = json.load(urllib.request.urlopen(url + '/v1/models', timeout=20))['data']
outros = [m['id'] for m in d if m.get('task') == 'automatic-speech-recognition'
          and m['id'] != os.environ.get('PLUGHUB_WEBRTC_STT_MODEL')]
print(outros[0] if outros else '')" 2>/dev/null | tr -d '\r')
if [ -z "$STT_ALT" ]; then
  incon "T1 o servico so tem um modelo de STT instalado — sem alternativa, a camada do tenant nao e discriminavel"
else
  T4=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  MEXEU_NS=1
  st=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$CFG/config/webrtc/stt_model" \
        -d "{\"value\":\"$STT_ALT\",\"tenant_id\":\"$TENANT\"}")
  n=0; for _ in $(seq 1 15); do
    n=$(docker logs --since "$T4" "$GW" 2>&1 | grep -c "segmentacao da fala invalidada")
    [ "${n:-0}" -ge 1 ] && break; sleep 1
  done
  if [ "$st" != 200 ] || [ "${n:-0}" -lt 1 ]; then
    incon "T1 o default do tenant nao chegou ao gateway (http $st, invalidacoes $n)"
  else
    sleep 3
    SID4=$(chamada "$POOL")
    VOZ4=$(linha "voz da chamada" "$SID4")
    if [ -z "$SID4" ]; then
      incon "T1 a chamada nao abriu sessao"
    else
      case "$VOZ4" in
        *"perfil=- "*"stt_model=$STT_ALT (tenant)"*)
          ok "T1 sem perfil, a chamada usou o modelo do TENANT: $STT_ALT (env era $STT_ENV)" ;;
        *) falha "T1 a chamada sem perfil nao pegou o default do tenant ($STT_ALT): voz=[${VOZ4:-<sem linha>}]" ;;
      esac
      W4=$(janela5 "$SID4")
      [ -n "$W4" ] && [ "$W4" -gt 0 ] \
        && ok "T2 e transcreveu com ele: 5a janela de fala ${W4} ms" \
        || falha "T2 nenhuma janela de fala na chamada com o modelo do tenant — escolheu e nao falou"
    fi
  fi
fi

limpa
fim
