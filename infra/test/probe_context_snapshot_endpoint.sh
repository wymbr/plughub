#!/usr/bin/env bash
# probe_context_snapshot_endpoint.sh — o `POST /internal/context-snapshot` (F5).
#
# Este endpoint devolve o ContextStore de QUALQUER sessão do tenant. Teste unitário
# não julga credencial nem valor real de PII — por isso ele existe.
#
# O que cada ramo prova, e por que pode REPROVAR:
#
#   A. SEM CREDENCIAL, RECUSA. Requisição sem `x-service-token` tem de sair 401
#      (ou 503, se o serviço estiver sem o env). Um 200 aqui é vazamento de ctx
#      de todo o tenant para quem alcançar a porta.
#   B. TOKEN ERRADO, RECUSA. Sem este ramo, "confere o token" e "aceita qualquer
#      string não-vazia" ficam indistinguíveis.
#   C. TOKEN CERTO, RESPONDE. Testemunha de presença: sem ela, um endpoint
#      quebrado passaria em A e B por reprovar tudo.
#   D. O VALOR VEM MASCARADO. Injeta um CPF real e exige que ele NÃO volte cru.
#      É o ramo que impede a F5 de virar um cofre de PII durável.
#   E. O PORTÃO DE NAMESPACE **NÃO** É APLICADO. `caller.*` tem de estar PRESENTE
#      (mascarado), porque persistência ≠ exibição: aplicar aqui o filtro de UI
#      faria a config de um pool apagar história em silêncio. É desvio deliberado
#      da letra do ADR — se alguém "consertar" para usar `applyContextMaskingDynamic`,
#      este ramo fica vermelho, que é exatamente o ponto.
#
# Limpa o que injetou em qualquer saída.
#
# Uso:  bash infra/test/probe_context_snapshot_endpoint.sh <session_id>
set -u

DC=${DC:-docker compose -f docker-compose.demo.yml}
MCP=${MCP:-http://localhost:3100}
TENANT=${TENANT:-tenant_demo}
TOKEN=${MCP_INTERNAL_SERVICE_TOKEN:-changeme_mcp_internal_service_token_demo}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }
redis() { $DC exec -T redis redis-cli "$@"; }

SID=${1:-}
if [ -z "$SID" ]; then
  echo "uso: $0 <session_id>"
  echo
  echo "sessões com ContextStore vivo:"
  redis --raw KEYS "$TENANT:ctx:*" | grep -v ':ctx:journey:' | sed "s/^$TENANT:ctx://" | head -20
  exit 2
fi

CTX="$TENANT:ctx:$SID"
if [ "$(redis --raw EXISTS "$CTX")" != "1" ]; then
  echo "INCONCLUSIVO: $CTX não existe"
  exit 2
fi

CPF_REAL="529.982.247-25"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkentry() { printf '{"value":"%s","confidence":0.9,"source":"probe","visibility":"agents_only","updated_at":"%s"}' "$1" "$NOW"; }

cleanup() { redis HDEL "$CTX" caller.cpf session.cpf >/dev/null 2>&1 || true; }
trap cleanup EXIT
# DOIS injetados, e a diferença entre eles é o ponto:
#   `caller.cpf`  → tem regra EXATA (score 20). Prova que o masking roda.
#   `session.cpf` → NÃO tem regra exata; só é coberto pelo glob de SUFIXO `*.cpf`
#                   (score 15). Prova que o MECANISMO de sufixo está em vigor.
# Sem o segundo, o gate passaria idêntico num mundo onde o sufixo nunca funcionou —
# e `session.cpf` em claro foi um dos três buracos que a varredura de 2026-08-26 achou.
redis HSET "$CTX" caller.cpf  "$(mkentry "$CPF_REAL")" >/dev/null
redis HSET "$CTX" session.cpf "$(mkentry "$CPF_REAL")" >/dev/null
echo "injetado caller.cpf (regra exata) + session.cpf (só por sufixo) em $CTX"
echo

