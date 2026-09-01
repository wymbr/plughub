#!/usr/bin/env bash
#
# probe_mcp_rest_surface.sh — CAP-10, a medição que destrava (2026-09-01)
#
# A PERGUNTA QUE ELE RESPONDE
# ==========================
# A CAP-10 estava BLOQUEADA por *"falta medir a superfície de rede do mcp-server"*.
# Esta é a medição, e ela inverteu a prioridade do item.
#
# ACHADOS QUE ELE TRAVA
# =====================
# 1. **A borda existe e é versionada** — o nginx é gerado dentro do
#    `packages/platform-ui/Dockerfile`, e é por isso que procurar `nginx*.conf` no
#    repositório não acha nada. 21 `location`, 14 upstreams.
# 2. **A borda NÃO publica o transporte MCP.** Pela borda, `/sse` devolve `text/html`
#    (o fallback SPA); direto na 3100 devolve `text/event-stream` com o handshake. Ou
#    seja: as 48 dívidas de tool da CAP-09 estão atrás de uma porta que a única borda
#    do repositório não expõe. ⚠️ `200` ali é o **valor plausível** do catálogo — o
#    código HTTP é idêntico nos dois casos e só o `content-type` separa.
# 3. **A borda PUBLICA `/api/*`** — 20 rotas, e **9 sem credencial**. Estas são a
#    superfície realmente exposta do mcp-server.
# 4. **Exposição ≠ dano** (D14.1): duas das abertas devolveram vazio com a stack
#    ociosa, mas `conversation_history` lê `session:{id}:messages`, que tem **três
#    produtores** (`session.ts:529`, `orchestrator-bridge/main.py:7340`,
#    `channel-gateway/session_registry.py:144`). Zero agora não é inócuo.
#
# DUAS METADES QUE NÃO SE SUBSTITUEM
# ==================================
#   A · censo AST de `server.ts`  — quais rotas EXIGEM que alguém decida
#   B · medição ATRAVÉS DA BORDA  — o que responde de fato, com CONTROLE POSITIVO
# A sozinha não vê o que o deploy publica; B sozinha não distingue "sem credencial" de
# "credencial que eu não sei apresentar" — por isso o controle positivo é obrigatório:
# sem ele, um 401 universal (serviço fora do ar, nginx quebrado) passaria por proteção.
#
# ⚠️ ESTE PROBE NÃO FECHA ROTA. Ele trava o censo. Fechar as 9 é decisão (CAP-11).
#
# Uso:  bash infra/test/probe_mcp_rest_surface.sh
# Env:  BORDA=http://localhost:5174   DIRETO=http://localhost:3100
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

BORDA="${BORDA:-http://localhost:5174}"
DIRETO="${DIRETO:-http://localhost:3100}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CENSO="$ROOT/infra/test/_mcp_rest_census.mjs"

FAIL=0; INCONCL=0
ok()  { echo "  ✓ $*"; }
bad() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ⏭️  $* (INCONCLUSIVO)"; INCONCL=$((INCONCL+1)); }

