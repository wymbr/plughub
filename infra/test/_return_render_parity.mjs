/**
 * _return_render_parity.mjs — RET-01: o render das formas REAIS não mudou.
 *
 * A suíte de unidade prova a janela sobre uma forma sintética. Esta prova a
 * COMPATIBILIDADE sobre a população: as 14 formas publicadas no dialog-api,
 * renderizadas SEM `fromQuestionId`, têm de sair idênticas ao que saíam.
 *
 * ⚠️ Por que isto não é redundante com o teste de unidade: 5 das 14 formas têm
 * MAIS DE UMA question (survey e wrap-up), e é exatamente nelas que uma janela
 * incondicional mudaria `statement_after`/`menu_prompt` em silêncio. Um teste
 * sobre uma fixture de duas questions não fala pela população — e a população é
 * o que o cliente lê.
 *
 * MODO:
 *   grava <arquivo>   renderiza tudo e grava a linha de base
 *   confere <arquivo> renderiza tudo e compara com a linha de base
 */
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// Importa o DIST construído, não o fonte: é o artefato que os consumidores usam.
const aqui = path.dirname(fileURLToPath(import.meta.url))
const { buildRender, entryQuestionId, returnRefErrors } = await import(
  process.env.SCHEMAS_DIST ||
  path.resolve(aqui, "../../packages/schemas/dist/index.js")
)

const DIALOG = process.env.DIALOG_API_URL || "http://localhost:3760"
const H = { "x-tenant-id": "tenant_demo" }

const modo = process.argv[2]
const arq  = process.argv[3]
if (!modo || !arq) {
  console.log("uso: _return_render_parity.mjs grava|confere <arquivo>")
  process.exit(2)
}

async function get(u) {
  const r = await fetch(u, { headers: H })
  if (!r.ok) throw new Error(`${u} → ${r.status}`)
  return r.json()
}

const lista = await get(`${DIALOG}/v1/dialog/forms`)
const formas = Array.isArray(lista) ? lista : (lista.forms || [])
if (formas.length === 0) {
  console.log("VEREDICTO: SEM AMOSTRA — o dialog-api não devolveu forma nenhuma")
  process.exit(3)
}

const atual = {}
let comMulti = 0
let comOnReturn = 0
for (const f of formas) {
  const doc = await get(`${DIALOG}/v1/dialog/forms/${encodeURIComponent(f.form_id)}`)
  const nQ = (doc.nodes || []).filter(n => n.kind === "question").length
  if (nQ > 1) comMulti++
  // TESTEMUNHA da inocuidade: enquanto nenhuma forma declarar `on_return`, o
  // campo novo no render não pode ter mudado nada para a população atual.
  if (JSON.stringify(doc).includes('"on_return"')) comOnReturn++
  // Sem `fromQuestionId` — é a chamada que TODO consumidor de hoje faz.
  const r = buildRender(doc)
  atual[f.form_id] = {
    questions:       nQ,
    entry:           entryQuestionId(doc) ?? null,
    prompt:          r.prompt,
    menu_prompt:     r.menu_prompt,
    statement_after: r.statement_after,
    output_key:      r.output_key,
    interaction:     r.interaction,
    n_fields:        r.fields.length,
    n_questions:     r.questions.length,
    options:         JSON.stringify(r.options),
    ret_errors:      returnRefErrors(doc).length,
  }
}

console.log(`formas: ${formas.length} · com mais de uma question: ${comMulti} · declarando on_return: ${comOnReturn}`)

if (modo === "grava") {
  fs.writeFileSync(arq, JSON.stringify(atual, null, 2))
  console.log(`linha de base gravada: ${arq}`)
  process.exit(0)
}

if (!fs.existsSync(arq)) {
  console.log(`VEREDICTO: SEM AMOSTRA — linha de base ausente (${arq})`)
  process.exit(3)
}
const base = JSON.parse(fs.readFileSync(arq, "utf8"))
const difs = []
for (const id of new Set([...Object.keys(base), ...Object.keys(atual)])) {
  const a = base[id], b = atual[id]
  if (!a) { difs.push(`${id}: forma NOVA (não estava na base)`); continue }
  if (!b) { difs.push(`${id}: forma SUMIU`); continue }
  for (const k of Object.keys(a)) {
    if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) {
      difs.push(`${id}.${k}: ${JSON.stringify(a[k])} → ${JSON.stringify(b[k])}`)
    }
  }
}

// ⚠️ ESTA LINHA DE BASE É PÓS-MUDANÇA, e o cabeçalho não deve fingir o
// contrário: ela não prova o passado, protege o FUTURO — o dia em que alguém
// tornar a janela incondicional. O passado foi coberto por outras duas vias, e
// nenhuma sozinha bastava: a suíte de `dialog-render.test.ts` (formas de UMA
// question, que passou intacta) e o teste `mantém o comportamento de sempre` de
// `dialog-return.test.ts` (o caso multi-question, que NÃO estava coberto antes).

if (comMulti === 0) {
  console.log("VEREDICTO: INCONCLUSIVO — nenhuma forma com múltiplas questions;")
  console.log("           é justamente nelas que a janela incondicional morderia.")
  process.exit(3)
}
if (difs.length) {
  console.log(`VEREDICTO: FALHA — ${difs.length} divergência(s) no render das formas reais`)
  for (const d of difs.slice(0, 20)) console.log(`   ${d}`)
  process.exit(1)
}
console.log(`VEREDICTO: OK — as ${formas.length} formas renderizam igual (${comMulti} com multi-question)`)
process.exit(0)