BODY=$(printf '{"tenant_id":"%s","session_id":"%s","include_journey":true}' "$TENANT" "$SID")
call() { # $1 = header extra (ou vazio)
  if [ -z "$1" ]; then
    curl -s -o /tmp/_ctxsnap.json -w '%{http_code}' -X POST "$MCP/internal/context-snapshot" \
      -H 'content-type: application/json' -d "$BODY"
  else
    curl -s -o /tmp/_ctxsnap.json -w '%{http_code}' -X POST "$MCP/internal/context-snapshot" \
      -H 'content-type: application/json' -H "x-service-token: $1" -d "$BODY"
  fi
}

fail=0
incon=0
note() { echo "  $*"; }

echo "A · sem credencial:"
code=$(call "")
case "$code" in
  401|503) note "✓ HTTP $code" ;;
  *) note "✗ HTTP $code — o ctx do tenant inteiro está alcançável sem credencial"; fail=1 ;;
esac
echo

echo "B · token errado:"
code=$(call "nao-e-o-token")
if [ "$code" = "401" ]; then note "✓ HTTP 401"
else note "✗ HTTP $code — qualquer string não-vazia passaria"; fail=1; fi
echo

echo "C · token certo (testemunha de presença):"
code=$(call "$TOKEN")
if [ "$code" != "200" ]; then
  note "✗ HTTP $code — nada abaixo pôde ser julgado"
  [ "$code" = "503" ] && note "  (503 = mcp-server sem MCP_INTERNAL_SERVICE_TOKEN — erro de deploy)"
  echo; echo "GATE context-snapshot: INCONCLUSIVO"; exit 2
fi
n=$(jq '.session.entries | length' /tmp/_ctxsnap.json)
note "✓ HTTP 200 · $n entradas no escopo session"
[ "${n:-0}" -lt 1 ] && { note "INCONCLUSIVO: zero entradas"; incon=1; }
echo

echo "D · o CPF NÃO volta cru:"
got=$(jq -r '.session.entries["caller.cpf"].value // "—"' /tmp/_ctxsnap.json)
if [ "$got" = "$CPF_REAL" ]; then
  note "✗ voltou EM CLARO ('$got') — a F5 gravaria PII crua num store durável"
  fail=1
elif [ "$got" = "—" ]; then
  note "✗ ausente — ver ramo E (o portão de namespace não pode ser aplicado aqui)"
  fail=1
else
  note "✓ mascarado: '$got'"
fi
echo

echo "E · o portão de namespace NÃO é aplicado (persistência ≠ exibição):"
if jq -e '.session.entries | has("caller.cpf")' /tmp/_ctxsnap.json >/dev/null; then
  note "✓ \`caller.*\` presente apesar de fora de \`operator_namespaces\`"
  note "  (se este ramo ficar vermelho, alguém trocou por applyContextMaskingDynamic"
  note "   e a config de UI de um pool passou a apagar história)"
else
  note "✗ \`caller.cpf\` sumiu — o filtro de EXIBIÇÃO está podando o registro durável"
  fail=1
fi

echo

echo "F · o glob de SUFIXO está em vigor (\`session.cpf\`, sem regra exata):"
gots=$(jq -r '.session.entries["session.cpf"].value // "—"' /tmp/_ctxsnap.json)
if [ "$gots" = "$CPF_REAL" ]; then
  note "✗ voltou EM CLARO — o glob \`*.cpf\` não está casando. Ou o matcher perdeu o"
  note "  suporte a sufixo, ou as regras de sufixo saíram da config. Um CPF fora do"
  note "  namespace \`caller\` volta a ser gravado cru no snapshot durável."
  fail=1
elif [ "$gots" = "—" ]; then
  note "✗ ausente — ver ramo E; nenhum filtro de exibição pode podar a persistência"
  fail=1
else
  note "✓ mascarado: '$gots' (protegido por TIPO de campo, não por namespace)"
fi

echo
[ "$fail" != 0 ] && { echo "GATE context-snapshot: REPROVADO"; exit 1; }
[ "$incon" != 0 ] && { echo "GATE context-snapshot: INCONCLUSIVO"; exit 2; }
echo "GATE context-snapshot: OK"; exit 0
