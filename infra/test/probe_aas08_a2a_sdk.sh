#!/usr/bin/env bash
# probe_aas08_a2a_sdk.sh — 2026-10-01  (AAS-08 · adr-a2a-server-binding A6)
#
# PERGUNTA: os SDKs OFICIAIS do A2A (Python e JavaScript, versões FIXAS) conversam com o canal `a2a`
# do jeito que eles mesmos entendem — lendo o card, escolhendo transporte e streaming por ele, e
# lendo nossas respostas com os parsers deles? E a porta segura quem é de OUTRO tenant?
#
# RAMOS
#   K1–K10  `_a2a_sdk_exercise.py`  (a2a-sdk, `python:3.11-slim`): card, task nova, continuação,
#           GetTask, ListTasks, streaming escolhido pelo card, SubscribeToTask, CancelTask, erros
#           TIPADOS (UnsupportedOperation, TaskNotFound)
#   J1–J9   `_a2a_sdk_exercise.mjs` (@a2a-js/sdk, `node:20-alpine`): o mesmo percurso pelo SDK JS,
#           que lê com o `fromJSON` do ts-proto e resolve o card RELATIVO ao endereço do agente —
#           por isso ele recebe o endereço COMO O CARD O ANUNCIA (com barra final)
#   V1 sem `A2A-Version` → -32009 (ausente = 0.3), id ecoado · V2 `0.3` explícita → -32009
#   V3 versão por query (`?A2A-Version=1.0`) chega ao adapter · V4 a interface do card termina em `/`
#      e a porta atende nela (POST com barra)
#   X1 principal de OUTRO tenant, cadastrado pela API oficial daquele tenant, na porta → 403, e o
#      log do gateway nomeia o motivo (tenant), não o pool
#
# Fixtures: as da AAS-06 (`_a2a_fixture.sh`). O tenant de X1 (`probe_aas08_outro`) é montado como
# o `auth-seed` monta uma instalação: um Bearer de bootstrap assinado com o segredo do auth-api, e
# daí só a API (`/auth/v1/agent-principals`).
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."
SDK_VERSION="${A2A_SDK_VERSION:-1.2.1}"
JS_SDK_VERSION="${A2A_JS_SDK_VERSION:-1.3.0}"
OUTRO="probe_aas08_outro"

echo "════════════════════════════════════════════════════════════════════"
echo " os SDKs oficiais do A2A (py $SDK_VERSION · js $JS_SDK_VERSION) conversam com o canal a2a?"
echo "════════════════════════════════════════════════════════════════════"
for dep in jq curl docker python3; do command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }; done

. infra/test/_a2a_fixture.sh

OUT=$(mktemp); JSDIR=$(mktemp -d); trap 'rm -rf "$BODY" "$OUT" "$JSDIR"' EXIT
julga() {  # $1 prefixo  $2.. ramos — lê as linhas JSON de $OUT
  local pre=$1; shift
  grep -v '^{' "$OUT" | grep -iE 'error|traceback' | head -5 | sed 's/^/    /'
  for r in "$@"; do
    linha=$(grep -E "\"ramo\": ?\"$r\"" "$OUT" | head -1)
    if [ -z "$linha" ]; then incon "$r não rodou (um ramo anterior parou o exercício)"
    elif [ "$(echo "$linha" | jq -r .ok)" = true ]; then ok "$r $(echo "$linha" | jq -r .detalhe)"
    else falha "$r $(echo "$linha" | jq -r .detalhe)"; fi
  done
}

# ── K · Python ───────────────────────────────────────────────────────────────
echo ""; echo "── K · SDK OFICIAL PYTHON ─────────────────────────────────────────────"
docker run --rm --network host -e A2A_BASE="$GW/a2a/$SLUG" -e A2A_CRED="$CRED" -e A2A_CRED2="$CRED2" \
  -v "$PWD/infra/test/_a2a_sdk_exercise.py:/x.py:ro" python:3.11-slim \
  sh -c "pip install -q 'a2a-sdk==$SDK_VERSION' httpx >/dev/null 2>&1 || { echo PIP_FALHOU; exit 3; }; python /x.py" >"$OUT" 2>&1
if grep -q PIP_FALHOU "$OUT"; then incon "K* não instalei o a2a-sdk $SDK_VERSION"
else julga K K1 K2 K3 K4 K5 K6 K7 K8 K9 K10; fi
limpa

# ── J · JavaScript ───────────────────────────────────────────────────────────
echo ""; echo "── J · SDK OFICIAL JAVASCRIPT ─────────────────────────────────────────"
cp infra/test/_a2a_sdk_exercise.mjs "$JSDIR/x.mjs"
docker run --rm --network host -e A2A_BASE="$GW/a2a/$SLUG/" -e A2A_CRED="$CRED" -e A2A_CRED2="$CRED2" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$JSDIR:/w" -w /w node:20-alpine \
  sh -c "npm init -y >/dev/null 2>&1; npm i -s '@a2a-js/sdk@$JS_SDK_VERSION' >/dev/null 2>&1 || { echo NPM_FALHOU; exit 3; }; node x.mjs" >"$OUT" 2>&1
if grep -q NPM_FALHOU "$OUT"; then incon "J* não instalei o @a2a-js/sdk $JS_SDK_VERSION"
else julga J J1 J2 J3 J4 J5 J6 J7 J8 J9; fi
limpa