# ── DECLARAÇÃO ────────────────────────────────────────────────────────────────
# rota|postura|motivo     · postura ∈ { gateada, aberta-divida, aberta-isenta }
#
# `aberta-divida` ≠ `aberta-isenta` pelo mesmo motivo do par `_SCOPE_EXEMPT`/
# `_SCOPE_DEBT`: *"decidimos que não precisa"* e *"ainda não decidimos"* são fatos
# diferentes, e juntá-los faz a dívida herdar a tranquilidade da decisão.
#
# ⚠️ Rota nova SEM linha aqui reprova. É o ponto do arquivo.
DECLARADO=$(cat <<'TABELA'
GET /api/agent-state|gateada|requireJwtRole
GET /api/approval_audit/:sessionId|gateada|verifyJwtPayload
GET /api/conversation_history/:sessionId|aberta-divida|SERVE CONTEUDO de conversa (session:{id}:messages, 3 produtores) — publicada pela borda (CAP-11)
GET /api/copilot_state/:sessionId|aberta-divida|estado de copilot da sessao — publicada pela borda (CAP-11)
GET /api/instances|gateada|verifyJwtPayload
GET /api/supervisor_capabilities/:sessionId|aberta-divida|publicada pela borda (CAP-11)
GET /api/supervisor_state/:sessionId|gateada|requireJwtRole
GET /api/work_queue/list|aberta-divida|lista fila de trabalho humano — publicada pela borda (CAP-11)
GET /api/work_queue/pending|aberta-divida|lista itens pendentes — publicada pela borda (CAP-11)
GET /health|aberta-isenta|liveness do compose; exigir credencial acopla o boot da stack ao do emissor de token (mesma isencao do analytics-api)
GET /internal/context-audit|aberta-divida|NAO publicada pela borda; direto na 3100 (CAP-11)
GET /sse|aberta-divida|transporte MCP, anonimo por construcao; NAO publicado pela borda (CAP-09/CAP-10)
POST /api/agent_done/:sessionId|aberta-divida|FECHA sessao — publicada pela borda (CAP-11)
POST /api/force-complete/:sessionId|gateada|requireJwtRole
POST /api/inject-context/:sessionId|gateada|requireJwtRole
POST /api/menu_submit/:sessionId|aberta-divida|SUBMETE no lugar do cliente — publicada pela borda (CAP-11)
POST /api/session_transfer/:sessionId|gateada|verifyJwtPayload
POST /api/work_queue/claim/:sessionId|aberta-divida|RECLAMA item de fila humana — publicada pela borda (CAP-11)
POST /api/work_queue/expire/:sessionId|gateada|requireJwtRole
POST /api/work_queue/release/:sessionId|aberta-divida|DEVOLVE item de fila humana — publicada pela borda (CAP-11)
POST /internal/context-snapshot|aberta-divida|NAO publicada pela borda; direto na 3100 (CAP-11)
POST /messages|aberta-divida|canal de escrita do transporte MCP; NAO publicado pela borda (CAP-09/CAP-10)
PUT /api/agent-pause|gateada|requireJwtRole
PUT /api/agent-resume|gateada|requireJwtRole
POST /api/agent-clear-pause|gateada|requireJwtRole
TABELA
)

echo "══ superfície REST do mcp-server-plughub ══"
echo "   borda=$BORDA  direto=$DIRETO"

command -v node >/dev/null 2>&1 || { echo "  ❌ node ausente"; exit 3; }
[ -f "$CENSO" ] || { echo "  ❌ ausente: $CENSO"; exit 3; }

echo
echo "── A · censo AST de server.ts ──"
AST="$(node "$CENSO" 2>&1)"
if ! echo "$AST" | head -1 | grep -q '^{'; then
  echo "$AST" | head -5 | sed 's/^/     /'; inc "censo AST não produziu JSON"; AST=""
else
  echo "$AST" | python3 -c "
