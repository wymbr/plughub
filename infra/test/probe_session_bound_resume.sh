#!/usr/bin/env bash
# probe_session_bound_resume.sh — 2026-09-13  (PID-01)
#
# PERGUNTA: `pending_workflow_get` e `workflow_resume` só agem para uma sessão que o
#           BRIDGE ativou — e os skills que as usam continuam funcionando?
#
# O DEFEITO QUE O ORIGINOU
#   As duas tools não sabiam quem chamava: tenant e âncoras vinham do input, e o
#   transporte MCP é anônimo. Medido antes do fix, pelo `/sse`: `pending_workflow_get`
#   sem credencial respondeu, e `workflow_resume` sem credencial chegou ao gateway.
#   O `session_token` que existia não servia: não carrega sessão, o `agent_login` é
#   auto-serviço, e o caminho bridge → skill-flow-service nunca recebeu nenhum.
#
# QUATRO RAMOS
#   A  CENSO — a lista de `@plughub/schemas` (SESSION_BOUND_TOOLS) é exatamente o conjunto
#      de tools cujo handler chama `sessionCaller`; o skill-flow-service injeta pelo
#      `injectSessionToken`; mutação do censo.
#   B  TRANSPORTE MCP VIVO — anônimo, token de agente e tenant alheio recusam nomeando;
#      o emissor interno exige credencial; com o token emitido as duas agem (controles).
#   C  ENVS — bridge e mcp-server carregam o MESMO MCP_INTERNAL_SERVICE_TOKEN (não vazio).
#   D  PONTA A PONTA — roda o probe da jornada (pendência e retomada dentro do chat) e
#      exige zero recusa de token de sessão e zero falha de emissão no intervalo.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
BRIDGE="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
TENANT="${TENANT:-tenant_demo}"
WF=packages/mcp-server-plughub/src/tools/workflow.ts
LISTA=packages/schemas/src/session-bound-tools.ts
SFS=packages/e2e-tests/services/skill-flow-service/src/index.ts
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

censo() {  # $1 workflow.ts → JSON {lista, gateadas, populacao}
  python3 - "$1" "$LISTA" <<'EOF'
import json, re, sys
wf = open(sys.argv[1], encoding="utf-8").read()
lista = re.search(r"SESSION_BOUND_TOOLS[^=]*=\s*\[([^\]]*)\]", open(sys.argv[2], encoding="utf-8").read())
lista = sorted(re.findall(r'"([a-z_]+)"', lista.group(1))) if lista else []
blocos = re.split(r"\n\s*server\.tool\(\s*\n\s*", wf)[1:]
tools, gate = [], []
for b in blocos:
    m = re.match(r'"([a-z_]+)"', b)
    if not m:
        continue
    tools.append(m.group(1))
    if re.search(r'sessionCaller\(\s*"%s"' % m.group(1), b):
        gate.append(m.group(1))
print(json.dumps({"lista": lista, "gateadas": sorted(gate), "populacao": len(tools)}))
EOF
}

echo "════════════════════════════════════════════════════════════════════"
echo " as tools de retomada só agem para sessão ativada pelo bridge?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
C=$(censo "$WF")
echo "   $C"
if [ "$(jexpr "$C" "d['populacao'] >= 8")" != "True" ]; then
  falha "o censo não está vendo as tools de workflow.ts (populacao=$(jexpr "$C" "d['populacao']"))"
elif [ "$(jexpr "$C" "d['lista'] == d['gateadas'] and len(d['lista']) >= 2")" = "True" ]; then
  ok "SESSION_BOUND_TOOLS == tools que chamam sessionCaller ($(jexpr "$C" "', '.join(d['lista'])"))"
else
  falha "lista e portão divergem: $C"
fi
if grep -q "injectSessionToken(tool, input, session_token, mcpServer)" "$SFS" && grep -q "mcpCall:      mcpCallDaSessao" "$SFS"; then
  ok "skill-flow-service injeta pelo injectSessionToken no mcpCall do engine"
