#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# gate_dialog_capture_roundtrip — abrir e salvar um DialogForm no editor NÃO pode
# perder a captura Arc 12. Fase F0 do `adr-dialog-tree-options`.
#
# POR QUE ESTE GATE EXISTE
# ────────────────────────
# `flattenBlocks` (o "salvar" do editor de DialogForm) reescrevia o `capture` de
# cada pergunta de dialog-block assim:
#
#     const metric = n.capture?.metric
#     nodes.push({ ...n, capture: metric ? { metric } : undefined })
#
# Uma ALLOWLIST: elege o que fica. Quando o schema canônico ganhou
# `capture.kind` (`scored`/`nominal`), a lista não cresceu junto — e `kind` passou
# a ser descartado a cada salvamento. O consumidor exige os DOIS
# (`deriveAgentEvents`, `segment.ts`: `if (!cap?.kind || !cap.metric) continue`),
# então o efeito é a métrica de negócio **parar de ser emitida**, com o único
# vestígio num `console.log` do mcp-server.
#
# ⚠️ **E a perda era invisível ao compilador**: o tipo espelhado na UI
# (`dialog-hooks.ts: DialogCapture`) também não tinha `kind`. Um tipo espelhado
# que perde um campo transforma perda de dado em código que compila — por isso o
# conserto teve duas metades, e por isso este gate exercita o COMPORTAMENTO, não
# a presença do campo no fonte.
#
# O QUE ESTE GATE PODE REPROVAR
# ─────────────────────────────
#   A  **controle positivo**: o form real precisa DECLARAR captura   → INCONCLUSIVO
#      — sem isto, "o round-trip preservou tudo" e "não havia nada a preservar"
#        são o mesmo verde. É o defeito da asserção 7b do smoke do limite (CNS-20),
#        que passava por ausência do dado que ela existia para conferir.
#   B  `capture.kind` sobrevive ao round-trip                        → VERMELHO
#   C  `option.capture.value` sobrevive                              → VERMELHO
#      — no `scored` o NÚMERO mora na opção (`fcr`: sim→1, nao→0); perdê-lo faz
#        `Number("sim")` → NaN e a métrica some pelo outro caminho.
#   D  form SEM `dimensions[]` faz round-trip LOSSLESS               → VERMELHO
#      — a rede geral: pega o PRÓXIMO campo que o schema ganhar, sem que ninguém
#        precise lembrar de estender este gate. B e C ficam por nomearem o perigo.
#
# ⚠️ LIMITE DECLARADO: o gate exercita `buildBlocks`+`flattenBlocks` diretamente,
# não o React em volta. Se a página parar de CHAMAR o flatten (ou chamar outra
# coisa antes), este gate segue verde. Ele guarda a transformação, não a tela.
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO.
# Uso:  bash infra/test/gate_dialog_capture_roundtrip.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DA="${DIALOG_API_URL:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM="${FORM_ID:-dialog_wrapup_arc12_v1}"
NODE_IMG="${NODE_IMAGE:-node:20-alpine}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
echo "${BLD}gate_dialog_capture_roundtrip — o editor não pode desarmar a captura Arc 12${RST}"
echo

inconclusivo() { echo "  ${YEL}—${RST} INCONCLUSIVO: $1"; exit 2; }

command -v docker >/dev/null || inconclusivo "docker ausente"
docker image inspect "$NODE_IMG" >/dev/null 2>&1 \
  || inconclusivo "imagem $NODE_IMG ausente (docker pull $NODE_IMG)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── o artefato REAL, publicado — não uma fixture que envelhece à parte ──────────
curl -sf "$DA/v1/dialog/forms/$FORM?status=published" -H "X-Tenant-ID: $TENANT" \
  -o "$TMP/form.json" \
  || inconclusivo "dialog-api não serviu o form publicado '$FORM' em $DA"

# ── transpila a UNIDADE SOB TESTE (o import é type-only ⇒ elidido) ─────────────
cp "$RAIZ/packages/platform-ui/src/modules/dialog-forms/dialog-blocks.ts" "$TMP/blocks.ts"
docker run --rm -v "$TMP:/t" -v "$RAIZ:/w:ro" -w /t "$NODE_IMG" \
  node /w/packages/platform-ui/node_modules/typescript/bin/tsc \
  --module es2020 --target es2020 --moduleResolution bundler \
  --noResolve --skipLibCheck --outDir /t blocks.ts >"$TMP/tsc.log" 2>&1
[ -s "$TMP/blocks.js" ] || { sed 's/^/      /' "$TMP/tsc.log" | head -10
  inconclusivo "não transpilei dialog-blocks.ts (log acima)"; }

cat > "$TMP/run.mjs" <<'JS'
import { readFileSync } from 'node:fs'
import { buildBlocks, flattenBlocks } from '/t/blocks.js'

