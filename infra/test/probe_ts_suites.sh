#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_ts_suites — as suites TypeScript rodam, e rodam a partir da IMAGEM
#
# Gemeo do `probe_python_suites.sh`, e existe pela mesma razao medida do outro lado
# do monorepo. O que motivou este aqui (AUT-24, 2026-08-31) foi pior que suite que
# nao roda: eram suites que RODAVAM e estavam VERMELHAS ha tempo indeterminado —
# `agent-registry` 6/33 e `mcp-server-plughub` 1/222.
#
#   Custo medido no MESMO dia: precisando saber se um conserto tinha quebrado algo,
#   foi preciso rodar cada pacote DUAS vezes — uma com a versao do `HEAD`, outra com
#   a nova — so para descobrir que o vermelho era herdado. Suite vermelha por default
#   deixa de ser instrumento de regressao e vira ritual manual.
#
#   E ela ESCONDE: as 6 falhas do `agent-registry` eram `401` do portao de
#   autorizacao, e atras delas havia TRES falhas de outra natureza (mocks velhos:
#   `publishRegistryChanged` e `poolSkillSlot` faltando) que so apareceram quando o
#   401 saiu. Vermelho guarda vermelho de naturezas diferentes.
#
# ⚠️ ARMADILHA que este gate evita, e que o `agent-registry` provou: o veredicto de
#   uma suite NAO PODE depender de ambiente nao declarado. Aquelas 6 assercoes
#   passavam na maquina de quem nao exporta `PLUGHUB_JWT_SECRET` (o gate de auth vira
#   no-op e a suite fica verde **sem exercer o portao**) e falhavam no container, onde
#   o segredo existe. Os dois estados errados, e o verde era o pior. Por isso o teste
#   passou a DECLARAR o proprio env (`vi.hoisted`), e por isso este gate roda a partir
#   da IMAGEM: e o unico ambiente reprodutivel.
#
#   O que o gate julga, em proposicoes SEPARADAS:
#     A. EXECUCAO — cada suite alcancavel roda e nao pode ter teste vermelho
#     B. COBERTURA — pacote que DECLARA testes e nao tem runner na imagem e NOMEADO,
#        nunca omitido. Declarar sem poder rodar e promessa sem mecanismo.
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO (pre-condicao falhou).
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }
PREFIX="${PREFIX:-plughub-demo}"

# pacote:container — o container e onde a IMAGEM daquele pacote esta montada.
# Manter esta tabela e obrigacao: pacote novo com testes entra aqui ou aparece no ramo B.
ALVOS="
agent-registry:agent-registry
mcp-server-plughub:mcp-server-plughub
skill-flow-engine:skill-flow-service
schemas:skill-flow-service
"

FAIL=0
TOTAL=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FAIL=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }

command -v docker >/dev/null || inc "docker ausente"
docker ps >/dev/null 2>&1   || inc "docker daemon nao responde"

echo "== probe_ts_suites =="
echo
echo "A. EXECUCAO — cada suite roda a partir da imagem"

for alvo in $ALVOS; do
  pkg="${alvo%%:*}"; svc="${alvo##*:}"
  c="${PREFIX}-${svc}-1"
  docker ps --format '{{.Names}}' | grep -qx "$c" || inc "container ausente: $c (stack no ar?)"
  if ! docker exec "$c" sh -c "[ -x /app/packages/$pkg/node_modules/.bin/vitest ]" 2>/dev/null; then
    bad "$pkg: vitest NAO esta na imagem de $svc — a suite nao pode ser executada"
    continue
  fi
  # WORKDIR do proprio pacote: rodar da raiz do monorepo troca o rootdir e leva a
  # config do vitest junto. Foi assim que o gemeo Python produziu 476 falsos vermelhos.
  OUT="$(docker exec "$c" sh -c "cd /app/packages/$pkg && ./node_modules/.bin/vitest run 2>&1" 2>/dev/null)"
  LINHA="$(printf '%s' "$OUT" | grep -E '^ +Tests +' | tail -1)"
  if [ -z "$LINHA" ]; then
    bad "$pkg: vitest nao produziu linha de sumario — execucao abortou"
    printf '%s\n' "$OUT" | tail -5 | sed 's/^/               /'
    continue
  fi
  N_FAIL="$(printf '%s' "$LINHA" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || true)"
  N_PASS="$(printf '%s' "$LINHA" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo 0)"
  TOTAL=$((TOTAL + N_PASS))
  if [ -n "${N_FAIL:-}" ] && [ "$N_FAIL" -gt 0 ]; then
    bad "$pkg: $N_FAIL vermelho(s), $N_PASS verde(s)"
    printf '%s' "$OUT" | grep -E '^ +× ' | head -8 | sed 's/^/               /'
  else
    ok "$pkg: $N_PASS testes, nenhum vermelho"
  fi
done

echo
echo "B. COBERTURA — pacote que declara testes e nao pode rodar e NOMEADO"
DECLARAM=""
for d in packages/*/; do
  pkg="$(basename "$d")"
  [ -f "$d/package.json" ] || continue
  grep -q '"test"' "$d/package.json" 2>/dev/null || continue
  [ "$(find "$d/src" -name '*.test.ts' -o -name '*.test.tsx' 2>/dev/null | wc -l)" -gt 0 ] || continue
  printf '%s\n' "$ALVOS" | grep -q "^${pkg}:" && continue
  DECLARAM="$DECLARAM $pkg"
done
if [ -n "$DECLARAM" ]; then
  # NAO e vermelho: e divida NOMEADA. Reprovar aqui faria o gate nascer vermelho e
  # ensinaria a ignora-lo — o defeito que o gemeo Python descreve em maiusculas.
  printf '  \033[33mNOMEADOS\033[0m     sem runner na imagem (divida, nao defeito):%s\n' "$DECLARAM"
  echo "               \`sdk\` e \`gitagent\` nao tem container; \`mcp-server-knowledge\` tem"
  echo "               container mas a imagem nao traz vitest — o mesmo 'declara e nao"
  echo "               instala' que o gemeo Python mediu nas 14 imagens."
else
  ok "todo pacote que declara testes tem runner na imagem"
fi

echo
echo "====================="
[ "$FAIL" -eq 0 ] && { echo "VERDE — $TOTAL testes TS verdes a partir da imagem"; exit 0; }
echo "VERMELHO"; exit 1
