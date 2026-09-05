/**
 * dialog-blocks.ts
 * Editor-side projection between the canonical DialogForm (flat `nodes[]` +
 * `dimensions[]` + per-question `capture.dimension_id`) and the block model the
 * editor presents (a sequence of blocks; each block is either a scored
 * INSTRUMENT — its questions compose one signal — or a plain DIALOG block —
 * unscored statements/verbatim/OTP).
 *
 * The block view is PURELY presentational: the runtime reads `nodes[]` order +
 * `dimension_id` form-wide (composeSurveySignals gathers members across the whole
 * form), so grouping never changes behavior. Contiguity constraint: an
 * instrument's questions are a contiguous run (matches how surveys are built).
 *
 * Checkpoint A: flatten only rewrites `capture` (dimension binding + weight) and
 * rebuilds `dimensions[]`. Instrument-level interaction/options materialization
 * is Checkpoint B.
 */
import type {
  DialogForm, DialogNode, DialogDimension, QuestionNode, DialogOption, DialogInteraction,
  DialogCapture,
} from '@/api/dialog-hooks'

export const DIALOG_KEY = '__dialog__'

/**
 * Prefixo de chave do bloco FORM (2026-09-05).
 *
 * `interaction: "form"` nao e "mais um formato de resposta": e o unico em que UM
 * turno coleta N valores nomeados. Enquanto ele morava no dropdown de interacao
 * da pergunta, aquele seletor era uma uniao discriminada — escolher o quinto
 * valor mudava o significado do painel inteiro, e quatro controles da pergunta
 * (`retry`/`options`/`validation` escalar/`masked` do no) passavam a ser inertes.
 *
 * Como bloco, isso deixa de EXISTIR em vez de ser tratado. E custa nada no
 * modelo: bloco e PROJECAO sobre o `nodes[]` plano, entao nada muda no schema,
 * no runtime ou nos dados — a pergunta continua sendo uma pergunta com
 * `interaction: "form"`; muda so como a tela a agrupa.
 *
 * A chave leva o id do no (`__form__:<id>`) para que cada formulario seja o seu
 * proprio run: dois formularios seguidos sao dois blocos, nunca um. ⚠️ Isso NAO
 * impede duas perguntas `form` no documento — o `buildRender` funde os `fields`
 * das duas num turno so, e quem denuncia e o preview. Proibir sem populacao
 * (medido: zero ocorrencias) seria politica contra ninguem.
 */
export const FORM_KEY = '__form__'
const formKeyOf = (id: string) => `${FORM_KEY}:${id}`
export const isFormKey = (k: string) => k === FORM_KEY || k.startsWith(`${FORM_KEY}:`)

const HAS_OPTIONS = (i: DialogInteraction) => i === 'button' || i === 'list' || i === 'checklist'

/** Derive the option list of a scored instrument from its scale + anchor labels
 *  (one option per scale point; value = the numeric score). */
export function deriveOptions(dim: DialogDimension): DialogOption[] {
  const min = dim.scale.min ?? 0
  const opts: DialogOption[] = []
  for (let v = min; v <= dim.scale.max; v++) {
    const anchor = dim.anchors?.[v - min]
    opts.push({ id: String(v), value: String(v), label: anchor ?? String(v), capture: { value: v } })
  }
  return opts
}

export interface InstrumentBlock { kind: 'instrument'; dim: DialogDimension; nodes: DialogNode[] }
export interface DialogBlock { kind: 'dialog'; nodes: DialogNode[] }
/** Bloco FORM: os `nodes` sao statements satelites + EXATAMENTE UMA pergunta
 *  `interaction: "form"`, que e quem da identidade ao bloco. O statement de
 *  abertura fica JUNTO de proposito — o `buildRender` o dobra no `menu_prompt`
 *  do formulario, entao agrupa-los na tela espelha o runtime, e mover o bloco
 *  leva o texto junto. */
export interface FormBlock { kind: 'form'; nodes: DialogNode[] }
export type Block = InstrumentBlock | DialogBlock | FormBlock

