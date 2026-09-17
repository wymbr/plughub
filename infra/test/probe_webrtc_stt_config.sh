#!/usr/bin/env bash
# probe_webrtc_stt_config.sh — 2026-09-17  (VOZ-21)
#
# PERGUNTA: a segmentação da fala de uma chamada WebRTC vem da config do TENANT no config-api —
# mudar o valor na config muda a próxima chamada, sem restart, com a procedência dita no log; valor
# inválido cai no default dizendo qual chave; e removido o override, a chamada seguinte volta ao global?
#
# O ESTADO QUE O ORIGINOU: limiar de energia, silêncio de fim, lacuna, fala mínima/máxima e VAD eram
# constantes no construtor do `SpeachesSTTProvider` — nada a recalibrar por instalação (ADR V13).
#
# RAMOS:
#   K0 as 6 chaves `webrtc.stt_*` existem no config-api (o seed chegou)
#   K1 o tenant não tem override próprio nelas (senão o probe NÃO mexe: INCONCLUSIVO)
#   FASE 1 — override do tenant: stt_end_silence_ms=2500 e stt_gap_ms="abc" (inválido)
#     P1 o gateway recebeu o config.changed e o cache foi invalidado (a linha é do próprio `invalidate`)
#     T1 a chamada abriu o STT com end_silence_ms=2500 (tenant)
#     T2 o inválido caiu no default nomeado: gap_ms=700 (default: valor invalido), com o ERRO da chave
#     T3 a fala do m4 (menu SEM ajuste próprio) juntou "Cancelar." + 1,2 s + "Atendente.": janela >= 3 s
#   FASE 2 — override removido (CONTROLE)
#     C1 a chamada seguinte abriu com end_silence_ms=700 (global) — esta é a prova da INVALIDAÇÃO: na
#        fase 1 o T1 passa com cache vazio (container recém-subido), sem invalidação nenhuma
#     C2 a mesma fala do m4 separou: janela < 2,5 s
#
# Reusa a fixture e o exercício do probe_webrtc_speech_tuning.sh; os veredictos DO EXERCÍCIO são só
# informação aqui (na fase 1 o S2 dele reprova por construção). Os overrides são removidos na saída.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
CFG="${CONFIG_API:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz18_speech"
SKILL="skill_probe_speech_tuning_v1"
FIXTURE="infra/test/fixtures/skill_probe_speech_tuning_v1.json"
KEYS="stt_energy_threshold stt_end_silence_ms stt_gap_ms stt_min_speech_ms stt_max_speech_ms stt_vad_filter"
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

echo "════════════════════════════════════════════════════════════════════"
echo " a segmentacao da fala vem da config do tenant, e volta quando sai?"
echo "════════════════════════════════════════════════════════════════════"

command -v jq >/dev/null || { echo "  INCONCL jq ausente — rode de dentro do WSL"; exit 2; }
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then incon "login do admin falhou ou gateway fora do ar — nada medido"; fim; fi
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

# K0/K1
ENT=$(curl -s --max-time 10 "$CFG/config/webrtc?tenant_id=$TENANT")
PROV=$(curl -s --max-time 10 "$CFG/config/webrtc/_provenance?tenant_id=$TENANT")
faltam=""; proprios=""
for k in $KEYS; do
  [ "$(printf '%s' "$ENT" | jq --arg k "$k" '.entries | has($k)')" = true ] || faltam="$faltam $k"
  [ "$(printf '%s' "$PROV" | jq -r --arg k "$k" '.keys[$k].tenant_present // false')" = true ] && proprios="$proprios $k"
done
[ -z "$faltam" ] && ok "K0 as 6 chaves webrtc.stt_* existem no config-api" || { falha "K0 faltam no config-api:$faltam (o seed nao chegou)"; fim; }
[ -z "$proprios" ] && ok "K1 o tenant nao tem override proprio nas chaves" \
                   || { incon "K1 $TENANT ja tem override em:$proprios — o probe nao mexe em config real"; fim; }

limpa() {
  [ "$MEXEU" = 1 ] || return 0
  for k in stt_end_silence_ms stt_gap_ms; do
    curl -s -o /dev/null -X DELETE "${H[@]}" "$CFG/config/webrtc/$k?tenant_id=$TENANT"
  done
  MEXEU=0
}
trap limpa EXIT INT TERM

