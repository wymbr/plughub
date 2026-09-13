#!/usr/bin/env bash
# probe_resume_requirement.sh — 2026-09-13  (PID-06)
#
# PERGUNTA: o `resume_token` de uma pendência com exigência de identidade só sai para a
#           sessão que provou AGORA — e o deploy recusa config abaixo do piso do skill?
#
# O DEFEITO QUE O ORIGINOU (medido ao vivo antes do fix)
#   A liberação do token tinha um portão só, e fixo: a âncora `possessed`, que é posse
#   DURÁVEL no cadastro. Uma sessão sintética que nunca fez OTP pediu a pendência de um
#   cliente importado cujo celular outra sessão tinha acabado de provar — e recebeu o
#   `resume_token` (vetor (4) do ADR identity-door). O OTP da sessão só era conferido por
#   um `choice` dentro do próprio intake: exigência autorada, não da plataforma.
#
# QUATRO RAMOS
#   A  CENSO — a cadeia inteira carrega o campo (schema · piso no set-next e no promote ·
#      engine · skill-flow-service · gateway · mcp-server), as DUAS saídas do
#      `pending_workflow_get` passam pelo julgamento, e a população de steps
#      `customer_resumable` declara exigência com piso. Mutações sobre CÓPIA.
#   B  PISO AO VIVO — set-next com a config sem `resume_requires` recusa (422 nomeando);
#      controle: com a config, aceita. E todo slot `current` que declara piso tem a chave.
#   C  LIBERAÇÃO AO VIVO — cliente importado + processo real + OTP real: a sessão que
#      provou leva o token (controle), a que não provou recebe `verification_required`.
#   D  PONTA A PONTA — a jornada de consulta do limite (OTP no chat, pendência, merge)
#      continua verde com a exigência em produção. `PULA_JORNADA=1` pula (INCONCLUSIVO).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
COMPOSE="docker compose -p plughub-demo -f docker-compose.demo.yml"
CG="${CG:-http://localhost:8010}"
AR="${AR:-http://localhost:3300}"
MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
PG="${PG_CONTAINER:-plughub-demo-postgres-1}"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
CENSO=infra/test/_resume_requirement_census.py
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

CENSO_LIMPO="d['schema_campo_nos_steps'] == 2 and d['deploy_piso_set_next'] and d['deploy_piso_promote'] and d['engine_repassa'] == ['delegate', 'collect'] and d['sfs_repassa'] == 2 and d['gw_campo_no_registro'] and d['gw_pending_entry'] == 3 and d['gw_chave_legada'] == 2 and d['gw_leituras'] == 3 and d['mcp_saidas_julgadas'] == 2 and d['mcp_retornos_crus'] == 0 and len(d['resumiveis']) >= 1 and d['resumiveis'] == d['declaram'] == d['com_piso']"

muta() {  # $1 arquivo relativo · $2 âncora · $3 troca → imprime o censo da cópia
  local d; d=$(mktemp -d)
  mkdir -p "$d/packages"
  for p in schemas/src agent-registry/src/routes skill-flow-engine/src/steps skill-flow-engine/skills \
           e2e-tests/services/skill-flow-service/src channel-gateway/src/plughub_channel_gateway/adapters \
           channel-gateway/src/plughub_channel_gateway/identity mcp-server-plughub/src/tools; do
    mkdir -p "$d/packages/$p"; cp -r "packages/$p/." "$d/packages/$p/"
  done
  python3 - "$d/$1" "$2" "$3" <<'EOF'
import sys
p, a, b = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
assert s.count(a) == 1, "ancora da mutacao: " + a[:70]
open(p, "w", encoding="utf-8").write(s.replace(a, b, 1))
EOF
  python3 "$CENSO" "$d"; rm -rf "$d"
}

echo "════════════════════════════════════════════════════════════════════"
echo " o token de retomada só sai para quem provou nesta sessão?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
C=$(python3 "$CENSO" .)
echo "   $C" | cut -c1-600
if [ "$(jexpr "$C" "d['mcp_bloco_encontrado']")" != "True" ]; then
  incon "o censo não achou o bloco do pending_workflow_get — não mediu nada"
elif [ "$(jexpr "$C" "$CENSO_LIMPO")" = "True" ]; then
  ok "cadeia completa · 2 saídas julgadas, 0 cruas · $(jexpr "$C" "len(d['resumiveis'])") steps customer_resumable, todos com exigência e piso"
else
  falha "censo sujo: $C"
fi
CM=$(muta packages/mcp-server-plughub/src/tools/workflow.ts \
  'const liberado = await withholdUnprovenResume(deps.redis, tenant_id, quem.caller.session_id, data)
      return { content: [{ type: "text" as const, text: JSON.stringify(liberado) }] }' \
  'return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }')
[ "$(jexpr "$CM" "d['mcp_retornos_crus'] == 1 and d['mcp_saidas_julgadas'] == 1")" = "True" ] \
  && ok "M1 (porta legada devolvendo sem julgar) acusada" || falha "M1 não acusada: $CM"
