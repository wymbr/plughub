#!/usr/bin/env bash
#
# probe_mcp_permissions_producer.sh — CAP-06 (2026-09-01)
#
# PROPOSIÇÃO
# ==========
# O `tools[]` declarado na skill chega ASSINADO ao `session_token` e é o que a borda
# `invoke` impõe. Cinco elos, e até 2026-09-01 o terceiro estava aberto:
#
#   YAML/registry declara `tools[]`  →  registry guarda  →  **agent_login ASSINA**
#     →  executor manda o session_token  →  mcp-server decodifica e impõe (judgeInvoke)
#
# O DEFEITO QUE ISTO FECHA (CAP-05, medido ao vivo antes da correção)
# ==================================================================
# `registry-client.getAgentType` devolvia `permissions: []` FIXO. Como o `judgeInvoke`
# NEGA com lista vazia, a única borda MCP que o `CLAUDE.md` declara em vigor recusava
# 100% das chamadas — inclusive de um agente que declarasse a tool. Rodando este probe
# contra o binário anterior: P1, P3 e P4 vermelhos, com o P3 (skill DECLARA a tool e
# ainda leva `permission_denied`) sendo o retrato do defeito.
#
# POR QUE A METADE PESADA É NODE, E NÃO SHELL
# ===========================================
# `agent_login` e `invoke` são tools MCP sobre SSE; um cliente SSE em `curl` não é
# escrevível. Medir por leitura de código deixaria de fora exatamente o elo quebrado —
# a assinatura do token. O `_mcp_permissions_probe.mjs` ATRAVESSA a borda e imprime uma
# linha `RESULT <ramo> <PASS|FAIL> <detalhe>`; este arquivo só orquestra e julga.
#
# O QUE FARIA ESTE PROBE FICAR VERMELHO (a pergunta que vale)
# ==========================================================
#   - voltar a fixar `permissions: []` no registry-client  → P1, P3, P4
#   - popular a lista mas quebrar o formato `"{server}:{tool}"` → P3 e P4
#   - afrouxar o `judgeInvoke` para deixar passar o não declarado → P4 e P5
#   - fazer a virada deixar de ser opt-in (skill sem `tools` ganhando permissão) → P2
#
# RAMO QUE NÃO PODE SUMIR: o **P2**. Sem ele, um `judgeInvoke` que aceitasse tudo
# passaria em P1/P3 e o probe ficaria verde com a borda escancarada.
#
# Re-executável: cria duas skills de teste e as APAGA no fim, inclusive em erro.
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

set -uo pipefail

REGISTRY_URL="${REGISTRY_URL:-http://localhost:3300}"
MCP_SSE_URL="${MCP_SSE_URL:-http://localhost:3100/sse}"
TENANT_ID="${TENANT_ID:-tenant_demo}"
export REGISTRY_URL MCP_SSE_URL TENANT_ID
export AGENT_REGISTRY_SERVICE_TOKEN="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_PROBE="$HERE/_mcp_permissions_probe.mjs"

PASS=0; FAIL=0; SKIPPED=0

ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
skip() { echo "  ⏭️  $* (INCONCLUSIVO)"; SKIPPED=$((SKIPPED+1)); }

echo "══ pré-condições ══"

if ! command -v node >/dev/null 2>&1; then
  echo "  ❌ node ausente — este probe precisa do cliente MCP; não há caminho em shell puro."
  exit 1
fi
ok "node presente ($(node --version))"

for svc in "$REGISTRY_URL/v1/skills:registry" "${MCP_SSE_URL%/sse}/health:mcp-server"; do
  url="${svc%:*}"; nome="${svc##*:}"
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Tenant-Id: $TENANT_ID" "$url" || echo 000)
  if [ "$code" = "200" ]; then ok "$nome no ar (HTTP $code)"
  else
    echo "  ❌ $nome inalcançável (HTTP $code) em $url"
    echo "     Suba a stack antes: docker compose -f docker-compose.demo.yml up -d"
    exit 1
  fi
done

[ -f "$NODE_PROBE" ] || { echo "  ❌ ausente: $NODE_PROBE"; exit 1; }

echo
echo "══ atravessando a borda (agent_login + invoke, MCP sobre SSE) ══"

OUT="$(node "$NODE_PROBE" 2>&1)"
echo "$OUT" | grep -v '^RESULT ' | sed 's/^/     /' | grep -v '^     $' || true

declare -A RAMO=(
  [P0]="testemunha OPT-IN · quantas skills já declaram tools"
  [P1]="O ELO · o tools[] declarado chega ASSINADO no session_token"
  [P2]="CONTROLE · skill SEM tools continua com [] (a virada é opt-in)"
  [P3]="a tool DECLARADA atravessa o gate de permissão"
  [P4]="a tool NÃO declarada é negada, e a recusa NOMEIA as duas listas"
  [P5]="CAP-05 medido · lista vazia NEGA — era o default de TODO agente"
)

for r in P0 P1 P2 P3 P4 P5; do
  linha="$(echo "$OUT" | grep "^RESULT $r " || true)"
  echo
  echo "── $r · ${RAMO[$r]} ──"
  if [ -z "$linha" ]; then
    skip "$r não produziu veredicto"
    continue
  fi
  veredicto="$(echo "$linha" | awk '{print $3}')"
  detalhe="$(echo "$linha" | cut -d' ' -f4-)"
  case "$veredicto" in
    PASS) ok "$detalhe" ;;
    *)    bad "$detalhe" ;;
  esac
done

if echo "$OUT" | grep -q "^RESULT FATAL"; then
  echo
  bad "$(echo "$OUT" | grep '^RESULT FATAL' | cut -d' ' -f4-)"
fi

echo
echo "══ resíduo ══"
RESTO=$(curl -s -H "X-Tenant-Id: $TENANT_ID" "$REGISTRY_URL/v1/skills" \
        | python3 -c "
import sys, json
d = json.load(sys.stdin)
itens = d if isinstance(d, list) else d.get('skills') or d.get('items') or []
print(len([s for s in itens if str(s.get('skill_id','')).startswith('skill_capseis_probe_')]))
" 2>/dev/null || echo ERR)
if [ "$RESTO" = "0" ]; then ok "nenhuma skill de teste ficou para trás"
else bad "sobraram $RESTO skills 'skill_capseis_probe_*' no registry"; fi

echo
echo "──────────────────────────────────────────────────────────"
echo "  PASS=$PASS  FAIL=$FAIL  SKIPPED=$SKIPPED"
# SKIPPED reprova de propósito: ramo que não roda não é ramo verde — é ramo sem
# medição, e é assim que um probe compra confiança sem dar nada.
if [ "$FAIL" -eq 0 ] && [ "$SKIPPED" -eq 0 ]; then
  echo "✅ o tools[] declarado é a autorização MCP — assinada no token, imposta na borda"
  exit 0
fi
echo "❌ CAP-06 NÃO fecha (FAIL=$FAIL SKIPPED=$SKIPPED)"
exit 1