put() {  # chave, valor JSON
  curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$CFG/config/webrtc/$1" -d "{\"value\":$2,\"tenant_id\":\"$TENANT\"}"
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

chamada() {  # imprime o SID da chamada (os veredictos do exercicio vao para stderr como INFO)
  local out
  out=$(timeout 400 docker run --rm -i --name "probe_voz21_$$_$RANDOM" --network "$NET" --entrypoint python \
        $ENV -e POOL="$POOL" "$IMG" - < infra/test/_webrtc_speech_tuning_exercise.py 2>&1)
  printf '%s\n' "$out" | grep -E '^(OK|FALHA|INCONCL|INFO) ' | sed 's/^/  INFO    exercicio: /' >&2
  printf '%s\n' "$out" | sed -n 's/^SID //p' | head -1
}
janela5() {  # janela (ms) da 5a fala do cliente no stream da sessao
  docker exec "$REDIS" redis-cli --raw XRANGE "session:$1:stream" - + 2>/dev/null | grep '"speech"' \
    | jq -r '((.content.speech.end_ms // 0) - (.content.speech.start_ms // 0))' 2>/dev/null | sed -n 5p
}
linha_seg() { docker logs --since 15m "$GW" 2>&1 | grep "segmentacao da fala session=$1" | tail -1; }
invalidacoes() { docker logs --since "$1" "$GW" 2>&1 | grep -c "segmentacao da fala invalidada"; }

# ── FASE 1 ──
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MEXEU=1
s1=$(put stt_end_silence_ms 2500); s2=$(put stt_gap_ms '"abc"')
if [ "$s1" != 200 ] || [ "$s2" != 200 ]; then incon "override nao gravado (http $s1 / $s2)"; fim; fi
n=0; for _ in $(seq 1 15); do n=$(invalidacoes "$T0"); [ "${n:-0}" -ge 2 ] && break; sleep 1; done
[ "${n:-0}" -ge 2 ] && ok "P1 o gateway recebeu o config.changed das 2 chaves e invalidou ($n)" \
                    || falha "P1 $n invalidacao(oes) no gateway desde $T0 — a mudanca so valeria no boot"
SID1=$(chamada)
if [ -z "$SID1" ]; then incon "FASE 1 a chamada nao abriu sessao"; fim; fi
L1=$(linha_seg "$SID1")
case "$L1" in *"end_silence_ms=2500 (tenant)"*) ok "T1 a chamada abriu o STT com end_silence_ms=2500 (tenant)" ;;
  *) falha "T1 segmentacao da sessao $SID1 nao traz o override do tenant: ${L1:-<sem linha>}" ;; esac
nerr=$(docker logs --since "$T0" "$GW" 2>&1 | grep -c "stt_gap_ms='abc' invalido")
case "$L1" in *"gap_ms=700 (default: valor invalido)"*)
  [ "${nerr:-0}" -ge 1 ] && ok "T2 o valor invalido caiu no default nomeado, com o ERRO da chave" \
                         || falha "T2 default aplicado sem o ERRO nomeando stt_gap_ms" ;;
  *) falha "T2 gap_ms nao caiu no default por valor invalido: ${L1:-<sem linha>}" ;; esac
W1=$(janela5 "$SID1")
[ "${W1:-0}" -ge 3000 ] && ok "T3 a fala do m4 (sem ajuste de menu) juntou as duas palavras pela config do tenant: ${W1} ms" \
                        || falha "T3 a fala do m4 fechou em ${W1:-?} ms — o silencio de fim do tenant nao valeu"

# ── FASE 2 (CONTROLE) ──
T1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
limpa
n=0; for _ in $(seq 1 15); do n=$(invalidacoes "$T1"); [ "${n:-0}" -ge 2 ] && break; sleep 1; done
[ "${n:-0}" -ge 2 ] || incon "FASE 2 o gateway nao recebeu a remocao dos overrides ($n) — o controle nao mede"
sleep 8
SID2=$(chamada)
if [ -z "$SID2" ]; then incon "FASE 2 a chamada nao abriu sessao"; fim; fi
L2=$(linha_seg "$SID2")
case "$L2" in *"end_silence_ms=700 (global)"*) ok "C1 CONTROLE removido o override, a chamada seguinte abriu com end_silence_ms=700 (global)" ;;
  *) falha "C1 a chamada depois da remocao nao voltou ao global: ${L2:-<sem linha>}" ;; esac
W2=$(janela5 "$SID2")
[ -n "$W2" ] && [ "$W2" -lt 2500 ] && ok "C2 CONTROLE a mesma fala do m4 separou: ${W2} ms" \
                                   || falha "C2 a fala do m4 durou ${W2:-?} ms sem o override"
fim
