#!/usr/bin/env bash
# probe_aut71_tenant_scope.sh — 2026-10-01  (AUT-71)
#
# PERGUNTA: o admin de um tenant — que administra todo mundo DENTRO dele — consegue ler ou gravar
# usuário, template, grupo ou módulo de OUTRO tenant pelo auth-api? E o outro tenant continua
# administrando o que é dele?
#
# Medido antes do conserto (2026-10-01, admin do tenant_demo contra `probe_aas08_outro`): criar
# usuário lá 201, listar os usuários e os templates de lá 200, ler ficha e module_config por id
# 200, listar os grupos de lá 200.
#
# RAMOS (ATACANTE = admin@ do tenant_demo; VÍTIMA = tenant `probe_aut71_outro`)
#   A  declarado no corpo/query → 403 tenant_mismatch: criar usuário · listar usuários · listar
#      templates · listar grupos · criar grupo — e nada nasceu lá (conferido pela VÍTIMA)
#   B  linha da vítima por id → 404 (igual a inexistente): ler usuário · module_config · editar
#      (o nome NÃO mudou) · apagar (o usuário CONTINUA lá) · ler o grupo · pôr usuário do
#      atacante no grupo da vítima (o grupo continua sem ele)
#   C  módulo de PLATAFORMA → 403 platform_module_write
#   D  CONTROLES POSITIVOS: a vítima lista, lê e edita o que é dela (200); o atacante lista o
#      próprio tenant sem `?tenant_id=` e recebe só gente dele (o default era "tenant_demo")
#
# A vítima é montada como o `auth-seed` monta uma instalação: Bearer de bootstrap assinado com o
# segredo do auth-api, e daí só a API oficial.
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."
AUTHB="${AUTH:-http://localhost:3202}"
OUTRO="probe_aut71_outro"
AUTHC="${AUTH_CONTAINER:-plughub-demo-auth-api-1}"

echo "════════════════════════════════════════════════════════════════════"
echo " o tenant de toda gestão no auth-api é o do token?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl docker python3; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
C="curl -s --max-time 20"
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
body() { cat "$BODY"; }
req() {  # $1 token $2 método $3 caminho [$4 corpo] → status
  local extra=(); [ -n "${4:-}" ] && extra=(-d "$4")
  $C -o "$BODY" -w '%{http_code}' -X "$2" -H "Authorization: Bearer $1" \
     -H 'Content-Type: application/json' "${extra[@]}" "$AUTHB$3"; }

