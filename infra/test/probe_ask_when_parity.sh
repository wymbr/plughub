#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_ask_when_parity — as TRÊS implementações de `ask_when` decidem igual.
#
# POR QUE ESTE PROBE EXISTE
# ─────────────────────────
# `evaluateAskWhen` responde *"esta pergunta é APRESENTADA ao cliente?"*, e existe
# três vezes:
#
#   canônico  packages/schemas/src/dialog.ts                   (o engine importa)
#   console   platform-ui/.../DialogFormRenderer.tsx            (cópia — ver abaixo)
#   web       channel-gateway/.../survey_web.py                 (cópia — ver abaixo)
#
# ⚠️ **O triplicamento é TOPOLOGIA, não desleixo, e por isso não se resolve
# unificando.** O platform-ui não importa `@plughub/schemas` (sem workspaces,
# risco de dual-instance de Zod — a mesma D2 do `adr-skill-flow-editor-validation`),
# e a cópia do `survey_web.py` não é sequer Python: é **JavaScript dentro de uma
# string**, o script inline da página web servida por um serviço Python, que não
# tem de onde importar nada. Restam duas posturas — gerar as duas espelhadas a
# partir da canônica, ou CONFERIR que decidem igual. Este probe é a segunda.
#
# Medido em 2026-09-05, antes de existir: as três eram equivalentes (mesmos 7 ops,
# mesma coerção numérica, mesma regra de ausência) e **nenhum teste as comparava**
# — nenhum `.sh` de `infra/test/` sequer mencionava `ask_when`. Paridade sem
# mecanismo é promessa, a mesma família do DDL de `participation_intervals`.
#
# A urgência é a D12 do `adr-dialog-tree-options`, que acrescenta o op `prefix`:
# op novo aplicado a duas das três cópias é skip-logic que diverge por superfície
# — a pergunta APARECE no Console e SOME na web, com o mesmo form publicado.
#
# O QUE ESTE PROBE PODE REPROVAR
# ──────────────────────────────
#   A  **controle positivo**: a tabela de casos tem de separar verdadeiro de   → INCONCLUSIVO
#      falso na canônica. Sem isto, "as três concordam" é verde trivial —
#      três implementações que sempre dissessem `false` passariam.
#   B  divergência de VEREDICTO entre as três, em qualquer caso              → VERMELHO
#   C  op que a canônica conhece e a tabela não exercita                     → VERMELHO
#      — é a rede que faz `prefix` (D12) NASCER coberto: quem acrescentar o op
#        ao `switch` e não à tabela reprova aqui, antes de espalhar a divergência.
#   D  recorte falhou (função renomeada, arquivo movido)                     → INCONCLUSIVO
#
# ⚠️ **LIMITE DECLARADO:** este probe compara as três ENTRE SI, nunca contra uma
# expectativa escrita aqui — escrevê-la seria a quarta cópia, e um gate que testa
# a própria cópia é verde garantido. Logo: as três erradas do MESMO jeito passam.
# É o ramo A que impede o caso degenerado, e é o `dialog.test.ts` que julga se a
# canônica está CERTA. Paridade e correção são dois fatos.
#
# COMO FALSEAR (bateria de mutação)
# ─────────────────────────────────
#   1. trocar `case "lte"` por `case "lt"` em DialogFormRenderer.tsx  → B vermelho
#   2. acrescentar `case "prefix":` ao switch de dialog.ts            → C vermelho
#   3. renomear `awEval` em survey_web.py                             → D inconclusivo
#
# Uso:  bash infra/test/probe_ask_when_parity.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_IMG="${NODE_IMG:-node:20-alpine}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inconclusivo() { echo "  ${YEL}—${RST} INCONCLUSIVO: $1"; exit 2; }

echo "${BLD}probe_ask_when_parity — a skip-logic decide igual nas três superfícies${RST}"
echo