# ── V · versão e endereço ────────────────────────────────────────────────────
echo ""; echo "── V · A2A-Version E ENDEREÇO DO CARD ─────────────────────────────────"
GETX='{"jsonrpc":"2.0","id":"p8","method":"GetTask","params":{"id":"probe-aas08-inexistente"}}'
cru() {  # $1 url  $2.. cabeçalhos extras
  local url=$1; shift
  $C -o "$BODY" -w '%{http_code}' -X POST "$url" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $CRED" "$@" -d "$GETX"; }
st=$(cru "$GW/a2a/$SLUG"); r=$(body | jq -c '{id, code: .error.code, req: .error.data.requested}')
[ "$st" = 200 ] && [ "$r" = '{"id":"p8","code":-32009,"req":"0.3"}' ] \
  && ok "V1 sem A2A-Version → -32009 (ausente vale 0.3), id ecoado" || falha "V1 -> $st $(body | head -c 200)"
st=$(cru "$GW/a2a/$SLUG" -H 'A2A-Version: 0.3'); r=$(body | jq -r '.error.code')
[ "$r" = -32009 ] && ok "V2 A2A-Version 0.3 explícita → -32009" || falha "V2 -> $st $(body | head -c 200)"
st=$(cru "$GW/a2a/$SLUG?A2A-Version=1.0"); r=$(body | jq -r '.error.code')
[ "$r" = -32001 ] && ok "V3 versão por query chega ao adapter (GetTask inexistente = -32001)" || falha "V3 -> $st $(body | head -c 200)"
$C -o "$BODY" "$GW/a2a/$SLUG/.well-known/agent-card.json" >/dev/null
URL=$(body | jq -r '.supportedInterfaces[0].url // empty')
st=$(cru "$GW/a2a/$SLUG/" -H 'A2A-Version: 1.0'); r=$(body | jq -r '.error.code')
case "$URL" in
  */a2a/$SLUG/) [ "$r" = -32001 ] && ok "V4 interface do card termina em '/' ($URL) e a porta atende nela" \
                  || falha "V4 card com barra, mas POST com barra -> $st $(body | head -c 200)";;
  *) falha "V4 interface do card sem barra final: '$URL' (o SDK JS perde o slug ao resolver o card)";;
esac

# ── X · outro tenant ─────────────────────────────────────────────────────────
echo ""; echo "── X · PRINCIPAL DE OUTRO TENANT ──────────────────────────────────────"
SEGREDO=$(docker exec plughub-demo-auth-api-1 printenv PLUGHUB_AUTH_JWT_SECRET 2>/dev/null)
if [ -z "$SEGREDO" ]; then incon "X1 sem o segredo do auth-api para o Bearer de bootstrap de $OUTRO"
else
  BOOT=$(SEGREDO="$SEGREDO" OUTRO="$OUTRO" python3 - <<'PY'
import base64, hashlib, hmac, json, os, time
b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
now = int(time.time())
h = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
p = b(json.dumps({"sub": "probe_aas08_bootstrap", "tenant_id": os.environ["OUTRO"], "roles": ["admin"],
                  "accessible_pools": ["probe_aas08_outro_a2a"],   # conceder pool exige detê-lo
                  "module_config": {"config": {"agents": {"access": "read_write", "scope": []},
                                               "resources": {"access": "read_write", "scope": []}}},
                  "iat": now, "exp": now + 600}).encode())
s = b(hmac.new(os.environ["SEGREDO"].encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
print(f"{h}.{p}.{s}")
PY
)
  TENANT_SALVO=$TENANT; TENANT=$OUTRO
  XPOOL="probe_aas08_outro_a2a"   # o principal só nasce com pool que expõe A2A NO TENANT DELE
  if [ "$(req "$BOOT" GET "$REG/v1/pools/$XPOOL")" = 404 ]; then
    req "$BOOT" POST "$REG/v1/pools" "{\"pool_id\":\"$XPOOL\",\"agent_kind\":\"ai\",\"channel_types\":[\"a2a\"],\"sla_target_ms\":60000,\"max_concurrent_sessions\":1,\"description\":\"fixture do probe AAS-08 (outro tenant)\",\"a2a\":$DESC}" >/dev/null
  fi
  req "$BOOT" GET "$P" >/dev/null
  XID=$(body | jq -r '.[]? | select(.display_name=="probe AAS-08 outro tenant") | .agent_principal_id' | head -1)
  if [ -z "$XID" ]; then req "$BOOT" POST "$P" "{\"display_name\":\"probe AAS-08 outro tenant\",\"allowed_pools\":[\"$XPOOL\"]}" >/dev/null
  else req "$BOOT" PUT "$P/$XID" '{"active":true}' >/dev/null; req "$BOOT" POST "$P/$XID/credential" >/dev/null; fi
  XCRED=$(body | jq -r '.credential // empty'); TENANT=$TENANT_SALVO
  if [ -z "$XCRED" ]; then incon "X1 não cadastrei o principal de $OUTRO: $(body | head -c 200)"
  else
    DESDE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    st=$($C -o "$BODY" -w '%{http_code}' -X POST "$GW/a2a/$SLUG" -H 'Content-Type: application/json' \
      -H 'A2A-Version: 1.0' -H "Authorization: Bearer $XCRED" -d "$GETX")
    sleep 1
    log=$(docker logs --since "$DESDE" plughub-demo-channel-gateway-1 2>&1 | grep -c "é do tenant $OUTRO")
    [ "$st" = 403 ] && [ "$log" -ge 1 ] \
      && ok "X1 principal de $OUTRO na porta de tenant_demo → 403, recusado pelo TENANT (log)" \
      || falha "X1 -> $st, linhas de log nomeando o tenant: $log"
  fi
fi

limpa
echo ""; echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — os dois SDKs oficiais leem o card e todas as respostas; versão e tenant recusados na porta."
exit 0
