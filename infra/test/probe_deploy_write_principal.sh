#!/usr/bin/env bash
# probe_deploy_write_principal.sh — 2026-09-13  (PID-07)
#
# PERGUNTA: a escrita no agent-registry age em nome de QUEM a credencial diz — tenant
#           e autor do token — e o deploy responde ao campo da tela de Deploy?
#
# O DEFEITO QUE O ORIGINOU (medido ao vivo antes do fix)
#   A ficha dizia "o deploy não tem portão". Não era isso: `/v1/pools` casava por
#   prefixo e cobria slots/promote/rollback com `config.resources` — portão por acidente,
#   no campo errado (o devops via a tela de Deploy e tomava 403). O que estava aberto:
#   um token de `tenant_outro` com `x-tenant-id: tenant_demo` GRAVOU um slot do
#   tenant_demo (200), em qualquer router; e o autor vinha do `x-user-id`, que a UI não
#   manda (34 slots como `system`) e que qualquer um forja.
#
# TRÊS RAMOS
#   A  CENSO — deploy montado antes de pools; toda rota de escrita de deploy com o
#      portão `skill_flows.operacao`; nenhum router lendo `x-user-id`; o middleware
#      amarra o tenant. Mutações sobre CÓPIA (ordem invertida, portão removido do
#      promote, leitura de `x-user-id` reintroduzida).
#   B  AO VIVO — anônimo 401; `config.resources` sozinho 403 nomeando o campo; devops
#      grava e o autor é o do token mesmo com `x-user-id` forjado; tenant alheio recusado
#      (`tenant_mismatch`) em slot, promote e pool, sem gravar; controle: o serviço grava
#      com o autor que declara.
#   C  POPULAÇÃO — quantos slots ainda carregam o autor `system` (informativo: é o
#      passivo de antes, e não se corrige por deploy).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

AR="${AR_CONTAINER:-plughub-demo-agent-registry-1}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
TENANT="${TENANT:-tenant_demo}"
POOL="${POOL:-probe_profile_agent}"
SKILL="${SKILL:-skill_formfill_demo_v1}"
SRC=packages/agent-registry/src
CENSO=infra/test/_deploy_write_principal_census.py
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
jexpr() { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }

julga_censo() {  # $1 json → "True" se o censo está limpo
  jexpr "$1" "d['deploy_antes_de_pools'] and len(d['rotas_escrita']) >= 3 and not d['rotas_sem_portao'] and not d['routers_lendo_x_user_id'] and d['populacao_routers'] >= 5 and d['middleware_recusa_tenant_divergente'] and d['middleware_preenche_tenant_do_token']"
}

copia_mutada() {  # $1 arquivo relativo a src  $2 âncora  $3 troca → imprime o dir da cópia
  local d; d=$(mktemp -d)
  cp -r "$SRC/app.ts" "$SRC/routes" "$SRC/middleware" "$d/"
  python3 - "$d/$1" "$2" "$3" <<'EOF'
import sys
p, a, b = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
assert s.count(a) == 1, "ancora da mutacao: " + a[:60]
open(p, "w", encoding="utf-8").write(s.replace(a, b, 1))
EOF
  echo "$d"
}

echo "════════════════════════════════════════════════════════════════════"
echo " a escrita no agent-registry age em nome da credencial?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
C=$(python3 "$CENSO" "$SRC")
echo "   $C" | cut -c1-400
if [ "$(jexpr "$C" "d['montagem_encontrada']")" != "True" ]; then
  incon "o censo não achou as montagens em app.ts — ele não está medindo nada"
elif [ "$(julga_censo "$C")" = "True" ]; then
  ok "deploy antes de pools · $(jexpr "$C" "len(d['rotas_escrita'])") rotas de escrita com skill_flows.operacao · 0 de $(jexpr "$C" "d['populacao_routers']") routers lendo x-user-id · tenant amarrado"
else
  falha "censo sujo: $C"
fi

D=$(copia_mutada app.ts 'app.use("/v1/pools/:pool_id",     poolSlotsRouter)
app.use("/v1/pools",              requireResourceWrite, poolsRouter)' 'app.use("/v1/pools",              requireResourceWrite, poolsRouter)
app.use("/v1/pools/:pool_id",     poolSlotsRouter)')
CM=$(python3 "$CENSO" "$D"); rm -rf "$D"
[ "$(jexpr "$CM" "not d['deploy_antes_de_pools']")" = "True" ] && [ "$(julga_censo "$CM")" != "True" ] \
  && ok "M1 (pools montado antes do deploy) acusada" || falha "M1 não acusada: $CM"

D=$(copia_mutada routes/pool-slots.ts 'poolSlotsRouter.post("/promote", requireDeployWrite, async' 'poolSlotsRouter.post("/promote", async')
CM=$(python3 "$CENSO" "$D"); rm -rf "$D"
[ "$(jexpr "$CM" "d['rotas_sem_portao'] == ['POST /promote']")" = "True" ] \
  && ok "M2 (portão removido do promote) acusada" || falha "M2 não acusada: $CM"

D=$(copia_mutada routes/channels.ts 'const createdBy = authorOf(req)' 'const createdBy = (req.headers["x-user-id"] as string) ?? "system"')
CM=$(python3 "$CENSO" "$D"); rm -rf "$D"
[ "$(jexpr "$CM" "d['routers_lendo_x_user_id'] == ['channels.ts']")" = "True" ] \
  && ok "M3 (x-user-id relido em channels.ts) acusada" || falha "M3 não acusada: $CM"

echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
if ! docker exec "$AR" sh -c 'curl -sf localhost:${PORT:-3300}/v1/health >/dev/null 2>&1 || wget -qO- localhost:${PORT:-3300}/v1/health >/dev/null 2>&1 || node -e "fetch(\"http://localhost:\"+(process.env.PORT||3300)+\"/v1/health\").then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"'; then
  incon "agent-registry fora do ar ($AR)"
else
  J=$(docker exec -i "$AR" node - "$TENANT" "$POOL" "$SKILL" < infra/test/_deploy_write_principal_exercise.cjs 2>/dev/null | tail -1)
  echo "   $J" | cut -c1-500
  if [ "$(jexpr "$J" "'casos' in d")" != "True" ]; then
    incon "exercício sem JSON: $J"
  elif [ "$(jexpr "$J" "d['casos']['devops_grava']")" != "True" ]; then
    # sem o positivo, os negativos passariam por um portão que recusa tudo
    falha "controle positivo: o devops não gravou no pool $POOL ($(jexpr "$J" "d['detalhe']['devops']")) — os negativos não provam nada"
  else
    for k in anonimo_401 resources_sozinho_403_nomeia_campo devops_grava autor_do_token_nao_do_header \
             slot_tenant_alheio_recusado slot_alheio_nao_gravou pools_tenant_alheio_recusado \
             promote_tenant_alheio_recusado servico_grava_com_autor_declarado; do
      [ "$(jexpr "$J" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k ($(jexpr "$J" "d['detalhe']"))"
    done
  fi
fi

echo ""
echo "── C · POPULAÇÃO (informativo) ────────────────────────────────────────"
P=$(docker exec "$PG" psql -U plughub -d plughub_registry -Atc "select count(*) filter (where set_by='system'), count(*) from pool_skill_slots" 2>/dev/null)
if [ -n "$P" ]; then
  echo "   slots com autor 'system' (passivo de antes do PID-07) / total: ${P/|/ \/ }"
else
  echo "   (sem leitura do Postgres — ramo informativo, não julga)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"; exit 0
