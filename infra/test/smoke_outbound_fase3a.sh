#!/usr/bin/env bash
# Outbound Fase 3a — portão de JANELA DE CONTATO (calendar) via API.
#
# Prova: uma campanha com contact_calendar_id apontando um calendário FECHADO faz o
# contact_eligibility_check negar `outside_window` (sem claim). Sem calendar_id, o
# portão não se aplica (regressão). O outbound nunca remodela "quando" — pergunta ao
# calendar-api (mesma autoridade do scheduler).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase3a.sh
# ⚠️ UTF-8 explicito na SAIDA do python. No Windows o `stdout` decodifica com cp1252 e
# um `print` de texto acentuado estoura `UnicodeEncodeError`, derrubando o probe por
# motivo de bancada — ou, pior, mutila o texto que o shell vai comparar.
#
# ⚠️ E o que esta linha NAO conserta, porque o diagnostico foi REFEITO em 2026-09-02:
# a corrupcao que motivou a CNS-12 nao vinha do `sys.stdin` (medido: `curl | python3 ->
# arquivo` preserva `Almoco`/`Reuniao` intactos). Vinha da VARIAVEL DE SHELL — passar
# JSON nao-ASCII por `VAR=$(…)` o mutila, medido 321 bytes contra 325. Contra isso a
# unica defesa e nao passar por variavel: producao e consumo por ARQUIVO.
export PYTHONIOENCODING=utf-8

set -euo pipefail

TENANT="tenant_demo"
MA="http://localhost:3660"
CAL="http://localhost:3700"
ts=(-H "X-Tenant-ID: $TENANT")
jqid()  { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
jqget() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))"; }
STAMP=$(date +%s)

echo "1) Cria calendário SEMPRE FECHADO (weekly_schedule vazio) ..."
CAL_ADMIN_TOKEN="${CAL_ADMIN_TOKEN:-demo_calendar_admin_token}"   # portao de escrita (sistema)
CALID=$(curl -s -X POST "$CAL/v1/calendars" -H 'content-type: application/json' -H "X-Admin-Token: $CAL_ADMIN_TOKEN" -d "{
  \"organization_id\":\"org-default\",\"tenant_id\":\"$TENANT\",\"scope\":\"tenant\",
  \"name\":\"F3a closed $STAMP\",\"always_open\":false,\"weekly_schedule\":[]
}" | jqid)
[ -n "$CALID" ] || { echo "FALHA: calendário sem id (calendar-api :3700 no ar?)"; exit 1; }
echo "   calendar = $CALID"
echo "   sanity is-open-calendar (esperado status=closed):"
curl -s "$CAL/v1/engine/is-open-calendar?calendar_id=$CALID" | python3 -m json.tool

echo "2) mailing + entry + campanha COM contact_calendar_id ..."
M=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
      -d '{"name":"F3a","dedup_policy":"customer"}' | jqid)
curl -s -X POST "$MA/v1/mailings/$M/entries" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"customer_id\":\"cus_3a_$STAMP\",\"metadata\":{\"channel\":\"webchat\"}}" >/dev/null
CG=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"name\":\"F3a camp\",\"mailing_id\":\"$M\",\"pool_id\":\"outbound_demo\",\"contact_calendar_id\":\"$CALID\"}" | jqid)
[ -n "$CG" ] || { echo "FALHA: campanha sem id"; exit 1; }

echo "3) eligibility com janela FECHADA → esperado allowed:False, reason:outside_window, claimed:False ..."
R=$(curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"customer_id\":\"cus_3a_$STAMP\",\"channel\":\"webchat\",\"campaign_id\":\"$CG\",\"claim\":true}")
echo "   $R"
[ "$(echo "$R" | jqget allowed)" = "False" ]          || { echo "FALHA: deveria ser negado (janela fechada)"; exit 1; }
[ "$(echo "$R" | jqget reason)"  = "outside_window" ] || { echo "FALHA: reason esperado outside_window"; exit 1; }
[ "$(echo "$R" | jqget claimed)" = "False" ]          || { echo "FALHA: não deveria claimar fora da janela"; exit 1; }
echo "   PASS: janela fechada barrou o contato (sem claim)."

echo "4) regressão: campanha SEM calendar → o portão de janela NÃO se aplica ..."
CG2=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
       -d "{\"name\":\"F3a nocal\",\"mailing_id\":\"$M\",\"pool_id\":\"outbound_demo\"}" | jqid)
R2=$(curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"customer_id\":\"cus_3a_nocal_$STAMP\",\"channel\":\"webchat\",\"campaign_id\":\"$CG2\",\"claim\":true}")
echo "   $R2"
[ "$(echo "$R2" | jqget allowed)" = "True" ] || { echo "FALHA: sem calendar deveria permitir"; exit 1; }
echo "   PASS: sem calendar, a janela não bloqueia."

echo "5) Limpeza ..."
curl -s -X DELETE "$CAL/v1/calendars/$CALID" -H "X-Admin-Token: $CAL_ADMIN_TOKEN" >/dev/null || true
echo "GATE outbound Fase 3a — OK."
