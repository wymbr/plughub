#!/bin/sh
# Mutacao da REDE no dist DEPLOYADO. Restaurar com `up -d`.
#
# M1 desliga a rede  -> o cartao em texto livre sai inteiro ao cliente
# M2 quebra o limite -> a rede passa a rodar para o SISTEMA (quebraria o invoke)
set -e
F=/app/packages/skill-flow-engine/dist/ctx-audit.js
case "$1" in
  M1) A='if (r.categories.length === 0)'; B='if (true)' ;;
  M2) A='if (plateia !== "customer" && plateia !== "operator")'; B='if (false)' ;;
  *)  echo "uso: $0 M1|M2"; exit 2 ;;
esac

grep -qF "$A" "$F" || { echo "ANCORA AUSENTE ($1) — a mutacao nao descreve o codigo"; exit 1; }
node -e '
const fs = require("fs")
const [p, a, b] = process.argv.slice(1)
const t = fs.readFileSync(p, "utf-8")
if (!t.includes(a)) { console.error("ANCORA AUSENTE no node"); process.exit(1) }
fs.writeFileSync(p, t.replace(a, b))
' "$F" "$A" "$B"
grep -qF "$A" "$F" && { echo "MUTACAO NAO APLICADA ($1)"; exit 1; }
echo "MUTADO $1"
