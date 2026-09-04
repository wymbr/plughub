#!/bin/sh
# Mutacao do filtro JA DEPLOYADO: o masker deixa de ser chamado.
# Roda DENTRO do container (imagem node, sem python). Restaurar com `up -d`.
set -e
F=/app/packages/skill-flow-engine/dist/ctx-audit.js
ALVO='(0, schemas_1.applyMaskingTypeToValue)(String(valor), mascara)'

grep -qF "$ALVO" "$F" || { echo "ANCORA AUSENTE — a mutacao nao descreve o codigo"; exit 1; }

node -e '
const fs = require("fs")
const p = process.argv[1]
const a = "(0, schemas_1.applyMaskingTypeToValue)(String(valor), mascara)"
const t = fs.readFileSync(p, "utf-8")
if (!t.includes(a)) { console.error("ANCORA AUSENTE no node"); process.exit(1) }
fs.writeFileSync(p, t.replace(a, "String(valor)"))
' "$F"

grep -qF "$ALVO" "$F" && { echo "MUTACAO NAO APLICADA"; exit 1; }
echo "MUTADO: o masker nao e mais chamado"
