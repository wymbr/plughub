#!/usr/bin/env bash
# probe_voz11_media_degradation.sh — VOZ-11 fatias b e d, ao vivo.
#
# Modelo de videoconferência: câmera e microfone desligados são ESCOLHA; o que se nomeia é a
# INCAPACIDADE de quem atende. Num pool que oferece VÍDEO e é atendido por IA nativa (que consome
# texto, e áudio só pelo bot leg), o vídeo do cliente não é recebido por ninguém — isso é dito.
#
#   D1 o cliente recebe `webrtc.notice` de VÍDEO (`attendant_cannot_consume`), DEPOIS do `webrtc.ready`
#   D2 o stream tem `media.degraded` (agents_only) da instância, vídeo, e o `system_notice` (all)
#   T1 o microfone que o cliente publicou vira `media.track` customer/audio/on no stream — o estado
#      REAL vem do webhook de trilha do SFU, não do teto
#   N1 CONTROLE: pool só-áudio com a mesma IA — nenhum `webrtc.notice`, nenhum `media.degraded`
#      (bot leg ligado: a IA ouve; não há incapacidade a dizer)
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq). ~2 min.
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL_VID="probe_voz11_video"
POOL_AUD="probe_voz11_audio"
SKILL="skill_probe_voz39_v1"
FIXTURE="infra/test/fixtures/skill_probe_voz39_v1.json"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
fim() {
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
  echo " VERDE"; exit 0
}

echo "probe_voz11_media_degradation — incapacidade de midia dita, estado real pelo SFU"
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
[ -n "$TOKEN" ] && [ -n "$IMG" ] || { incon "login do admin falhou ou gateway fora do ar"; fim; }
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

prepara() {  # $1 pool, $2 kinds JSON → 0 pronto
  local pool="$1" kinds="$2" st
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$pool")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$pool\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_voz11_media_degradation\",\"media_policy\":{\"customer_publish\":$kinds,\"agent_publish\":$kinds,\"recording\":false}}")
    [ "$st" = 201 ] || { incon "pool $pool nao criado (http $st)"; return 1; }
  fi
  local sn pm
  sn=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$pool/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  pm=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$pool/promote" -d '{}')
  [ "$sn" = 200 ] && [ "$pm" = 200 ] || { incon "deploy em $pool recusado (set-next=$sn promote=$pm)"; return 1; }
  for _ in $(seq 1 40); do
    [ -n "$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$pool:instances" | head -1)" ] && return 0
    sleep 1
  done
  incon "nenhuma instancia de IA em $pool 40 s depois do promote"; return 1
}

PUB=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
{ [ "$PUB" = 200 ] || [ "$PUB" = 201 ]; } || { incon "fixture $SKILL nao publicada (http $PUB)"; fim; }
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')

exercicio() {  # $1 pool
  timeout 200 docker run --rm -i --name "probe_voz11_$$_$RANDOM" --network "$NET" --entrypoint python \
    $ENV -e POOL="$1" "$IMG" - < infra/test/_voz11_degradation_exercise.py 2>&1
}
# O stream de uma sessão encerrada expira — lê-se logo depois do exercício. Uma linha por entrada:
# "<type> <visibility> <payload>".
stream_de() {
  docker exec "$REDIS" redis-cli --raw XRANGE "session:$1:stream" - + | python3 -c '
import sys
linhas = sys.stdin.read().split("\n")
ent, k = {}, None
out = []
for l in linhas:
    if "-" in l and l.split("-")[0].isdigit() and len(l.split("-")) == 2 and l.split("-")[1].isdigit():
        if ent: out.append(ent)
        ent, k = {}, None
    elif k is None:
        k = l
    else:
        ent[k] = l; k = None
if ent: out.append(ent)
for e in out:
    print(e.get("type", "?"), e.get("visibility", "?"), e.get("payload", ""))
'
}

