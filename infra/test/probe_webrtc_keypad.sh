#!/usr/bin/env bash
# probe_webrtc_keypad.sh — 2026-09-16  (VOZ-05 fatia 5c)
#
# PERGUNTA: o widget recebe do gateway o que precisa para desenhar o teclado pelo DOMÍNIO da coleta,
# e a resposta que chega pela TELA — inclusive a do PIN mascarado — passa pela mesma regra da tecla,
# sem o valor protegido aparecer no log?
#
# RAMOS: F1 menu de botões leva a coleta · F2 PIN leva domínio, tamanhos e máscara · S2 PIN curto
#   (longo demais) recusado pela tela e fora do menu · S3 CONTROLE PIN válido chega ao menu · L0 a recusa foi
#   registrada nesta sessão (sem ela o L1 não prova nada) · L1 o PIN não aparece no log do gateway
#
# O teclado no NAVEGADOR (DTMF pelo SFU no menu comum; campo protegido sem DTMF nenhum no PIN) é
# o roteiro assistido de `docs/arcos/arc15-webrtc.md` § coleta, com a mesma fixture.
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
POOL="probe_voz05c_keypad"
SKILL="skill_probe_keypad_v1"
FIXTURE="infra/test/fixtures/skill_probe_keypad_v1.json"
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
echo " o widget tem o teclado da coleta, e a tela segue a regra da tecla?"
echo "════════════════════════════════════════════════════════════════════"

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then
  incon "login do admin falhou ou gateway fora do ar — nada medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_webrtc_keypad e do roteiro do widget (VOZ-05 fatia 5c)\",\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
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
      name="probe_voz05c_$$_$RANDOM"
      OUT=$(timeout "${EXERCISE_TIMEOUT_S:-240}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            $ENV -e POOL="$POOL" "$IMG" - < infra/test/_webrtc_keypad_exercise.py 2>&1)
      [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
      while IFS= read -r l; do
        case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";; esac
      done <<< "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL) ')"
      SID=$(printf '%s\n' "$OUT" | sed -n 's/^SID //p')
      PIN=$(printf '%s\n' "$OUT" | sed -n 's/^PIN //p')
      RUIM=$(printf '%s\n' "$OUT" | sed -n 's/^RUIM //p')
      if [ -z "$SID" ] || [ -z "$PIN" ]; then
        incon "L0 o exercicio nao abriu sessao — log nao conferido"
      elif log_tem "resposta pela tela INVALIDA .*session=$SID"; then
        ok "L0 a recusa do PIN pela tela foi registrada na sessao $SID"
        n=$(docker logs --since 20m "$GW" 2>&1 | grep -cE "$PIN|${RUIM:-x_sem_valor_x}")
        [ "${n:-0}" -eq 0 ] && ok "L1 nem o PIN aceito nem o recusado aparecem no log do gateway"                              || falha "L1 valor protegido aparece $n vez(es) no log do gateway"
      else
        falha "L0 nenhuma recusa pela tela registrada na sessao $SID — o L1 nao prova nada"
      fi
      N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA|INCONCL) (F1|F2|S2|S3) ')
      [ "$N" -ge 4 ] || falha "exercicio emitiu $N de 4 veredictos: $(printf '%s' "$OUT" | grep -vE '^(OK|FALHA|INCONCL) ' | tail -3 | tr '\n' ' ' | cut -c1-300)"
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
