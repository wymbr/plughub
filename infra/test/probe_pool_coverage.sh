#!/usr/bin/env bash
# ==============================================================================
# probe_pool_coverage.sh — pool sem vigia e ALARME; "preso a um" e DADO (AUT-14)
# ==============================================================================
#
# Compila `access/pool-coverage.ts` — o arquivo de PRODUCAO — e o exerce. Mesma tecnica
# do `probe_apifetch_reauth.sh`, e pela mesma razao: o `platform-ui` nao tem framework
# de teste (zero `*.test.*`), e ficar sem instrumento seria pior que a tecnica ser
# incomum.
#
# POR QUE UM CALCULO DE TELA MERECE PORTAO. Porque ele erra CALADO nas duas direcoes:
#   · contar usuario INATIVO faz um pool orfao parecer coberto — e o sintoma do orfao ja
#     era AUSENCIA (a linha que ninguem ve), entao o alarme quebrado nao deixa rastro;
#   · avisar sobre orfao PREEXISTENTE ao desativar alguem sem relacao com ele culpa a
#     mudanca errada; o aviso passa a aparecer sempre, e aviso que sempre aparece e
#     aviso que alguem desliga.
#
# ⚠️ O que este portao NAO julga: se o pool orfao importa. Ele conta EXPOSICAO. A
# decisao do dono (2026-08-31) foi que so ZERO usuarios e alarme — "preso a UM" e dado,
# nunca emblema —, justamente para nao repetir a D14.1 do `CLAUDE.md`: publicar
# exposicao como se fosse dano. Na instalacao medida, "preso a um" seriam 31 de 36.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

UI="packages/platform-ui"
TSC="$UI/node_modules/typescript/bin/tsc"
SRC="$UI/src/modules/access/pool-coverage.ts"
HARNESS="infra/test/_pool_coverage_harness.mjs"

command -v node >/dev/null || { echo "INCONCLUSIVO: node ausente"; exit 2; }
for f in "$TSC" "$SRC" "$HARNESS"; do
  [ -f "$f" ] || { echo "INCONCLUSIVO: ausente: $f"; exit 2; }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp "$SRC" "$TMP/pool-coverage.ts"

if ! node "$TSC" "$TMP/pool-coverage.ts" --outDir "$TMP/out" \
      --module es2022 --target es2022 --moduleResolution bundler \
      --strict --noUncheckedIndexedAccess --skipLibCheck >"$TMP/tsc.log" 2>&1; then
  echo "A. VERMELHO — pool-coverage.ts nao compila em strict:"
  sed 's/^/   /' "$TMP/tsc.log" | head -20
  echo "VERMELHO"; exit 1
fi
for f in "$TMP/out"/*.js; do mv "$f" "${f%.js}.mjs"; done
echo "A. verde — compila em strict (com noUncheckedIndexedAccess)"

if node "$HARNESS" "$TMP/out"; then
  echo "B. verde — onze casos de comportamento"
  echo "=========================="
  echo "VERDE"; exit 0
fi
echo "B. VERMELHO — comportamento do censo de cobertura"
echo "=========================="
echo "VERMELHO"; exit 1
