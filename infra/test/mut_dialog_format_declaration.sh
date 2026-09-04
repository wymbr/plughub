#!/usr/bin/env bash
# mut_dialog_format_declaration.sh — falseabilidade do probe da F3.
#
# ⚠️ **Um ramo é falseado de forma mais fraca que os outros, e isso está dito.**
# O ramo A pergunta à IMAGEM em execução; refutá-lo de verdade exigiria
# reconstruir o agent-registry com o schema velho (minutos). O que a M1 falseia é
# o DISCRIMINADOR (`hasOwnProperty('pattern')` sabe distinguir os dois mundos),
# rodando o mesmo JS contra o `dist` LOCAL com `pattern` restaurado. Isso não
# prova o caminho do container — e o que cobre essa metade é o A', que reprova
# se o probe estiver lendo nada.
#
# M2/M3 usam `docker cp` + `restart`, que é o caminho de iteração efêmera
# documentado no CLAUDE.md (`cp` sobrevive a `restart`, nunca a `up -d`).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

RAIZ="$PWD"
G="infra/test/probe_dialog_format_declaration.sh"
SKILL="packages/schemas/src/skill.ts"
GUARD="packages/dialog-api/src/plughub_dialog_api/format_guard.py"
EDITOR="packages/platform-ui/src/modules/dialog-forms/DialogFormsPage.tsx"
DLG_CT="${DLG_CT:-plughub-demo-dialog-api-1}"
FALHAS=0

echo "falseabilidade — probe_dialog_format_declaration"
echo

# ── M1: o DISCRIMINADOR de `pattern` sabe distinguir os dois mundos ──────────
cp "$SKILL" "$SKILL.bak"
python3 - <<'PY'
import io
p = "packages/schemas/src/skill.ts"
t = io.open(p, encoding="utf-8").read()
a = "  numeric:    z.boolean().optional(),\n  min_length:"
assert a in t, "ancora M1 nao encontrada"
io.open(p, "w", encoding="utf-8", newline="").write(
    t.replace(a, "  numeric:    z.boolean().optional(),\n  pattern:    z.string().optional(),\n  min_length:", 1))
PY
docker run --rm -v "$RAIZ:/w" -w /w/packages/schemas node:20-alpine \
  node node_modules/typescript/bin/tsc >/dev/null 2>&1

JS=$(mktemp)
cat > "$JS" <<'NODE'
const S = require('/w/packages/schemas/dist/index.js')
const r = S.MenuStepSchema.safeParse({
  id: 'c', type: 'menu', prompt: 'p', interaction: 'text',
  on_success: 'a', on_failure: 'b', timeout_s: 30,
  validation: { format: 'date_br', pattern: '^[0-9]{6}$', max_length: 10 },
})
console.log(r.success
  ? String(Object.prototype.hasOwnProperty.call(r.data.validation || {}, 'pattern'))
  : 'PARSE_FALHOU')
NODE
COM=$(docker run --rm -v "$RAIZ:/w" -v "$JS:/probe.js" -w /w node:20-alpine node /probe.js 2>&1 | tail -1)

cp "$SKILL.bak" "$SKILL"; rm -f "$SKILL.bak"
docker run --rm -v "$RAIZ:/w" -w /w/packages/schemas node:20-alpine \
  node node_modules/typescript/bin/tsc >/dev/null 2>&1
SEM=$(docker run --rm -v "$RAIZ:/w" -v "$JS:/probe.js" -w /w node:20-alpine node /probe.js 2>&1 | tail -1)
rm -f "$JS"

if [ "$COM" = "true" ] && [ "$SEM" = "false" ]; then
  echo "  OK        M1 discriminador separa os dois mundos (com pattern=$COM, sem=$SEM)"
else
  echo "  FALHOU    M1 o discriminador não distingue (com=$COM, sem=$SEM)"
  FALHAS=$((FALHAS + 1))
fi

# ── M2: o guarda neutralizado — o ramo C tem de reprovar ─────────────────────
cp "$GUARD" "$GUARD.bak"
python3 - <<'PY'
import io
p = "packages/dialog-api/src/plughub_dialog_api/format_guard.py"
t = io.open(p, encoding="utf-8").read()
a = "    mapa = _formato_por_tipo()"
assert a in t
io.open(p, "w", encoding="utf-8", newline="").write(
    t.replace(a, "    return []  # MUTACAO M2\n" + a, 1))
