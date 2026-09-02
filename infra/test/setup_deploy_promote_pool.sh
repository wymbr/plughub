#!/usr/bin/env bash
# Scheduler Fase 2 — setup ONE-TIME do pool do corpo do job (deploy_promote_ia).
#
# Porquê: o bridge só provisiona INSTÂNCIA para pools com slot `current`
# (`deployed_skill_id` vem do PoolSkillSlot.current — pools.ts). O RegistrySyncer
# tentou semear o slot mas tomou 422: o demo já declara Σ=312 > C=310 (o tenant
# cresceu além da capacidade contratada — ver comentário em tenant_demo.yaml). Sem
# slot `current`, o webhook cria sessão mas ela fica na fila (sem instância) e o
# promote nunca roda.
#
# Este script (idempotente):
#   1) sobe a capacidade IA contratada via a API OFICIAL do pricing (upsert keyed —
#      não duplica; re-sync da quota de admissão embutido). Provisioning só via API
#      oficial (invariante) — nunca escreve Redis/DB de config direto.
#   2) encena o slot `current` de deploy_promote_ia (set-next + promote, com o
#      service-token que o gate de escrita do agent-registry exige).
#   3) confere que o pool passou a expor `deployed_skill_id` (⇒ o bridge instancia).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/setup_deploy_promote_pool.sh
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
AR="http://localhost:3300"      # agent-registry
PR="http://localhost:3900"      # pricing-api
BODY_POOL="deploy_promote_ia"
BODY_SKILL="skill_deploy_promote_v1"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
PRICING_TOKEN="${PRICING_ADMIN_TOKEN:-demo_pricing_admin_token}"

thw=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC")

show() { curl -s -o /tmp/_setup_body -w '%{http_code}' "$@"; echo; cat /tmp/_setup_body; echo; }

echo "1) Sobe a capacidade IA contratada (ai_agent base → 340; C = 340+10 = 350) ..."
echo "   (upsert keyed por (tenant,installation,resource_type) — idempotente)"
show -X POST "$PR/v1/pricing/resources/$TENANT" \
  -H "X-Admin-Token: $PRICING_TOKEN" -H 'content-type: application/json' \
  -d '{"installation_id":"default","resource_type":"ai_agent","quantity":340,"pool_type":"base","label":"Agentes IA (deploys do demo + Fase 2 + margem)"}'
echo "   Capacidade contratada agora:"
curl -s "$PR/v1/pricing/capacity/$TENANT?installation_id=default" | python3 -m json.tool || true
sleep 2

echo "2) Encena o slot next de $BODY_POOL = $BODY_SKILL ..."
SN=$(curl -s -o /tmp/_setup_body -w '%{http_code}' -X PUT "$AR/v1/pools/$BODY_POOL/slots/next" "${thw[@]}" \
  -H 'content-type: application/json' -d "{\"skill_id\":\"$BODY_SKILL\"}")
echo "   HTTP $SN"; cat /tmp/_setup_body; echo
[ "$SN" = "200" ] || { echo "FALHA: set-next em $BODY_POOL (HTTP $SN). Se 422 de capacidade, suba mais o ai_agent no passo 1."; exit 1; }

echo "3) Promove ($BODY_POOL: next → current) ..."
PM=$(curl -s -o /tmp/_setup_body -w '%{http_code}' -X POST "$AR/v1/pools/$BODY_POOL/promote" "${thw[@]}" \
  -H 'content-type: application/json' -d '{}')
echo "   HTTP $PM"; cat /tmp/_setup_body | python3 -m json.tool 2>/dev/null || cat /tmp/_setup_body; echo
[ "$PM" = "200" ] || { echo "FALHA: promote de $BODY_POOL (HTTP $PM)."; exit 1; }

echo "4) Confere que $BODY_POOL expõe deployed_skill_id (⇒ bootstrap instancia) ..."
sleep 3
curl -s "$AR/v1/pools" -H "x-tenant-id: $TENANT" \
  | python3 -c "import sys,json;ps=[p for p in json.load(sys.stdin)['pools'] if p['pool_id']=='$BODY_POOL'];print(json.dumps(ps[0],indent=2,ensure_ascii=False) if ps else 'POOL NAO ENCONTRADO')"

echo
echo "SETUP OK — agora rode:  bash infra/test/smoke_scheduled_promote.sh"
echo "(dê ~15s p/ o bridge provisionar a instância de $BODY_POOL após o promote)"
