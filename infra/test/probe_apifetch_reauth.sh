#!/usr/bin/env bash
# ==============================================================================
# probe_apifetch_reauth.sh — o re-auth reativo do browser (AUT-19)
# ==============================================================================
#
# METADE CLIENTE da AUT-19. A metade servidor (401 nas rotas operacionais) e medida por
# `probe_ts_scope_resolvers.sh`; as duas juntas sao UMA decisao, porque 401 sem
# renovacao reativa troca "Monitor vazio" por "Monitor quebrado".
#
# POR QUE COMPILAR EM VEZ DE TESTAR NO LUGAR. O `platform-ui` nao tem framework de teste
# nenhum — medido em 2026-08-31: sem vitest, sem jest, zero `*.test.*`. Acrescentar um e
# decisao maior que esta tarefa. Mas ficar sem instrumento seria pior, porque o modo de
# falha do single-flight e SILENCIOSO: o refresh token e ROTATIVO, entao N renovacoes
# concorrentes se invalidam e a sessao morre exatamente quando tenta se salvar — na tela
# isso aparece como "deslogou sozinho", que ninguem liga a este codigo.
#
# Aqui compilamos os DOIS ARQUIVOS DE PRODUCAO (nao copias mantidas a mao) e os
# exercemos contra um `fetch` de mentira. Unica adaptacao: o import de alias
# `@/auth/token-store` vira relativo, porque o alias e do bundler.
#
# ⚠️ Este portao NAO substitui `tsc --noEmit` do pacote. Ele compila dois arquivos; o
# defeito que a AUT-18 deixou em 2026-08-30 (helper local homonimo + auto-recursao em
# QUATRO arquivos, 34 erros, build do platform-ui parado) so aparece typechecando o
# pacote inteiro. Por isso o ramo C existe.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

UI="packages/platform-ui"
TSC="$UI/node_modules/typescript/bin/tsc"
SRC_API="$UI/src/api/apiFetch.ts"
SRC_TOK="$UI/src/auth/token-store.ts"
HARNESS="infra/test/_apifetch_reauth_harness.mjs"

command -v node >/dev/null || { echo "INCONCLUSIVO: node ausente"; exit 2; }
for f in "$TSC" "$SRC_API" "$SRC_TOK" "$HARNESS"; do
  [ -f "$f" ] || { echo "INCONCLUSIVO: ausente: $f"; exit 2; }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Alias do bundler -> import relativo. E a UNICA adaptacao; se ela crescer, o teste
# deixou de exercer o arquivo de producao e passa a exercer uma copia.
sed "s|'@/auth/token-store'|'./token-store'|" "$SRC_API" > "$TMP/apiFetch.ts"
cp "$SRC_TOK" "$TMP/token-store.ts"

FAIL=0

# ─────────────────────────────────────────────────── ramo A: compila?
if ! node "$TSC" "$TMP/apiFetch.ts" "$TMP/token-store.ts" \
      --outDir "$TMP/out" --module es2022 --target es2022 \
      --moduleResolution bundler --strict --skipLibCheck >"$TMP/tsc.log" 2>&1; then
  echo "A. VERMELHO — apiFetch/token-store nao compilam:"
  sed 's/^/   /' "$TMP/tsc.log" | head -20
  echo "VERMELHO"; exit 1
fi
# Node so trata .mjs/.js-com-type-module como ESM; os artefatos saem .js.
for f in "$TMP/out"/*.js; do mv "$f" "${f%.js}.mjs"; done
sed -i "s|from './token-store'|from './token-store.mjs'|" "$TMP/out/apiFetch.mjs"
echo "A. verde — os dois arquivos de producao compilam em strict"

# ─────────────────────────────────────────────────── ramo B: comportamento
if node "$HARNESS" "$TMP/out"; then
  echo "B. verde — sete casos de comportamento"
else
  echo "B. VERMELHO — comportamento do re-auth reativo"
  FAIL=1
fi

# ─────────────────────────────────────────────────── ramo C: o pacote INTEIRO
# Existe porque o ramo A nao pega colisao de nome entre um import novo e um helper
# local homonimo — foi assim que a varredura da AUT-18 quebrou quatro arquivos e o
# build do platform-ui sem nada ficar vermelho (o `vite dev` nao typecheca).
N="$(cd "$UI" && node node_modules/typescript/bin/tsc --noEmit 2>&1 | grep -c 'error TS' || true)"
if [ "$N" -eq 0 ]; then
  echo "C. verde — \`tsc --noEmit\` do platform-ui inteiro: 0 erros"
else
  echo "C. VERMELHO — platform-ui nao typecheca: $N erro(s). Rode: (cd $UI && npx tsc --noEmit)"
  FAIL=1
fi

echo "=========================="
[ "$FAIL" -eq 0 ] && { echo "VERDE"; exit 0; }
echo "VERMELHO"; exit 1
