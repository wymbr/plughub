#!/usr/bin/env bash
# Outbound Fase 5b — SURVEY outbound via substrato de campanha (e2e).
#
# Conecta o survey ao substrato de mailing/campaign: uma mailing de survey (metadata =
# {origin_session_id, grain, form_id}) é drenada por uma campanha+agenda; o survey worker
# cria o link web (survey_link_create) keyed ao ORIGIN do processo (não à sessão do
# dispatcher — por isso o veículo web, com origin explícito). A submissão do cliente na
# página grava session.signals no origin+grão. Closure: sinal + `contacted` (o token fica
# na delivery p/ drill; responded por-delivery é refinamento).
#
# Prova: N deliveries → contacted (token guardado) → submit em /survey/web/{token}/submit
# → signals_recorded>0 (o dado do survey é do CLIENTE, gravado pela mesma trilha do
# survey_record).
#
# Requer: skills novas (survey_dispatch/worker) publicadas; dialog-api (:3760), o form
# dialog_nps_buttons publicado (o smoke semeia), channel-gateway (:8010).
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase5b.sh
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
MA="http://localhost:3660"
SC="http://localhost:3650"
CGW="http://localhost:8010"
DIALOG="http://localhost:3760"
EVAL="http://localhost:3400"
ts=(-H "X-Tenant-ID: $TENANT")
jqid() { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
STAMP=$(date +%s)
N=2
FORM="dialog_nps_buttons"
# Segurança Fase B — o pool da SESSÃO PESQUISADA (origem), carimbado na metadata do
# mailing → repassado pelo dispatcher → survey_link_create → congelado no token →
# carimbado na resposta (survey_instance.pool_id) e no session.signals no submit.
POOL_ORIGIN="retencao_humano"

echo "0) garante o DialogForm '$FORM' publicado (idempotente) ..."
DIALOG_API="$DIALOG" TENANT="$TENANT" bash infra/test/seed_dialog_nps_buttons_form.sh >/dev/null 2>&1 \
  || echo "   (form provavelmente já existe — seguindo)"

echo "1) survey mailing + $N entries (metadata origin/grain/form; clientes distintos) ..."
M=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"name\":\"5b survey $STAMP\",\"dedup_policy\":\"customer\",\"metadata_contract\":\"survey_context_v1\"}" | jqid)
[ -n "$M" ] || { echo "FALHA: mailing sem id"; exit 1; }
for i in $(seq 1 $N); do
  CUS="cus_5b_${STAMP}_$i"
  ORIG="sess_proc_${STAMP}_$i"      # a sessão/raiz do processo pesquisado (chave do sinal)
  curl -s -X POST "$MA/v1/mailings/$M/entries" "${ts[@]}" -H 'content-type: application/json' -d "{
    \"customer_id\":\"$CUS\",\"contacts\":{\"webchat\":\"w$i\"},
    \"metadata\":{\"channel\":\"webchat\",\"origin_session_id\":\"$ORIG\",\"grain\":\"journey\",
                  \"form_id\":\"$FORM\",\"customer_key\":\"$CUS\",\"origin_pool\":\"$POOL_ORIGIN\"}
  }" >/dev/null
done

echo "2) campanha (pool = outbound_survey_dispatch) + agenda fire ..."
C=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"name\":\"5b camp\",\"mailing_id\":\"$M\",\"pool_id\":\"outbound_survey_dispatch\",\"batch_size\":50}" | jqid)
[ -n "$C" ] || { echo "FALHA: campanha sem id"; exit 1; }
AG=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"5b\",\"target_pool_id\":\"outbound_survey_dispatch\",\"payload\":{\"campaign_id\":\"$C\"},
  \"validity\":{\"starts_at\":\"2026-07-21T00:00:00Z\"},
  \"schedule\":{\"mode\":\"once\",\"fire_at\":\"2030-01-01T09:00:00Z\"}
}" | jqid)
[ -n "$AG" ] || { echo "FALHA: agenda sem id"; exit 1; }
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" >/dev/null

