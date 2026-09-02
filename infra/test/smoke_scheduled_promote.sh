#!/usr/bin/env bash
# Scheduler Fase 2 — smoke/gate E2E: uma Agenda dispara um pool webhook cujo skill
# EFETIVA a promoção de OUTRO pool (o gate homolog→prod fechado de ponta a ponta).
#
# Cadeia provada:
#   Agenda (once, fire_at no passado)  → poller dispara (≤ poll_interval_s = 15s)
#   → POST /v1/channels/webhook/pool/deploy_promote_ia (payload {target_pool, action})
#   → skill_deploy_promote_v1: invoke pool_promote(@ctx.target_pool)
#   → agent-registry POST /v1/pools/<alvo>/promote (next→current, SkillDeployment)
#
# Dois casos:
#   A) SUCESSO: alvo tem `next` encenado → current vira o ex-next + SkillDeployment.
#   B) `next` VAZIO: promote 409 → pool_promote isError → invoke on_failure →
#      complete(failed). O slot do alvo NÃO muda (não promove em silêncio). A falha
#      vive no CICLO DA SESSÃO (drill-through), não no AgendaDispatch — coerente com
#      a camada (a channel-gateway devolve 201+session_id; admitir/executar é da
#      sessão). O que o gate exige e prova: NENHUMA promoção silenciosa.
#
# Pré: demo no ar; mcp-server-plughub rebuildado (tool pool_promote); orchestrator-
#      bridge reiniciado (RegistrySyncer semeou skill_deploy_promote_v1 + pool
#      deploy_promote_ia); e **`bash infra/test/setup_deploy_promote_pool.sh` rodado
#      UMA vez** (sobe a capacidade IA + encena o slot `current` de deploy_promote_ia
#      → o bridge provisiona a instância; sem isso a sessão do promote fica na fila).
#      Uso (raiz do repo):  bash infra/test/smoke_scheduled_promote.sh
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
SC="http://localhost:3650"      # scheduler-api
TARGET="sac_ia"                 # pool-alvo da promoção (skill inalterado — seguro no demo)
TARGET_SKILL="skill_atendimento_sac_v1"
BODY_POOL="deploy_promote_ia"   # pool webhook cujo skill efetiva o promote

# O agent-registry gateia MUTAÇÕES de /v1/pools (requireResourceWrite vaza para o
# sub-path /slots pelo mount de prefixo) → escritas exigem o service-token OU Bearer+ABAC.
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
th=(-H "x-tenant-id: $TENANT")                                 # leitura (GET aberto)
thw=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC")     # escrita (set-next)
ts=(-H "X-Tenant-ID: $TENANT")                                 # scheduler-api usa X-Tenant-ID

jqget() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" | head -1; }

# req METHOD URL [curl-args...] → ecoa "CODE\nBODY" (status na 1ª linha)
req() {
  local method="$1" url="$2"; shift 2
  curl -s -o /tmp/_smoke_body -w '%{http_code}' -X "$method" "$url" "$@"
  echo; cat /tmp/_smoke_body; echo
}

# Resumo enxuto dos 3 slots (só skill_id/set_by/set_at — sem o yaml_snapshot gigante)
slots_summary() {
  curl -s "$AR/v1/pools/$1/slots" "${th[@]}" | python3 -c '
import sys,json
d=json.load(sys.stdin)["slots"]
for s in ("previous","current","next"):
    x=d[s]
    if x.get("set"):
        print("   %-9s: skill=%s set_by=%s set_at=%s" % (s, x.get("skill_id"), x.get("set_by"), x.get("set_at")))
    else:
        print("   %-9s: (vazio)" % s)'
}
cur_set_at() {  # ecoa o set_at do slot current de $1 (vazio se não setado)
  curl -s "$AR/v1/pools/$1/slots" "${th[@]}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["slots"]["current"].get("set_at",""))' 2>/dev/null || echo ""
}

echo "── CASO A: promoção com next encenado ─────────────────────────────────────"

echo "0) Slots atuais do alvo $TARGET:"
SLOTS_JSON=$(curl -s "$AR/v1/pools/$TARGET/slots" "${th[@]}")
slots_summary "$TARGET"

# Reusa o config_json do current do alvo → declarada de deploy INALTERADA (igual →
# passa a governança de capacidade item 3b, e o promote é não-destrutivo). Sem
# current → {} (declara 1).
CUR_CFG=$(echo "$SLOTS_JSON" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);c=d["slots"]["current"];print(json.dumps(c.get("config_json") or {}) if c.get("set") else "{}")')
CUR_BEFORE_A=$(echo "$SLOTS_JSON" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["slots"]["current"].get("set_at",""))' 2>/dev/null || echo "")

echo "1) Encena o slot next do alvo ($TARGET = $TARGET_SKILL, config=$CUR_CFG) ..."
SN=$(req PUT "$AR/v1/pools/$TARGET/slots/next" "${thw[@]}" \
  -H 'content-type: application/json' -d "{\"skill_id\":\"$TARGET_SKILL\",\"config_json\":$CUR_CFG}")
