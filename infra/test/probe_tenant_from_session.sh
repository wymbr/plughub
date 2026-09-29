#!/usr/bin/env bash
#
# probe_tenant_from_session.sh — TNT-01 (2026-09-29)
#
# A PERGUNTA
# ==========
# O tenant que uma leitura usa é o da SESSÃO do usuário — na UI e no servidor?
#
# O QUE FOI MEDIDO ANTES
# ======================
# UI: sete telas liam o tenant de `VITE_TENANT_ID` (default `tenant_demo`), env que build
# nenhum define — numa instância de outro cliente, consultariam `tenant_demo`.
# Servidor, e pior: a analytics-api recebia `tenant_id` pela QUERY e não o comparava com o
# do JWT. O token do `tenant_demo` leu o histórico e a visão 360 do cliente `d-00`, que só
# existe em `t_poss_1213d358`. O escopo de pool não segurava: pool é recorte DENTRO do tenant.
#
# RAMOS
#   A  UI: nenhum `VITE_TENANT_ID` em src/ (código, não comentário), com mutação.
#   B  servidor: toda rota da analytics-api que declara `tenant_id` está numa classe
#      conhecida (censo AST `_analytics_tenant_census.py`): `coberta` (query + principal de
#      pool, onde a recusa mora), `sistema` (effective_tenant) ou `fora` DECLARADA abaixo.
#      Rota nova fora da declaração reprova.
#   C  vivo, pela borda: com o JWT do admin (tenant_demo), outro tenant na query → 403
#      `tenant_mismatch`; o próprio tenant → 200 (controle); e a leitura cruzada medida
#      (cliente `d-00` do `t_poss_1213d358`) deixa de responder.
#
# Veredicto: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 3
export PYTHONIOENCODING=utf-8

BORDA="${BORDA:-http://localhost:5174}"
FAIL=0; INCONCL=0
ok()  { echo "  ✓ $*"; }
bad() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ⏭️  $* (INCONCLUSIVO)"; INCONCL=$((INCONCL+1)); }

# Rotas que tiram o tenant de outra origem ou decidem no corpo, e POR QUE estão seguras.
FORA_DECLARADAS=$(cat <<'TABELA'
GET /sessions/{session_id}/messages|audit.py: _check_audit_access devolve o tenant das CLAIMS, que vence o da query (claims_tenant or tenant_id)
GET /mcp-calls|audit.py: idem — o tenant efetivo é o das claims verificadas
TABELA
)

vite_tenant() {
  grep -rn "VITE_TENANT_ID" "$1" --include=*.ts --include=*.tsx 2>/dev/null \
    | grep -vE '^[^:]+:[0-9]+:[[:space:]]*(//|\*|/\*)'
}

echo "== probe_tenant_from_session =="
echo
echo "── A · a UI não tira o tenant do build ──"
A=$(vite_tenant packages/platform-ui/src)
if [ -z "$A" ]; then ok "zero VITE_TENANT_ID em código da UI"
else echo "$A" | sed 's/^/     /'; bad "tenant de build no código da UI"; fi
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
printf 'const T = import.meta.env.VITE_TENANT_ID ?? "tenant_demo"\n// VITE_TENANT_ID em comentario nao conta\n' > "$TMP/x.ts"
[ "$(vite_tenant "$TMP" | wc -l)" = "1" ] && ok "mutação: a linha de código é vista, o comentário não" \
                                          || bad "mutação: o detector não vê o que diz ver"

echo
echo "── B · toda rota com tenant_id está numa classe conhecida ──"
CENSO=$(python3 infra/test/_analytics_tenant_census.py 2>&1)
if ! printf '%s' "$CENSO" | head -1 | grep -q '^{'; then
  inc "censo ilegível: $(printf '%s' "$CENSO" | head -2)"
else
  SAIDA=$(python3 - "$CENSO" "$FORA_DECLARADAS" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
decl = {l.split("|", 1)[0] for l in sys.argv[2].strip().splitlines() if l.strip()}
s = d["summary"]
print(f"CONTAGEM coberta={s['coberta']} sistema={s['sistema']} fora={s['fora']}")
fora = {r["route"] for r in d["routes"] if r["class"] == "fora"}
for r in sorted(fora - decl):
    print(f"ERRO rota FORA da casa única e não declarada: {r}")
for r in sorted(decl - fora):
    print(f"ERRO declaração órfã (a rota não está mais fora): {r}")
if s["coberta"] < 60:
    print(f"ERRO só {s['coberta']} rotas cobertas — o censo encolheu, provavelmente parou de ver as dependências")
PY
)
  echo "$SAIDA" | grep '^CONTAGEM' | sed 's/^CONTAGEM /     /'
  if echo "$SAIDA" | grep -q '^ERRO'; then
    echo "$SAIDA" | grep '^ERRO' | sed 's/^ERRO /  ❌ /'; FAIL=$((FAIL + $(echo "$SAIDA" | grep -c '^ERRO')))
  else ok "cobertas pela recusa, de sistema, ou fora com motivo declarado"; fi
fi

echo
echo "── C · pela borda, ao vivo ──"
TOK=$(curl -s -m 5 -X POST "${AUTH:-http://localhost:3202}/auth/login" -H 'content-type: application/json' \
  -d '{"email":"admin@plughub.local","password":"changeme_admin","tenant_id":"tenant_demo"}' \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("access_token",""))
except Exception: print("")')
if [ -z "$TOK" ]; then
  inc "sem token do auth-api — sem credencial não há o que medir"
else
  code() { curl -s -m 8 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" "$BORDA$1"; }
  body() { curl -s -m 8 -H "Authorization: Bearer $TOK" "$BORDA$1"; }
  for u in "/analytics/sessions/customer/x?limit=1" "/reports/customers/x/360?_=1" \
           "/reports/journeys?customer_id=x" "/analytics/sessions/customer/x/search?q=a&limit=1"; do
    c_outro=$(code "$u&tenant_id=outro_tenant_probe"); c_meu=$(code "$u&tenant_id=tenant_demo")
    rota="${u%%\?*}"
    if [ "$c_outro" = "403" ] && [ "$c_meu" = "200" ]; then ok "$rota: outro tenant 403, o próprio 200"
    else bad "$rota: outro tenant $c_outro (esperado 403), o próprio $c_meu (esperado 200)"; fi
  done
  b=$(body "/analytics/sessions/customer/d-00?limit=5&tenant_id=t_poss_1213d358")
  if printf '%s' "$b" | grep -q 'tenant_mismatch'; then ok "a leitura cruzada medida (d-00 de t_poss_1213d358) agora é recusada"
  else bad "a leitura cruzada ainda responde: ${b:0:120}"; fi
fi

echo
echo "  FAIL=$FAIL  INCONCLUSIVO=$INCONCL"
[ "$FAIL" -gt 0 ] && { echo "❌ REPROVOU"; exit 1; }
[ "$INCONCL" -gt 0 ] && { echo "⏭️  INCONCLUSIVO — não é verde"; exit 3; }
echo "✅ o tenant é o da sessão — na UI e no servidor"
exit 0
