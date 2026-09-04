#!/usr/bin/env bash
# probe_dialog_format_surfaces.sh — o gate que CARREGA o arco do catálogo.
#
# A §D2 aceita três interpretadores porque as três superfícies não compartilham
# import (engine em Node · Console em React · `<script>` inline servido por
# Python). O que separa isso do `evaluateAskWhen` triplicado é ESTE gate: as três
# implementações são interrogadas com os MESMOS vetores, que vivem no catálogo e
# não no teste.
#
# Sem ele, a duplicação declarada vira, com o tempo, exatamente a que a casa
# passou meses caçando: três respostas para a mesma pergunta, e a mais permissiva
# valendo.
#
#   A  schemas   (`validateDialogFormat`, a implementação canônica)
#   B  Console   (`format-interpreter.ts`, transpilado)
#   C  página    (`<script>` extraído entre os marcadores)
#   D  as três CONCORDAM, vetor a vetor
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

RAIZ="$PWD"
WEB="packages/channel-gateway/src/plughub_channel_gateway/survey_web.py"
FALHAS=0
INCONCLUSIVOS=0

ok()  { echo "  OK           $*"; }
bad() { echo "  REPROVA      $*"; FALHAS=$((FALHAS+1)); }
inc() { echo "  INCONCLUSIVO $*"; INCONCLUSIVOS=$((INCONCLUSIVOS+1)); }

echo "== probe_dialog_format_surfaces =="
echo

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── extrai o interpretador da PÁGINA (entre os marcadores) ───────────────────
python3 - "$WEB" "$TMP/pagina.js" <<'PY'
import io, sys
src, dst = sys.argv[1], sys.argv[2]
t = io.open(src, encoding="utf-8").read()
a = t.index("// <<<FORMAT-INTERPRETER>>>")
b = t.index("// <<<END-FORMAT-INTERPRETER>>>")
corpo = t[a:b]
io.open(dst, "w", encoding="utf-8", newline="").write(
    corpo + "\nmodule.exports = { julgaFormato, julgaDeclaracao, aplicaMascara, setFormats: function (f) { FORMATS = f } }\n")
PY
[ -s "$TMP/pagina.js" ] || { inc "não consegui extrair o interpretador da página"; }

# ── transpila o interpretador do CONSOLE ─────────────────────────────────────
cp packages/platform-ui/src/modules/dialog-forms/format-interpreter.ts "$TMP/console.ts"
# O import de tipo é elidido pelo tsc; `--noResolve` evita puxar o app inteiro.
docker run --rm -v "$TMP:/t" -v "$RAIZ:/w" -w /t node:20-alpine \
  node /w/packages/schemas/node_modules/typescript/bin/tsc \
  --target es2020 --module commonjs --noResolve --skipLibCheck console.ts >/dev/null 2>&1
[ -s "$TMP/console.js" ] || { bad "não consegui transpilar o interpretador do Console"; }

# ── roda os três contra os MESMOS vetores ────────────────────────────────────
cat > "$TMP/run.js" <<'NODE'
const S       = require('/w/packages/schemas/dist/index.js')
const consoleI = require('/t/console.js')
const pagina   = require('/t/pagina.js')

const cat = S.DEFAULT_DIALOG_FORMAT_CATALOG
pagina.setFormats(cat.formats)

const div = []
let vetores = 0
for (const f of cat.formats) {
  const casos = [
    ...(f.vectors?.valid   ?? []).map(v => [v, true]),
    ...(f.vectors?.invalid ?? []).map(v => [v, false]),
  ]
  for (const [valor, esperado] of casos) {
    vetores++
    const a = S.validateDialogFormat(valor, f.id, cat).ok
    // Os contratos DIFEREM de proposito: o do Console devolve {ok, reason}
    // porque a tela precisa do motivo para escolher a mensagem; o da pagina
    // devolve booleano porque so acende a linha vermelha. A normalizacao e
    // explicita aqui — comparar as formas cruas faria o gate reprovar por
    // assinatura e nunca chegar a julgar o COMPORTAMENTO.
    const b = consoleI.julgaFormato(valor, f).ok
    const c = pagina.julgaFormato(valor, f)
    if (a !== esperado || b !== esperado || c !== esperado || a !== b || b !== c) {
      div.push(`${f.id} ← ${JSON.stringify(valor)}: esperado=${esperado} schemas=${a} console=${b} pagina=${c}`)
    }
  }
}
// A máscara é afordância e também precisa concordar: uma que agrupe diferente
// faz o mesmo valor digitado virar duas strings distintas por superfície.
const mascaras = []
for (const f of cat.formats) {
  const m = f.affordance?.mask
  if (!m) continue
  const bruto = '12345678901234567890'
  const b = consoleI.aplicaMascara(bruto, m)
  const c = pagina.aplicaMascara(bruto, m)
  if (b !== c) mascaras.push(`${f.id}: console=${b} pagina=${c}`)
}
console.log(JSON.stringify({ vetores, divergencias: div, mascaras }))
NODE

SAIDA=$(docker run --rm -v "$TMP:/t" -v "$RAIZ:/w" -w /t node:20-alpine node /t/run.js 2>&1 | tail -1)

case "$SAIDA" in
  \{*)
    N=$(echo "$SAIDA" | jq -r '.vetores')
    D=$(echo "$SAIDA" | jq -r '.divergencias | length')
    M=$(echo "$SAIDA" | jq -r '.mascaras | length')
    # Testemunha de presença: zero divergências sobre zero vetores não é acordo.
    if [ "${N:-0}" -lt 50 ]; then
      bad "A/B/C. só $N vetores exercidos — o catálogo perdeu os vetores, e sem eles o acordo não significa nada"
    else
      ok "A/B/C. $N vetores exercidos nas três implementações"
    fi
    if [ "${D:-1}" -eq 0 ]; then
      ok "D. as três concordam em todos os $N vetores"
    else
      bad "D. $D divergência(s) entre superfícies:"
      echo "$SAIDA" | jq -r '.divergencias[]' | head -10 | sed 's/^/       /'
    fi
    if [ "${M:-1}" -eq 0 ]; then
      ok "D'. as máscaras agrupam igual nas duas superfícies que as aplicam"
    else
      bad "D'. $M máscara(s) divergem:"
      echo "$SAIDA" | jq -r '.mascaras[]' | head -5 | sed 's/^/       /'
    fi
    ;;
  *) inc "não consegui rodar a comparação: $(echo "$SAIDA" | head -c 200)" ;;
esac

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then echo "REPROVADO ($FALHAS)"; exit 1; fi
if [ "$INCONCLUSIVOS" -gt 0 ]; then echo "INCONCLUSIVO ($INCONCLUSIVOS) — não é verde"; exit 2; fi
echo "VERDE"
