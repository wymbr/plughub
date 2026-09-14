#!/usr/bin/env bash
# probe_batch_deploy_retired.sh — 2026-09-14  (PID-08)
#
# PERGUNTA: um registro de "deploy" ainda pode dizer que algo foi implantado sem que o slot
#           que RODA tenha mudado?
#
# O DEFEITO QUE O ORIGINOU (medido antes de aposentar)
#   `POST /v1/skills/:id/deploy` gravava `skill.flow` e um `SkillDeployment` com `pool_ids`,
#   e não tocava slot nenhum — enquanto a produção executa só o snapshot do slot `current`.
#   Dos 115 registros, 110 eram de promote e 5 de um seed de demonstração (`sac_ia`, slot
#   nunca mudou) que desenhavam triângulos de deploy na lente sobre deploys que não houve.
#   Zero usos reais; nenhum chamador de UI. A tool `skill_deploy` e o workflow
#   `skill_scheduled_deploy_v1` (não implantado em pool nenhum) eram as outras duas portas.
#
# DOIS RAMOS
#   A  CENSO — o SkillDeployment tem exatamente UM escritor (o promote); a rota do lote
#      responde 410 sem tocar o prisma; `skill_deploy` não está registrada e `pool_promote`
#      está; o workflow agendado não existe; o seed da lente usa promote, não o lote.
#      Mutações sobre CÓPIA (nunca na árvore compartilhada): devolver a escrita ao lote,
#      re-registrar a tool, voltar o seed ao lote — e TIRAR a escrita do promote, porque
#      "nenhum escritor" também é sujo.
#   B  AO VIVO — o lote responde 410 com credencial e 401 sem ela; a leitura vizinha
#      segue 200; o `tools/list` do servidor no ar não tem `skill_deploy` e tem
#      `pool_promote`; a população de `skill_deployments` só tem `notes='promote'` e não
#      está vazia.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
REG="${REG:-http://localhost:3300}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
CENSO=infra/test/_batch_deploy_retired_census.py
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

LIMPO="d['escritores_deployment'] == ['routes/pool-slots.ts'] and d['rota_lote_410'] is True and not d['tool_skill_deploy'] and d['tool_pool_promote'] and not d['workflow_agendado'] and not d['seed_usa_lote'] and d['seed_usa_promote']"

echo "════════════════════════════════════════════════════════════════════"
echo " um registro de deploy ainda pode mentir sobre o slot que roda?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
C=$(python3 "$CENSO" .)
echo "   $C"
if [ "$(jexpr "$C" "d['tool_pool_promote']")" != "True" ]; then
  incon "o censo não achou nem a pool_promote — o padrão de registro de tool não casa, não mediu nada"
elif [ "$(jexpr "$C" "$LIMPO")" = "True" ]; then
  ok "um escritor (promote), lote 410, tool e workflow removidos, seed por promote"
else
  falha "censo sujo: $C"
fi