echo "3) poll deliveries → esperado $N contacted (survey workers criaram o link) ..."
count_contacted() {
  curl -s "$MA/v1/campaigns/$C/deliveries" "${ts[@]}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin)['deliveries'];print(sum(1 for x in d if x['result']=='contacted'))"
}
CONTACTED=0
for _ in $(seq 1 20); do
  sleep 3
  CONTACTED=$(count_contacted)
  [ "$CONTACTED" = "$N" ] && break
done
echo "   contacted=$CONTACTED (esperado $N)"
[ "$CONTACTED" = "$N" ] || { echo "FALHA: os $N survey workers deveriam criar o link e marcar contacted (só $CONTACTED)"; exit 1; }

echo "4) tokens guardados nas deliveries → submit em /survey/web/{token}/submit (NPS=9) ..."
TOKENS=$(curl -s "$MA/v1/campaigns/$C/deliveries" "${ts[@]}" \
  | python3 -c "import sys,json;d=json.load(sys.stdin)['deliveries'];print('\n'.join(x['session_id'] for x in d if x['result']=='contacted' and x.get('session_id')))")
[ -n "$TOKENS" ] || { echo "FALHA: nenhuma delivery guardou o token do survey"; exit 1; }
NSUB=0
for T in $TOKENS; do
  R=$(curl -s -X POST "$CGW/v1/survey/web/$T/submit" -H 'content-type: application/json' -d '{"answers":{"nps":"9"}}')
  SR=$(echo "$R" | python3 -c "import sys,json;r=json.load(sys.stdin);print(r.get('signals_recorded',0) if r.get('ok') else -1)")
  echo "   token=${T:0:10}… signals_recorded=$SR"
  [ "$SR" -ge 1 ] && NSUB=$((NSUB+1))
done
[ "$NSUB" = "$N" ] || { echo "FALHA: esperado $N submits com signal gravado (só $NSUB)"; exit 1; }
echo "   PASS: $N surveys criados pelo substrato de campanha e $N sinais gravados (session.signals) no origin/grão."

echo "5) Segurança Fase B: as respostas nasceram com pool='$POOL_ORIGIN' (não vazio) ..."
count_scoped() {  # $1 = pool filtrado; conta as respostas DESTE run (origin sess_proc_$STAMP_*)
  curl -s "$EVAL/v1/evaluation/survey/responses?tenant_id=$TENANT&pool_ids=$1&limit=200" "${ts[@]}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin).get('data',[]);print(sum(1 for x in d if x.get('origin_session_id','').startswith('sess_proc_${STAMP}_')))"
}
SEEN=0
for _ in $(seq 1 10); do  # o persist é síncrono no submit, mas dá folga p/ o commit
  sleep 1
  SEEN=$(count_scoped "$POOL_ORIGIN")
  [ "$SEEN" = "$N" ] && break
done
echo "   respostas do run escopadas a '$POOL_ORIGIN': $SEEN (esperado $N)"
[ "$SEEN" = "$N" ] || { echo "FALHA Fase B: esperado $N respostas com pool='$POOL_ORIGIN' (pool não fluiu na escrita)"; exit 1; }
# Controle negativo: filtrando por outro pool do domínio, as nossas NÃO aparecem.
LEAK=$(count_scoped "aprovacao_deploy")
[ "$LEAK" = "0" ] || { echo "FALHA Fase B: $LEAK respostas vazaram p/ pool alheio (scoping quebrado)"; exit 1; }
echo "   PASS: pool da origem carimbado na resposta; controle negativo (pool alheio) = 0."

echo "6) Limpeza ..."
curl -s -X POST "$SC/v1/agendas/$AG/cancel" "${ts[@]}" >/dev/null || true
echo "GATE outbound Fase 5b — OK."
