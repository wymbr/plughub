#!/usr/bin/env bash
# mut_ui_color_scale_classes.sh — bateria de mutação do probe_ui_color_scale_classes.sh (PUI-03)
#
# M0  controle positivo: o gate está VERDE no repositório real (senão as mutações não provam nada)
# M1  classe numerada de família redefinida (`bg-gray-900`) num arquivo → VERMELHO
# M2  com prefixo de variante (`hover:text-red-600`) → VERMELHO
# M3  família NOVA redefinida no config (`blue`) + `text-blue-500` → VERMELHO  (derivação, não lista)
# M4  a mesma `text-blue-500` com o config ORIGINAL → VERDE  (família não redefinida funciona)
# M5  token do tema (`bg-red-light`, `text-muted`) → VERDE  (o gate não recusa o conserto)
# M6  config sem bloco `colors` → INCONCLUSIVO, nunca verde
#
# Entradas de mentira por env (UI_SRC, TW_CONFIG) em diretório temporário; nada no repo é editado.
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu
set -u
cd "$(dirname "$0")/../.." || exit 2
GATE="bash infra/test/probe_ui_color_scale_classes.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM
CFG=packages/platform-ui/tailwind.config.ts

run() { UI_SRC="$1" TW_CONFIG="$2" $GATE >/dev/null 2>&1; echo $?; }
fail=0
check() {  # nome esperado obtido
  if [ "$2" = "$3" ]; then echo "  OK    $1 (rc=$3)"; else echo "  FALHA $1 esperado rc=$2, obtido rc=$3"; fail=1; fi
}

m0=$($GATE >/dev/null 2>&1; echo $?)
if [ "$m0" != 0 ]; then echo "  M0 gate nao esta VERDE no repo (rc=$m0) — mutacoes nao provam nada"; echo " INCONCLUSIVO"; exit 2; fi
echo "  OK    M0 controle positivo (rc=0)"

mk() { mkdir -p "$TMP/$1"; printf '%s\n' "$2" > "$TMP/$1/X.tsx"; echo "$TMP/$1"; }

check "M1 bg-gray-900"            1 "$(run "$(mk m1 '<div className="bg-gray-900" />')" "$CFG")"
check "M2 hover:text-red-600"     1 "$(run "$(mk m2 '<b className="hover:text-red-600" />')" "$CFG")"
sed "s/^\(\s*\)primary:/\1blue: '#0000ff',\n\1primary:/" "$CFG" > "$TMP/blue.config.ts"
grep -q "blue: '#0000ff'" "$TMP/blue.config.ts" || { echo "  M3 nao consegui plantar blue no config"; echo " INCONCLUSIVO"; exit 2; }
check "M3 familia nova (blue)"    1 "$(run "$(mk m3 '<i className="text-blue-500" />')" "$TMP/blue.config.ts")"
check "M4 blue nao redefinida"    0 "$(run "$(mk m4 '<i className="text-blue-500" />')" "$CFG")"
check "M5 tokens do tema"         0 "$(run "$(mk m5 '<p className="bg-red-light text-muted border-green/30" />')" "$CFG")"
printf 'export default { theme: {} }\n' > "$TMP/nocolors.config.ts"
check "M6 config sem colors"      2 "$(run "$(mk m6 '<p className="x" />')" "$TMP/nocolors.config.ts")"

if [ $fail = 0 ]; then echo " TODAS PEGAS"; exit 0; fi
echo " ALGUMA SOBREVIVEU"; exit 1