else
  falha "skill-flow-service não injeta o token no mcpCall do engine"
fi
TMP=$(mktemp --suffix=.ts)
python3 - "$WF" "$TMP" <<'EOF'
import sys
s = open(sys.argv[1], encoding="utf-8").read()
alvo = 'const quem = sessionCaller("workflow_resume", input)'
assert s.count(alvo) == 1, "ancora da mutacao"
open(sys.argv[2], "w", encoding="utf-8").write(s.replace(alvo, "const quem = { caller: { tenant_id: '' } } as any", 1))
EOF
CM=$(censo "$TMP"); rm -f "$TMP"
[ "$(jexpr "$CM" "d['gateadas'] == ['pending_workflow_get'] and d['lista'] != d['gateadas']")" = "True" ] \
  && ok "mutação (portão removido de workflow_resume) acusada" \
  || falha "mutação do censo não acusou: $CM"

echo ""
echo "── B · TRANSPORTE MCP VIVO ────────────────────────────────────────────"
J=$(docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - $TENANT" < infra/test/_session_bound_exercise.cjs 2>/dev/null | tail -1)
echo "   $J" | cut -c1-400
if [ "$(jexpr "$J" "'casos' in d")" != "True" ]; then
  incon "exercício sem JSON: $J"
else
  for k in anon_pending_recusa anon_resume_recusa agente_recusado yaml_tenant_alheio_recusa emissor_sem_credencial_401 emissor_emite pending_com_token_age resume_com_token_chega; do
    [ "$(jexpr "$J" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k"
  done
fi

echo ""
echo "── C · ENVS ───────────────────────────────────────────────────────────"
T_MCP=$(docker exec "$MCP" printenv MCP_INTERNAL_SERVICE_TOKEN 2>/dev/null)
T_BR=$(docker exec "$BRIDGE" printenv MCP_INTERNAL_SERVICE_TOKEN 2>/dev/null)
if [ -n "$T_MCP" ] && [ "$T_MCP" = "$T_BR" ]; then
  ok "bridge e mcp-server carregam o mesmo MCP_INTERNAL_SERVICE_TOKEN"
else
  falha "MCP_INTERNAL_SERVICE_TOKEN: mcp=$([ -n "$T_MCP" ] && echo set || echo VAZIO) bridge=$([ "$T_MCP" = "$T_BR" ] && echo igual || echo DIFERENTE)"
fi

echo ""
echo "── D · PONTA A PONTA (jornada no chat) ────────────────────────────────"
if [ "${PULA_JORNADA:-}" = "1" ]; then
  incon "PULA_JORNADA=1 — ponta a ponta não medido"
else
  DESDE=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  bash infra/test/probe_journey_merge_status_access.sh > /tmp/pid01_jornada.log 2>&1
  EJ=$?
  RECUSAS=$(docker logs --since "$DESDE" "$MCP" 2>&1 | grep -c "RECUSADO: \(missing\|invalid\)_session_token")
  EMISSAO=$(docker logs --since "$DESDE" "$BRIDGE" 2>&1 | grep -c "PID-01")
  EMITIDOS=$(docker logs --since "$DESDE" "$MCP" 2>&1 | grep -c "session-token\] RECUSADO")
  if [ "$EJ" = "0" ]; then ok "probe da jornada verde (pendência + retomada pelo mcp-server)"; else falha "probe da jornada EXIT=$EJ (ver /tmp/pid01_jornada.log)"; fi
  [ "$RECUSAS" = "0" ] && ok "zero recusa de token de sessão no mcp-server durante a jornada" || falha "$RECUSAS recusa(s) de token de sessão durante a jornada"
  [ "$EMISSAO" = "0" ] && [ "$EMITIDOS" = "0" ] && ok "zero falha de emissão no bridge" || falha "falhas de emissão: bridge=$EMISSAO mcp=$EMITIDOS"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
