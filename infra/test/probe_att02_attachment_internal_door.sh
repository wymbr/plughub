#!/usr/bin/env bash
# probe_att02_attachment_internal_door.sh — ATT-02: a porta INTERNA do anexo do cliente.
#
# PROPOSIÇÃO: o anexo só sai para quem pode ler a conversa dele — `contacts.transcricao` E o pool
# da sessão no domínio do chamador — e CADA acesso e recusa chega à trilha LGPD
# (`audit_access_log`). Medido pelo caminho do Console: `/analytics/v1/attachments/{id}` na borda
# do platform-ui (5174), que leva à analytics-api, que busca os bytes no gateway por rota interna.
#
# Cada recusa tem o controle positivo ao lado: um portão que nega tudo passaria nelas.
#
# RAMOS (um JPEG sintético numa sessão de probe com pool de entrada PA; expirado no fim):
#   A1 sem credencial → 401
#   A2 sem o campo (só `contacts.monitorar`), domínio PA → 403; id INEXISTENTE também 403 (sem oráculo)
#   A3 com o campo, domínio só PB → 403
#   A4 com o campo, domínio PA → 200, corpo JPEG, `nosniff`, CSP `sandbox` (CONTROLE POSITIVO)
#   A5 com o campo, id inexistente → 404
#   I1 a rota interna do gateway recusa usuário (403) e anônimo (401): não é porta alternativa
#   T1 trilha: ≥1 ok · ≥2 denied · ≥1 not_found, para os ids do probe
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq).
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CH="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
GWURL="${GWURL:-http://localhost:8010}"
UIURL="${UIURL:-http://localhost:5174}"
CHDB="${CHDB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
PA="probe_att02_a"; PB="probe_att02_b"
SID="probe-att02-$(date +%s)-$RANDOM"
FAIL=0; INC=0; FILE=""
GHOST=$(cat /proc/sys/kernel/random/uuid)

ok()    { echo "  ✓ $1"; }
bad()   { echo "  ✗ $1"; FAIL=1; }
incon() { echo "  ? $1"; INC=1; }
fim()   {
  [ -n "$FILE" ] && docker exec -i -e MODE=expire -e SID="$SID" -e FILE="$FILE" -e TENANT="$TENANT" \
      "$GW" python - < infra/test/_att02_fixture.py >/dev/null 2>&1
  echo
  if [ $FAIL -ne 0 ]; then echo "VEREDICTO: VERMELHO"; exit 1; fi
  if [ $INC -ne 0 ]; then echo "VEREDICTO: INCONCLUSIVO"; exit 2; fi
  echo "VEREDICTO: VERDE"; exit 0
}
command -v jq >/dev/null || { incon "jq ausente — rode de dentro do WSL"; fim; }
docker inspect "$GW" >/dev/null 2>&1 || { incon "$GW fora do ar"; fim; }

echo "══ probe_att02_attachment_internal_door — anexo: capacidade, pool da sessão, trilha ══"

FX=$(docker exec -i -e MODE=create -e SID="$SID" -e POOL="$PA" -e TENANT="$TENANT" "$GW" python - \
      < infra/test/_att02_fixture.py 2>/dev/null | tail -1)
FILE=$(printf '%s' "$FX" | jq -r '.file // empty' 2>/dev/null)
[ -n "$FILE" ] || { incon "fixture nao criada: $FX"; fim; }
T0=$(date -u +'%Y-%m-%d %H:%M:%S')
echo "  (sessão $SID · pool de entrada $PA · anexo $FILE)"

mint() {  # $1 = module_config JSON · $2 = accessible_pools JSON
  docker exec -i "$GW" python - "$1" "$2" "$TENANT" <<'PY' 2>/dev/null | tail -1
import json, os, sys, time, jwt
mc, pools, tenant = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
now = int(time.time())
print(jwt.encode({"sub": "probe_att02", "tenant_id": tenant, "iat": now, "exp": now + 600,
                  "module_config": mc, "accessible_pools": pools},
                 os.environ["PLUGHUB_AUTH_JWT_SECRET"], algorithm="HS256"))
PY
}
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1 → $3"; else bad "$1: esperado $2, veio $3"; fi; }
H()    { echo "Authorization: Bearer $1"; }
URL()  { echo "$UIURL/analytics/v1/attachments/$1"; }

