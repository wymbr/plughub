#!/usr/bin/env bash
# probe_att06_attachment_view.sh — ATT-06: nítido para quem ATENDE, borrado para os demais, e o
# "revelar" na trilha.
#
# PROPOSIÇÃO: o original de um anexo só sai para quem atende o contato (`human-{sub}` no roster da
# sessão com papel primary/specialist); quem não atende (supervisor, avaliador, replay) recebe a
# prévia BORRADA da imagem, e o original só com `?reveal=true`, que a trilha registra como
# `revealed`. Não-imagem sem revelar → 409. Medido pelo caminho do Console (5174 → analytics-api →
# gateway), com os MESMOS grants e o MESMO pool nos três usuários — só o roster os distingue.
#
# RAMOS (um JPEG com detalhe e um PDF numa sessão de probe; roster: ATENDE=primary, SUP=supervisor):
#   V1 quem atende → 200, vista `original` (CONTROLE POSITIVO: o portão deixa passar)
#   V2 quem não está no roster → 200, vista `blurred`, bytes ≠ do original
#   V3 supervisor no roster → `blurred` (papel não é de atendimento)
#   V4 quem não atende + reveal → vista `revealed`, bytes = do original
#   V5 PDF, quem não atende → 409 · com reveal → 200 · quem atende → 200 direto
#   S1 a transcrição (SSE) traz o anexo pelo file_id e NUNCA o link assinado que entradas antigas
#      gravaram (ele abria o original pela porta pública, fora da prévia e da trilha)
#   I1 a variante borrada no gateway continua só de serviço (usuário 403)
#   T1 trilha: ok ≥2 · ok_blurred ≥2 · revealed ≥2 · reveal_required ≥1, para os ids do probe
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
PA="probe_att06_a"
SID="probe-att06-$(date +%s)-$RANDOM"
FAIL=0; INC=0; FILE=""; PDF=""
TMP=$(mktemp -d)

ok()    { echo "  ✓ $1"; }
bad()   { echo "  ✗ $1"; FAIL=1; }
incon() { echo "  ? $1"; INC=1; }
fim()   {
  [ -n "$FILE" ] && docker exec -i -e MODE=expire -e SID="$SID" -e FILE="$FILE,$PDF" -e TENANT="$TENANT" \
      "$GW" python - < infra/test/_att02_fixture.py >/dev/null 2>&1
  rm -rf "$TMP"
  echo
  if [ $FAIL -ne 0 ]; then echo "VEREDICTO: VERMELHO"; exit 1; fi
  if [ $INC -ne 0 ]; then echo "VEREDICTO: INCONCLUSIVO"; exit 2; fi
  echo "VEREDICTO: VERDE"; exit 0
}
command -v jq >/dev/null || { incon "jq ausente — rode de dentro do WSL"; fim; }
docker inspect "$GW" >/dev/null 2>&1 || { incon "$GW fora do ar"; fim; }

echo "══ probe_att06_attachment_view — anexo: nítido para quem atende, borrado para os demais ══"

ROSTER='[{"participant_id":"human-probe_att06_atende","role":"primary"},{"participant_id":"human-probe_att06_sup","role":"supervisor"}]'
FX=$(docker exec -i -e MODE=create -e SID="$SID" -e POOL="$PA" -e TENANT="$TENANT" -e ROSTER="$ROSTER" -e PDF=1 -e STREAM=1 \
      "$GW" python - < infra/test/_att02_fixture.py 2>/dev/null | tail -1)
FILE=$(printf '%s' "$FX" | jq -r '.file // empty' 2>/dev/null)
PDF=$(printf '%s' "$FX" | jq -r '.pdf // empty' 2>/dev/null)
[ -n "$FILE" ] && [ -n "$PDF" ] || { incon "fixture nao criada: $FX"; fim; }
T0=$(date -u +'%Y-%m-%d %H:%M:%S')
echo "  (sessão $SID · pool $PA · imagem $FILE · pdf $PDF)"

mint() {  # $1 = sub
  docker exec -i "$GW" python - "$1" "$TENANT" "$PA" <<'PY' 2>/dev/null | tail -1
import json, os, sys, time, jwt
sub, tenant, pool = sys.argv[1:4]
now = int(time.time())
print(jwt.encode({"sub": sub, "tenant_id": tenant, "iat": now, "exp": now + 600,
                  "module_config": {"contacts": {"transcricao": {"access": "read_only", "scope": []}}},
                  "accessible_pools": [pool]},
                 os.environ["PLUGHUB_AUTH_JWT_SECRET"], algorithm="HS256"))
PY
}
T_AT=$(mint probe_att06_atende); T_OUT=$(mint probe_att06_outro); T_SUP=$(mint probe_att06_sup)
[ -n "$T_AT" ] && [ -n "$T_OUT" ] && [ -n "$T_SUP" ] || { incon "nao consegui cunhar token"; fim; }

