#!/usr/bin/env bash
# Outbound Fase 2b — fadiga CROSS-CAMPANHA no fluxo REAL (skill + gate).
#
# Prova ponta-a-ponta: com uma contact_policy (cap 24h max 1), a campanha A contata o
# cliente X (skill → contact_eligibility_check(claim) grava o contact_log → contacted);
# a campanha B, mesmo cliente X, é BARRADA pelo gate no skill → skipped_ineligible.
# Diferente do smoke da Fase 2 (que exercita a API direto), aqui o gate roda DENTRO do
# skill_outbound_demo_v1 (loop → verificar_elegibilidade → decidir → registrar_*).
#
# Requer: a cadeia rebuildada (mcp-server-plughub tem contact_eligibility_check) E o skill
# atualizado RE-SNAPSHOTADO no slot current do pool (pool com PoolSkillSlot roda o snapshot
# do slot, não o skill.flow) — republicar skill.flow/reconcile NÃO basta:
#   TOKEN=$AGENT_REGISTRY_SERVICE_TOKEN
#   curl -X PUT  .../v1/pools/outbound_demo/slots/next -H "x-service-token: $TOKEN" \
#        -d '{"skill_id":"skill_outbound_demo_v1","config_json":{"max_concurrent_sessions":3}}'
#   curl -X POST .../v1/pools/outbound_demo/promote   -H "x-service-token: $TOKEN"
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase2b.sh
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
SC="http://localhost:3650"
ts=(-H "X-Tenant-ID: $TENANT")
jqid() { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }

STAMP=$(date +%s)
CUS="cus_2b_$STAMP"        # MESMO cliente nas duas campanhas → prova fadiga cross-campanha

# cria mailing + 1 entry (CUS, canal webchat) + campaign → ecoa o campaign_id
mk_campaign() {  # $1 = sufixo
  local m c
  m=$(curl -s -X POST "$MA/v1/mailings" "${ts[@]}" -H 'content-type: application/json' \
        -d "{\"name\":\"2b-$1\",\"dedup_policy\":\"customer\"}" | jqid)
  curl -s -X POST "$MA/v1/mailings/$m/entries" "${ts[@]}" -H 'content-type: application/json' -d "{
    \"customer_id\":\"$CUS\",\"contacts\":{\"webchat\":\"w\"},
    \"metadata\":{\"channel\":\"webchat\",\"mensagem\":\"oi\"}
  }" >/dev/null
  curl -s -X POST "$MA/v1/campaigns" "${ts[@]}" -H 'content-type: application/json' \
    -d "{\"name\":\"2b-camp-$1\",\"mailing_id\":\"$m\",\"pool_id\":\"outbound_demo\"}" | jqid
}

# cria agenda (fire futuro — só /fire dispara) + dispara → ecoa o agenda_id
fire_campaign() {  # $1 = campaign_id
  local ag
  ag=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
    \"name\":\"2b\",\"target_pool_id\":\"outbound_demo\",\"payload\":{\"campaign_id\":\"$1\"},
    \"validity\":{\"starts_at\":\"2026-07-21T00:00:00Z\"},
    \"schedule\":{\"mode\":\"once\",\"fire_at\":\"2030-01-01T09:00:00Z\"}
  }" | jqid)
  curl -s -X POST "$SC/v1/agendas/$ag/fire" "${ts[@]}" >/dev/null
  echo "$ag"
}

# poll: aguarda a 1ª entrega sair de claimed → ecoa o result final
poll_result() {  # $1 = campaign_id
  local r=""
  for _ in $(seq 1 15); do
    sleep 3
    r=$(curl -s "$MA/v1/campaigns/$1/deliveries" "${ts[@]}" \
        | python3 -c 'import sys,json;d=json.load(sys.stdin)["deliveries"];print(d[0]["result"] if d else "none")')
    [ "$r" != "none" ] && [ "$r" != "claimed" ] && break
  done
  echo "$r"
}

echo "1) contact_policy (tenant, cap 24h max 1) ..."
POL=$(curl -s -X POST "$MA/v1/contact-policies" "${ts[@]}" -H 'content-type: application/json' \
      -d '{"scope":"tenant","frequency_caps":[{"window":"24h","max":1}]}' | jqid)
[ -n "$POL" ] || { echo "FALHA: policy sem id"; exit 1; }

echo "2) Campanha A + fire → esperado contacted (X contatado; grava contact_log) ..."
CA=$(mk_campaign A); AGA=$(fire_campaign "$CA")
RA=$(poll_result "$CA")
echo "   A result=$RA"
[ "$RA" = "contacted" ] || { echo "FALHA: A deveria contatar (result=$RA)"; exit 1; }

echo "3) Campanha B (MESMO cliente) + fire → esperado skipped_ineligible (fadiga) ..."
CB=$(mk_campaign B); AGB=$(fire_campaign "$CB")
RB=$(poll_result "$CB")
echo "   B result=$RB"
[ "$RB" = "skipped_ineligible" ] || { echo "FALHA: B deveria ser skipped_ineligible (result=$RB)"; exit 1; }
echo "   PASS: a fadiga cross-campanha barrou o 2º contato NO FLUXO REAL (gate no skill)."

echo "4) Limpeza ..."
curl -s -X DELETE "$MA/v1/contact-policies/$POL" "${ts[@]}" >/dev/null || true
curl -s -X POST "$SC/v1/agendas/$AGA/cancel" "${ts[@]}" >/dev/null || true
curl -s -X POST "$SC/v1/agendas/$AGB/cancel" "${ts[@]}" >/dev/null || true
echo "GATE outbound Fase 2b — OK."
