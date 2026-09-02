#!/usr/bin/env bash
# S8 — navegador de respostas: leitura via GET /v1/evaluation/survey/responses.
#
# Semeia respostas pelo POST (store) e lê pelo GET: total, filtro por metric, filtro
# por grain, e verbatim presente na leitura. Escopa tudo num pool_id do smoke p/ contagem
# determinística. Gate do GET = _require_any_evaluation (degrada sem JWT no demo).
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_survey_response_read.sh
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
EVAL="http://localhost:3400"
STAMP=$(date +%s)
POOL="pool_s8_${STAMP}"
SVCTOK="${PLUGHUB_EVALUATION_SERVICE_TOKEN:-changeme_eval_service_token_demo}"
svc=(-H "X-Service-Token: $SVCTOK" -H 'content-type: application/json')

post() { # $1 metric  $2 grain  $3 ik  $4 open_text
  curl -s -o /dev/null -w "%{http_code}" -X POST "$EVAL/v1/evaluation/survey/responses" "${svc[@]}" -d "{
    \"tenant_id\":\"$TENANT\",\"idempotency_key\":\"$3\",\"grain\":\"$2\",
    \"pool_id\":\"$POOL\",\"origin_session_id\":\"orig_$3\",
    \"signals\":[{\"metric\":\"$1\",\"value\":9}],\"open_text\":\"$4\"
  }"
}
get() { curl -s "$EVAL/v1/evaluation/survey/responses?tenant_id=$TENANT&pool_id=$POOL&$1"; }
total_of() { python3 -c "import sys,json;print(json.load(sys.stdin)['total'])"; }

echo "1) semeia 3 respostas (nps/journey, csat/session, ces/session) ..."
C1=$(post nps  journey "s8_${STAMP}_1" "recomendo muito")
C2=$(post csat session "s8_${STAMP}_2" "atendimento ok")
C3=$(post ces  session "s8_${STAMP}_3" "")
echo "   http: $C1 $C2 $C3"
[ "$C1" = "201" ] && [ "$C2" = "201" ] && [ "$C3" = "201" ] || { echo "FALHA: seed (esperado 201×3)"; exit 1; }

echo "2) GET lista (pool do smoke) → esperado 3 ..."
N=$(get "" | total_of)
echo "   total=$N"
[ "$N" = "3" ] || { echo "FALHA: esperado 3, veio $N"; exit 1; }

echo "3) filtro metric=nps → 1 ..."
NN=$(get "metric=nps" | total_of)
echo "   nps=$NN"
[ "$NN" = "1" ] || { echo "FALHA: filtro metric (veio $NN)"; exit 1; }

echo "4) filtro grain=session → 2 ..."
NG=$(get "grain=session" | total_of)
echo "   session=$NG"
[ "$NG" = "2" ] || { echo "FALHA: filtro grain (veio $NG)"; exit 1; }

echo "5) verbatim presente na leitura (resposta nps) ..."
V=$(get "metric=nps" | python3 -c "import sys,json;d=json.load(sys.stdin)['data'][0];print(d.get('open_text') or '')")
echo "   open_text=$V"
echo "$V" | grep -q "recomendo" || { echo "FALHA: verbatim ausente na leitura"; exit 1; }

echo "6) filtro de data como a UI (from=hoje-30, to=HOJE) → ainda 3 (to_dt inclusivo) ..."
TODAY=$(date -u +%F); FROM=$(date -u -d '29 days ago' +%F 2>/dev/null || date -u -v-29d +%F)
ND=$(get "from_dt=$FROM&to_dt=$TODAY" | total_of)
echo "   com datas=$ND"
[ "$ND" = "3" ] || { echo "FALHA: to_dt não inclui o dia de hoje (veio $ND) — respostas de hoje sumiriam na tela"; exit 1; }

echo "GATE S8 leitura — OK (lista + filtros metric/grain/data + verbatim)."
