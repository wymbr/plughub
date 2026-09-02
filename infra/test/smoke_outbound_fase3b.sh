#!/usr/bin/env bash
# Outbound Fase 3b — portão de OPT-OUT GLOBAL (do_not_contact) via API.
#
# Prova: (a) mailing_unsubscribe scope=global grava `do_not_contact` no cadastro do
# cliente (via Identity Resolver); (b) o contact_eligibility_check veta `opt_out`
# (maior precedência, sem claim); (c) um cliente SEM opt-out passa na MESMA campanha
# (testemunha — sem ela, um bug que vetasse todo mundo ficaria verde); (d) uma campanha
# `transactional` FURA o opt-out (notificação obrigatória); (e) o opt-out por CANAL veta
# só aquele canal. O opt-out vive no cadastro, não no outbound.
#
# NÃO exercitado (declarado de propósito): a degradação graciosa do portão — identity
# fora do ar / cliente ausente → degrada para NÃO opted-out (allow, barulhento no log).
# Esse ramo exige derrubar o channel-gateway e não cabe num smoke de API.
#
# Requer o Identity Resolver habilitado (PLUGHUB_IDENTITY_RESOLVER_ENABLED=true) e a
# mailing-api com identity_api_url → channel-gateway.
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase3b.sh
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
ts=(-H "X-Tenant-ID: $TENANT")
jqid()  { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
jqget() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))"; }
STAMP=$(date +%s)
CUS="cus_3b_$STAMP"        # opta por sair (all)
CTRL="cus_3b_ctrl_$STAMP"  # testemunha: NUNCA opta por sair
CHAN="cus_3b_chan_$STAMP"  # opta por sair de UM canal

# cria mailing+entry(CUS)+campanha → ecoa campaign_id ; $1 = transactional (true|false)
mk_campaign() {
  local m
  m=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
        -d '{"name":"3b","dedup_policy":"customer"}' | jqid)
  curl -s -X POST "$MA/v1/mailings/$m/entries" "${ts[@]}" -H 'content-type: application/json' \
       -d "{\"customer_id\":\"$CUS\",\"metadata\":{\"channel\":\"webchat\"}}" >/dev/null
  curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
    -d "{\"name\":\"3b camp\",\"mailing_id\":\"$m\",\"pool_id\":\"outbound_demo\",\"transactional\":$1}" | jqid
}

# eligibility → ecoa o corpo ; $1=customer $2=channel $3=campaign_id $4=claim(true|false)
elig() {
  curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' \
    -d "{\"customer_id\":\"$1\",\"channel\":\"$2\",\"campaign_id\":\"$3\",\"claim\":$4}"
}

echo "1) unsubscribe GLOBAL → grava do_not_contact.all no cadastro (via identity) ..."
U=$(curl -s -X POST "$MA/v1/unsubscribe" "${ts[@]}" -H 'content-type: application/json' \
     -d "{\"customer_id\":\"$CUS\",\"scope\":\"global\"}")
echo "   $U"
[ "$(echo "$U" | jqget do_not_contact_set)" = "True" ] || { echo "FALHA: do_not_contact não gravado (Identity Resolver habilitado?)"; exit 1; }

echo "2) eligibility em campanha NÃO-transactional → esperado allowed:False, reason:opt_out, claimed:False ..."
CG=$(mk_campaign false)
R=$(elig "$CUS" webchat "$CG" true)
echo "   $R"
[ "$(echo "$R" | jqget allowed)" = "False" ]   || { echo "FALHA: opt-out deveria vetar"; exit 1; }
[ "$(echo "$R" | jqget reason)"  = "opt_out" ] || { echo "FALHA: reason esperado opt_out"; exit 1; }
[ "$(echo "$R" | jqget claimed)" = "False" ]   || { echo "FALHA: não deveria claimar sob opt-out"; exit 1; }
echo "   PASS: opt-out global vetou o contato (sem claim)."

echo "3) TESTEMUNHA — cliente SEM opt-out, MESMA campanha, MESMO canal → esperado allowed:True ..."
# Único valor que muda em relação ao passo 2 é o cadastro do cliente. Sem este passo o
# gate não distingue "vetou quem optou por sair" de "veta todo mundo".
RC=$(elig "$CTRL" webchat "$CG" false)
echo "   $RC"
[ "$(echo "$RC" | jqget allowed)" = "True" ] || { echo "FALHA: cliente sem opt-out foi vetado — o portão não discrimina (reason: $(echo "$RC" | jqget reason))"; exit 1; }
[ "$(echo "$RC" | jqget reason)"  = "None" ] || { echo "FALHA: testemunha negada por outro portão"; exit 1; }
echo "   PASS: o veto é do CLIENTE que optou por sair, não da campanha."

echo "4) eligibility em campanha TRANSACTIONAL → esperado allowed:True (fura o opt-out) ..."
CGT=$(mk_campaign true)
RT=$(elig "$CUS" webchat "$CGT" true)
echo "   $RT"
[ "$(echo "$RT" | jqget allowed)" = "True" ] || { echo "FALHA: campanha transactional deveria furar o opt-out"; exit 1; }
[ "$(echo "$RT" | jqget claimed)" = "True" ] || { echo "FALHA: transactional permitida deveria claimar"; exit 1; }
echo "   PASS: campanha transactional fura o opt-out (notificação obrigatória)."

echo "5) opt-out POR CANAL → veta o canal optado e SÓ ele ..."
UC=$(curl -s -X POST "$MA/v1/unsubscribe" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"customer_id\":\"$CHAN\",\"scope\":\"global\",\"channel\":\"whatsapp\"}")
echo "   $UC"
[ "$(echo "$UC" | jqget do_not_contact_set)" = "True" ] || { echo "FALHA: do_not_contact por canal não gravado"; exit 1; }
RW=$(elig "$CHAN" whatsapp "$CG" false)
echo "   whatsapp: $RW"
[ "$(echo "$RW" | jqget allowed)" = "False" ]   || { echo "FALHA: canal optado deveria ser vetado"; exit 1; }
[ "$(echo "$RW" | jqget reason)"  = "opt_out" ] || { echo "FALHA: reason esperado opt_out no canal optado"; exit 1; }
RO=$(elig "$CHAN" webchat "$CG" false)
echo "   webchat:  $RO"
[ "$(echo "$RO" | jqget allowed)" = "True" ] || { echo "FALHA: opt-out de UM canal vazou para os outros"; exit 1; }
echo "   PASS: o opt-out por canal é escopado ao canal."

echo "GATE outbound Fase 3b — OK (opt-out all + testemunha + transactional + por canal)."
echo "     NÃO exercitado: degradação com o identity fora do ar (allow barulhento)."
