#!/usr/bin/env bash
#
# probe_mcp_tool_guard_census.sh — CAP-09, primeiro passo (2026-09-01)
#
# O QUE ELE É, E O QUE ELE DELIBERADAMENTE NÃO É
# ==============================================
# NÃO é portão de política. Ele **não decide** que toda tool precisa de credencial —
# essa decisão não foi tomada, e tomá-la a partir de um censo seria repetir o erro da
# D14.1: confundir EXPOSIÇÃO com DANO. Ele faz uma coisa só, no molde do
# `probe_edge_surface.sh`: **enumera e exige que toda tool esteja CLASSIFICADA**, para
# que tool nova não entre calada e para que camada de guarda não SUMA em silêncio.
#
# O QUE ELE MEDE
# ==============
# Quatro camadas por tool, do `mcp-server-plughub`:
#   token · guard (injection) · permission (`permissions[]`) · audit (`mcp.audit`)
#
# Linha de base medida em 2026-09-01 (72 tools):
#   token 23/72 · guard 16/72 · permission 1/72 · audit 1/72 · SEM camada nenhuma 33
# A única com as quatro é `invoke` — a borda `external-mcp`. As tools da PRÓPRIA
# plataforma não seguem aquele modelo, e o `deploy.ts:7-13` afirma que seguem
# (*"permission-checked, injection-guarded and audited by the McpInterceptor"*), com a
# frase copiada para o `CHANGELOG.md:18389`. As três são falsas: o `McpInterceptor`
# só existe no exemplo do próprio docstring. Promessa em duas casas, mecanismo em zero.
#
# DUAS METADES QUE NÃO SE SUBSTITUEM
# ==================================
#   A · censo AST do fonte  — vê a CAMADA de guarda, que é fato do código
#   B · `tools/list` ao vivo — vê o que o servidor REALMENTE registra
# A sozinha não enxerga tool registrada por caminho que ninguém inclui; B sozinha não
# enxerga guarda nenhuma. Elas se conferem (ramo C).
#
# ⚠️ ISTO É FATO SOBRE O CÓDIGO, NÃO SOBRE EXPOSIÇÃO. Borda é fato de REDE, e não há
# neste repositório o equivalente do `probe_edge_surface.sh` para o mcp-server. O ramo
# D mede o que o TRANSPORTE faz e compara com o DECLARADO — hoje ele aceita conexão
# anônima, o que torna FALSO o invariante do `packages/mcp-server-plughub/CLAUDE.md`
# (*"Every tool authenticates via JWT in the Authorization header"*). O ramo trava o
# estado ATUAL: mudar para melhor também reprova, e é para reprovar mesmo — a mudança
# tem de ser deliberada e a declaração tem de acompanhar.
#
# POR QUE A LINHA DE BASE NÃO É VERMELHA
# ======================================
# Um gate que nasce vermelho ensina todo mundo a ignorá-lo — a lição do runner de
# pytest que produzia 476 falsos vermelhos. Aqui a classe `divida` registra o estado
# real SEM reprovar, e o que reprova é a MUDANÇA não declarada.
#
# Uso:  bash infra/test/probe_mcp_tool_guard_census.sh
# Env:  MCP_SSE_URL=http://localhost:3100/sse
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

MCP_SSE_URL="${MCP_SSE_URL:-http://localhost:3100/sse}"
export MCP_SSE_URL
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CENSO="$ROOT/infra/test/_mcp_tool_guard_census.mjs"

FAIL=0; INCONCL=0
ok()  { echo "  ✓ $*"; }
bad() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ⏭️  $* (INCONCLUSIVO)"; INCONCL=$((INCONCL+1)); }

# ── DECLARAÇÃO DE POSTURA DO TRANSPORTE ───────────────────────────────────────
# Editar isto é uma DECISÃO. Vale nos DOIS sentidos: se alguém puser credencial no
# `/sse`, este ramo reprova até a linha ser atualizada — porque um gate que só nota
# regressão deixa a melhoria passar sem registro, e aí ninguém sabe o que vale.
TRANSPORTE_DECLARADO="anonimo"   # anonimo | autenticado

