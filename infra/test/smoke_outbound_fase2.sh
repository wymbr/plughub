#!/usr/bin/env bash
# Outbound Fase 2 — smoke do motor de GOVERNANÇA DE CONTATO (via API, isola o motor).
#
# Prova: (a) fadiga cross-chamada — uma contact_policy (cap 24h max 1) barra o 2º
# contato ao mesmo cliente na janela (contact_eligibility_check com claim); (b)
# supressão mailing-scoped — mailing_unsubscribe tira o cliente do drain.
# O motor é AGNÓSTICO: contact_eligibility_check substitui survey_eligibility_check.
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase2.sh
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
CUS="cus_f2_$STAMP"        # cliente novo por execução → smoke re-rodável

echo "1) Cria contact_policy (tenant, cap 24h max 1) ..."
POL=$(curl -s -X POST "$MA/v1/contact-policies" "${ts[@]}" -H 'content-type: application/json' -d '{
  "scope":"tenant",
  "frequency_caps":[{"window":"24h","max":1}]
}' | jqid)
[ -n "$POL" ] || { echo "FALHA: policy sem id (mailing-api Fase 2 no ar?)"; exit 1; }
echo "   policy = $POL"

echo "2) Eligibility check #1 (cliente novo) — esperado allowed:True, claimed:True ..."
R1=$(curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"customer_id\":\"$CUS\",\"channel\":\"webchat\",\"claim\":true
}")
echo "   $R1"
[ "$(echo "$R1" | jqget allowed)" = "True" ] || { echo "FALHA: 1º check deveria ser allowed"; exit 1; }
[ "$(echo "$R1" | jqget claimed)" = "True" ] || { echo "FALHA: 1º check deveria ter gravado o fato (claimed)"; exit 1; }

echo "3) Eligibility check #2 (mesmo cliente/janela) — esperado allowed:False, reason:frequency_cap ..."
R2=$(curl -s -X POST "$MA/v1/contact/eligibility" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"customer_id\":\"$CUS\",\"channel\":\"webchat\",\"claim\":true
}")
echo "   $R2"
[ "$(echo "$R2" | jqget allowed)" = "False" ] || { echo "FALHA: 2º check deveria ser negado (fadiga)"; exit 1; }
[ "$(echo "$R2" | jqget reason)" = "frequency_cap" ] || { echo "FALHA: reason esperado frequency_cap"; exit 1; }
echo "   PASS: a fadiga barrou o 2º contato na janela."

echo "4) Unsubscribe: mailing + 2 entries (A,B) + campaign; unsub A; drena → só B ..."
MAILING=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' -d '{"name":"F2 unsub","dedup_policy":"customer"}' | jqid)
for c in A B; do
  curl -s -X POST "$MA/v1/mailings/$MAILING/entries" "${ts[@]}" -H 'content-type: application/json' -d "{
    \"customer_id\":\"cus_f2_${STAMP}_$c\",\"contacts\":{\"webchat\":\"d-$c\"},\"metadata\":{\"k\":\"$c\"}
  }" >/dev/null
done
CAMP=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' -d "{\"name\":\"F2 unsub camp\",\"mailing_id\":\"$MAILING\",\"pool_id\":\"outbound_demo\"}" | jqid)

echo "   unsubscribe cliente A ..."
curl -s -X POST "$MA/v1/unsubscribe" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"customer_id\":\"cus_f2_${STAMP}_A\",\"mailing_id\":\"$MAILING\"
}"; echo

DRAINED=$(curl -s -X POST "$MA/v1/campaigns/$CAMP/drain" "${ts[@]}" -H 'content-type: application/json' -d '{}')
echo "   drain: $DRAINED"
ND=$(echo "$DRAINED" | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["drained"]))')
DCUS=$(echo "$DRAINED" | python3 -c 'import sys,json;d=json.load(sys.stdin)["drained"];print(d[0]["customer_id"] if d else "")')
[ "$ND" = "1" ] || { echo "FALHA: drain deveria trazer só 1 (B); veio $ND"; exit 1; }
case "$DCUS" in
  *_B) echo "   PASS: só o cliente B foi drenado (A suprimido pelo unsubscribe).";;
  *)   echo "FALHA: drenou '$DCUS', esperado *_B"; exit 1;;
esac

echo "5) Limpeza: remove a contact_policy de teste ..."
curl -s -X DELETE "$MA/v1/contact-policies/$POL" "${ts[@]}" >/dev/null && echo "   policy removida."
echo "GATE outbound Fase 2 — OK."