pega() {  # $1 = nome · $2 = token · $3 = url → status em $TMP/$1.st, vista em $TMP/$1.view, corpo em $TMP/$1.body
  curl -s --max-time 20 -D "$TMP/$1.hdr" -o "$TMP/$1.body" -H "Authorization: Bearer $2" "$3"
  awk 'NR==1{print $2}' "$TMP/$1.hdr" > "$TMP/$1.st"
  (grep -i '^x-attachment-view' "$TMP/$1.hdr" | awk '{print $2}' | tr -d '\r') > "$TMP/$1.view"
}
st()   { cat "$TMP/$1.st"; }
view() { cat "$TMP/$1.view"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1 → $3"; else bad "$1: esperado $2, veio $3"; fi; }
URL()  { echo "$UIURL/analytics/v1/attachments/$1${2:-}"; }

echo; echo "V1 · quem atende (controle positivo)"
pega at "$T_AT" "$(URL "$FILE")"
chk "status" 200 "$(st at)"; chk "vista" original "$(view at)"
[ "$(st at)" = 200 ] || { incon "o controle positivo não abriu — as recusas abaixo não provariam nada"; fim; }

echo; echo "V2 · quem não está no roster"
pega out "$T_OUT" "$(URL "$FILE")"
chk "status" 200 "$(st out)"; chk "vista" blurred "$(view out)"
cmp -s "$TMP/at.body" "$TMP/out.body" && bad "a prévia é o ORIGINAL (bytes iguais)" || ok "bytes ≠ do original"

echo; echo "V3 · supervisor no roster"
pega sup "$T_SUP" "$(URL "$FILE")"
chk "vista" blurred "$(view sup)"

echo; echo "V4 · quem não atende, revelando"
pega rev "$T_OUT" "$(URL "$FILE" '?reveal=true')"
chk "vista" revealed "$(view rev)"
cmp -s "$TMP/at.body" "$TMP/rev.body" && ok "bytes = do original" || bad "o revelado não é o original"

echo; echo "V5 · PDF (sem prévia borrada)"
pega p1 "$T_OUT" "$(URL "$PDF")";               chk "quem não atende" 409 "$(st p1)"
pega p2 "$T_OUT" "$(URL "$PDF" '?reveal=true')"; chk "quem não atende, revelando" 200 "$(st p2)"
pega p3 "$T_AT" "$(URL "$PDF")";                chk "quem atende" 200 "$(st p3)"

echo; echo "S1 · a transcrição mostra o anexo, e nunca o link da porta pública"
curl -s -N --max-time 4 -H "Authorization: Bearer $T_OUT" \
  "$UIURL/sessions/$SID/stream?tenant_id=$TENANT" > "$TMP/sse" 2>/dev/null
if ! grep -q 'att06-probe\|Anexo: foto' "$TMP/sse"; then
  incon "a transcrição não trouxe a fala da fixture ($(head -c 200 "$TMP/sse"))"
else
  grep -q "\"file_id\": \"$FILE\"" "$TMP/sse" && ok "o anexo chega pelo file_id" || bad "a transcrição perdeu o anexo"
  grep -q 'LINKANTIGO' "$TMP/sse" && bad "o link assinado da porta pública chegou a quem não atende" \
                                  || ok "o link gravado não sai"
fi

echo; echo "I1 · a variante no gateway continua só de serviço"
chk "usuário direto no gateway" 403 "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
    -H "Authorization: Bearer $T_AT" "$GWURL/v1/attachments/$FILE/content?tenant_id=$TENANT&variant=blurred")"

echo; echo "T1 · trilha LGPD ($CHDB.audit_access_log)"
Q="SELECT countIf(result='ok'), countIf(result='ok_blurred'), countIf(result='revealed'), countIf(result='reveal_required')
FROM $CHDB.audit_access_log
WHERE endpoint='analytics-api:attachments.view'
  AND accessed_at >= toDateTime64('$T0', 3, 'UTC') - INTERVAL 5 SECOND
  AND (position(target_id, '$FILE') > 0 OR position(target_id, '$PDF') > 0)
FORMAT TSV"
R=""
for _ in $(seq 1 10); do
  R=$(docker exec -i "$CH" clickhouse-client -q "$Q" 2>&1 | tail -1)
  set -- $R
  [ "${1:-0}" -ge 2 ] 2>/dev/null && [ "${2:-0}" -ge 2 ] && [ "${3:-0}" -ge 2 ] && [ "${4:-0}" -ge 1 ] && break
  sleep 1
done
set -- $R
if ! [ "${1:-x}" -ge 0 ] 2>/dev/null; then incon "ClickHouse nao respondeu: $R"
else
  [ "$1" -ge 2 ] && ok "vista original de quem atende ($1)" || bad "ok na trilha: $1 (esperado ≥2)"
  [ "$2" -ge 2 ] && ok "prévias borradas ($2)" || bad "ok_blurred na trilha: $2 (esperado ≥2)"
  [ "$3" -ge 2 ] && ok "revelações ($3)" || bad "revealed na trilha: $3 (esperado ≥2)"
  [ "$4" -ge 1 ] && ok "pedido de revelar ($4)" || bad "reveal_required não chegou à trilha"
fi

fim
