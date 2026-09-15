#!/usr/bin/env bash
# probe_invoke_absent_ref.sh — 2026-09-15
#
# PERGUNTA: um `invoke` cuja entrada é uma referência que NÃO resolveu chama a tool sem aquele
# argumento — ou manda `null` e a tool recusa?
#
# O DEFEITO (medido ao vivo em 2026-09-15, webchat, pool `auth_form_ia`)
#   `skill_auth_form_v1` chama `validate_pin` com `customer_id: "@ctx.caller.customer_id"`. Contato
#   sem identidade resolvida: o ContextStore devolve `null` (`getValue` faz `?? null`), o argumento
#   viajava como `null`, e o schema da tool (`z.string().optional()`) recusava com -32602. O cliente
#   digitava a senha CERTA e lia "Não foi possível verificar sua identidade" — falha plausível
#   escondendo contrato quebrado. `$.` ausente já virava `undefined` e sumia do JSON: a mesma
#   ausência em dois formatos. 164 campos das tools são `.optional()` sem `.nullable()`.
#
# O CONSERTO mora no MOTOR (`resolveInputMap`): referência pura que não resolveu é chave AUSENTE.
#   `null` literal no YAML viaja; valor falsy resolvido viaja; elemento de array não sai.
#
# RAMOS
#   U  suíte do `invoke` contra a IMAGEM do skill-flow-service (5 casos, com controles)
#   P  PRÉ-CONDIÇÃO: a sessão exercida não tem `customer_id` no ContextStore — senão o ramo L
#      mediria o caminho que sempre funcionou (INCONCLUSIVO)
#   L1 CONTROLE: senha inválida → "Credenciais inválidas" (a validação RODA; sem isto, um
#      `validate_pin` que aceitasse tudo passaria em L2)
#   L2 senha válida na reabertura → "Identidade verificada"
#   L3 nenhum -32602 / "received null" no skill-flow-service durante o exercício
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

SFS="${SFS_CONTAINER:-plughub-demo-skill-flow-service-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
REDIS="${REDIS_CONTAINER:-plughub-demo-redis-1}"
REG="${REGISTRY:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
POOL="auth_form_ia"
SKILL="skill_auth_form_v1"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " referencia que nao resolveu chega a tool como AUSENTE, nao null?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── U · suite do invoke (imagem) ───────────────────────────────────────"
IMG=$(docker inspect -f '{{.Image}}' "$SFS" 2>/dev/null)
if [ -z "$IMG" ]; then
  incon "U: skill-flow-service fora do ar"
elif ! docker run --rm "$IMG" sh -c "grep -q 'referência que não resolve não vira null' /app/packages/skill-flow-engine/src/__tests__/steps/invoke.test.ts" 2>/dev/null; then
  incon "U: a IMAGEM nao tem os casos de referencia ausente — imagem velha, nao suite verde"
else
  OUT=$(docker run --rm "$IMG" sh -c "cd /app/packages/skill-flow-engine && ./node_modules/.bin/vitest run src/__tests__/steps/invoke.test.ts 2>&1" | grep -E '^ +Tests +' | tail -1 | tr -s ' ')
  case "$OUT" in
    "") falha "U: vitest nao produziu sumario" ;;
    *failed*) falha "U:$OUT" ;;
    *) ok "U:$OUT" ;;
  esac
fi

echo ""
echo "── L · ao vivo (webchat, pool $POOL) ──────────────────────────────────"
ATUAL=$(curl -s -H "x-tenant-id: $TENANT" "$REG/v1/pools/$POOL/slots" | jq -r '.slots.current.skill_id // empty')
INST=$(docker exec "$REDIS" redis-cli smembers "$TENANT:pool:$POOL:instances" 2>/dev/null | head -1)
if [ "$ATUAL" != "$SKILL" ] || [ -z "$INST" ]; then
  incon "L: $POOL nao roda $SKILL no slot current (roda '$ATUAL') ou nao tem instancia — nada medido"
else
  docker cp infra/test/_ws_chat.py "$GW:/tmp/_ws_chat.py" >/dev/null
  CID="probe-absent-ref-$RANDOM$RANDOM"
  T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # `match` e o PROMPT do form, nunca uma palavra solta: "novamente" casava com o aviso
  # "Credenciais inválidas. Tente novamente" (texto livre) e o cliente respondia ali, antes do form.
  RULES='[{"match":"Preencha seus dados de acesso","answer":{"email":"probe.ref@exemplo.com","senha":"999999","codigo_2fa":"000000"}},
          {"match":"Preencha novamente","answer":{"email":"probe.ref@exemplo.com","senha":"123456","codigo_2fa":"000000"}}]'
  OUT=$(docker exec "$GW" python3 /tmp/_ws_chat.py "$TENANT" "$POOL" "$CID" "$RULES" 30 2>&1)
  SID=$(printf '%s\n' "$OUT" | sed -n 's/^AUTHENTICATED session_id=//p')
  if [ -z "$SID" ]; then
    incon "L: cliente webchat nao autenticou — $(printf '%s' "$OUT" | tail -2 | tr '\n' ' ' | cut -c1-200)"
  else
    CTX=$(docker exec "$REDIS" redis-cli hkeys "$TENANT:ctx:$SID" | grep -ciE 'customer_id')
    [ "$CTX" = 0 ] && ok "P a sessao $SID nao tem customer_id no ContextStore (o caso ausente e o exercido)" \
      || incon "P a sessao tem customer_id no ContextStore — L2 mediria o caminho que sempre funcionou"
    printf '%s\n' "$OUT" | grep -q "^NOTIFY Credenciais inválidas" \
      && ok "L1 CONTROLE senha invalida → 'Credenciais inválidas' (a validacao roda)" \
      || falha "L1 senha invalida nao produziu 'Credenciais inválidas': $(printf '%s\n' "$OUT" | grep '^NOTIFY' | head -3 | tr '\n' '|' | cut -c1-240)"
    printf '%s\n' "$OUT" | grep -q "^NOTIFY Identidade verificada" \
      && ok "L2 senha valida → 'Identidade verificada'" \
      || falha "L2 senha valida nao verificou: $(printf '%s\n' "$OUT" | grep '^NOTIFY' | tail -3 | tr '\n' '|' | cut -c1-240)"
    sleep 2
    ERR=$(docker logs --since "$T0" "$SFS" 2>&1 | grep -E "$SID|validate_pin" | grep -cE -- '-32602|received null')
    [ "$ERR" = 0 ] && ok "L3 nenhum -32602/'received null' no skill-flow-service" \
      || falha "L3 $ERR erro(s) de validacao de argumento: $(docker logs --since "$T0" "$SFS" 2>&1 | grep -E -- '-32602|received null' | head -1 | cut -c1-220)"
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
