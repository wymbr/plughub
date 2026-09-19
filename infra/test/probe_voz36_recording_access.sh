#!/usr/bin/env bash
# probe_voz36_recording_access.sh — VOZ-36: ouvir e exportar a gravação da chamada.
#
# PROPOSIÇÃO: a gravação só sai para quem tem `contacts.recording` no nível pedido
# (read_only ouve · read_write exporta), num pool que ATENDEU a parte e que está no domínio
# de linhas do chamador — e CADA escuta, exportação e recusa chega à trilha LGPD
# (`audit_access_log`), inclusive a chamada sem credencial.
#
# Cada ramo de recusa tem controle positivo: um portão que nega tudo passaria em todos eles.
#
# RAMOS (duas partes sintéticas numa sessão de probe: parte 1 atendida por PA, parte 2 por PB,
# mais um anexo de webchat como controle de classe; tudo EXPIRADO no fim):
#   C0 catálogo: o auth-api declara `contacts.recording` (senão ninguém o recebe pela tela)
#   A1 sem credencial → 401
#   A2 sem o campo → 403, para id existente E inexistente (sem oráculo de existência)
#   A3 read_only, accessible_pools=[PB] → parte 1 403; lista mostra só a 2 e omitted=1
#   A4 read_only, pools=[PA] → parte 1 200 com `OggS` (controle); exportar 403
#   A5 read_write, grant escopado a pool:PA → exporta a 1 (attachment); a 2 → 403
#   A6 anexo de webchat pela porta de gravação → 404; gravação pela porta pública → 404
#   P1 pelo proxy do platform-ui (5174): a rota chega ao gateway (401 com a frase dele)
#   T1 trilha: ≥1 listen ok · ≥1 export ok · ≥1 anonymous denied · ≥4 user denied, para os ids
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq).
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CH="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
GWURL="${GWURL:-http://localhost:8010}"
UIURL="${UIURL:-http://localhost:5174}"
CHDB="${CHDB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
PA="probe_voz36_a"; PB="probe_voz36_b"
SID="probe-voz36-$(date +%s)-$RANDOM"
FAIL=0; INC=0

ok()    { echo "  ✓ $1"; }
bad()   { echo "  ✗ $1"; FAIL=1; }
incon() { echo "  ? $1"; INC=1; }
fim()   {
  [ -n "${IDS:-}" ] && docker exec -i -e MODE=expire -e IDS="$IDS" -e TENANT="$TENANT" "$GW" python - \
      < infra/test/_voz36_fixture.py >/dev/null 2>&1
  echo
  if [ $FAIL -ne 0 ]; then echo "VEREDICTO: VERMELHO"; exit 1; fi
  if [ $INC -ne 0 ]; then echo "VEREDICTO: INCONCLUSIVO"; exit 2; fi
  echo "VEREDICTO: VERDE"; exit 0
}
command -v jq >/dev/null || { incon "jq ausente — rode de dentro do WSL"; fim; }
docker inspect "$GW" >/dev/null 2>&1 || { incon "$GW fora do ar"; fim; }

echo "══ probe_voz36_recording_access — ouvir/exportar gravação: capacidade, pool, trilha ══"

# ── C0 catálogo ──────────────────────────────────────────────────────────────
echo; echo "C0 · catálogo ABAC (estado vivo: auth.module_registry, não o YAML)"
N=$(docker exec "$PG" psql -U plughub -d plughub_demo -tAc   "SELECT count(*) FROM auth.module_registry WHERE module_id='contacts' AND tenant_id IS NULL AND schema::text LIKE '%\"recording\"%'" 2>&1 | tr -d '[:space:]')
case "$N" in
  1) ok "contacts.recording registrado no catálogo vivo" ;;
  0) bad "contacts.recording AUSENTE do auth.module_registry — auth-api não foi recriado?" ;;
  *) incon "catálogo ilegível: $N" ;;
esac

