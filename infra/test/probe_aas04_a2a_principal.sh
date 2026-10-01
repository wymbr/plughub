#!/usr/bin/env bash
# probe_aas04_a2a_principal.sh — 2026-10-01  (AAS-04 · adr-a2a-server-binding D6, D7)
#
# PERGUNTA: quem chama pelo canal a2a é um `agent_principal` externo (partner) cadastrado pelo
# admin, com credencial que só existe como hash — e a porta `POST /a2a/{slug}` autentica ANTES de
# revelar o endereço, toma o tenant DA CREDENCIAL e só aceita os pools concedidos?
#
# RAMOS
#   A  ADMINISTRAÇÃO (auth-api): sem `config.agents` → 403; criar/rotacionar devolve a credencial
#      e o banco guarda só o SHA-256; pool que não expõe A2A a partner → 422; quem não é master
#      não concede pool fora do próprio escopo (403), e tirar pool passa (controle).
#   B  INTROSPECÇÃO: só serviço (401 sem token); credencial ativa traz tenant e pools; desconhecida
#      → `{"active": false}`.
#   C  PORTA (gateway :8010): anônimo → 401 com WWW-Authenticate, inclusive em slug inexistente
#      (sem oráculo); credencial válida no pool concedido CHEGA AO ADAPTER (desde a AAS-06: um
#      `GetTask` de id inexistente responde -32001, sem efeito colateral), com o `id` ecoado; pool
#      não concedido → 403; slug inexistente, autenticado → 404.
#   D  REVOGAÇÃO: rotacionar invalida a anterior na hora no auth-api; desativar derruba a porta em
#      ≤ 30 s (cache do gateway). O principal é reativado no fim.
#
# NÃO MEDIDO AO VIVO (declarado): credencial de OUTRO tenant na porta (403) — exige pool A2A noutro
# tenant; guardado por `test_aas04_a2a_principal.py::test_credencial_de_outro_tenant…`.
#
# Fixtures fixas: pools `probe_aas04_a2a` (endpoint `probe-aas04`), `probe_aas04_outro`
# (`probe-aas04-outro`), `probe_aas04_so_cliente`; usuários `probe-aas04-adm@` (admin = master) e
# `probe-aas04-esc@` (só config.agents, escopo = probe_aas04_a2a); principal "probe AAS-04".
# Espera 31 s pelo cache. EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

REG="${REGISTRY:-http://localhost:3300}"
GW="${GATEWAY:-http://localhost:8010}"
AUTHB="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
AUTH_C="${AUTH_CONTAINER:-plughub-demo-auth-api-1}"
PG_C="${PG_CONTAINER:-plughub-demo-postgres-1}"
PRINC="probe AAS-04"
PASS="probe_aas04_Senha!1"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " quem chama por A2A é um principal cadastrado — e a porta confere?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl python3 docker; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