CM=$(muta packages/agent-registry/src/routes/pool-slots.ts \
  'judgeIdentityFloor(snapshot, config_json' 'judgeRequiredConfig(snapshot, config_json')
[ "$(jexpr "$CM" "not d['deploy_piso_set_next']")" = "True" ] \
  && ok "M2 (piso fora do set-next) acusada" || falha "M2 não acusada: $CM"
CM=$(muta packages/skill-flow-engine/skills/skill_limite_processo_v1.yaml \
  '    resume_requires_floor: ["otp"]
' '')
[ "$(jexpr "$CM" "'skill_limite_processo_v1.aprovar' not in d['com_piso']")" = "True" ] && [ "$(jexpr "$CM" "$CENSO_LIMPO")" != "True" ] \
  && ok "M3 (step resumível sem piso) acusada" || falha "M3 não acusada: $CM"

echo ""
echo "── B · PISO AO VIVO ───────────────────────────────────────────────────"
H=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" -H 'content-type: application/json')
sn() { curl -s -o /tmp/_pid06_sn.json -w '%{http_code}' --max-time 20 -X PUT "$AR/v1/pools/limite_processo/slots/next" "${H[@]}" -d "$1"; }
c=$(sn '{"skill_id":"skill_limite_processo_v1","config_json":{"max_concurrent_sessions":10}}')
if [ "$c" = "422" ] && grep -q resume_requires_ausente_na_config /tmp/_pid06_sn.json; then
  ok "set-next sem resume_requires recusado (422 resume_requires_ausente_na_config)"
else
  falha "set-next sem resume_requires deu $c: $(head -c 200 /tmp/_pid06_sn.json)"
fi
c=$(sn '{"skill_id":"skill_limite_processo_v1","config_json":{"max_concurrent_sessions":10,"resume_requires":[]}}')
[ "$c" = "422" ] && grep -q resume_requires_abaixo_do_piso /tmp/_pid06_sn.json \
  && ok "set-next com [] abaixo do piso recusado" || falha "set-next com [] deu $c: $(head -c 200 /tmp/_pid06_sn.json)"
c=$(sn '{"skill_id":"skill_limite_processo_v1","config_json":{"max_concurrent_sessions":10,"resume_requires":["otp"]}}')
[ "$c" = "200" ] && ok "controle: set-next com [\"otp\"] aceito" || falha "controle: set-next com [\"otp\"] deu $c: $(head -c 200 /tmp/_pid06_sn.json)"
P=$(docker exec "$PG" psql -U plughub -d plughub_registry -Atc \
  "select count(*) filter (where yaml_snapshot::text like '%resume_requires_floor%'),
          count(*) filter (where yaml_snapshot::text like '%resume_requires_floor%' and config_json->'resume_requires' ? 'otp')
     from pool_skill_slots where slot='current'" 2>/dev/null)
N1=${P%%|*}; N2=${P##*|}
if [ -z "$P" ]; then incon "sem leitura do Postgres"
elif [ "$N1" -ge 1 ] && [ "$N1" = "$N2" ]; then ok "$N1 slot(s) current com piso, todos com resume_requires contendo otp"
else falha "slots current com piso=$N1, com a config certa=$N2"
fi

echo ""
echo "── C · LIBERAÇÃO AO VIVO ──────────────────────────────────────────────"
# shellcheck source=/dev/null
. infra/test/_auth.sh
plughub_gw_service_shim
. infra/test/_resume_requirement_setup.sh
if ! rr_setup; then
  incon "cenário não montou: $RR_ERRO"
elif [ "$(jexpr "$RESULT" "'casos' in d")" != "True" ]; then
  incon "exercício sem JSON: $RESULT"
else
  echo "   $(jexpr "$RESULT" "d['detalhe']['p1']") | $(jexpr "$RESULT" "d['detalhe']['p2']")"
  if [ "$(jexpr "$RESULT" "d['casos']['s1_provou'] and d['casos']['s1_recebe_token']")" != "True" ]; then
    falha "controle positivo: a sessão que provou não levou o token — os negativos não provam nada ($(jexpr "$RESULT" "d['detalhe']"))"
  else
    ok "a sessão que provou leva o token (pendência exige $(jexpr "$RESULT" "d['detalhe']['p1']['required']"))"
    for k in s2_nao_recebe_token s2_pede_verificacao; do
      [ "$(jexpr "$RESULT" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k ($(jexpr "$RESULT" "d['detalhe']['p2']"))"
    done
  fi
fi

echo ""
echo "── D · PONTA A PONTA ──────────────────────────────────────────────────"
if [ "${PULA_JORNADA:-}" = "1" ]; then
  incon "PULA_JORNADA=1 — jornada não medida"
else
  timeout 600 bash infra/test/probe_journey_merge_status_access.sh > /tmp/pid06_jornada.log 2>&1
  EJ=$?
  [ "$EJ" = "0" ] && ok "jornada de consulta do limite verde com a exigência em produção" \
    || falha "jornada EXIT=$EJ (ver /tmp/pid06_jornada.log)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"; exit 0