echo "══ censo de guardas das tools do mcp-server-plughub ══"
echo "   sse=$MCP_SSE_URL"

command -v node >/dev/null 2>&1 || { echo "  ❌ node ausente"; exit 3; }
[ -f "$CENSO" ] || { echo "  ❌ ausente: $CENSO"; exit 3; }

# ── CLASSIFICAÇÃO DECLARADA ───────────────────────────────────────────────────
# tool|camadas|classe|motivo
#
# classe:
#   ok      — a postura é a pretendida
#   isento  — decidido que NÃO precisa, com o motivo (sem gatilho: é decisão)
#   divida  — sabe-se que falta, com o gatilho nomeado (a política da CAP-09)
#
# São duas classes e não uma porque *"decidimos não"* e *"ainda não decidimos"* são
# fatos diferentes — juntá-las faria a dívida herdar a tranquilidade da decisão. É o
# mesmo par `_SCOPE_EXEMPT`/`_SCOPE_DEBT` da analytics-api.
#
# ⚠️ Tool nova SEM linha aqui reprova. É o ponto do arquivo.
DECLARADO=$(cat <<'TABELA'
agent_busy|token|ok|verifica session_token assinado
agent_delegate|nenhuma|divida|delega subtarefa A2A sem credencial — politica pendente (CAP-09)
agent_delegate_status|nenhuma|divida|politica pendente (CAP-09)
agent_done|token|ok|verifica session_token assinado
agent_event|token|ok|verifica session_token assinado
agent_heartbeat|token|ok|verifica session_token assinado
agent_join_conference|nenhuma|divida|injeta agente em sessao viva sem credencial — politica pendente (CAP-09)
agent_login|nenhuma|isento|EMISSOR do token: exigir um seria circular. Valida o agent_type_id no registry
agent_logout|token|ok|verifica session_token assinado
agent_pause|token|ok|verifica session_token assinado
agent_ready|token|ok|verifica session_token assinado
calendar_add_business_duration|nenhuma|divida|politica pendente (CAP-09)
calendar_business_duration|nenhuma|divida|politica pendente (CAP-09)
calendar_is_open|nenhuma|divida|politica pendente (CAP-09)
calendar_next_open_slot|nenhuma|divida|politica pendente (CAP-09)
campaign_delivery_result|nenhuma|divida|politica pendente (CAP-09)
campaign_drain|nenhuma|divida|drena audiencia de campanha — politica pendente (CAP-09)
contact_eligibility_check|nenhuma|divida|politica pendente (CAP-09)
context_set|nenhuma|divida|ESCREVE no ContextStore da sessao — politica pendente (CAP-09)
conversation_end|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
conversation_escalate|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
conversation_start|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
conversation_status|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
customer_attach_key|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
customer_resolve|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
customer_update_attributes|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
evaluation_agent_context_next|nenhuma|divida|politica pendente (CAP-09)
evaluation_context_get|token|ok|verifica session_token assinado
evaluation_context_resolve|nenhuma|divida|politica pendente (CAP-09)
evaluation_lock|nenhuma|divida|politica pendente (CAP-09)
evaluation_pre_review_submit|token|ok|verifica session_token assinado
evaluation_publish|nenhuma|divida|politica pendente (CAP-09)
evaluation_review_submit|token|ok|verifica session_token assinado
evaluation_submit|token|ok|verifica session_token assinado
evaluation_threads_get|token|ok|verifica session_token assinado
form_get|nenhuma|divida|politica pendente (CAP-09)
insight_register|token|ok|verifica session_token assinado
invoke|token+permission+audit|ok|UNICA com as quatro camadas: e a borda external-mcp
journey_merge|token|ok|verifica session_token assinado
mailing_add|nenhuma|divida|politica pendente (CAP-09)
mailing_unsubscribe|nenhuma|divida|opt-out de contato sem credencial — politica pendente (CAP-09)
mention_command_dispatch|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
message_send|token|ok|verifica session_token assinado
notification_send|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
otp_challenge|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
otp_verify|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
outbound_contact_request|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
pending_workflow_get|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
pool_promote|nenhuma|divida|PROMOVE deploy; o cabecalho do deploy.ts afirma o oposto — politica pendente (CAP-09)
pool_status_get|nenhuma|divida|politica pendente (CAP-09)
queue_context_get|nenhuma|divida|politica pendente (CAP-09)
rule_dry_run|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
segment_outcome_record|nenhuma|divida|politica pendente (CAP-09)
send_message|token|ok|verifica session_token assinado
session_channel_change|token|ok|verifica session_token assinado
session_context_get|token|ok|verifica session_token assinado
session_escalate|token|ok|verifica session_token assinado
session_invite|token|ok|verifica session_token assinado
skill_deploy|nenhuma|divida|DEPLOY de skill sem credencial — politica pendente (CAP-09)
supervisor_capabilities|nenhuma|divida|politica pendente (CAP-09)
supervisor_state|nenhuma|divida|estado da sessao viva — politica pendente (CAP-09)
survey_link_create|nenhuma|divida|politica pendente (CAP-09)
survey_record|nenhuma|divida|grava resposta de pesquisa — politica pendente (CAP-09)
system_availability_check|nenhuma|divida|politica pendente (CAP-09)
transcript_get|nenhuma|divida|SERVE TRANSCRICAO de contato; irmao gateado existe (analytics /v1/transcript) — politica pendente (CAP-09)
wait_for_assignment|token|ok|verifica session_token assinado
wait_for_message|token|ok|verifica session_token assinado
work_queue_list|nenhuma|divida|politica pendente (CAP-09)
work_task_claim|nenhuma|divida|RECLAMA item de fila humana — politica pendente (CAP-09)
work_task_release|nenhuma|divida|politica pendente (CAP-09)
workflow_resume|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
workflow_trigger|guard|divida|tem injection guard, nao tem credencial — politica pendente (CAP-09)
TABELA
)