SN_CODE=$(echo "$SN" | head -1)
echo "   HTTP $SN_CODE"; echo "$SN" | tail -n +2
[ "$SN_CODE" = "200" ] || { echo "FALHA: set-next (HTTP $SN_CODE — ver corpo acima)"; exit 1; }
echo "   next encenado"

PAST=$(date -u -d '-1 minute' +%Y-%m-%dT%H:%M:%SZ)
echo "2) Cria a Agenda once (fire_at=$PAST, no passado → dispara já) → $BODY_POOL ..."
AG_A=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"E2E scheduled promote OK ($TARGET)\",
  \"target_pool_id\":\"$BODY_POOL\",
  \"payload\":{\"target_pool\":\"$TARGET\",\"action\":\"promote\"},
  \"validity\":{\"starts_at\":\"$PAST\"},
  \"schedule\":{\"mode\":\"once\",\"fire_at\":\"$PAST\"}
}" | jqget id)
[ -n "$AG_A" ] || { echo "FALHA: agenda A sem id"; exit 1; }
echo "   agenda A = $AG_A"

echo "3) Aguardando o poller disparar + o skill promover (25s) ..."
sleep 25

echo "4) Ledger de disparos da agenda A:"
curl -s "$SC/v1/agendas/$AG_A/dispatches" "${ts[@]}" \
  | python3 -m json.tool

echo "5) Slots do alvo $TARGET DEPOIS (esperado: current=$TARGET_SKILL set_by=scheduler:deploy_promote, next vazio):"
slots_summary "$TARGET"
CUR_AFTER_A=$(cur_set_at "$TARGET")

echo "6) Último SkillDeployment de $TARGET_SKILL (esperado: notes=promote, recém-gravado):"
curl -s "$AR/v1/skills/$TARGET_SKILL/deployments" "${th[@]}" | python3 -c '
import sys,json
ds=json.load(sys.stdin).get("deployments",[])
d=ds[0] if ds else {}
print("   id=%s pools=%s notes=%s deployed_by=%s deployed_at=%s" % (d.get("id"), d.get("pool_ids"), d.get("notes"), d.get("deployed_by"), d.get("deployed_at")))'

if [ -n "$CUR_AFTER_A" ] && [ "$CUR_AFTER_A" != "$CUR_BEFORE_A" ]; then
  echo "   PASS A: current.set_at mudou ($CUR_BEFORE_A → $CUR_AFTER_A) — promoveu."
else
  echo "   ATENÇÃO A: current.set_at não mudou — inspecione o ledger/slots acima."
fi

echo
echo "── CASO B: next vazio → promove nada (409 não some) ───────────────────────"
# Após o caso A, o next do alvo está LIMPO. Uma nova agenda para o mesmo alvo deve
# resultar em promote 409 → sessão failed → current INALTERADO.

PAST2=$(date -u -d '-1 minute' +%Y-%m-%dT%H:%M:%SZ)
echo "7) Cria a Agenda once para $TARGET (next agora vazio) ..."
AG_B=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"E2E scheduled promote EMPTY-NEXT ($TARGET)\",
  \"target_pool_id\":\"$BODY_POOL\",
  \"payload\":{\"target_pool\":\"$TARGET\",\"action\":\"promote\"},
  \"validity\":{\"starts_at\":\"$PAST2\"},
  \"schedule\":{\"mode\":\"once\",\"fire_at\":\"$PAST2\"}
}" | jqget id)
[ -n "$AG_B" ] || { echo "FALHA: agenda B sem id"; exit 1; }
echo "   agenda B = $AG_B"

echo "8) Aguardando disparo + tentativa de promote (25s) ..."
sleep 25

echo "9) Slots do alvo $TARGET (esperado: current.set_at INALTERADO = $CUR_AFTER_A):"
slots_summary "$TARGET"
CUR_AFTER_B=$(cur_set_at "$TARGET")
echo "   current.set_at = $CUR_AFTER_B"

if [ "$CUR_AFTER_B" = "$CUR_AFTER_A" ]; then
  echo "   PASS B: current INALTERADO — o 409 não promoveu em silêncio."
else
  echo "   FALHA B: current mudou sem next encenado — promoção silenciosa!"; exit 1
fi

echo "10) (drill-through) A falha vive na SESSÃO disparada pela agenda B. Ver o"
echo "    session_id no ledger e conferir outcome=failed:"
curl -s "$SC/v1/agendas/$AG_B/dispatches" "${ts[@]}" | python3 -m json.tool

echo
echo "GATE Fase 2 — RESUMO:"
echo "  A) next encenado  → current promovido + SkillDeployment (promote real)."
echo "  B) next vazio      → 409 → sessão failed, slot intacto (nada em silêncio)."