# ── vídeo oferecido, IA atende ───────────────────────────────────────────────
if prepara "$POOL_VID" '["audio","video"]'; then
  sleep 3
  OUT=$(exercicio "$POOL_VID")
  SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
  if [ -z "$SID" ] || printf '%s\n' "$OUT" | grep -q '^INCONCL'; then
    incon "D* exercicio sem sessao: $(printf '%s\n' "$OUT" | tail -3 | tr '\n' ' ' | cut -c1-300)"
  else
    ST=$(stream_de "$SID")
    FR=$(printf '%s\n' "$OUT" | sed -n 's/^FRAMES //p')
    echo "  INFO    frames: $(echo "$FR" | cut -c1-200)"
    if printf '%s\n' "$OUT" | grep -q '^NOTICE video attendant_cannot_consume True'; then
      pos_r=$(echo "$FR" | tr ' ' '\n' | grep -n '^webrtc.ready$' | head -1 | cut -d: -f1)
      pos_n=$(echo "$FR" | tr ' ' '\n' | grep -n '^webrtc.notice$' | head -1 | cut -d: -f1)
      [ -n "$pos_r" ] && [ -n "$pos_n" ] && [ "$pos_r" -lt "$pos_n" ] \
        && ok "D1 aviso de video ao cliente, depois do webrtc.ready (frames $pos_r < $pos_n)" \
        || falha "D1 aviso de video chegou ANTES do webrtc.ready (ready=$pos_r notice=$pos_n)"
    else
      falha "D1 nenhum webrtc.notice de video (attendant_cannot_consume) — $(printf '%s\n' "$OUT" | grep '^NOTICE' | tr '\n' ' ')"
    fi
    DEG=$(printf '%s\n' "$ST" | grep '^media.degraded agents_only ' | grep -c '"kind": "video".*attendant_cannot_consume')
    NOT=$(printf '%s\n' "$ST" | grep -c '^system_notice "all" ')
    { [ "$DEG" -ge 1 ] && [ "$NOT" -ge 1 ]; } \
      && ok "D2 stream: media.degraded de video agents_only=$DEG, system_notice ao cliente=$NOT" \
      || falha "D2 stream: media.degraded de video=$DEG system_notice=$NOT (esperado >=1 e >=1)"
    TRK=$(printf '%s\n' "$ST" | grep '^media.track agents_only ' | grep -c '"role": "customer", "kind": "audio", "state": "on"')
    [ "$TRK" -ge 1 ] \
      && ok "T1 o microfone publicado pelo cliente virou media.track customer/audio/on (webhook do SFU)" \
      || falha "T1 nenhum media.track customer/audio/on no stream de $SID"
  fi
fi

# ── controle: só áudio, mesma IA ─────────────────────────────────────────────
if prepara "$POOL_AUD" '["audio"]'; then
  sleep 3
  OUT=$(exercicio "$POOL_AUD")
  SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
  if [ -z "$SID" ] || printf '%s\n' "$OUT" | grep -q '^INCONCL'; then
    incon "N1 exercicio sem sessao: $(printf '%s\n' "$OUT" | tail -3 | tr '\n' ' ' | cut -c1-300)"
  else
    ST=$(stream_de "$SID")
    NN=$(printf '%s\n' "$OUT" | grep -c '^NOTICE')
    ND=$(printf '%s\n' "$ST" | grep -c '^media.degraded ')
    NT=$(printf '%s\n' "$ST" | grep -c '^media.track ')
    if [ "$NT" -lt 1 ]; then
      incon "N1 sem media.track no controle: o stream nao foi lido (testemunha de presenca ausente)"
    else
      { [ "$NN" = 0 ] && [ "$ND" = 0 ]; } \
        && ok "N1 controle so-audio: 0 avisos, 0 media.degraded (com $NT media.track lidos)" \
        || falha "N1 controle so-audio disse degradacao: avisos=$NN media.degraded=$ND"
    fi
  fi
fi
fim