# ── fixture ──────────────────────────────────────────────────────────────────
FX=$(docker exec -i -e MODE=create -e SID="$SID" -e POOL_A="$PA" -e POOL_B="$PB" -e TENANT="$TENANT" "$GW" python - \
      < infra/test/_voz36_fixture.py 2>&1 | tail -1)
FA=$(printf '%s' "$FX" | jq -r '.a // empty' 2>/dev/null)
FB=$(printf '%s' "$FX" | jq -r '.b // empty' 2>/dev/null)
FW=$(printf '%s' "$FX" | jq -r '.web // empty' 2>/dev/null)
[ -n "$FA" ] && [ -n "$FB" ] && [ -n "$FW" ] || { incon "fixture nao criada: $FX"; fim; }
IDS="$FA,$FB,$FW"
T0=$(date -u +'%Y-%m-%d %H:%M:%S')
echo "  (sessão $SID · parte1=$FA · parte2=$FB)"

mint() {  # $1 = JSON do module_config · $2 = JSON de accessible_pools
  docker exec -i "$GW" python - "$1" "$2" "$TENANT" <<'PY' 2>/dev/null | tail -1
import json, os, sys, time, jwt
mc, pools, tenant = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
now = int(time.time())
print(jwt.encode({"sub": "probe_voz36", "tenant_id": tenant, "iat": now, "exp": now + 600,
                  "module_config": mc, "accessible_pools": pools},
                 os.environ["PLUGHUB_AUTH_JWT_SECRET"], algorithm="HS256"))
PY
}
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }
RO='{"contacts":{"recording":{"access":"read_only","scope":[]}}}'
RW_A='{"contacts":{"recording":{"access":"read_write","scope":["pool:'"$PA"'"]}}}'
T_NONE=$(mint '{}' "[\"$PA\",\"$PB\"]")
T_RO_B=$(mint "$RO" "[\"$PB\"]")
T_RO_A=$(mint "$RO" "[\"$PA\"]")
T_RW_A=$(mint "$RW_A" "[\"$PA\",\"$PB\"]")
[ -n "$T_NONE" ] && [ -n "$T_RW_A" ] || { incon "nao consegui cunhar token (PLUGHUB_AUTH_JWT_SECRET no gateway?)"; fim; }
H() { echo "Authorization: Bearer $1"; }

chk() {  # $1 rótulo · $2 esperado · $3 obtido
  if [ "$2" = "$3" ]; then ok "$1 → $3"; else bad "$1: esperado $2, veio $3"; fi
}

echo; echo "A1 · sem credencial"
chk "ouvir parte 1 sem token" 401 "$(code "$GWURL/v1/recordings/$FA/audio")"

echo; echo "A2 · sem o campo"
chk "ouvir parte 1" 403 "$(code -H "$(H "$T_NONE")" "$GWURL/v1/recordings/$FA/audio")"
chk "ouvir id INEXISTENTE (sem oráculo)" 403 "$(code -H "$(H "$T_NONE")" "$GWURL/v1/recordings/$(cat /proc/sys/kernel/random/uuid)/audio")"
chk "listar a sessão" 403 "$(code -H "$(H "$T_NONE")" "$GWURL/v1/recordings/sessions/$SID")"

echo; echo "A3 · read_only, domínio só PB"
chk "ouvir parte 1 (atendida por PA)" 403 "$(code -H "$(H "$T_RO_B")" "$GWURL/v1/recordings/$FA/audio")"
L=$(curl -s --max-time 10 -H "$(H "$T_RO_B")" "$GWURL/v1/recordings/sessions/$SID")
chk "lista: partes visíveis" "$FB" "$(printf '%s' "$L" | jq -r '[.parts[].file_id]|join(",")')"
chk "lista: omitted" 1 "$(printf '%s' "$L" | jq -r '.omitted')"
chk "lista: can_export com read_only" false "$(printf '%s' "$L" | jq -r '.parts[0].can_export')"