C="curl -s --max-time 20"
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
body() { cat "$BODY"; }
login() { $C -X POST "$AUTHB/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'; }
# req TOKEN METODO URL [CORPO]
req() { local extra=(); [ -n "${4:-}" ] && extra=(-d "$4")
  $C -o "$BODY" -w '%{http_code}' -X "$2" -H "Authorization: Bearer $1" -H "x-tenant-id: $TENANT" \
     -H 'Content-Type: application/json' "${extra[@]}" "$3"; }
sql() { docker exec "$PG_C" psql -U plughub -d plughub_demo -tAc "$1" 2>/dev/null; }

ADMIN=$(login admin@plughub.local changeme_admin)
[ -n "$ADMIN" ] || { echo "INCONCLUSIVO — login do admin@ falhou"; exit 2; }

# ── fixtures ─────────────────────────────────────────────────────────────────
D='{"display_name":"probe AAS-04","description":"Fixture do probe AAS-04.","input_schema":{"type":"object"},"output_schema":{"type":"object"},"skills":[{"id":"probe","name":"probe","description":"probe"}],"principal_kinds":["partner"]}'
garante_pool() {  # $1 pool $2 kinds(json)
  local d; d=$(echo "$D" | jq -c --argjson k "$2" '.principal_kinds=$k')
  local st; st=$(req "$ADMIN" GET "$REG/v1/pools/$1")
  if [ "$st" = 404 ]; then
    st=$(req "$ADMIN" POST "$REG/v1/pools" "{\"pool_id\":\"$1\",\"agent_kind\":\"ai\",\"channel_types\":[\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":1,\"description\":\"fixture do probe AAS-04\",\"a2a\":$d}")
  else st=$(req "$ADMIN" PUT "$REG/v1/pools/$1" "{\"channel_types\":[\"a2a\"],\"a2a\":$d}"); fi
  [ "${st:0:1}" = 2 ] || { echo "    pool $1 -> $st $(body | head -c 200)"; return 1; }
}
garante_ep() {  # $1 slug $2 pool
  req "$ADMIN" GET "$REG/v1/channel-endpoints?channel=a2a" >/dev/null
  local id; id=$(body | jq -r --arg s "$1" '(.endpoints // .)[]? | select(.identifier==$s) | .id' | head -1)
  if [ -z "$id" ]; then
    local st; st=$(req "$ADMIN" POST "$REG/v1/channel-endpoints" "{\"channel\":\"a2a\",\"identifier\":\"$1\",\"pool_id\":\"$2\",\"display_name\":\"probe AAS-04\"}")
    [ "$st" = 201 ] || { echo "    endpoint $1 -> $st $(body | head -c 200)"; return 1; }
  else req "$ADMIN" PUT "$REG/v1/channel-endpoints/$id" '{"active":true}' >/dev/null; fi
}
garante_user() {  # $1 email $2 roles(json) $3 pools(json)
  local st; st=$(req "$ADMIN" POST "$AUTHB/auth/users" \
    "{\"tenant_id\":\"$TENANT\",\"email\":\"$1\",\"password\":\"$PASS\",\"name\":\"probe AAS-04\",\"roles\":$2,\"accessible_pools\":$3}")
  [ "$st" = 201 ] || [ "$st" = 409 ] || { echo "    usuário $1 -> $st $(body | head -c 200)"; return 1; }
  req "$ADMIN" GET "$AUTHB/auth/users?tenant_id=$TENANT" >/dev/null
  body | jq -r --arg e "$1" '.[] | select(.email==$e) | .id' | head -1
}
if ! garante_pool probe_aas04_a2a '["partner"]' || ! garante_pool probe_aas04_outro '["partner"]' \
   || ! garante_pool probe_aas04_so_cliente '["customer_agent"]' \
   || ! garante_ep probe-aas04 probe_aas04_a2a || ! garante_ep probe-aas04-outro probe_aas04_outro; then
  echo "INCONCLUSIVO — pools/endpoints de fixture não montados"; exit 2
fi
ADM_ID=$(garante_user probe-aas04-adm@plughub.local '["admin"]' '[]') || { echo "INCONCLUSIVO — usuário master"; exit 2; }
ESC_ID=$(garante_user probe-aas04-esc@plughub.local '["operator"]' '["probe_aas04_a2a"]') || { echo "INCONCLUSIVO — usuário de escopo"; exit 2; }
# o de escopo: SÓ config.agents (sem permissions = não é master), e o escopo de volta ao de partida
req "$ADMIN" PATCH "$AUTHB/auth/users/$ESC_ID" '{"accessible_pools":["probe_aas04_a2a"]}' >/dev/null
st=$(req "$ADMIN" PATCH "$AUTHB/auth/users/$ESC_ID/module-config/config" '{"agents":{"access":"read_write"}}')
[ "${st:0:1}" = 2 ] || { echo "INCONCLUSIVO — grant config.agents do usuário de escopo -> $st $(body | head -c 200)"; exit 2; }
MASTER=$(login probe-aas04-adm@plughub.local "$PASS"); ESC=$(login probe-aas04-esc@plughub.local "$PASS")
[ -n "$MASTER" ] && [ -n "$ESC" ] || { echo "INCONCLUSIVO — login dos usuários de fixture"; exit 2; }
SVC=$(docker exec "$AUTH_C" printenv PLUGHUB_AUTH_SERVICE_TOKEN 2>/dev/null)
[ -n "$SVC" ] || { echo "INCONCLUSIVO — PLUGHUB_AUTH_SERVICE_TOKEN vazio no auth-api"; exit 2; }
P="$AUTHB/auth/v1/agent-principals"
intro() { $C -o "$BODY" -w '%{http_code}' -X POST "$P/introspect" -H 'Content-Type: application/json' \
  ${2:+-H "x-service-token: $2"} -d "{\"credential\":\"$1\"}"; }
porta() {  # $1 slug $2 credencial (vazia = anônimo)
  $C -o "$BODY" -D "$BODY.h" -w '%{http_code}' -X POST "$GW/a2a/$1" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
    ${2:+-H "Authorization: Bearer $2"} -d '{"jsonrpc":"2.0","id":"probe-7","method":"GetTask","params":{"id":"probe-aas04-inexistente"}}'; }

# ── A ────────────────────────────────────────────────────────────────────────
echo ""; echo "── A · ADMINISTRAÇÃO ──────────────────────────────────────────────────"
st=$(req "$ADMIN" GET "$P")
[ "$st" = 403 ] && ok "A1 sem config.agents (admin@, nascido antes do campo) -> 403" || falha "A1 admin@ sem o campo -> $st"
req "$MASTER" GET "$P" >/dev/null
PID=$(body | jq -r --arg n "$PRINC" '.[] | select(.display_name==$n) | .agent_principal_id' | head -1)
if [ -z "$PID" ]; then
  st=$(req "$MASTER" POST "$P" "{\"display_name\":\"$PRINC\",\"allowed_pools\":[\"probe_aas04_a2a\"]}")
  PID=$(body | jq -r '.agent_principal_id // empty')
else
  req "$MASTER" PUT "$P/$PID" '{"allowed_pools":["probe_aas04_a2a"],"active":true}' >/dev/null
  st=$(req "$MASTER" POST "$P/$PID/credential")
fi
CRED=$(body | jq -r '.credential // empty')
if [ -z "$CRED" ] || [ -z "$PID" ]; then
  echo "INCONCLUSIVO — não obtive principal/credencial ($st $(body | head -c 200))"; exit 2
fi
H=$(printf '%s' "$CRED" | sha256sum | cut -d' ' -f1)
n_hash=$(sql "select count(*) from auth.agent_principals where credential_hash='$H'")
n_txt=$(sql "select count(*) from auth.agent_principals p where position('$CRED' in row_to_json(p)::text) > 0")
[ "${CRED:0:4}" = pha_ ] && [ "$n_hash" = 1 ] && [ "$n_txt" = 0 ] \
  && ok "A2 credencial entregue uma vez; o banco tem o SHA-256 e nunca o texto" \
  || falha "A2 credencial=${CRED:0:8}… linhas com o hash=$n_hash, linhas com o texto=$n_txt"
st=$(req "$MASTER" PUT "$P/$PID" '{"allowed_pools":["probe_aas04_a2a","probe_aas04_so_cliente"]}')
[ "$st" = 422 ] && body | grep -q partner && ok "A3 pool cujo contrato não admite partner -> 422" || falha "A3 -> $st $(body | head -c 200)"
st=$(req "$MASTER" PUT "$P/$PID" '{"allowed_pools":["probe_aas04_a2a","probe_aas04_inexistente"]}')
[ "$st" = 422 ] && ok "A4 pool inexistente -> 422" || falha "A4 -> $st"
st=$(req "$ESC" PUT "$P/$PID" '{"allowed_pools":["probe_aas04_a2a","probe_aas04_outro"]}')
[ "$st" = 403 ] && body | grep -q probe_aas04_outro && ok "A5 sem master, conceder pool fora do próprio escopo -> 403" || falha "A5 -> $st $(body | head -c 200)"
st=$(req "$ESC" PUT "$P/$PID" '{"allowed_pools":["probe_aas04_a2a"],"display_name":"probe AAS-04"}')
[ "$st" = 200 ] && ok "A6 controle: o mesmo usuário edita sem conceder nada novo -> 200" || falha "A6 controle -> $st $(body | head -c 200)"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""; echo "── B · INTROSPECÇÃO ───────────────────────────────────────────────────"
st=$(intro "$CRED"); [ "$st" = 401 ] && ok "B1 introspecção sem token de serviço -> 401" || falha "B1 -> $st"
st=$(intro "$CRED" "$SVC")
r=$(body | jq -c '{a:.active, t:.tenant_id, k:.kind, p:.allowed_pools, s:.subject_type}')
[ "$st" = 200 ] && [ "$r" = "{\"a\":true,\"t\":\"$TENANT\",\"k\":\"partner\",\"p\":[\"probe_aas04_a2a\"],\"s\":\"agent\"}" ] \
  && ok "B2 credencial ativa -> tenant, tipo e pools DA CREDENCIAL ($r)" || falha "B2 -> $st $r"
st=$(intro "pha_inventada_$(date +%s)" "$SVC")
[ "$(body | jq -c .)" = '{"active":false}' ] && ok "B3 credencial desconhecida -> {\"active\":false}" || falha "B3 -> $st $(body)"

# ── C ────────────────────────────────────────────────────────────────────────
echo ""; echo "── C · A PORTA DO GATEWAY ─────────────────────────────────────────────"
st=$(porta probe-aas04 "")
[ "$st" = 401 ] && grep -qi '^www-authenticate: Bearer' "$BODY.h" && ok "C1 anônimo -> 401 com WWW-Authenticate: Bearer" || falha "C1 -> $st"
st=$(porta "probe-aas04-nunca-$(date +%s)" "")
[ "$st" = 401 ] && ok "C2 anônimo em slug inexistente -> 401, não 404 (sem oráculo)" || falha "C2 -> $st"
st=$(porta probe-aas04 "$CRED")
r=$(body | jq -c '{id, code: .error.code}')
[ "$st" = 200 ] && [ "$r" = '{"id":"probe-7","code":-32001}' ] \
  && ok "C3 credencial no pool concedido -> chega ao adapter (GetTask inexistente = -32001), id ecoado" || falha "C3 -> $st $(body | head -c 200)"
st=$(porta probe-aas04-outro "$CRED"); [ "$st" = 403 ] && ok "C4 pool NÃO concedido -> 403" || falha "C4 -> $st"
st=$(porta "probe-aas04-nunca-$(date +%s)" "$CRED"); [ "$st" = 404 ] && ok "C5 autenticado em slug inexistente -> 404" || falha "C5 -> $st"
st=$(porta probe-aas04 "pha_inventada_$(date +%s)"); [ "$st" = 401 ] && ok "C6 credencial inventada -> 401" || falha "C6 -> $st"

# ── D ────────────────────────────────────────────────────────────────────────
echo ""; echo "── D · ROTAÇÃO E REVOGAÇÃO ────────────────────────────────────────────"
req "$MASTER" POST "$P/$PID/credential" >/dev/null; NOVA=$(body | jq -r '.credential // empty')
intro "$CRED" "$SVC" >/dev/null; velha=$(body | jq -r .active)
intro "$NOVA" "$SVC" >/dev/null; nova=$(body | jq -r .active)
[ -n "$NOVA" ] && [ "$velha" = false ] && [ "$nova" = true ] && ok "D1 rotacionar: a anterior morre na hora no auth-api, a nova vale" \
  || falha "D1 velha=$velha nova=$nova"
st=$(porta probe-aas04 "$NOVA"); [ "$st" = 200 ] && ok "D2 a nova credencial passa pela porta" || falha "D2 -> $st"
req "$MASTER" PUT "$P/$PID" '{"active":false}' >/dev/null
intro "$NOVA" "$SVC" >/dev/null
[ "$(body | jq -r .active)" = false ] && ok "D3 desativado -> introspecção inativa" || falha "D3 -> $(body)"
echo "    (esperando 31 s pelo cache da porta)"; sleep 31
st=$(porta probe-aas04 "$NOVA"); [ "$st" = 401 ] && ok "D4 desativado -> a porta recusa em ≤ 30 s" || falha "D4 -> $st"
st=$(porta probe-aas04 "$CRED"); [ "$st" = 401 ] && ok "D5 a credencial rotacionada também está fora da porta" || falha "D5 -> $st"
req "$MASTER" PUT "$P/$PID" '{"active":true}' >/dev/null

echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — o principal é cadastrado, a credencial só existe como hash, e a porta confere antes de revelar."
exit 0