COPIA=$(mktemp -d)
trap 'rm -rf "$COPIA"' EXIT
copia_limpa() {
  rm -rf "$COPIA"/*
  for f in packages/agent-registry/src packages/mcp-server-plughub/src infra/test/seed_deploy_lens_demo.sh; do
    mkdir -p "$COPIA/$(dirname "$f")"
    cp -r "$f" "$COPIA/$f"
  done
  rm -rf "$COPIA/packages/mcp-server-plughub/src/__tests__" "$COPIA/packages/agent-registry/src/__tests__"
}
muta() {  # $1 rótulo · $2 arquivo relativo · $3 expressão python sobre `s`
  copia_limpa
  python3 - "$COPIA/$2" "$3" <<'PY'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
novo = eval(expr)
assert novo != s, "mutação no-op"
open(p, "w", encoding="utf-8", newline="").write(novo)
PY
  if [ $? -ne 0 ]; then incon "mutação '$1' não se aplicou (âncora sumiu)"; return; fi
  M=$(python3 "$CENSO" "$COPIA")
  if [ "$(jexpr "$M" "$LIMPO")" = "False" ]; then ok "mutação '$1' → censo sujo"
  else falha "mutação '$1' passou no censo: $M"; fi
}

copia_limpa
C0=$(python3 "$CENSO" "$COPIA")
if [ "$(jexpr "$C0" "$LIMPO")" != "True" ]; then
  incon "a CÓPIA sem mutação não é limpa — as mutações não provariam nada: $C0"
else
  ok "controle: a cópia sem mutação é limpa"
  muta "lote volta a gravar deployment" packages/agent-registry/src/routes/skills.ts \
    "s.replace('return res.status(410).json({\n    error:   \"deploy_route_retired\",', 'await (prisma as any).skillDeployment.create({ data: {} })\n  return res.status(410).json({\n    error:   \"deploy_route_retired\",', 1)"
  muta "promote deixa de gravar (zero escritores)" packages/agent-registry/src/routes/pool-slots.ts \
    "s.replace('skillDeployment.create(', 'skillDeploymentOff(', 1)"
  muta "tool skill_deploy re-registrada" packages/mcp-server-plughub/src/tools/deploy.ts \
    "s.replace('  server.tool(\n    \"pool_promote\",', '  server.tool(\n    \"skill_deploy\", \"x\", {} as any, async () => ok({}))\n  server.tool(\n    \"pool_promote\",', 1)"
  muta "seed volta ao lote" infra/test/seed_deploy_lens_demo.sh \
    "s.replace('  PR=\$(\$CURL -X POST \"\$REGISTRY/v1/pools/\$POOL/promote\"', '  PR=\$(\$CURL -X POST \"\$REGISTRY/v1/skills/\$SKILL/deploy\"', 1)"
fi

echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
if ! curl -sf "$REG/v1/health" >/dev/null 2>&1; then
  incon "agent-registry inalcançável em $REG — a metade viva não mediu"
else
  SKILL=$(curl -s "$REG/v1/skills" -H "x-tenant-id: $TENANT" | python3 -c 'import json,sys
d = json.load(sys.stdin); d = d if isinstance(d, list) else d.get("skills", [])
print(sorted(x["skill_id"] for x in d)[0] if d else "")' 2>/dev/null)
  if [ -z "$SKILL" ]; then
    incon "nenhum skill no registry — sem amostra para exercer a rota"
  else
    R=$(curl -s -w '\n%{http_code}' -X POST "$REG/v1/skills/$SKILL/deploy" -H 'Content-Type: application/json' \
        -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" -H 'x-user-id: probe_pid08' -d '{"pool_ids":["probe_inexistente"]}')
    CODE=$(printf '%s' "$R" | tail -1); BODY=$(printf '%s' "$R" | sed '$d')
    if [ "$CODE" = "410" ] && [ "$(jexpr "$BODY" "d['error']")" = "deploy_route_retired" ]; then
      ok "lote com credencial → 410 deploy_route_retired"
    else falha "lote com credencial → $CODE $BODY"; fi
    CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$REG/v1/skills/$SKILL/deploy" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json' -d '{}')
    [ "$CODE" = "401" ] && ok "lote sem credencial → 401 (o portão segue na frente)" || falha "lote sem credencial → $CODE"
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "$REG/v1/skills/$SKILL/deployments/scheduled" -H "x-tenant-id: $TENANT")
    [ "$CODE" = "410" ] && ok "deployments/scheduled → 410" || falha "deployments/scheduled → $CODE"
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "$REG/v1/skills/$SKILL/deployments" -H "x-tenant-id: $TENANT")
    [ "$CODE" = "200" ] && ok "controle: GET deployments → 200" || falha "controle: GET deployments → $CODE"
  fi

  POP=$(docker exec "$PG" psql -U plughub -d plughub_registry -Atc \
        "select count(*) filter (where notes is distinct from 'promote'), count(*) from skill_deployments" 2>/dev/null)
  OUTROS=${POP%%|*}; TOTAL=${POP##*|}
  if ! [[ "$TOTAL" =~ ^[0-9]+$ ]]; then incon "população de skill_deployments ilegível ($POP)"
  elif [ "$TOTAL" -eq 0 ]; then incon "skill_deployments vazia — 'só promote' seria verdade vazia"
  elif [ "$OUTROS" -eq 0 ]; then ok "população: $TOTAL registro(s), todos de promote"
  else falha "população: $OUTROS de $TOTAL registro(s) não vieram do promote"; fi
fi

LIVE=$(node infra/test/_mcp_tool_guard_census.mjs --live 2>&1)
if ! printf '%s' "$LIVE" | head -1 | grep -q '^{'; then
  incon "mcp-server inalcançável — tools/list não medido"
else
  TEM_SD=$(jexpr "$LIVE" "'skill_deploy' in d['tools']"); TEM_PP=$(jexpr "$LIVE" "'pool_promote' in d['tools']")
  if [ "$TEM_PP" != "True" ]; then incon "tools/list sem pool_promote — a listagem não é a esperada"
  elif [ "$TEM_SD" = "False" ]; then ok "tools/list ao vivo: pool_promote presente, skill_deploy ausente"
  else falha "tools/list ao vivo ainda oferece skill_deploy"; fi
fi

echo ""
if [ "$FALHA" -gt 0 ]; then echo "VEREDICTO: FALHA ($FALHA)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "VEREDICTO: INCONCLUSIVO ($INCONCL)"; exit 2; fi
echo "VEREDICTO: OK — o deploy tem um caminho, e o registro dele é o do promote"
exit 0
