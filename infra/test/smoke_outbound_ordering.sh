#!/usr/bin/env bash
# Outbound — ordenação declarativa do drain (campaign.ordering) via API.
#
# Prova: (a) uma campanha com ordering [{path:priority,dir:desc,type:number}] drena na
# ordem de prioridade (5,3,1), não FIFO; (b) sem ordering, o drain é FIFO por added_at
# (1,5,3). A ordenação lê um path do metadata OPACO que a campanha nomeou (opt-in).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_ordering.sh
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
ts=(-H "X-Tenant-ID: $TENANT")
jqid()  { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
seq_of() { python3 -c 'import sys,json;d=json.load(sys.stdin)["drained"];print(",".join(str(x["metadata"]["priority"]) for x in d))'; }
STAMP=$(date +%s)

echo "1) mailing + 3 entries com metadata.priority na ordem de inserção 1, 5, 3 ..."
M=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
      -d '{"name":"ordering","dedup_policy":"none"}' | jqid)
[ -n "$M" ] || { echo "FALHA: mailing sem id"; exit 1; }
for p in 1 5 3; do
  curl -s -X POST "$MA/v1/mailings/$M/entries" "${ts[@]}" -H 'content-type: application/json' \
       -d "{\"customer_id\":\"cus_ord_${STAMP}_$p\",\"metadata\":{\"priority\":$p}}" >/dev/null
  sleep 0.2   # garante added_at distinto (desempate FIFO determinístico)
done

echo "2) campanha COM ordering (priority desc, number) → drain esperado 5,3,1 ..."
CA=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"ord-desc\",\"mailing_id\":\"$M\",\"pool_id\":\"outbound_demo\",
  \"ordering\":[{\"path\":\"priority\",\"dir\":\"desc\",\"type\":\"number\"}]
}" | jqid)
SEQ=$(curl -s -X POST "$MA/v1/campaigns/$CA/drain" "${ts[@]}" -H 'content-type: application/json' -d '{}' | seq_of)
echo "   ordem drenada = $SEQ"
[ "$SEQ" = "5,3,1" ] || { echo "FALHA: esperado 5,3,1 (priority desc)"; exit 1; }
echo "   PASS: ordenação declarativa por metadata.priority (desc)."

echo "3) campanha SEM ordering → drain esperado FIFO por added_at (1,5,3) ..."
CB=$(curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"name\":\"ord-fifo\",\"mailing_id\":\"$M\",\"pool_id\":\"outbound_demo\"}" | jqid)
SEQ2=$(curl -s -X POST "$MA/v1/campaigns/$CB/drain" "${ts[@]}" -H 'content-type: application/json' -d '{}' | seq_of)
echo "   ordem drenada = $SEQ2"
[ "$SEQ2" = "1,5,3" ] || { echo "FALHA: esperado FIFO 1,5,3"; exit 1; }
echo "   PASS: default FIFO por added_at."

echo "GATE outbound ordering — OK."
