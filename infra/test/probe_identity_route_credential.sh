#!/usr/bin/env bash
# probe_identity_route_credential.sh — 2026-09-13  (IDN-06)
#
# PERGUNTA: as rotas de identidade e pendencia do channel-gateway exigem credencial —
#           e os chamadores internos continuam alcancando-as?
#
# O DEFEITO QUE O ORIGINOU
#   `/v1/channels/webhook/identity/*` e `/pending/*` nao pediam credencial nenhuma e
#   tomavam o `tenant_id` do CORPO. A UI as proxia, entao pelo host da UI (5174) e sem
#   login a busca devolvia o cadastro de clientes, o resolve respondia, e
#   `pending/by-customer` entregava `resume_token` — medido 200 nas quatro antes do fix.
#
# CINCO RAMOS
#   A  CENSO AST — toda rota do bloco chama o portao (`_identity_caller` ou, nas duas so
#      de usuario, `verify_user_jwt`); populacao minima declarada; mutacao do censo.
#   B  ROTA VIVA (8010, dentro do gateway) — as nove anonimas 401; servico errado 401;
#      servico certo 200; usuario em rota interna 403; busca sem campo 403; busca com
#      campo 200 no tenant do JWT; tenant divergente 403.
#   C  IMAGEM + MUTACOES — token vazio no gateway FECHA; mutar "vazio abre" e "sem
#      portao" derruba cada um o seu caso, e o controle fica.
#   D  PELA BORDA DA UI (5174) — anonimo 401; login real com `contacts.visualizar` busca
#      200; o mesmo token no resolve 403.
#   E  CHAMADORES INTERNOS — os tres envs concordam e nao sao vazios; o mcp-server e a
#      mailing-api, com o token do PROPRIO container, recebem 200 (e sem ele, 401).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
MAIL="${MAILING_CONTAINER:-plughub-demo-mailing-api-1}"
UI="${UI:-http://localhost:5174}"
AUTH="${AUTH:-http://localhost:3202/auth}"
TENANT="${TENANT:-tenant_demo}"
MAIN=packages/channel-gateway/src/plughub_channel_gateway/main.py
POP_MIN=11   # 9 rotas de duas portas + import + operator/register
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
jexpr() { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }
roda()  { docker exec -i "$GW" python - "$@" < infra/test/_identity_route_credential_exercise.py 2>/dev/null | tail -1; }
julga() {  # $1 json · $2 casos
  if [ -z "$1" ] || [ "$(jexpr "$1" "'casos' in d")" != "True" ]; then
    incon "o exercicio nao devolveu JSON"; return
  fi
  for k in $2; do
    [ "$(jexpr "$1" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k"
  done
}

echo "════════════════════════════════════════════════════════════════════"
echo " as rotas de identidade exigem credencial, e os internos passam?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO AST ──────────────────────────────────────────────────────"
C=$(python3 infra/test/_identity_route_census.py "$MAIN")
POP=$(jexpr "$C" "d['populacao']")
SEM=$(jexpr "$C" "len(d['sem_portao'])")
echo "   populacao=$POP sem_portao=$SEM"
if [ "$POP" -lt "$POP_MIN" ] 2>/dev/null; then
  falha "populacao $POP < $POP_MIN — o censo nao esta vendo as rotas (prefixo mudou?)"
elif [ "$SEM" = "0" ]; then
  ok "as $POP rotas do bloco decidem credencial"
else
  falha "rotas sem portao: $(jexpr "$C" "'; '.join(d['sem_portao'])")"
fi
# Mutacao: tira o portao de UMA rota numa copia; o censo tem de acusar exatamente ela.
TMP=$(mktemp --suffix=.py)
python3 - "$MAIN" "$TMP" <<'EOF'
import sys
s = open(sys.argv[1], encoding="utf-8").read()
alvo = "    tenant = _identity_caller(request, tenant_id)\n    if _webhook_adapter is None:\n        return {\"found\": False, \"count\": 0, \"pendings\": []}"
assert s.count(alvo) == 1, "ancora da mutacao nao casou"
open(sys.argv[2], "w", encoding="utf-8").write(s.replace(alvo, alvo.replace("_identity_caller(request, tenant_id)", "tenant_id"), 1))
EOF
CM=$(python3 infra/test/_identity_route_census.py "$TMP")
rm -f "$TMP"
if [ "$(jexpr "$CM" "d['sem_portao'] == ['GET /v1/channels/webhook/pending/by-customer/{customer_id} (webhook_pending_by_customer)']")" = "True" ]; then
  ok "mutacao (portao removido de pending/by-customer) acusada, e so ela"
else
  falha "mutacao do censo nao acusou a rota certa: $CM"
fi

echo ""
echo "── B · ROTA VIVA (8010) ───────────────────────────────────────────────"
J=$(roda rota)
echo "   $J" | cut -c1-600
if [ "$(jexpr "$J" "d.get('token_configurado')")" != "True" ]; then
  incon "gateway sem PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN — B nao mede a porta de servico"
fi
julga "$J" "anonimo_401 servico_errado_401 servico_resolve_200 servico_pending_200 usuario_interno_403 usuario_sem_campo_403 usuario_busca_200 usuario_outro_tenant_403"

echo ""
echo "── C · IMAGEM + MUTACOES ──────────────────────────────────────────────"
J=$(roda imagem)
julga "$J" "vazio_fecha anonimo_401 usuario_interno_403 servico_200"
muta() {  # $1 flag · $2 casos que TEM de cair · $3 controles que TEM de ficar
  local J; J=$(roda imagem "$1")
  [ -z "$J" ] && { incon "$1: sem JSON"; return; }
  for k in $2; do
    [ "$(jexpr "$J" "d['casos'].get('$k')")" = "False" ] && ok "$1 derruba $k" || falha "$1 NAO derrubou $k — o caso nao mede a regra"
  done
  for k in $3; do
    [ "$(jexpr "$J" "d['casos'].get('$k')")" = "True" ] && ok "$1 mantem o controle $k" || falha "$1 derrubou o controle $k"
  done
}
muta --mutar-vazio-abre "vazio_fecha" "anonimo_401 usuario_interno_403 servico_200"
muta --mutar-sem-portao "anonimo_401 usuario_interno_403" "servico_200"

echo ""
echo "── D · PELA BORDA DA UI (5174) ────────────────────────────────────────"
S=$(curl -s -o /dev/null -w '%{http_code}' "$UI/v1/channels/webhook/identity/customers/search?tenant_id=$TENANT&q=maria")
[ "$S" = "401" ] && ok "busca anonima pela UI: 401" || falha "busca anonima pela UI: $S (esperado 401)"
TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("access_token") or "")
except Exception: print("")')
if [ -z "$TOK" ]; then
  incon "login do admin falhou — D sem porta de usuario"