import sys, json
r = json.load(sys.stdin)['routes']
pub = [x for x in r if x['published']]
print(f'  ✓ {len(r)} rotas · {len(pub)} publicadas pela borda (^/api)')
print(f\"     publicadas SEM credencial: {sum(1 for x in pub if not x['credentials'])}/{len(pub)}\")
print(f\"     total    SEM credencial: {sum(1 for x in r if not x['credentials'])}/{len(r)}\")
"
fi

echo
echo "── B · o transporte MCP atravessa a BORDA? ──"
CT_BORDA=$(curl -s -m 6 -o /dev/null -w '%{content_type}' "$BORDA/sse" 2>/dev/null || echo "")
CT_DIR=$(curl -s -m 3 -o /dev/null -w '%{content_type}' "$DIRETO/sse" 2>/dev/null || echo "")
if [ -z "$CT_DIR" ]; then
  inc "mcp-server inalcançável em $DIRETO — sem testemunha de que /sse existe"
elif ! echo "$CT_DIR" | grep -qi "event-stream"; then
  bad "TESTEMUNHA FALHOU: $DIRETO/sse não é event-stream (é '$CT_DIR') — o resto do ramo não vale"
elif [ -z "$CT_BORDA" ]; then
  inc "borda inalcançável em $BORDA"
elif echo "$CT_BORDA" | grep -qi "event-stream"; then
  bad "a BORDA publica o transporte MCP (content-type '$CT_BORDA') — mudou desde 2026-09-01"
else
  ok "borda devolve '$CT_BORDA' e não event-stream ⇒ o transporte NÃO é publicado"
  echo "     (o código HTTP é 200 nos dois; só o content-type separa — não julgar por status)"
fi

echo
echo "── C · CONTROLE POSITIVO · rota gateada recusa sem credencial ──"
GATE_OK=0
for r in "/api/instances" "/api/agent-state"; do
  c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$BORDA$r" 2>/dev/null || echo 000)
  if [ "$c" = "401" ] || [ "$c" = "403" ]; then ok "$r → $c"; GATE_OK=$((GATE_OK+1))
  else bad "$r → $c (esperado 401/403; sem este ramo, um 401 universal passaria por proteção)"; fi
done

echo
echo "── D · as ABERTAS respondem sem credencial, pela borda ──"
if [ "$GATE_OK" -eq 0 ]; then
  inc "sem controle positivo, este ramo não distingue aberto de serviço fora do ar"
else
  for r in "/api/work_queue/list" "/api/work_queue/pending"; do
    c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$BORDA$r" 2>/dev/null || echo 000)
    if [ "$c" = "200" ]; then ok "$r → 200 SEM credencial (dívida declarada, CAP-11)"
    elif [ "$c" = "401" ] || [ "$c" = "403" ]; then
      bad "$r → $c: FECHOU desde 2026-09-01 — mova a linha de 'aberta-divida' para 'gateada'"
    else inc "$r → $c (nem aberto nem gateado; investigar)"; fi
  done
fi

echo
echo "── E · toda rota CLASSIFICADA, e a classificação bate com a medição ──"
if [ -n "$AST" ]; then
  SAIDA=$(python3 - "$AST" <<PY
import sys, json
medido = {x['key']: x for x in json.loads(sys.argv[1])['routes']}
decl = {}
for l in """$DECLARADO""".strip().splitlines():
    k, postura, motivo = l.split("|", 2)
    decl[k] = (postura, motivo)

erros = []
for k in sorted(set(medido) - set(decl)):
    erros.append(f"SEM LINHA: '{k}' — classifique (gateada/aberta-divida/aberta-isenta)")
for k in sorted(set(decl) - set(medido)):
    erros.append(f"linha ÓRFÃ: '{k}' não existe mais em server.ts — remova")
for k in sorted(set(medido) & set(decl)):
    real = "gateada" if medido[k]['credentials'] else "aberta"
    espera = decl[k][0]
    if espera == "gateada" and real != "gateada":
        erros.append(f"REGRESSÃO em '{k}': declarada gateada, medida ABERTA")
    if espera.startswith("aberta") and real == "gateada":
        erros.append(f"MELHORIA não declarada em '{k}': agora tem credencial — mova para 'gateada'")

from collections import Counter
c = Counter(v[0] for v in decl.values())
print(f"CONTAGEM gateada={c['gateada']} aberta-divida={c['aberta-divida']} aberta-isenta={c['aberta-isenta']} total={len(decl)}")
for e in erros: print("ERRO " + e)
PY
)
  echo "$SAIDA" | grep '^CONTAGEM' | sed 's/^CONTAGEM /     /'
  if echo "$SAIDA" | grep -q '^ERRO '; then
    echo "$SAIDA" | grep '^ERRO ' | sed 's/^ERRO //' | while read -r l; do echo "  ❌ $l"; done
    FAIL=$((FAIL + $(echo "$SAIDA" | grep -c '^ERRO ')))
  else ok "as 25 estão classificadas e nenhuma postura mudou"; fi
else
  inc "sem o censo AST, não há o que classificar"
fi

echo
echo "──────────────────────────────────────────────────────────"
echo "  FAIL=$FAIL  INCONCLUSIVO=$INCONCL"
if [ "$INCONCL" -gt 0 ]; then echo "⏭️  INCONCLUSIVO — não mediu tudo; isto NÃO é verde"; exit 3; fi
if [ "$FAIL" -eq 0 ]; then
  echo "✅ superfície estável — borda não publica o transporte; as 9 abertas seguem declaradas"
  exit 0
fi
echo "❌ a superfície MUDOU sem a declaração acompanhar"
exit 1