ADMIN=$($C -X POST "$AUTHB/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"admin@plughub.local","password":"changeme_admin","tenant_id":"tenant_demo"}' | jq -r '.access_token // empty')
[ -n "$ADMIN" ] || { echo "INCONCLUSIVO — login do admin@ falhou"; exit 2; }
SEGREDO=$(docker exec "$AUTHC" printenv PLUGHUB_AUTH_JWT_SECRET 2>/dev/null)
[ -n "$SEGREDO" ] || { echo "INCONCLUSIVO — sem o segredo do auth-api para montar a vítima"; exit 2; }
VITIMA=$(SEGREDO="$SEGREDO" OUTRO="$OUTRO" python3 - <<'PY'
import base64, hashlib, hmac, json, os, time
b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
now = int(time.time())
h = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
p = b(json.dumps({"sub": "probe_aut71_bootstrap", "tenant_id": os.environ["OUTRO"], "roles": ["admin"],
                  "module_config": {"config": {"users": {"access": "read_write", "scope": []},
                                               "permissions": {"access": "read_write", "scope": []}}},
                  "iat": now, "exp": now + 600}).encode())
print(f"{h}.{p}.{b(hmac.new(os.environ['SEGREDO'].encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest())}")
PY
)

# ── fixtures da vítima (idempotentes, pela API) ───────────────────────────────
VEMAIL="probe-aut71-vitima@plughub.local"
st=$(req "$VITIMA" POST /auth/users "{\"tenant_id\":\"$OUTRO\",\"email\":\"$VEMAIL\",\"password\":\"probe_aut71_Senha!1\",\"name\":\"vitima\",\"roles\":[\"operator\"]}")
[ "$st" = 201 ] || [ "$st" = 409 ] || { echo "INCONCLUSIVO — fixture usuário da vítima -> $st $(body | head -c 200)"; exit 2; }
req "$VITIMA" GET /auth/users >/dev/null
VID=$(body | jq -r --arg e "$VEMAIL" '.[]? | select(.email==$e) | .id' | head -1)
req "$VITIMA" PATCH "/auth/users/$VID" '{"name":"vitima"}' >/dev/null
req "$VITIMA" GET /auth/v1/groups >/dev/null
GID=$(body | jq -r '.[]? | select(.name=="probe aut71 grupo") | .group_id' | head -1)
if [ -z "$GID" ]; then
  req "$VITIMA" POST /auth/v1/groups "{\"tenant_id\":\"$OUTRO\",\"name\":\"probe aut71 grupo\"}" >/dev/null
  GID=$(body | jq -r '.group_id // empty')
fi
[ -n "$VID" ] && [ -n "$GID" ] || { echo "INCONCLUSIVO — fixtures da vítima (user=$VID grupo=$GID)"; exit 2; }
req "$ADMIN" GET /auth/me >/dev/null
AID=$(body | jq -r '.id // .user_id // .sub // empty')
[ -n "$AID" ] || { echo "INCONCLUSIVO — /auth/me não deu o id do atacante (B6/B8 sem sujeito)"; exit 2; }
# Sobra de rodada anterior (uma bateria de mutação deixa o intruso criado e o atacante no grupo):
# a vítima a remove ANTES, senão A6/B8 reprovariam por um fato que não é desta rodada.
req "$VITIMA" GET /auth/users >/dev/null
for x in $(body | jq -r '.[]? | select(.email=="probe-aut71-intruso@plughub.local") | .id'); do
  req "$VITIMA" DELETE "/auth/users/$x" >/dev/null; done
[ -n "$AID" ] && req "$VITIMA" DELETE "/auth/v1/groups/$GID/users/$AID" >/dev/null

# ── A ────────────────────────────────────────────────────────────────────────
echo ""; echo "── A · TENANT DECLARADO DIFERENTE DO TOKEN → 403 ──────────────────────"
mm() {  # $1 rótulo $2 método $3 caminho [$4 corpo]
  local st; st=$(req "$ADMIN" "$2" "$3" "${4:-}")
  if [ "$st" = 403 ] && [ "$(body | jq -r .detail)" = tenant_mismatch ]; then ok "$1 → 403 tenant_mismatch"
  else falha "$1 → $st $(body | head -c 160)"; fi; }
mm "A1 criar usuário na vítima" POST /auth/users "{\"tenant_id\":\"$OUTRO\",\"email\":\"probe-aut71-intruso@plughub.local\",\"password\":\"probe_aut71_Senha!1\",\"roles\":[\"admin\"]}"
mm "A2 listar usuários da vítima" GET "/auth/users?tenant_id=$OUTRO"
mm "A3 listar templates da vítima" GET "/auth/templates?tenant_id=$OUTRO"
mm "A4 listar grupos da vítima" GET "/auth/v1/groups?tenant_id=$OUTRO"
mm "A5 criar grupo na vítima" POST /auth/v1/groups "{\"tenant_id\":\"$OUTRO\",\"name\":\"probe aut71 intruso\"}"
req "$VITIMA" GET /auth/users >/dev/null
n=$(body | jq '[.[]? | select(.email=="probe-aut71-intruso@plughub.local")] | length')
[ "$n" = 0 ] && ok "A6 nenhum intruso nasceu na vítima (conferido pela vítima)" || falha "A6 o intruso EXISTE na vítima ($n)"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""; echo "── B · LINHA DA VÍTIMA POR ID → 404 ───────────────────────────────────"
nf() {  # $1 rótulo $2 método $3 caminho [$4 corpo]
  local st; st=$(req "$ADMIN" "$2" "$3" "${4:-}")
  [ "$st" = 404 ] && ok "$1 → 404" || falha "$1 → $st $(body | head -c 160)"; }
nf "B1 ler o usuário da vítima" GET "/auth/users/$VID"
nf "B2 ler o module_config dele" GET "/auth/users/$VID/module-config"
nf "B3 editar o usuário da vítima" PATCH "/auth/users/$VID" '{"name":"tomado"}'
nf "B4 apagar o usuário da vítima" DELETE "/auth/users/$VID"
nf "B5 ler o grupo da vítima" GET "/auth/v1/groups/$GID"
nf "B6 pôr-se no grupo da vítima" POST "/auth/v1/groups/$GID/users" "{\"user_id\":\"$AID\"}"
st=$(req "$VITIMA" GET "/auth/users/$VID")
[ "$st" = 200 ] && [ "$(body | jq -r .name)" = vitima ] \
  && ok "B7 o usuário da vítima existe e não foi renomeado (conferido pela vítima)" \
  || falha "B7 usuário da vítima -> $st nome=$(body | jq -r .name)"
req "$VITIMA" GET "/auth/v1/groups/$GID/users" >/dev/null
n=$(body | jq --arg a "$AID" '[.[]? | select((.user_id // .id)==$a)] | length')
[ "$n" = 0 ] && ok "B8 o atacante não entrou no grupo da vítima" || falha "B8 o atacante É membro do grupo da vítima"

# ── C ────────────────────────────────────────────────────────────────────────
echo ""; echo "── C · MÓDULO DE PLATAFORMA ───────────────────────────────────────────"
st=$(req "$ADMIN" POST /auth/modules '{"module_id":"probe_aut71_mod"}')
[ "$st" = 403 ] && [ "$(body | jq -r .detail)" = platform_module_write ] \
  && ok "C1 gravar módulo de plataforma → 403" || falha "C1 -> $st $(body | head -c 160)"
st=$(req "$ADMIN" PATCH "/auth/modules/contacts/active?active=true")
[ "$st" = 403 ] && ok "C2 ligar/desligar módulo de plataforma → 403" || falha "C2 -> $st $(body | head -c 160)"

# ── D ────────────────────────────────────────────────────────────────────────
echo ""; echo "── D · CONTROLES POSITIVOS ────────────────────────────────────────────"
st=$(req "$VITIMA" GET /auth/users)
n=$(body | jq '[.[]? | select(.tenant_id != "'"$OUTRO"'")] | length')
[ "$st" = 200 ] && [ "$(body | jq length)" -ge 1 ] && [ "$n" = 0 ] \
  && ok "D1 a vítima lista os SEUS usuários ($(body | jq length)), e só os dela" || falha "D1 -> $st, alheios=$n"
st=$(req "$VITIMA" PATCH "/auth/users/$VID" '{"name":"vitima"}')
[ "$st" = 200 ] && ok "D2 a vítima edita o usuário dela" || falha "D2 -> $st $(body | head -c 160)"
st=$(req "$VITIMA" GET "/auth/v1/groups/$GID")
[ "$st" = 200 ] && ok "D3 a vítima lê o grupo dela" || falha "D3 -> $st"
st=$(req "$ADMIN" GET /auth/users)
n=$(body | jq '[.[]? | select(.tenant_id != "tenant_demo")] | length')
[ "$st" = 200 ] && [ "$(body | jq length)" -ge 1 ] && [ "$n" = 0 ] \
  && ok "D4 o atacante lista o PRÓPRIO tenant sem ?tenant_id= ($(body | jq length) usuários, nenhum alheio)" \
  || falha "D4 -> $st, alheios=$n"
# a chamada das telas (Acesso, Grupos) manda `?tenant_id=` com o tenant da SESSÃO
for cam in /auth/users /auth/templates /auth/v1/groups; do
  st=$(req "$ADMIN" GET "$cam?tenant_id=tenant_demo")
  [ "$st" = 200 ] && ok "D5 $cam?tenant_id=<o do token> → 200 (a chamada da tela)" || falha "D5 $cam -> $st $(body | head -c 160)"
done

echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — gestão no auth-api só alcança o tenant do token, e cada tenant segue administrando o seu."
exit 0
