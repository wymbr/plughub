#!/usr/bin/env bash
# probe_webrtc_voice_collect.sh — 2026-09-16  (VOZ-05 fatia 5b)
#
# PERGUNTA: numa chamada WebRTC atendida por IA, um menu que declara coleta por TECLADO e/ou FALA
# é respondido por tecla e por fala do CLIENTE — e só do cliente, só no modo declarado — e produz
# UM desfecho (valor, inválido, prazo) que o fluxo segue?
#
# O ESTADO QUE O ORIGINOU (medido antes, `TODO.md` § VOZ-05 fatia 5): qualquer fala do cliente
# respondia qualquer menu (o bridge a entregava crua; "espera um pouco" virou escolha de botão), não
# havia DTMF nenhum no canal, e o SFU entrega a tecla a TODOS na sala, atendente incluído.
#
# RAMOS (exercício, cada um uma chamada com participante LiveKit real): H prompt falado com as
#   teclas · A0 a tecla do intruso chegou ao ouvinte · A1 tecla de outro participante não responde · K1 CONTROLE tecla do cliente responde ·
#   K2 código de vários dígitos com terminador · R1 fala em campo só de teclado não responde ·
#   R0 a fala do R1 chegou e foi classificada · R2 CONTROLE teclado responde o mesmo campo · V1 "opção dois" dita responde · I1 dois
#   inválidos → on_invalid com a mensagem antes · T1 nada → on_timeout depois do prazo
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
POOL="probe_voz05b_voice"
SKILL="skill_probe_voice_collect_v1"
FIXTURE="infra/test/fixtures/skill_probe_voice_collect_v1.json"
FALHA=0
INCONCL=0

# A linha existe no log do gateway? `grep -c`, NUNCA `grep -q`: com `pipefail`, o `-q` sai no primeiro
# match, o `docker logs` morre de SIGPIPE e o pipeline reprova tendo ACHADO a linha — intermitente,
# tanto mais quanto maior o log (medido 2026-09-16: A0 e R0 vermelhos com a linha no log).
log_tem() {
  local n
  for _ in 1 2 3 4 5; do
    n=$(docker logs --since 20m "$GW" 2>&1 | grep -c "$1")
    [ "${n:-0}" -gt 0 ] && return 0
    sleep 2
  done
  return 1
}

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " o menu de voz e teclado responde ao cliente, e so a ele?"
echo "════════════════════════════════════════════════════════════════════"

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then
  incon "login do admin falhou ou gateway fora do ar — nada medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_webrtc_voice_collect (VOZ-05 fatia 5b)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
  fi
  BODY=$(mktemp)
  PUB=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/$SKILL" --data-binary "@$FIXTURE")
  PUBMSG=$(head -c 300 "$BODY")
  SN=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/pools/$POOL/slots/next" -d "{\"skill_id\":\"$SKILL\",\"config_json\":{}}")
  PM=$(curl -s -o "$BODY" -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools/$POOL/promote" -d '{}')
  rm -f "$BODY"
  if [ "$PUB" != 200 ] && [ "$PUB" != 201 ] || [ "$SN" != 200 ] || [ "$PM" != 200 ]; then
    incon "fixture nao implantada (publish=$PUB set-next=$SN promote=$PM) $PUBMSG"
  else
    INST=""
    for _ in $(seq 1 40); do
      INST=$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" | head -1)
      [ -n "$INST" ] && break
      sleep 1
    done
    if [ -z "$INST" ]; then
      incon "nenhuma instancia de IA em $POOL 40 s depois do promote"
    else
      sleep 3
      ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
      name="probe_voz05b_$$_$RANDOM"
      OUT=$(timeout "${EXERCISE_TIMEOUT_S:-600}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            $ENV -e POOL="$POOL" "$IMG" - < infra/test/_webrtc_voice_collect_exercise.py 2>&1)
      [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
      while IFS= read -r l; do
        case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";; esac
      done <<< "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL) ')"
      # A0 — o A1 ("a tecla do intruso nao respondeu") passaria com uma tecla que nunca chegou: o
      # ouvinte tem de ter recebido a tecla do outro participante e a recusado nesta sessao
      SIDK=$(printf '%s
' "$OUT" | sed -n 's/^SIDK //p')
      if [ -z "$SIDK" ]; then
        incon "A0 o ramo K nao abriu sessao — nao se sabe se a tecla do intruso chegou"
      elif log_tem "tecla de 'agent-probe-voz05b' ignorada .*session=$SIDK"; then
        ok "A0 a tecla do outro participante chegou ao ouvinte e foi recusada (session=$SIDK)"
      else
        falha "A0 nenhuma tecla de outro participante recebida na sessao $SIDK — o A1 nao prova nada"
      fi
      # R0 — o R1 ("a fala nao respondeu") passaria com uma fala que nunca chegou: o gateway tem de
      # ter ouvido a fala do cliente e a classificado como registro nesta sessao
      SIDR=$(printf '%s
' "$OUT" | sed -n 's/^SIDR //p')
      if [ -z "$SIDR" ]; then
        incon "R0 o ramo R nao abriu sessao — nao se sabe se a fala chegou"
      elif log_tem "fala do cliente NAO responde o menu .*session=$SIDR"; then
        ok "R0 a fala do cliente chegou ao gateway e ficou como registro (session=$SIDR)"
      else
        falha "R0 nenhuma fala do cliente classificada na sessao $SIDR — o R1 nao prova nada"
      fi
      N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA|INCONCL) (H|A1|K1|K2|R1|R2|V1|I1|T1) ')
      [ "$N" -ge 9 ] || falha "exercicio emitiu $N de 9 veredictos: $(printf '%s' "$OUT" | grep -vE '^(OK|FALHA|INCONCL) ' | tail -3 | tr '\n' ' ' | cut -c1-300)"
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