command -v docker >/dev/null || inconclusivo "docker ausente"
docker image inspect "$NODE_IMG" >/dev/null 2>&1 \
  || inconclusivo "imagem $NODE_IMG ausente (docker pull $NODE_IMG)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── D — recorte das TRÊS, dos arquivos reais ──────────────────────────────────
SAIDA_EXTRACAO="$(python3 "$RAIZ/infra/test/_ask_when_extract.py" "$RAIZ" "$TMP" 2>&1)"
echo "$SAIDA_EXTRACAO" | sed 's/^/      /'
echo "$SAIDA_EXTRACAO" | grep -q '^FALHA' \
  && inconclusivo "recorte falhou — função renomeada ou arquivo movido (acima)"

# ── transpila os dois recortes TS (o .js da web já é JS) ──────────────────────
docker run --rm -v "$TMP:/t" -v "$RAIZ:/w:ro" -w /t "$NODE_IMG" \
  node /w/packages/platform-ui/node_modules/typescript/bin/tsc \
  --module es2020 --target es2020 --moduleResolution bundler \
  --noResolve --skipLibCheck --outDir /t canon.ts console.ts >"$TMP/tsc.log" 2>&1
[ -s "$TMP/canon.js" ] && [ -s "$TMP/console.js" ] || {
  sed 's/^/      /' "$TMP/tsc.log" | head -10
  inconclusivo "não transpilei os recortes (log acima)"; }

cat > "$TMP/run.mjs" <<'JS'
import { readFileSync } from 'node:fs'
import { avalia as canonico } from '/t/canon.js'
import { avalia as consoleUi } from '/t/console.js'
import { avalia as web }       from '/t/web.js'

// A tabela é do PROBE, não do produto: cada linha é um par (guarda, respostas)
// que as três têm de julgar igual. `esperado` NÃO existe de propósito — ver o
// LIMITE DECLARADO no cabeçalho.
const CASOS = [
  ['sem guarda',            undefined,                                   { a: 1 }],
  ['campo ausente',         { field: 'a', op: 'eq',  value: 1 },         {}],
  ['campo null',            { field: 'a', op: 'eq',  value: 1 },         { a: null }],
  ['campo string vazia',    { field: 'a', op: 'eq',  value: '' },        { a: '' }],
  ['campo zero',            { field: 'a', op: 'eq',  value: 0 },         { a: 0 }],
  ['campo array vazio',     { field: 'a', op: 'in',  value: ['x'] },     { a: [] }],
  ['lt verdadeiro',         { field: 'a', op: 'lt',  value: 3 },         { a: 2 }],
  ['lt falso',              { field: 'a', op: 'lt',  value: 3 },         { a: 4 }],
  ['lt string numerica',    { field: 'a', op: 'lt',  value: 3 },         { a: '2' }],
  ['lt string nao numerica',{ field: 'a', op: 'lt',  value: 3 },         { a: 'x' }],
  ['lte no limite',         { field: 'a', op: 'lte', value: 3 },         { a: 3 }],
  ['gt verdadeiro',         { field: 'a', op: 'gt',  value: 3 },         { a: 4 }],
  ['gte no limite',         { field: 'a', op: 'gte', value: 3 },         { a: 3 }],
  ['eq numero x string',    { field: 'a', op: 'eq',  value: '3' },       { a: 3 }],
  ['eq string x string',    { field: 'a', op: 'eq',  value: 'sim' },     { a: 'sim' }],
  ['eq divergente',         { field: 'a', op: 'eq',  value: 'sim' },     { a: 'nao' }],
  ['ne verdadeiro',         { field: 'a', op: 'ne',  value: 'sim' },     { a: 'nao' }],
  ['ne falso',              { field: 'a', op: 'ne',  value: 'sim' },     { a: 'sim' }],
  ['in casa',               { field: 'a', op: 'in',  value: ['a','b'] }, { a: 'b' }],
  ['in nao casa',           { field: 'a', op: 'in',  value: ['a','b'] }, { a: 'c' }],
  ['in com valor nao-array',{ field: 'a', op: 'in',  value: 'a' },       { a: 'a' }],
  ['op desconhecido',       { field: 'a', op: 'zzz', value: 1 },         { a: 1 }],
  ['prefix (D12, futuro)',  { field: 'a', op: 'prefix', value: 'fin' },  { a: 'fin.cob' }],
]