else
  TEM=$(printf '%s' "$TOK" | python3 -c 'import sys,json,base64
p=sys.stdin.read().split(".")[1]; p+="="*(-len(p)%4); d=json.loads(base64.urlsafe_b64decode(p))
mc=d.get("module_config") or {}
print(any((mc.get(m,{}).get(f,{}) or {}).get("access") in ("read_only","read_write") for m,f in (("contacts","visualizar"),("agent_assist","atender"))))')
  if [ "$TEM" != "True" ]; then
    incon "o admin nao tem contacts.visualizar nem agent_assist.atender — sem controle positivo de usuario"
  else
    S=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" "$UI/v1/channels/webhook/identity/customers/search?tenant_id=$TENANT&q=maria")
    [ "$S" = "200" ] && ok "busca com login real pela UI: 200" || falha "busca com login real pela UI: $S"
    S=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" -X POST "$UI/v1/channels/webhook/identity/resolve" \
      -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\",\"provision\":false,\"anchors\":[{\"kind\":\"email\",\"value\":\"x@y.z\"}]}")
    [ "$S" = "403" ] && ok "o mesmo login no resolve (interna) pela UI: 403" || falha "resolve com login pela UI: $S (esperado 403)"
  fi
fi

echo ""
echo "── E · CHAMADORES INTERNOS ────────────────────────────────────────────"
T_GW=$(docker exec "$GW" printenv PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN 2>/dev/null)
T_MCP=$(docker exec "$MCP" printenv CHANNEL_GATEWAY_SERVICE_TOKEN 2>/dev/null)
T_MAIL=$(docker exec "$MAIL" printenv PLUGHUB_MAILING_IDENTITY_SERVICE_TOKEN 2>/dev/null)
if [ -z "$T_GW" ]; then
  falha "gateway sem token"
elif [ "$T_GW" = "$T_MCP" ] && [ "$T_GW" = "$T_MAIL" ]; then
  ok "gateway, mcp-server e mailing-api carregam o MESMO token (nao vazio)"
else
  falha "tokens divergem: mcp=$([ "$T_GW" = "$T_MCP" ] && echo igual || echo DIFERENTE) mailing=$([ "$T_GW" = "$T_MAIL" ] && echo igual || echo DIFERENTE)"
fi
# mcp-server: o header que o `identityHeaders` monta, com o env do proprio container.
M=$(docker exec -i "$MCP" node -e '
const u = "http://channel-gateway:8010/v1/channels/webhook/pending/by-customer/cus_demo_maria?tenant_id=" + process.argv[1];
const h = { "X-Service-Token": process.env.CHANNEL_GATEWAY_SERVICE_TOKEN ?? "", "X-Service-Name": "mcp-server-plughub" };
Promise.all([fetch(u, { headers: h }), fetch(u)]).then(([a, b]) => console.log(a.status + " " + b.status)).catch(e => console.log("ERRO " + e));
' "$TENANT" 2>/dev/null)
[ "$M" = "200 401" ] && ok "mcp-server: com o token 200, sem ele 401" || falha "mcp-server: '$M' (esperado '200 401')"
# mailing-api: o cliente REAL (headers do IdentityClient montado pelo settings).
L=$(docker exec -i "$MAIL" python - "$TENANT" 2>/dev/null <<'EOF'
import sys, httpx
from plughub_mailing_api.config import get_settings
from plughub_mailing_api.identity_client import IdentityClient
s = get_settings(); c = IdentityClient(s.identity_api_url, service_token=s.identity_service_token)
u = c.base_url + "/v1/channels/webhook/identity/customers/cus_demo_maria"
a = httpx.get(u, params={"tenant_id": sys.argv[1]}, headers=c.headers).status_code
b = httpx.get(u, params={"tenant_id": sys.argv[1]}).status_code
print(a, b)
EOF
)
case "$L" in
  "200 401"|"404 401") ok "mailing-api: com o token ${L% *}, sem ele 401" ;;
  *) falha "mailing-api: '$L' (esperado '200 401' ou '404 401')" ;;
esac

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
