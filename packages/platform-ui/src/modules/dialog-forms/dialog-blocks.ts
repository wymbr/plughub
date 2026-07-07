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
} from '@/api/dialog-hooks'

export const DIALOG_KEY = '__dialog__'

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
export type Block = InstrumentBlock | DialogBlock

/** The block key a node belongs to: a real dimension id (instrument) or DIALOG_KEY.
 *  Statements have no binding — they inherit the NEXT question's key (an intro
 *  statement leads its block); a trailing statement falls back to DIALOG_KEY. */
function keyOfNode(nodes: DialogNode[], i: number, dimIds: Set<string>): string {
  const n = nodes[i]
  if (n.kind === 'question') {
    const d = n.capture?.dimension_id
    return d && dimIds.has(d) ? d : DIALOG_KEY
  }
  for (let j = i + 1; j < nodes.length; j++) {
    const m = nodes[j]
    if (m.kind === 'question') {
      const d = m.capture?.dimension_id
      return d && dimIds.has(d) ? d : DIALOG_KEY
    }
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
      for (const n of block.nodes) {
        if (n.kind === 'question') {
          const metric = n.capture?.metric
          nodes.push({ ...n, capture: metric ? { metric } : undefined })
        } else {
          nodes.push(n)
        }
      }
    }
  }
  return { nodes, dimensions }
}
