#!/usr/bin/env bash
# Survey response store (S8/S9) — ADR adr-survey-response-store.
#
# Prova as duas garantias do arco:
#   (1) CAPTURA DE VERBATIM: o submit web para de descartar texto aberto — uma pergunta
#       sem `capture.metric` vira verbatim no store operacional (PG), NÃO no session.signals.
#   (2) PERSIST-FIRST + IDEMPOTÊNCIA: a resposta é gravada no PG antes do sinal; o endpoint
#       é idempotente por (tenant_id, idempotency_key) — replay = 200 created:false, sem duplicar.
#
# Toca: dialog-api (:3760), channel-gateway (:8010), evaluation-api (:3400), postgres.
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_survey_response_store.sh
# ⚠️ UTF-8 explicito na saida do python — e esta linha E o conserto, nao um paliativo.
#
# Nesta bancada o `stdout` do python usa cp1252. Um `print` com acento sai em bytes
# cp1252, e todo consumidor a jusante — `grep` com padrao UTF-8, outro python, o proprio
# shell — deixa de casar sobre um texto que ESTA la. Medido com A/B em 2026-09-02
# (CNS-18): sem a env, `grep -c 'meta NAO escrito'` devolve 0 pelos DOIS caminhos
# (arquivo e variavel); com a env, devolve 1 pelos dois.
#
# ⚠️ O diagnostico levou TRES tentativas e as duas primeiras foram publicadas erradas:
# `sys.stdin` (CNS-12) e a variavel de shell (CNS-17). Nao era o fluxo de ENTRADA nem o
# transporte — era a SAIDA. Variavel e arquivo sao ambos inocentes, e `docker logs`
# tambem: medido, sobrevive intacto pelos dois. Se voce for mexer nisto, o teste que
# separa as hipoteses e o A/B na propria env, com UMA variavel por vez.
export PYTHONIOENCODING=utf-8

set -euo pipefail

TENANT="tenant_demo"
DIALOG="http://localhost:3760"
DIALOG_ADMIN_TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"   # portao de escrita (sistema)
CGW="http://localhost:8010"
EVAL="http://localhost:3400"
COMPOSE="docker compose -f docker-compose.demo.yml"
ts=(-H "X-Tenant-ID: $TENANT")
STAMP=$(date +%s)
FORM="dialog_survey_store_test"
ORIG="sess_store_${STAMP}"

pg() { $COMPOSE exec -T postgres psql -U plughub -d plughub_demo -tA "$@"; }

echo "1) DialogForm '$FORM' (NPS com metric + pergunta de texto 'motivo' SEM metric) publicado ..."
read -r -d '' BODY <<JSON || true
{
  "form_id": "$FORM",
  "name": "Survey response store test",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["survey","test"],
  "nodes": [
    { "id":"nps","kind":"question","prompt":{"pt-BR":"NPS 0-10?"},
      "interaction":"text","output_key":"nps","capture":{"metric":"nps"} },
    { "id":"motivo","kind":"question","prompt":{"pt-BR":"Por quê?"},
      "interaction":"text","output_key":"motivo" }
  ]
}
JSON
curl -fsS -X POST -H "X-Admin-Token: ${DIALOG_ADMIN_TOKEN}" "$DIALOG/v1/dialog/forms" "${ts[@]}" -H 'content-type: application/json' -d "$BODY" >/dev/null 2>&1 \
  || echo "   (form provavelmente já existe — seguindo)"
curl -fsS -X POST -H "X-Admin-Token: ${DIALOG_ADMIN_TOKEN}" "$DIALOG/v1/dialog/forms/$FORM/publish" "${ts[@]}" >/dev/null

echo "2) cria token de survey web (grain=session) ..."
CREATE=$(curl -s -w $'\n%{http_code}' -X POST "$CGW/v1/survey/web/create" -H 'content-type: application/json' -d "{
  \"tenant_id\":\"$TENANT\",\"form_id\":\"$FORM\",\"origin_session_id\":\"$ORIG\",\"grain\":\"session\"
}")
CODE_C=$(printf '%s' "$CREATE" | tail -n1)
BODY_C=$(printf '%s' "$CREATE" | sed '$d')
echo "   http=$CODE_C body=$BODY_C"
TOKEN=$(printf '%s' "$BODY_C" | python3 -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
[ -n "$TOKEN" ] || { echo "FALHA: sem token — channel-gateway no ar? ('docker compose -f docker-compose.demo.yml ps channel-gateway' + 'logs --tail=50 channel-gateway')"; exit 1; }
echo "   token=${TOKEN:0:12}…"

echo "3) submit com nps=9 (signal) + motivo (verbatim) ..."
R=$(curl -s -X POST "$CGW/v1/survey/web/$TOKEN/submit" -H 'content-type: application/json' \
     -d '{"answers":{"nps":"9","motivo":"atendimento rápido"}}')
echo "   $R"
SR=$(echo "$R" | python3 -c "import sys,json;r=json.load(sys.stdin);print(r.get('signals_recorded',-1))")
VR=$(echo "$R" | python3 -c "import sys,json;r=json.load(sys.stdin);print(r.get('verbatims_recorded',-1))")
[ "$SR" -ge 1 ] || { echo "FALHA: signals_recorded=$SR"; exit 1; }
[ "$VR" -ge 1 ] || { echo "FALHA: verbatims_recorded=$VR (verbatim ainda descartado?)"; exit 1; }

echo "4) PG: survey_response gravado com o verbatim (LGPD, só no store operacional) ..."
VTXT=$(pg -c "SELECT r.verbatims::text FROM survey.survey_response r
              JOIN survey.survey_instance i ON i.instance_id = r.instance_id
              WHERE i.tenant_id='$TENANT' AND i.idempotency_key='$TOKEN';")
echo "   verbatims=$VTXT"
echo "$VTXT" | grep -q "atendimento" || { echo "FALHA: verbatim não persistido/sem o texto"; exit 1; }

echo "5) idempotência do endpoint: POST direto 2x com a MESMA idempotency_key ..."
IK="smoke_ik_${STAMP}"
# _require_service da evaluation-api — o demo seta o token; o header é obrigatório.
SVCTOK="${PLUGHUB_EVALUATION_SERVICE_TOKEN:-changeme_eval_service_token_demo}"
svc=(-H "X-Service-Token: $SVCTOK" -H 'content-type: application/json')
mkbody() { echo "{\"tenant_id\":\"$TENANT\",\"idempotency_key\":\"$IK\",\"grain\":\"session\",\"origin_session_id\":\"$ORIG\",\"signals\":[{\"metric\":\"csat\",\"value\":4}],\"open_text\":\"ok\"}"; }
P1=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$EVAL/v1/evaluation/survey/responses" "${svc[@]}" -d "$(mkbody)")
P2=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$EVAL/v1/evaluation/survey/responses" "${svc[@]}" -d "$(mkbody)")
echo "   1º=$P1 (esperado 201)  2º=$P2 (esperado 200)"
[ "$P1" = "201" ] || { echo "FALHA: 1º POST != 201"; exit 1; }
[ "$P2" = "200" ] || { echo "FALHA: 2º POST != 200 (replay idempotente)"; exit 1; }
NROWS=$(pg -c "SELECT count(*) FROM survey.survey_instance WHERE tenant_id='$TENANT' AND idempotency_key='$IK';")
[ "$NROWS" = "1" ] || { echo "FALHA: idempotência criou $NROWS instâncias (esperado 1)"; exit 1; }

echo "GATE survey response store — OK (verbatim capturado + persist-first + idempotência)."