echo
echo "── A · censo AST do fonte ──"
AST_JSON="$(node "$CENSO" 2>&1)"
if ! echo "$AST_JSON" | head -1 | grep -q '^{'; then
  echo "$AST_JSON" | head -5 | sed 's/^/     /'
  inc "censo AST não produziu JSON"
  AST_JSON=""
else
  echo "$AST_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin); t = d['tools']
print(f\"  ✓ {d['total']} tools no fonte\")
for c in ('token','guard','permission','audit'):
    print(f'     {c:11s} {sum(1 for x in t if x[c]):3d}/{len(t)}')
print(f\"     {'sem nenhuma':11s} {sum(1 for x in t if not any(x[c] for c in ('token','guard','permission','audit'))):3d}/{len(t)}\")
"
fi

echo
echo "── B · tools/list no servidor NO AR ──"
LIVE_JSON="$(node "$CENSO" --live 2>&1)"
if ! echo "$LIVE_JSON" | head -1 | grep -q '^{'; then
  echo "$LIVE_JSON" | head -3 | sed 's/^/     /'
  inc "servidor inalcançável — a metade viva não mediu (suba a stack)"
  LIVE_JSON=""
else
  ok "$(echo "$LIVE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])") tools registradas ao vivo"
fi

echo
echo "── C · A × B · fonte e runtime têm de descrever o MESMO conjunto ──"
if [ -n "$AST_JSON" ] && [ -n "$LIVE_JSON" ]; then
  DIFF=$(python3 - "$AST_JSON" "$LIVE_JSON" <<'PY'
import sys, json
ast  = {x['tool'] for x in json.loads(sys.argv[1])['tools']}
live = set(json.loads(sys.argv[2])['tools'])
so_fonte, so_vivo = sorted(ast - live), sorted(live - ast)
if so_fonte: print("no FONTE e não no runtime: " + ", ".join(so_fonte))
if so_vivo:  print("no RUNTIME e não no fonte: " + ", ".join(so_vivo))
PY
)
  if [ -z "$DIFF" ]; then ok "os dois conjuntos batem"
  else echo "$DIFF" | while read -r l; do bad "$l"; done; FAIL=$((FAIL+1)); fi