PY
docker cp "$GUARD" "$DLG_CT:/app/packages/dialog-api/src/plughub_dialog_api/format_guard.py" >/dev/null
docker restart "$DLG_CT" >/dev/null; sleep 8
OUT=$(bash "$G" 2>&1); RC=$?
if [ "$RC" != "0" ] && printf '%s' "$OUT" | grep -qE 'REPROVA +C\. '; then
  echo "  OK        M2 guarda neutralizado -> ramo C reprovou"
else
  echo "  FALHOU    M2 (rc=$RC) — o gate não notou o guarda inerte"
  printf '%s' "$OUT" | grep -E 'REPROVA|INCONCLUSIVO' | head -3 | sed 's/^/       /'
  FALHAS=$((FALHAS + 1))
fi

# ── M3: o guarda recusando SEMPRE — o controle positivo tem de reprovar ──────
cp "$GUARD.bak" "$GUARD"
python3 - <<'PY'
import io
p = "packages/dialog-api/src/plughub_dialog_api/format_guard.py"
t = io.open(p, encoding="utf-8").read()
a = "    mapa = _formato_por_tipo()"
assert a in t
io.open(p, "w", encoding="utf-8", newline="").write(
    t.replace(a, '    return ["MUTACAO M3"]\n' + a, 1))
PY
docker cp "$GUARD" "$DLG_CT:/app/packages/dialog-api/src/plughub_dialog_api/format_guard.py" >/dev/null
docker restart "$DLG_CT" >/dev/null; sleep 8
OUT=$(bash "$G" 2>&1); RC=$?
if [ "$RC" != "0" ] && printf '%s' "$OUT" | grep -qE "REPROVA +C'\."; then
  echo "  OK        M3 guarda recusando tudo -> controle positivo C' reprovou"
else
  echo "  FALHOU    M3 (rc=$RC) — o gate passaria por um guarda que recusa qualquer forma"
  printf '%s' "$OUT" | grep -E 'REPROVA|INCONCLUSIVO' | head -3 | sed 's/^/       /'
  FALHAS=$((FALHAS + 1))
fi

cp "$GUARD.bak" "$GUARD"; rm -f "$GUARD.bak"
docker cp "$GUARD" "$DLG_CT:/app/packages/dialog-api/src/plughub_dialog_api/format_guard.py" >/dev/null
docker restart "$DLG_CT" >/dev/null; sleep 8

# ── M4: o campo livre de regex de volta no editor ────────────────────────────
cp "$EDITOR" "$EDITOR.bak"
python3 - <<'PY'
import io
p = "packages/platform-ui/src/modules/dialog-forms/DialogFormsPage.tsx"
t = io.open(p, encoding="utf-8").read()
a = "  const setOptions = (options: DialogOption[]) => onChange({ ...node, options })"
assert a in t
io.open(p, "w", encoding="utf-8", newline="").write(
    t.replace(a, "  const _m4 = node.validation?.pattern  // MUTACAO M4\n" + a, 1))
PY
OUT=$(bash "$G" 2>&1); RC=$?
if [ "$RC" != "0" ] && printf '%s' "$OUT" | grep -qE 'REPROVA +D\.'; then
  echo "  OK        M4 campo livre de regex de volta -> ramo D reprovou"
else
  echo "  FALHOU    M4 (rc=$RC) — o gate não vê a regex voltar ao editor"
  FALHAS=$((FALHAS + 1))
fi
cp "$EDITOR.bak" "$EDITOR"; rm -f "$EDITOR.bak"

echo
OUT=$(bash "$G" 2>&1); RC=$?
if [ "$RC" = "0" ]; then
  echo "restaurado: gate VERDE"
else
  echo "restaurado: gate NÃO voltou ao verde (rc=$RC)"
  printf '%s' "$OUT" | tail -8
  FALHAS=$((FALHAS + 1))
fi

echo
if [ "$FALHAS" -gt 0 ]; then echo "BATERIA REPROVADA ($FALHAS)"; exit 1; fi
echo "BATERIA OK — 4 mutações, 4 ramos reprovando (A por discriminador; ver cabeçalho)"
