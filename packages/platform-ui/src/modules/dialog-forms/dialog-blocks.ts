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
import type { DialogForm, DialogNode, DialogDimension } from '@/api/dialog-hooks'

export const DIALOG_KEY = '__dialog__'

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

  const blocks: Block[] = runs.map(run =>
    run.key === DIALOG_KEY
      ? { kind: 'dialog', nodes: run.nodes }
      : { kind: 'instrument', dim: dimById.get(run.key)!, nodes: run.nodes },
  )

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
      const id = block.dim.dimension_id
      if (id && !seen.has(id)) { dimensions.push(block.dim); seen.add(id) }
      for (const n of block.nodes) {
        if (n.kind === 'question') {
          const weight = n.capture?.weight
          nodes.push({ ...n, capture: { dimension_id: id, ...(weight != null ? { weight } : {}) } })
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