const IMPL = [['canonico', canonico], ['console', consoleUi], ['web', web]]

const linhas = CASOS.map(([nome, guarda, respostas]) => {
  const veredictos = IMPL.map(([rotulo, fn]) => {
    try { return [rotulo, !!fn(guarda, respostas)] }
    catch (e) { return [rotulo, 'ERRO:' + e.message] }
  })
  const vals = veredictos.map(v => v[1])
  return { nome, veredictos, concordam: vals.every(v => v === vals[0]), valor: vals[0] }
})

// Ops que a CANÔNICA conhece — lidos do recorte, nunca de uma lista aqui: op novo
// no `switch` entra na cobrança sem ninguém precisar lembrar de estender o probe.
const fonte = readFileSync('/t/canon.ts', 'utf8')
const opsCanon = [...fonte.matchAll(/case\s+["']([a-z_]+)["']/g)].map(m => m[1])
const opsTabela = new Set(CASOS.map(([, g]) => g && g.op).filter(Boolean))

console.log(JSON.stringify({
  linhas,
  opsCanon,
  opsSemCaso: opsCanon.filter(o => !opsTabela.has(o)),
  temVerdadeiro: linhas.some(l => l.valor === true),
  temFalso:      linhas.some(l => l.valor === false),
}))
JS

SAIDA="$(docker run --rm -v "$TMP:/t" -w /t "$NODE_IMG" node /t/run.mjs 2>&1)"
echo "$SAIDA" | head -c 1 | grep -q '{' || {
  echo "$SAIDA" | sed 's/^/      /' | head -12
  inconclusivo "o runner não produziu JSON (log acima)"; }

# ── A — controle positivo ─────────────────────────────────────────────────────
echo "A — controle positivo: a tabela separa verdadeiro de falso"
if ! python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
sys.exit(0 if d['temVerdadeiro'] and d['temFalso'] else 1)" <<<"$SAIDA"; then
  inconclusivo "a tabela não produz os dois veredictos — 'as três concordam' seria trivial"
fi
ok "a canônica diz true em ao menos um caso e false em outro"

# ── B — paridade ──────────────────────────────────────────────────────────────
echo
echo "B — as três decidem igual em todos os casos"
DIV="$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
for l in d['linhas']:
    if not l['concordam']:
        print(l['nome'] + ': ' + ', '.join(f\"{r}={v}\" for r,v in l['veredictos']))
" <<<"$SAIDA")"
if [ -z "$DIV" ]; then
  N="$(python3 -c "import json,sys;print(len(json.loads(sys.stdin.read())['linhas']))" <<<"$SAIDA")"
  ok "$N casos, veredicto idêntico nas três"
else
  bad "as superfícies DIVERGEM — o mesmo form publicado pergunta coisas diferentes"
  echo "$DIV" | sed 's/^/        /'
fi

# ── C — todo op da canônica tem caso ──────────────────────────────────────────
echo
echo "C — todo op que a canônica conhece é exercitado pela tabela"
SEM="$(python3 -c "
import json,sys
print(' '.join(json.loads(sys.stdin.read())['opsSemCaso']))" <<<"$SAIDA")"
OPS="$(python3 -c "
import json,sys
print(' '.join(json.loads(sys.stdin.read())['opsCanon']))" <<<"$SAIDA")"
if [ -z "$SEM" ]; then
  ok "ops cobertos: $OPS"
else
  bad "op(s) sem caso na tabela: $SEM — acrescente a linha antes de espalhar o op"
fi

# ── veredicto ─────────────────────────────────────────────────────────────────
echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}VERMELHO${RST} — $FAIL ramo(s) reprovaram."
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — as três superfícies julgam a mesma guarda do mesmo jeito."
