#!/usr/bin/env bash
# probe_menu_signal_contract.sh — 2026-09-16  (MEN-07 · VOZ-05 fatia 5a)
#
# PERGUNTA: um step bloqueado distingue a RESPOSTA do cliente de um SINAL da plataforma — e o
# desfecho da coleta que o canal manda (timeout, inválido) chega ao fluxo pela saída declarada?
#
# O DEFEITO (MEN-07): interrupções de @mention viajavam em `menu:result`, a fila da resposta do
# cliente, e o engine as reconhecia por JSON.parse do texto. Um cliente que DIGITASSE
# `{"_mention_trigger_step":"<passo>"}` saltava o fluxo para qualquer passo. Conserto estrutural:
# sinais numa fila própria (`menu:signal`), que só o bridge escreve.
#
# RAMOS (no exercício, cada um uma sessão WebRTC de texto com a fixture `skill_probe_collect_v1`):
#   S1 cliente digita a interrupção forjada → resposta, nunca salto
#   S2 CONTROLE a mesma interrupção na fila de sinal → salto
#   S3 desfecho timeout do canal → on_timeout · S4 desfecho invalid → on_invalid
#   S5 CONTROLE resposta comum → on_success
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
POOL="probe_voz05_collect"
SKILL="skill_probe_collect_v1"
FIXTURE="infra/test/fixtures/skill_probe_collect_v1.json"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " o step distingue resposta do cliente de sinal da plataforma?"
echo "════════════════════════════════════════════════════════════════════"

TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$TOKEN" ] || [ -z "$IMG" ]; then
  incon "login do admin falhou ou gateway fora do ar — nada medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":2,\"description\":\"fixture do probe_menu_signal_contract (MEN-07, VOZ-05 fatia 5a)\",\"media_policy\":{\"customer_publish\":[],\"agent_publish\":[]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
  fi
  BODY=$(mktemp)
  # R1 — o REGISTRY vivo recusa coleta por voz/teclado que nunca termina (espera infinita), e o
  # controle é a própria fixture, aceita logo abaixo. Skill descartável, nunca promovida.
  RUIM=$(python3 - "$FIXTURE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
d["skill_id"] = "skill_probe_collect_ruim_v1"
m = next(s for s in d["flow"]["steps"] if s["id"] == "m1")
m["collect"] = {"input": ["dtmf"], "max_invalid": 2}
m["timeout_s"] = -1
print(json.dumps(d))
PY
)
  R1=$(curl -s -o "$BODY" -w '%{http_code}' -X PUT "${H[@]}" "$REG/v1/skills/skill_probe_collect_ruim_v1" -d "$RUIM")
  if [ "$R1" -ge 400 ] && [ "$R1" -lt 500 ] && grep -q "first_input_timeout_s" "$BODY"; then
    ok "R1 registry recusa menu dtmf sem timeout de canal e com espera infinita (http $R1)"
  else
    falha "R1 registry aceitou (ou recusou sem nomear) coleta dtmf que nunca termina: http $R1 $(head -c 200 "$BODY")"
  fi
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
      ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(JWT_SECRET|TENANT_ID|KAFKA_BROKERS)=' | sed 's/^/-e /' | tr '\n' ' ')
      name="probe_men07_$$_$RANDOM"
      OUT=$(timeout "${EXERCISE_TIMEOUT_S:-400}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            $ENV -e POOL="$POOL" "$IMG" - < infra/test/_menu_signal_contract_exercise.py 2>&1)
      [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
      while IFS= read -r l; do
        case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";; esac
      done <<< "$(printf '%s\n' "$OUT" | grep -E '^(OK|FALHA|INCONCL) ')"
      N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA|INCONCL) S[1-5] ')
      [ "$N" -ge 5 ] || falha "exercicio emitiu $N de 5 veredictos: $(printf '%s' "$OUT" | grep -vE '^(OK|FALHA|INCONCL) ' | tail -3 | tr '\n' ' ' | cut -c1-300)"
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
