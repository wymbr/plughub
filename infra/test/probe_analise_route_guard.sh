#!/usr/bin/env bash
# probe_analise_route_guard.sh — GATE estrutural do furo 3 (2026-08-27).
#
# O defeito, em duas direcoes opostas:
#   · NAVEGACAO restritiva demais — o grupo `analise` do `Sidebar.tsx` declarava
#     `roles: ['supervisor','admin','business']` A MONTANTE da ABAC. Hardcoded, nao
#     editavel pela tela de Acesso, e o `operator` (que TEM `contacts.visualizar` no
#     seed) nunca alcancava o menu porque o papel falhava antes de a ABAC ser lida.
#     Violava "Every config field is UI-editable" e esvaziava o `module_config`.
#   · ROTA permissiva demais — `app/routes.tsx` registrava `analise/*` NUAS. O papel
#     escondia o MENU; digitar a URL entrava.
#
# ⚠️ Este gate NAO afirma que ha autorizacao. A fronteira de dados e o escopo de pool
# no backend; os ~8 endpoints de conteudo da (d) seguem sem portao. O que ele fixa e
# que MENU e ROTA respondem a MESMA regra, e que ela e a ABAC — nao o papel.
#
# Roda do HOST, so le arquivo. Como fica vermelho: `git stash` das mudancas de
# 2026-08-27 e rodar — a secao A acha o `roles:` e a B acha 10 rotas nuas.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

SIDEBAR=packages/platform-ui/src/shell/Sidebar.tsx
ROUTES=packages/platform-ui/src/app/routes.tsx
PERMS=packages/platform-ui/src/lib/permissions.ts

fail=0
ok()   { printf '  ✅ %s\n' "$*"; }
bad()  { printf '  ❌ %s\n' "$*"; fail=1; }
inc()  { printf '  ⚠️  INCONCLUSIVO: %s\n' "$*"; fail=1; }   # inconclusivo NAO sai OK

for f in "$SIDEBAR" "$ROUTES" "$PERMS"; do
  [ -f "$f" ] || { inc "arquivo ausente: $f — o inventario esta desatualizado"; }
done
[ "$fail" -eq 1 ] && { echo "RESULTADO: FALHOU (preflight)"; exit 1; }

echo "── A. o grupo \`analise\` nao pode ter portao de PAPEL ──"
# bloco do grupo: da linha do navKey ate a linha do `children:`
BLOCO=$(awk "/navKey: 'analise'/,/children:/" "$SIDEBAR")
if [ -z "$BLOCO" ]; then
  inc "nao achei o grupo \`analise\` no Sidebar — derivacao quebrada, nao 'esta limpo'"
elif echo "$BLOCO" | grep -qE "^\s*roles:\s*\["; then
  bad "o grupo \`analise\` AINDA declara \`roles:\` a montante da ABAC"
else
  ok "grupo \`analise\` sem \`roles:\` — quem decide e o grant de cada filho"
fi

echo "── A'. TESTEMUNHA: o arquivo e o que penso (nao passei por ausencia) ──"
# ⚠️ ATE 2026-08-27 esta testemunha era outra: contava quantos OUTROS grupos ainda
# tinham \`roles:\`, para provar que a ausencia no \`analise\` significava algo. Era
# correta enquanto a remocao era PARCIAL. O passo 5 removeu os 7 portoes, e a
# comparacao perdeu referencia — ficaria vermelha para sempre por um NAO-defeito, que
# e a forma mais rapida de ensinar a ignorar o vermelho.
#
# A proposicao "nenhum grupo tem portao de papel" mudou de casa: vive em
# \`probe_nav_grant_first.sh\` (S1), com testemunha de presenca propria. Aqui basta
# provar que o arquivo tem a forma esperada, senao a secao A passaria por vacuidade.
N_ABAC=$(grep -c "abac:" "$SIDEBAR")
N_ROLES=$(grep -cE "^\s*roles:\s*\[" "$SIDEBAR" || true)
if [ "${N_ABAC:-0}" -lt 10 ]; then
  inc "so $N_ABAC regras \`abac:\` no Sidebar — o arquivo nao e o que penso"
elif [ "${N_ROLES:-0}" -ne 0 ]; then
  bad "$N_ROLES portao(oes) de papel de volta no Sidebar (ver probe_nav_grant_first.sh)"
else
  ok "$N_ABAC regras \`abac:\` e zero \`roles:\` — estado-alvo do passo 5"
fi

echo "── B. toda rota de PAGINA sob \`analise/\` tem guard ──"
# Derivacao: linhas de rota `analise/...` que renderizam uma PAGINA (nao Navigate,
# nao RedirectPreservingQuery — redirect nao mostra dado, e exigir guard nele daria
# falso positivo).
mapfile -t LINHAS < <(grep -nE "path: 'analise/[a-z-]+'" "$ROUTES" \
                      | grep -v "Navigate" | grep -v "RedirectPreservingQuery")
if [ "${#LINHAS[@]}" -eq 0 ]; then
  inc "zero rotas de pagina sob \`analise/\` — a derivacao quebrou (havia 10)"
else
  nuas=0
  for l in "${LINHAS[@]}"; do
    echo "$l" | grep -q "RequireAbac" || { bad "rota NUA: ${l%%:*} → $(echo "$l" | grep -oE "path: '[^']+'")"; nuas=$((nuas+1)); }
  done
  [ "$nuas" -eq 0 ] && ok "${#LINHAS[@]} rotas de pagina, todas com \`RequireAbac\`"
fi

echo "── B'. TESTEMUNHA NEGATIVA: redirect NAO deve ser envolvido ──"
# Se o gate casasse tudo por atacado, envolver redirects passaria despercebido e a
# secao B viraria "achei RequireAbac em algum lugar", nao "cada pagina tem o seu".
REDIR=$(grep -E "path: 'analise/[a-z-]+'" "$ROUTES" | grep -E "Navigate|RedirectPreservingQuery")
if [ -z "$REDIR" ]; then
  inc "nenhum redirect \`analise/*\` encontrado — sem ele a secao B nao discrimina"
elif echo "$REDIR" | grep -q "RequireAbac"; then
  bad "um redirect foi envolvido em \`RequireAbac\` — guard no lugar errado"
else
  ok "$(echo "$REDIR" | wc -l) redirects, nenhum envolvido"
fi

echo "── C. a regra tem UMA casa, consumida pelos dois ──"
grep -q "export function passesAbacRule" "$PERMS" \
  && ok "\`passesAbacRule\` declarada em lib/permissions.ts" \
  || bad "\`passesAbacRule\` ausente — a regra voltou a ter dono local"
grep -q "passesAbacRule" "$SIDEBAR" \
  && ok "Sidebar consome o predicado compartilhado" \
  || bad "Sidebar reimplementou a decisao — duas portas, uma regra"
grep -q "passesAbacRule" packages/platform-ui/src/auth/RequireEvalAccess.tsx \
  && ok "guard de rota consome o MESMO predicado" \
  || bad "o guard reimplementou a decisao — menu e rota podem divergir"

echo
if [ "$fail" -eq 0 ]; then echo "RESULTADO: OK"; exit 0
else echo "RESULTADO: FALHOU"; exit 1; fi