echo; echo "A4 · read_only, domínio PA (controle positivo)"
B=$(curl -s --max-time 10 -H "$(H "$T_RO_A")" -D /tmp/voz36_h.$$ "$GWURL/v1/recordings/$FA/audio" | head -c 4)
chk "ouvir parte 1: corpo" OggS "$B"
chk "ouvir parte 1: disposition" inline "$(grep -i '^content-disposition' /tmp/voz36_h.$$ | awk '{print $2}' | tr -d ';\r')"
rm -f /tmp/voz36_h.$$
chk "exportar com read_only" 403 "$(code -H "$(H "$T_RO_A")" "$GWURL/v1/recordings/$FA/export")"

echo; echo "A5 · read_write escopado a pool:PA"
curl -s --max-time 10 -o /dev/null -D /tmp/voz36_h.$$ -H "$(H "$T_RW_A")" "$GWURL/v1/recordings/$FA/export"
chk "exportar parte 1: status" 200 "$(head -1 /tmp/voz36_h.$$ | awk '{print $2}')"
chk "exportar parte 1: disposition" attachment "$(grep -i '^content-disposition' /tmp/voz36_h.$$ | awk '{print $2}' | tr -d ';\r')"
rm -f /tmp/voz36_h.$$
chk "exportar parte 2 (PB fora do escopo do grant)" 403 "$(code -H "$(H "$T_RW_A")" "$GWURL/v1/recordings/$FB/export")"

echo; echo "A6 · classe"
chk "anexo de webchat pela porta de gravação" 404 "$(code -H "$(H "$T_RW_A")" "$GWURL/v1/recordings/$FW/audio")"
chk "gravação pela porta pública de anexos" 404 "$(code "$GWURL/webchat/v1/attachments/$FA")"
chk "controle: o anexo de webchat SAI pela porta pública" 200 "$(code "$GWURL/webchat/v1/attachments/$FW")"

echo; echo "P1 · proxy do platform-ui"
PR=$(curl -s --max-time 10 "$UIURL/v1/recordings/sessions/$SID")
if printf '%s' "$PR" | grep -c "gravacao exige credencial" >/dev/null; then ok "5174 → gateway (401 do portão)"
else bad "5174 nao chega ao gateway: $(printf '%s' "$PR" | cut -c1-120)"; fi

echo; echo "T1 · trilha LGPD ($CHDB.audit_access_log)"
Q="SELECT
  countIf(endpoint='channel-gateway:recording.listen' AND result='ok'),
  countIf(endpoint='channel-gateway:recording.export' AND result='ok'),
  countIf(actor_kind='anonymous' AND result='denied'),
  countIf(actor_kind='user' AND result='denied')
FROM $CHDB.audit_access_log
WHERE accessed_at >= toDateTime64('$T0', 3, 'UTC') - INTERVAL 5 SECOND
  AND (position(target_id, '$FA') > 0 OR position(target_id, '$FB') > 0 OR position(target_id, '$FW') > 0)
FORMAT TSV"
R=""
for _ in $(seq 1 20); do
  R=$(docker exec -i "$CH" clickhouse-client -q "$Q" 2>&1 | tail -1)
  set -- $R
  [ "${1:-0}" -ge 1 ] 2>/dev/null && [ "${2:-0}" -ge 1 ] && [ "${3:-0}" -ge 1 ] && [ "${4:-0}" -ge 4 ] && break
  sleep 2
done
set -- $R
if ! [ "${1:-x}" -ge 0 ] 2>/dev/null; then incon "ClickHouse nao respondeu: $R"
else
  [ "$1" -ge 1 ] && ok "escuta concedida registrada ($1)" || bad "nenhuma escuta ok na trilha"
  [ "$2" -ge 1 ] && ok "exportação concedida registrada ($2)" || bad "nenhuma exportação ok na trilha"
  [ "$3" -ge 1 ] && ok "recusa SEM credencial registrada ($3)" || bad "a recusa anônima nao chegou à trilha"
  [ "$4" -ge 4 ] && ok "recusas de usuário registradas ($4, esperado ≥4)" || bad "recusas de usuário: $4 (esperado ≥4)"
fi

fim