/** A pergunta que DA identidade a um bloco form (a primeira, e deve haver uma). */
export function formQuestionOf(block: FormBlock): QuestionNode | undefined {
  return block.nodes.find((n): n is QuestionNode => n.kind === 'question')
}

/** The block key a node belongs to: a real dimension id (instrument) or DIALOG_KEY.
 *  Statements have no binding — they inherit the NEXT question's key (an intro
 *  statement leads its block); a trailing statement falls back to DIALOG_KEY. */
function keyOfNode(nodes: DialogNode[], i: number, dimIds: Set<string>): string {
  // A DIMENSAO VENCE, e isso e decisao: pergunta com `dimension_id` E
  // `interaction: "form"` e contraditoria (instrumento rende UM numero,
  // formulario rende N valores). Deixar o form vencer arrancaria a pergunta do
  // instrumento e mudaria a nota; deixar a dimensao vencer preserva o que o
  // `flattenBlocks` ja fazia antes deste bloco existir.
  const keyOfQuestion = (q: QuestionNode): string => {
    const d = q.capture?.dimension_id
    if (d && dimIds.has(d)) return d
    return q.interaction === 'form' ? formKeyOf(q.id) : DIALOG_KEY
  }

  const n = nodes[i]
  if (n.kind === 'question') return keyOfQuestion(n)
  for (let j = i + 1; j < nodes.length; j++) {
    const m = nodes[j]
    if (m.kind === 'question') return keyOfQuestion(m)
  }
  return DIALOG_KEY
}

/** Group a form's nodes into blocks (contiguous runs by dimension binding).
 *  Declared instruments with no member questions are appended as empty blocks so
 *  the author can still see/fill them. */
export function buildBlocks(form: DialogForm): Block[] {
  const dims = form.dimensions ?? []
  const dimById = new Map(dims.map(d => [d.dimension_id, d] as const))
  const dimIds = new Set(dims.map(d => d.dimension_id))
  const nodes = form.nodes ?? []

  const runs: { key: string; nodes: DialogNode[] }[] = []
  for (let i = 0; i < nodes.length; i++) {
    const key = keyOfNode(nodes, i, dimIds)
    const last = runs[runs.length - 1]
    if (last && last.key === key) last.nodes.push(nodes[i]!)
    else runs.push({ key, nodes: [nodes[i]!] })
  }

  const blocks: Block[] = runs.map(run => {
    if (isFormKey(run.key)) return { kind: 'form', nodes: run.nodes }
    if (run.key === DIALOG_KEY) return { kind: 'dialog', nodes: run.nodes }
    let dim = dimById.get(run.key)!
    // Migration: infer the instrument-level interaction from its questions when
    // unset (older forms carry it per-question), so the editor can present the
    // question as prompt-only and the header owns the render.
    if (dim.interaction === undefined) {
      const set = new Set(
        run.nodes.filter((n): n is QuestionNode => n.kind === 'question').map(q => q.interaction),
      )
      if (set.size === 1) dim = { ...dim, interaction: [...set][0] }
    }
    return { kind: 'instrument', dim, nodes: run.nodes }
  })

  const present = new Set(
    blocks.filter((b): b is InstrumentBlock => b.kind === 'instrument').map(b => b.dim.dimension_id),
  )
  for (const d of dims) {
    if (!present.has(d.dimension_id)) blocks.push({ kind: 'instrument', dim: d, nodes: [] })
  }
  return blocks
}

/** Flatten blocks back to the canonical { nodes, dimensions }. Rewrites each
 *  question's `capture`: instrument questions bind to the block's dimension
 *  (preserving any weight); dialog questions drop the dimension (keeping a legacy
 *  `metric` if present). Statements pass through. Dimensions are the instrument
 *  blocks' dims, de-duplicated by id (first wins). */