const form = JSON.parse(readFileSync('/t/form.json', 'utf8'))
const antes = form.nodes || []
const depois = flattenBlocks(buildBlocks(form)).nodes

const perguntas = (ns) => ns.filter(n => n.kind === 'question')
const porId = (ns) => new Map(ns.map(n => [n.id, n]))
const d = porId(depois)

const out = []
const bad = (ramo, m) => out.push({ ok: false, ramo, m })
const ok  = (ramo, m) => out.push({ ok: true,  ramo, m })

// ── A — controle positivo ─────────────────────────────────────────────────────
const comKind  = perguntas(antes).filter(q => q.capture?.kind)
const comValor = perguntas(antes).filter(q => (q.options || []).some(o => o?.capture?.value !== undefined))
if (!comKind.length || !comValor.length) {
  console.log(JSON.stringify({ inconclusivo:
    `o form '${form.form_id}' declara ${comKind.length} pergunta(s) com capture.kind e ` +
    `${comValor.length} com option.capture.value — sem as duas populações este gate ` +
    `passaria por AUSÊNCIA, não por preservação` }))
  process.exit(0)
}
ok('A', `controle positivo: ${comKind.length} pergunta(s) com capture.kind, ` +
        `${comValor.length} com option.capture.value`)

// ── B — kind sobrevive ────────────────────────────────────────────────────────
for (const q of comKind) {
  const dep = d.get(q.id)
  if (dep?.capture?.kind !== q.capture.kind) {
    bad('B', `'${q.id}': capture.kind '${q.capture.kind}' virou ${JSON.stringify(dep?.capture?.kind)} ` +
             `no round-trip — deriveAgentEvents exige kind E metric, logo a métrica para de ser emitida`)
  }
}
if (out.every(r => r.ok)) ok('B', `capture.kind preservado em ${comKind.length} pergunta(s)`)

// ── C — o número do `scored` mora na OPÇÃO ────────────────────────────────────
let ruimC = 0
for (const q of comValor) {
  const dep = d.get(q.id)
  for (const o of q.options || []) {
    if (o?.capture?.value === undefined) continue
    const od = (dep?.options || []).find(x => x.id === o.id)
    if (od?.capture?.value !== o.capture.value) {
      bad('C', `'${q.id}'/opção '${o.id}': option.capture.value ${JSON.stringify(o.capture.value)} ` +
               `virou ${JSON.stringify(od?.capture?.value)} — sem ele Number(resposta) vira NaN`)
      ruimC++
    }
  }
}
if (!ruimC) ok('C', 'option.capture.value preservado')

// ── D — a rede geral: sem dimensions[], o round-trip é idêntico ───────────────
if ((form.dimensions || []).length) {
  ok('D', `pulado: '${form.form_id}' declara dimensions[] (o flatten reescreve por desenho)`)
} else {
  const a = JSON.stringify(antes), b = JSON.stringify(depois)
  if (a !== b) {
    const q1 = perguntas(antes).find(x => JSON.stringify(x) !== JSON.stringify(d.get(x.id)))
    bad('D', `round-trip NÃO é lossless num form sem dimensions[]. Primeira divergência: ` +
             `'${q1?.id}'\n        antes : ${JSON.stringify(q1)}\n        depois: ${JSON.stringify(d.get(q1?.id))}`)
  } else {
    ok('D', 'round-trip lossless (form sem dimensions[])')
  }
}
console.log(JSON.stringify({ resultados: out }))
JS

SAIDA="$(docker run --rm -v "$TMP:/t" -w /t "$NODE_IMG" node /t/run.mjs 2>&1)"
if ! printf '%s' "$SAIDA" | grep -q '^{'; then
  printf '%s\n' "$SAIDA" | sed 's/^/      /' | head -12
  inconclusivo "o round-trip não executou (saída acima)"
fi

printf '%s' "$SAIDA" | RED="$RED" GRN="$GRN" YEL="$YEL" python3 -c '
import json, os, sys
d = json.loads(sys.stdin.read().strip().split("\n")[-1])
RED, GRN, YEL, RST = os.environ["RED"], os.environ["GRN"], os.environ["YEL"], "\033[0m"
if "inconclusivo" in d:
    print("  %s—%s INCONCLUSIVO: %s" % (YEL, RST, d["inconclusivo"])); sys.exit(2)
falhas = 0
for r in d["resultados"]:
    if r["ok"]:
        print("  %s✓%s %s — %s" % (GRN, RST, r["ramo"], r["m"]))
    else:
        print("  %s✗%s %s — %s" % (RED, RST, r["ramo"], r["m"])); falhas += 1
sys.exit(1 if falhas else 0)
'
RC=$?

echo
if [ "$RC" -eq 2 ]; then exit 2; fi
if [ "$RC" -ne 0 ]; then
  echo "${RED}${BLD}REPROVADO${RST} — salvar no editor desarma a captura Arc 12"
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — abrir e salvar preserva a captura"
