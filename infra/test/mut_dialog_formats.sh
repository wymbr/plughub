#!/usr/bin/env bash
# mut_dialog_formats.sh — falseabilidade do probe_dialog_formats_parity.
#
# Um gate que nunca reprova compra confiança sem dar nada. Cada mutação abaixo
# quebra UMA proposição e o gate tem de ficar vermelho por ELA — não por outra.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

TS="packages/schemas/src/dialog-format.ts"
GEMEO="packages/py-contextstore/src/plughub_contextstore/dialog_formats.py"
G="infra/test/probe_dialog_formats_parity.sh"
FALHAS=0

cp "$TS" "$TS.bak"; cp "$GEMEO" "$GEMEO.bak"
restaura() {
  cp "$TS.bak" "$TS"; cp "$GEMEO.bak" "$GEMEO"
}

roda() { bash "$G" 2>&1; }

# Espera: o gate reprova E o ramo $2 aparece como REPROVA.
julga() {
  local nome="$1" ramo="$2" out="$3" rc="$4"
  if [ "$rc" = "0" ]; then
    echo "  FALHOU    $nome — gate ficou VERDE com a mutação aplicada"
    FALHAS=$((FALHAS+1)); return
  fi
  if printf '%s' "$out" | grep -qE "REPROVA +$ramo\."; then
    echo "  OK        $nome -> ramo $ramo reprovou (rc=$rc)"
    printf '%s' "$out" | grep -E "REPROVA +$ramo\." | head -1 | sed 's/^/       /'
  else
    echo "  PARCIAL   $nome — gate reprovou (rc=$rc) mas NÃO pelo ramo $ramo"
    printf '%s' "$out" | grep -E 'REPROVA|INCONCLUSIVO' | head -3 | sed 's/^/       /'
    FALHAS=$((FALHAS+1))
  fi
}

echo "falseabilidade — probe_dialog_formats_parity"
echo

# ── M1: alguém edita o GERADO à mão (o cenário que o gate existe para pegar) ──
sed -i 's|"\^\[0-9\]+\$"|"^[0-9x]+$"|' "$GEMEO"
OUT=$(roda); RC=$?
julga "M1 gerado editado à mão" "A" "$OUT" "$RC"
restaura

# ── M2: from_masked_type apontando para tipo que não existe ──────────────────
sed -i 's|from_masked_type: "cpf"|from_masked_type: "cpf_que_nao_existe"|' "$TS"
docker run --rm -v "$PWD:/w" -w /w/packages/schemas node:20-alpine \
  node_modules/.bin/vite-node ../../infra/scripts/gen_dialog_formats_py.ts >/dev/null 2>&1
OUT=$(roda); RC=$?
julga "M2 referência pendurada" "C" "$OUT" "$RC"
# O ramo E (oráculo TS) também pega este caso — e isso é bom, não redundância
# ruim: C julga contra o catálogo VIVO e E contra o default embutido. São
# populações diferentes, e só C pega o tenant que editou `masking.types`.
printf '%s' "$OUT" | grep -qE 'REPROVA +E\.' \
  && echo "       (o ramo E também reprovou — julga o default, C julga o vivo)"
restaura

# ── M3: shape sem âncora — finder disfarçado de validador ────────────────────
sed -i 's|shape: "\^\[0-9\]+\$"|shape: "[0-9]+"|' "$TS"
docker run --rm -v "$PWD:/w" -w /w/packages/schemas node:20-alpine \
  node_modules/.bin/vite-node ../../infra/scripts/gen_dialog_formats_py.ts >/dev/null 2>&1
OUT=$(roda); RC=$?
julga "M3 shape sem âncora" "E" "$OUT" "$RC"
restaura

# ── M4: catálogo esvaziado — a testemunha de presença ────────────────────────
python3 - <<'PY'
import io, re
p = "packages/schemas/src/dialog-format.ts"
t = io.open(p, encoding="utf-8").read()
i = t.index("export const DEFAULT_DIALOG_FORMAT_CATALOG")
j = t.index("// ─────────────────────────────────────────────\n// Primitivas semânticas", i)
io.open(p, "w", encoding="utf-8", newline="").write(
    t[:i] + "export const DEFAULT_DIALOG_FORMAT_CATALOG: DialogFormatCatalog = { formats: [] }\n\n" + t[j:]
)
PY
docker run --rm -v "$PWD:/w" -w /w/packages/schemas node:20-alpine \
  node_modules/.bin/vite-node ../../infra/scripts/gen_dialog_formats_py.ts >/dev/null 2>&1
OUT=$(roda); RC=$?
julga "M4 catálogo esvaziado" "B" "$OUT" "$RC"
restaura

rm -f "$TS.bak" "$GEMEO.bak"

echo
OUT=$(roda); RC=$?
if [ "$RC" = "0" ]; then
  echo "restaurado: gate VERDE"
else
  echo "restaurado: gate NÃO voltou ao verde (rc=$RC)"
  printf '%s' "$OUT" | tail -8
  FALHAS=$((FALHAS+1))
fi

echo
[ "$FALHAS" -gt 0 ] && { echo "BATERIA REPROVADA ($FALHAS)"; exit 1; }
echo "BATERIA OK — os 4 ramos sabem reprovar"
