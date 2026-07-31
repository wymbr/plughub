#!/usr/bin/env bash
# check_wrapup_summary.sh — SONDA do `GET /reports/wrapup-summary`
# (I5 / ADR § D7b, fatia 2 — histórico do trabalho author-bound).
#
# Não é um smoke: o dado nasce de wrap-ups reais já encerrados. O script afirma
# sobre o que existe e se declara INCONCLUSIVO quando não há amostra.
#
# A asserção que mais importa é a **B**: `error: data_unavailable`. Esse campo é
# como o analytics-api reporta uma query que explodiu, e o corpo vem com
# `data: []` — indistinguível de "não houve wrap-up no período" para quem só olha
# a tela. É exatamente o modo de falha que deixou o `/reports/timeseries/handle_time`
# quebrado por meses, mudo (ver TODO § "Auditar duration_ms × handle_time_ms").
#
# Uso (raiz do repo):
#   bash infra/test/check_wrapup_summary.sh
#   FROM_DAYS=90 bash infra/test/check_wrapup_summary.sh
set -euo pipefail

TENANT="tenant_demo"
AN="${AN:-http://localhost:3500}"
# auth-api = 3202 no HOST (3200 é o ai-gateway).
AUTH="${AUTH:-http://localhost:3202}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
FROM_DAYS="${FROM_DAYS:-30}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

echo "══ 0) login (o pool-scoping do analytics depende do Bearer) ══"
TOK=$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -n "$TOK" ] || { echo "   ✗ login falhou em $AUTH (3202 é auth-api; 3200 é o ai-gateway)"; exit 1; }
echo "   ✓ token obtido"

FROM_DT=$(date -u -d "-${FROM_DAYS} days" +%Y-%m-%dT%H:%M:%SZ)
URL="$AN/reports/wrapup-summary?tenant_id=$TENANT&group_by=agent&from_dt=$FROM_DT"

echo "══ 1) resposta do endpoint ══"
# `BODY=$(curl …)` sob `set -e` MORRE SEM IMPRIMIR NADA quando o curl falha —
# e ele falha, com código 7, enquanto o container ainda sobe (um `up -d
# --force-recreate` leva ~50 s). O sintoma era o script sumir entre dois
# cabeçalhos, que é a falha silenciosa que este arquivo existe para evitar.
# Captura-se o código HTTP e reporta-se; a espera é explícita.
HTTP="000"
for i in $(seq 1 15); do
  HTTP=$($CURL -o /tmp/_wus_body -w '%{http_code}' \
         -H "Authorization: Bearer $TOK" "$URL" || echo "000")
  [ "$HTTP" != "000" ] && break
  [ "$i" = "1" ] && echo "   … analytics-api ainda não responde em $AN; aguardando"
  sleep 3
done
BODY=$(cat /tmp/_wus_body 2>/dev/null || true)
[ "$HTTP" != "000" ] || {
  echo "   ✗ analytics-api INALCANÇÁVEL em $AN após ~45 s (curl não conectou)."
  echo "     O serviço subiu? \`docker compose -f docker-compose.demo.yml ps analytics-api\`"
  exit 1
}
[ "$HTTP" = "200" ] || { echo "   ✗ HTTP $HTTP — ${BODY:0:300}"; exit 1; }
echo "$BODY" | jq -e '.' >/dev/null 2>&1 || { echo "   ✗ resposta não é JSON: ${BODY:0:200}"; exit 1; }

echo "══ A) forma do payload ══"
for f in data totals meta; do
  echo "$BODY" | jq -e "has(\"$f\")" >/dev/null 2>&1 \
    && ok "campo $f presente" || bad "campo $f AUSENTE"
done

echo "══ B) a query NÃO degradou para data_unavailable ══"
ERR=$(echo "$BODY" | jq -r '.error // ""')
if [ -z "$ERR" ]; then
  ok "sem campo error — a query rodou de verdade"
else
  bad "error='$ERR' — a lista vazia é FALHA, não ausência de wrap-up. Ver logs do analytics-api"
fi

N=$(echo "$BODY" | jq -r '.data | length')
echo "   linhas: $N"
echo "$BODY" | jq -r '.totals'

if [ "${N:-0}" -eq 0 ]; then
  echo
  echo "   ⚠️  NENHUMA amostra nos últimos $FROM_DAYS dias — INCONCLUSIVO para as"
  echo "      asserções de conteúdo. Encerre wrap-ups (submetendo e deixando vencer)"
  echo "      ou aumente a janela com FROM_DAYS=."
  echo "══════════════════════════════════════"
  echo "  passou: $pass    falhou: $fail"
  [ "$fail" -eq 0 ] || exit 1
  exit 2
fi

echo "══ C) escopo: toda linha vem de pool interno (-int) ══"
# Se aparecer pool de CONTATO aqui, o filtro caiu e o relatório passou a contar
# APROVAÇÃO como wrap-up — os dois usam `task_submitted`, e a mistura seria
# invisível no número final.
LEAK=$(echo "$BODY" | jq -r '[.data[] | select(.pool_id | endswith("-int") | not)] | length')
[ "$LEAK" = "0" ] && ok "nenhum pool de contato na amostra" \
                  || bad "$LEAK linha(s) de pool não-interno — aprovação está sendo contada como wrap-up"

echo "══ D) totals fecham com a soma das categorias ══"
SUM_OK=$(echo "$BODY" | jq -r '
  (.totals.total // 0) as $t |
  ((.totals.submitted // 0) + (.totals.expired // 0) + (.totals.supervisor_closed // 0)) as $s |
  if $t == $s then "ok" else "\($t) != \($s)" end')
[ "$SUM_OK" = "ok" ] && ok "total = submetidos + vencidos + encerrados" \
                     || bad "totals inconsistentes: $SUM_OK"

echo "══ E) group_by=pool devolve o mesmo total ══"
# Mesmo universo, recorte diferente — divergência aqui significa que um dos eixos
# está filtrando algo que o outro não filtra.
T_AGENT=$(echo "$BODY" | jq -r '.totals.total // 0')
# Mesmo cuidado do passo 1: sem o `|| true` um curl que falhe mata o script sem
# imprimir, e o teste "some" em vez de reprovar.
T_POOL=$($CURL -H "Authorization: Bearer $TOK" \
  "$AN/reports/wrapup-summary?tenant_id=$TENANT&group_by=pool&from_dt=$FROM_DT" \
  2>/dev/null | jq -r '.totals.total // 0' || echo "erro")
[ "$T_AGENT" = "$T_POOL" ] && ok "agente=$T_AGENT e pool=$T_POOL batem" \
                           || bad "agente=$T_AGENT × pool=$T_POOL — os eixos não veem o mesmo universo"

echo
echo "══════════════════════════════════════"
echo "  passou: $pass    falhou: $fail"
[ "$fail" -eq 0 ] || exit 1
echo "  ✅ /reports/wrapup-summary em vigor (fatia 2)"