export function flattenBlocks(blocks: Block[]): { nodes: DialogNode[]; dimensions: DialogDimension[] } {
  const nodes: DialogNode[] = []
  const dimensions: DialogDimension[] = []
  const seen = new Set<string>()

  for (const block of blocks) {
    if (block.kind === 'instrument') {
      const dim = block.dim
      const id = dim.dimension_id
      if (id && !seen.has(id)) { dimensions.push(dim); seen.add(id) }
      for (const n of block.nodes) {
        if (n.kind === 'question') {
          const weight = n.capture?.weight
          const q: QuestionNode = { ...n, capture: { dimension_id: id, ...(weight != null ? { weight } : {}) } }
          // Materialize the instrument-level render into the node (runtime reads
          // interaction/options per-node). Options are derived from scale+anchors.
          if (dim.interaction) {
            q.interaction = dim.interaction
            if (HAS_OPTIONS(dim.interaction)) q.options = deriveOptions(dim)
            else delete (q as { options?: unknown }).options
          }
          nodes.push(q)
        } else {
          nodes.push(n)
        }
      }
    } else {
      // `dialog` E `form` compartilham este ramo de proposito: os dois sao
      // "nao-instrumento", e o que o achatamento faz e SOLTAR o vinculo de
      // dimensao. O bloco form nao materializa nada — `interaction: "form"` e
      // `fields[]` ja estao no no, e reescreve-los aqui recriaria a classe de
      // defeito que a materializacao de instrumento tem (o editor muda o que o
      // autor digitou sem dizer).
      for (const n of block.nodes) {
        if (n.kind === 'question') {
          // DENYLIST, nao allowlist. Um dialog-block so precisa SOLTAR o que
          // pertence ao modelo de instrumento (`dimension_id`/`weight`); todo o
          // resto e semantica da propria pergunta e sobrevive.
          //
          // A versao anterior enumerava o que MANTER (`{ metric }`) e por isso
          // perdeu `capture.kind` quando o schema cresceu — abrir um form com
          // captura Arc 12 no editor e salvar desarmava a metrica, e o unico
          // vestigio era um `console.log` no mcp-server. Allowlist envelhece com
          // o schema; denylist sobrevive ao proximo campo.
          const { dimension_id: _dim, weight: _w, ...rest } = n.capture ?? {}
          const cap = Object.keys(rest).length ? (rest as DialogCapture) : undefined
          nodes.push({ ...n, capture: cap })
        } else {
          nodes.push(n)
        }
      }
    }
  }
  return { nodes, dimensions }
}

/**
 * O que a projeção de blocos REESCREVERIA neste documento.
 *
 * `flattenBlocks` não é identidade: ela reescreve o `capture` de cada pergunta
 * (vínculo de dimensão) e materializa `interaction`/`options` a partir do
 * instrumento. Enquanto a única entrada era o próprio editor de widgets isso era
 * invisível — os widgets só produziam o que a projeção já produziria. Com o
 * editor JSON o autor passa a escrever à mão, e uma reescrita silenciosa vira
 * *"editei, apliquei e o campo mudou sozinho"*.
 *
 * Devolve um item por nó divergente, nomeando as CHAVES — a regra da casa é que
 * degradação não é silenciosa, e aqui a "degradação" é a edição do próprio autor
 * sendo desfeita.
 */
export function reprojectionDrift(form: DialogForm): Array<{ node_id: string; keys: string[] }> {
  const { nodes } = flattenBlocks(buildBlocks(form))
  const depois = new Map(nodes.map(n => [n.id, n] as const))
  const saida: Array<{ node_id: string; keys: string[] }> = []

  for (const antes of form.nodes ?? []) {
    const dep = depois.get(antes.id)
    if (!dep) { saida.push({ node_id: antes.id, keys: ['(removido)'] }); continue }
    const chaves = [...new Set([...Object.keys(antes), ...Object.keys(dep)])].filter(k => {
      const a = (antes as unknown as Record<string, unknown>)[k]
      const d = (dep   as unknown as Record<string, unknown>)[k]
      return JSON.stringify(a ?? null) !== JSON.stringify(d ?? null)
    })
    if (chaves.length) saida.push({ node_id: antes.id, keys: chaves })
  }
  return saida
}
