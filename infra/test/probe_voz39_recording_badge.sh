#!/usr/bin/env bash
# probe_voz39_recording_badge.sh — 2026-09-24  (VOZ-39)
#
# PERGUNTA: o widget sabe, por um estado do SERVIDOR, que a chamada está sendo gravada — depois do
# aviso, até o fim — e o pool que não grava não manda estado nenhum?
#
# O ESTADO QUE O ORIGINOU: o aviso de gravação chegava ao widget como mensagem de chat, que rola e
# some; quem olhasse a conversa depois não sabia que a chamada era gravada. O padrão das salas de
# conferência (a referência da decisão do dono de 2026-09-21) é uma faixa fixa "● Gravando". O
# gravador passou a anunciar `webrtc.recording {state}` a cada MUDANÇA, e os dois widgets de demo a
# mostram.
#
# RAMOS (pools e skill de fixture, criados se ausentes; o skill é promovido a cada rodada):
#   B1 pool que GRAVA: `recording` chega, e depois do aviso de sistema
#   B2 o `stopped` chega antes do `webrtc.session_closed`
#   B3 sequência exata ['recording', 'stopped'] — sem repetição, sem `paused`
#   L1 o gateway registrou a parte 1 INICIADA para a sessão (a faixa acompanha um fato, não um desejo)
#   L2 (VOZ-44) e a parte foi CONFIRMADA gravando pelo SFU — nos B* o cliente ENTRA na sala e publica
#      um tom (como o widget com microfone), que é o que faz o egress sair de STARTING
#   N1 CONTROLE: pool sem `recording` — nenhum `webrtc.recording`
#   E1 (VOZ-44) cliente SEM midia publicada: o egress fica em STARTING (medido: a parte inteira, e só
#      vira ABORTED no stop) — a faixa NÃO acende
#   E2 (VOZ-44) o gateway diz que o egress NÃO confirmou, e nunca chama a parte de GRAVANDO
# ⚠️ `paused` (bloco mascarado) existe só na perna SIP, que não tem tela; ele é coberto pelos testes
# de unidade (`TestEstadoNoWidget`) e não por este probe.
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq).
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL_REC="probe_voz39_rec"
POOL_NOREC="probe_voz39_norec"
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

echo "probe_voz39_recording_badge — a faixa fixa de gravacao no widget"
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
[ -n "$TOKEN" ] && [ -n "$IMG" ] || { incon "login do admin falhou ou gateway fora do ar"; fim; }
H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')

prepara() {  # $1 pool, $2 recording (true|false) → 0 pronto
  local pool="$1" rec="$2" st
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$pool")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$pool\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_voz39_recording_badge (recording=$rec)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"],\"recording\":$rec}}")
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

exercicio() {  # $1 pool, $2 modo
  timeout 300 docker run --rm -i --name "probe_voz39_$$_$RANDOM" --network "$NET" --entrypoint python \
    $ENV -e POOL="$1" -e MODE="$2" "$IMG" - < infra/test/_voz39_badge_exercise.py 2>&1
}
julga() {
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      INFO\ *) echo "  INFO    ${l#INFO }" | cut -c1-400;; esac
  done <<< "$(printf '%s\n' "$1" | grep -E '^(OK|FALHA|INCONCL|INFO) ')"
}

if prepara "$POOL_REC" true; then
  sleep 3
  T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  OUT=$(exercicio "$POOL_REC" grava)
  julga "$OUT"
  [ "$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) B[123] ')" -ge 1 ] \
    || incon "B* o exercicio nao produziu veredicto: $(printf '%s\n' "$OUT" | tail -3 | tr '\n' ' ' | cut -c1-300)"
  SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
  if [ -n "$SID" ]; then
    docker logs --since "$T0" "$GW" 2>&1 | grep "parte 1 INICIADA" | grep -q "session=$SID" \
      && ok "L1 o gateway iniciou a parte 1 da gravacao de $SID" \
      || falha "L1 nenhuma parte INICIADA para $SID no log — a faixa acompanharia um desejo, nao um fato"
    CONF=$(docker logs --since "$T0" "$GW" 2>&1 | grep "session=$SID" | grep "parte 1 GRAVANDO" | sed 's/.*confirmou/confirmou/' | cut -c1-80)
    [ -n "$CONF" ] && ok "L2 o SFU confirmou o egress antes da faixa: $CONF" \
                   || falha "L2 a parte 1 de $SID nunca foi confirmada GRAVANDO — a faixa acendeu sem fato"
  fi
fi

if [ -n "${SID:-}" ]; then
  T1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  OUT=$(exercicio "$POOL_REC" aborta)
  julga "$OUT"
  printf '%s\n' "$OUT" | grep -qE '^(OK|FALHA) E1 ' \
    || incon "E1 sem veredicto: $(printf '%s\n' "$OUT" | tail -2 | tr '\n' ' ' | cut -c1-300)"
  SIDA=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p' | head -1)
  if [ -n "$SIDA" ]; then
    LOGA=$(docker logs --since "$T1" "$GW" 2>&1 | grep "session=$SIDA")
    NCF=$(printf '%s\n' "$LOGA" | grep -c "NAO confirmou que grava")
    GRV=$(printf '%s\n' "$LOGA" | grep -c "parte 1 GRAVANDO")
    { [ "$NCF" -ge 1 ] && [ "$GRV" = 0 ]; } \
      && ok "E2 o gateway disse que o egress nao confirmou e nunca chamou a parte de GRAVANDO" \
      || falha "E2 nao_confirmou=$NCF gravando=$GRV para $SIDA"
  fi
fi

if prepara "$POOL_NOREC" false; then
  sleep 3
  julga "$(exercicio "$POOL_NOREC" nao_grava)"
fi
fim