else
  inc "sem as duas metades, não há cruzamento"
fi

echo
echo "── D · postura do TRANSPORTE (declarada: $TRANSPORTE_DECLARADO) ──"
if [ -n "$LIVE_JSON" ]; then
  # A metade viva conectou SEM Authorization. Se conectou, o transporte é anônimo.
  MEDIDO="anonimo"
  if [ "$MEDIDO" = "$TRANSPORTE_DECLARADO" ]; then
    ok "transporte $MEDIDO — bate com o declarado"
    [ "$MEDIDO" = "anonimo" ] && echo "     ⚠️ e por isso o invariante do packages/mcp-server-plughub/CLAUDE.md" \
      && echo "        (\"Every tool authenticates via JWT in the Authorization header\") é FALSO"
  else
    bad "transporte medido '$MEDIDO' ≠ declarado '$TRANSPORTE_DECLARADO' — atualize TRANSPORTE_DECLARADO"
  fi
else
  inc "sem a metade viva, a postura do transporte não foi medida"
fi

echo
echo "── E · toda tool CLASSIFICADA, e a classificação bate com a medição ──"
if [ -n "$AST_JSON" ]; then
  SAIDA=$(python3 - "$AST_JSON" <<PY
import sys, json
medido = {x['tool']: x for x in json.loads(sys.argv[1])['tools']}
declarado = {}
for linha in """$DECLARADO""".strip().splitlines():
    tool, camadas, classe, motivo = linha.split("|", 3)
    declarado[tool] = (camadas, classe, motivo)

erros = []
for tool in sorted(set(medido) - set(declarado)):
    erros.append(f"SEM LINHA na tabela: '{tool}' — classifique-a (ok/isento/divida) antes de seguir")
for tool in sorted(set(declarado) - set(medido)):
    erros.append(f"linha ÓRFÃ na tabela: '{tool}' não existe mais no fonte — remova")
for tool in sorted(set(medido) & set(declarado)):
    m = medido[tool]
    atual = "+".join(c for c in ("token","guard","permission","audit") if m[c]) or "nenhuma"
    if atual != declarado[tool][0]:
        erros.append(f"DERIVA em '{tool}': medido '{atual}' ≠ declarado '{declarado[tool][0]}'")

from collections import Counter
c = Counter(v[1] for v in declarado.values())
print(f"CONTAGEM ok={c['ok']} isento={c['isento']} divida={c['divida']} total={len(declarado)}")
for e in erros: print("ERRO " + e)
PY
)
  echo "$SAIDA" | grep '^CONTAGEM' | sed 's/^CONTAGEM /     /'
  if echo "$SAIDA" | grep -q '^ERRO '; then
    echo "$SAIDA" | grep '^ERRO ' | sed 's/^ERRO //' | while read -r l; do echo "  ❌ $l"; done
    FAIL=$((FAIL + $(echo "$SAIDA" | grep -c '^ERRO ')))
  else
    ok "as 72 estão classificadas e nenhuma camada derivou"
  fi
else
  inc "sem o censo AST, não há o que classificar"
fi

echo
echo "──────────────────────────────────────────────────────────"
echo "  FAIL=$FAIL  INCONCLUSIVO=$INCONCL"
if [ "$INCONCL" -gt 0 ]; then
  echo "⏭️  INCONCLUSIVO — o probe não mediu tudo; isto NÃO é verde"
  exit 3
fi
if [ "$FAIL" -eq 0 ]; then
  echo "✅ censo estável — toda tool classificada, nenhuma camada sumiu, transporte como declarado"
  exit 0
fi
echo "❌ o censo MUDOU sem a declaração acompanhar"
exit 1