T_MON=$(mint '{"contacts":{"monitorar":{"access":"read_write","scope":[]}}}' "[\"$PA\"]")
T_TB=$(mint '{"contacts":{"transcricao":{"access":"read_only","scope":[]}}}' "[\"$PB\"]")
T_TA=$(mint '{"contacts":{"transcricao":{"access":"read_only","scope":[]}}}' "[\"$PA\"]")
[ -n "$T_MON" ] && [ -n "$T_TA" ] || { incon "nao consegui cunhar token (PLUGHUB_AUTH_JWT_SECRET no gateway?)"; fim; }

echo; echo "A1 · sem credencial"
chk "anexo sem token" 401 "$(code "$(URL "$FILE")")"

echo; echo "A2 · sem o campo (só monitorar), domínio PA"
chk "anexo existente" 403 "$(code -H "$(H "$T_MON")" "$(URL "$FILE")")"
chk "id INEXISTENTE (sem oráculo)" 403 "$(code -H "$(H "$T_MON")" "$(URL "$GHOST")")"

echo; echo "A3 · com o campo, domínio só PB"
chk "anexo da sessão de PA" 403 "$(code -H "$(H "$T_TB")" "$(URL "$FILE")")"

echo; echo "A4 · com o campo, domínio PA (controle positivo)"
HDR=$(mktemp)
B=$(curl -s --max-time 15 -D "$HDR" -H "$(H "$T_TA")" "$(URL "$FILE")" | head -c 3 | od -An -tx1 | tr -d ' \n')
chk "status" 200 "$(head -1 "$HDR" | awk '{print $2}')"
chk "corpo (magic JPEG)" ffd8ff "$B"
chk "nosniff" nosniff "$(grep -i '^x-content-type-options' "$HDR" | awk '{print $2}' | tr -d '\r')"
grep -qi '^content-security-policy:.*sandbox' "$HDR" && ok "CSP sandbox" || bad "CSP sem sandbox"
rm -f "$HDR"

echo; echo "A5 · com o campo, id inexistente"
chk "id inexistente" 404 "$(code -H "$(H "$T_TA")" "$(URL "$GHOST")")"

echo; echo "I1 · a rota interna do gateway não é porta alternativa"
chk "usuário COM o campo, direto no gateway" 403 "$(code -H "$(H "$T_TA")" "$GWURL/v1/attachments/$FILE/content?tenant_id=$TENANT")"
chk "anônimo direto no gateway" 401 "$(code "$GWURL/v1/attachments/$FILE/content?tenant_id=$TENANT")"

echo; echo "T1 · trilha LGPD ($CHDB.audit_access_log)"
Q="SELECT countIf(result='ok'), countIf(result='denied'), countIf(result='not_found')
FROM $CHDB.audit_access_log
WHERE endpoint='analytics-api:attachments.view'
  AND accessed_at >= toDateTime64('$T0', 3, 'UTC') - INTERVAL 5 SECOND
  AND (position(target_id, '$FILE') > 0 OR position(target_id, '$GHOST') > 0)
FORMAT TSV"
R=""
for _ in $(seq 1 10); do
  R=$(docker exec -i "$CH" clickhouse-client -q "$Q" 2>&1 | tail -1)
  set -- $R
  [ "${1:-0}" -ge 1 ] 2>/dev/null && [ "${2:-0}" -ge 3 ] && [ "${3:-0}" -ge 1 ] && break
  sleep 1
done
set -- $R
if ! [ "${1:-x}" -ge 0 ] 2>/dev/null; then incon "ClickHouse nao respondeu: $R"
else
  [ "$1" -ge 1 ] && ok "acesso concedido registrado ($1)" || bad "nenhum acesso ok na trilha"
  [ "$2" -ge 3 ] && ok "recusas registradas ($2, esperado ≥3)" || bad "recusas na trilha: $2 (esperado ≥3)"
  [ "$3" -ge 1 ] && ok "id desconhecido registrado ($3)" || bad "o 404 não chegou à trilha"
fi

fim
