#!/usr/bin/env bash
# Outbound Fase 4 — importador de arquivo em DUAS camadas.
#
# Camada A (POST /entries/batch): ingestão agnóstica de formato (resolve+valida+upsert+
#   relatório). Camada B (POST /import): adaptador CSV/xlsx que lê o column_map do mailing
#   e delega à Camada A. Prova:
#   1) mailing com column_map;
#   2) import CSV → relatório (total/added/rejected), linha inalcançável rejeitada c/ nº;
#   3) re-import do MESMO CSV → deduped (idempotência por dedup_key);
#   4) Camada A direta (batch) com linha inválida rejeitada.
#
# O resolve de âncora usa o Identity Resolver; as linhas válidas do teste têm contato
# (whatsapp/email), então passam independentemente do resultado do resolve.
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase4.sh
set -euo pipefail

TENANT="tenant_demo"
MA="http://localhost:3660"
ts=(-H "X-Tenant-ID: $TENANT")
jqid()  { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }
jqget() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1'))"; }
jqlen() { python3 -c "import sys,json;print(len(json.load(sys.stdin).get('$1',[])))"; }
jqrej0(){ python3 -c "import sys,json;print(json.load(sys.stdin)['rejected'][0]['row'])"; }

STAMP=$(date +%s)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "1) cria mailing com column_map ..."
CM='{"customer_id_column":"id_cliente","anchors":[{"kind":"phone","column":"telefone"},{"kind":"email","column":"email"}],"contacts":{"whatsapp":"telefone","email":"email"},"metadata_columns":["prioridade","operadora"]}'
M=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
      -d "{\"name\":\"fase4 $STAMP\",\"dedup_policy\":\"customer_context\",\"column_map\":$CM}" | jqid)
[ -n "$M" ] || { echo "FALHA: mailing não criado"; exit 1; }
echo "   mailing=$M"

echo "2) monta CSV (2 válidas c/ id nativo, 1 só-âncora c/ contato, 1 inalcançável) ..."
cat > "$TMP/audience.csv" <<'CSV'
id_cliente,telefone,email,prioridade,operadora
cus_a,5511999990001,a@x.com,3,claro
cus_b,5511999990002,b@x.com,1,vivo
,5511999990003,c@x.com,2,tim
,,,9,zzz
CSV

echo "3) IMPORT (Camada B) → relatório ..."
R=$(curl -s -X POST "$MA/v1/mailings/$M/import" "${ts[@]}" -F "file=@$TMP/audience.csv;type=text/csv")
echo "   $R"
[ "$(echo "$R" | jqget total)" = "4" ]  || { echo "FALHA: total esperado 4"; exit 1; }
[ "$(echo "$R" | jqget added)" = "3" ]  || { echo "FALHA: added esperado 3"; exit 1; }
[ "$(echo "$R" | jqlen rejected)" = "1" ] || { echo "FALHA: 1 linha rejeitada esperada"; exit 1; }
[ "$(echo "$R" | jqrej0)" = "5" ]       || { echo "FALHA: linha rejeitada deveria ser a 5"; exit 1; }
echo "   PASS: 3 inseridas, linha 5 (sem contato/id) rejeitada com nº de linha."

echo "4) RE-IMPORT mesmo CSV → deduped (idempotência) ..."
R2=$(curl -s -X POST "$MA/v1/mailings/$M/import" "${ts[@]}" -F "file=@$TMP/audience.csv;type=text/csv")
echo "   $R2"
[ "$(echo "$R2" | jqget added)" = "0" ]   || { echo "FALHA: re-import não deveria inserir"; exit 1; }
[ "$(echo "$R2" | jqget deduped)" = "3" ] || { echo "FALHA: deduped esperado 3"; exit 1; }
echo "   PASS: re-import não duplicou (deduped=3)."

echo "5) lista entries do mailing (3 active) ..."
L=$(curl -s "$MA/v1/mailings/$M/entries?status=active" "${ts[@]}")
[ "$(echo "$L" | jqget total)" = "3" ] || { echo "FALHA: esperado 3 entries active"; exit 1; }
echo "   PASS: 3 entries active."

echo "6) Camada A direta (POST /entries/batch) — 1 válida + 1 inválida ..."
BODY='{"rows":[{"customer_id":"cus_batch","contacts":{"whatsapp":"5511888880000"},"metadata":{"src":"batch"}},{"metadata":{"src":"noreach"}}],"resolve":false,"source":"sync:test"}'
B=$(curl -s -X POST "$MA/v1/mailings/$M/entries/batch" "${ts[@]}" -H 'content-type: application/json' -d "$BODY")
echo "   $B"
[ "$(echo "$B" | jqget total)" = "2" ]   || { echo "FALHA: batch total esperado 2"; exit 1; }
[ "$(echo "$B" | jqget added)" = "1" ]   || { echo "FALHA: batch added esperado 1"; exit 1; }
[ "$(echo "$B" | jqlen rejected)" = "1" ] || { echo "FALHA: batch deveria rejeitar 1"; exit 1; }
echo "   PASS: Camada A ingere linha válida e rejeita a inalcançável."

echo "GATE outbound Fase 4 — OK."
