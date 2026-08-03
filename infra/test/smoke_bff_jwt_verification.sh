#!/usr/bin/env bash
# smoke_bff_jwt_verification.sh — o BFF verifica ASSINATURA e EXPIRAÇÃO? (2026-08-03)
#
# CONTEXTO. `mcp-server-plughub` não recebia `PLUGHUB_JWT_SECRET`, então
# `verifyJwtPayload` (server.ts:796) caía no fallback de desenvolvimento: base64-decode
# do payload, sem verificar nada. Isso valia para TODAS as rotas de UI do BFF, incluindo
# os gates `supervisor|admin`.
#
# POR QUE UM SMOKE E NÃO UM TESTE UNITÁRIO. `_JWT_SECRET` é lido do ambiente no momento
# do import do módulo. Um teste unitário provaria o comportamento da função com o
# ambiente do TESTE — e o defeito era exatamente o ambiente do DEPLOY não ter a variável.
# O que precisa ser afirmado é sobre o serviço em execução.
#
# OS TRÊS CASOS, e por que nenhum sobra:
#   1. sem token          → 401. Já passava antes; é o controle de que a rota tem gate.
#   2. assinatura FORJADA → 401. Falhava antes (o fallback aceitava qualquer coisa).
#   3. token EXPIRADO     → 401. **O caso que ninguém tinha notado**: decode-only não lê
#      `exp`, então o TTL de 1 h do access token era decorativo — um token vazado valia
#      para sempre.
#   4. token VÁLIDO       → NÃO-401. Controle positivo: sem ele, um serviço que recusasse
#      tudo passaria nos três primeiros. É o mesmo verde-por-recusa-universal que a
#      varredura de suítes achou hoje em `TestAdminGate`.
#
# Uso:  bash infra/test/smoke_bff_jwt_verification.sh
# Pré:  stack demo no ar.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SECRET="changeme_auth_jwt_secret_demo_32c"
BFF="http://mcp-server-plughub:3100"
ROUTE="/api/agent-state"          # requireJwtRole([operator,supervisor,admin])
FAILED=0

# Assina um HS256 na mão dentro do container do redis (tem sh + nada mais é preciso).
# Feito via node no próprio mcp-server para não depender de ferramenta externa.
mint() {  # $1 = exp offset em segundos, $2 = segredo
  $DC exec -T mcp-server-plughub node -e "
    const c=require('crypto');
    const b=o=>Buffer.from(JSON.stringify(o)).toString('base64url');
    const h=b({alg:'HS256',typ:'JWT'});
    const p=b({sub:'u1',tenant_id:'tenant_demo',role:'supervisor',
               roles:['supervisor'],exp:Math.floor(Date.now()/1000)+($1)});
    const s=c.createHmac('sha256','$2').update(h+'.'+p).digest('base64url');
    process.stdout.write(h+'.'+p+'.'+s);
  " 2>/dev/null | tr -d '\r'
}

probe() {  # $1 = rótulo, $2 = esperado(401|not401), $3 = header Authorization (ou vazio)
  local code
  if [ -n "$3" ]; then
    code=$($DC exec -T mcp-server-plughub sh -lc \
      "wget -qS -O /dev/null --header='Authorization: $3' '$BFF$ROUTE' 2>&1 | awk '/HTTP\//{print \$2}' | tail -1" \
      < /dev/null | tr -d '\r')
  else
    code=$($DC exec -T mcp-server-plughub sh -lc \
      "wget -qS -O /dev/null '$BFF$ROUTE' 2>&1 | awk '/HTTP\//{print \$2}' | tail -1" \
      < /dev/null | tr -d '\r')
  fi
  code="${code:-000}"

  local ok=1
  case "$2" in
    401)     [ "$code" = "401" ] && ok=0 ;;
    not401)  [ "$code" != "401" ] && [ "$code" != "000" ] && ok=0 ;;
  esac

  if [ "$ok" -eq 0 ]; then
    printf '   ✅ %-38s HTTP %s\n' "$1" "$code"
  else
    printf '   ❌ %-38s HTTP %s (esperado %s)\n' "$1" "$code" "$2"
    FAILED=$((FAILED + 1))
  fi
}

echo "── o segredo está no ambiente do serviço? ──────────────────────────────────"
HAS=$($DC exec -T mcp-server-plughub sh -lc 'echo "${PLUGHUB_JWT_SECRET:-VAZIO}"' < /dev/null | tr -d '\r')
if [ "$HAS" = "VAZIO" ]; then
  echo "   ⚠️  INCONCLUSIVO: PLUGHUB_JWT_SECRET vazio no container."
  echo "      Os casos 2 e 3 abaixo NÃO podem reprovar sem ele — e um verde aqui"
  echo "      significaria apenas que o fallback de dev está ativo."
  exit 2
fi
echo "   ✅ definido"

echo
echo "── verificação de assinatura e expiração ($ROUTE) ──────────────────────────"
probe "sem token"                401 ""
probe "assinatura FORJADA"       401 "Bearer $(mint 3600 'segredo_errado_qualquer_32ch')"
probe "token EXPIRADO"           401 "Bearer $(mint -60 "$SECRET")"
probe "token VÁLIDO (controle)"  not401 "Bearer $(mint 3600 "$SECRET")"

echo
if [ "$FAILED" -gt 0 ]; then
  echo "── ❌ $FAILED caso(s) falharam ─────────────────────────────────────────────"
  exit 1
fi
echo "── ✅ o BFF verifica assinatura E expiração ────────────────────────────────"
