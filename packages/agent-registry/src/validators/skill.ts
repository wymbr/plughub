/**
 * validators/skill.ts
 * Validação Zod dos payloads de skill + validações cross-step.
 *
 * Cross-field validators:
 *   - validateMaskedBlock  — reason step inside begin/end_transaction block
 */

import { z }           from "zod"
import { SkillSchema } from "@plughub/schemas"
import type { FlowStep, SkillFlow } from "@plughub/schemas"

export const CreateSkillSchema = SkillSchema

// SkillSchema is ZodEffects (has .refine). Access the inner ZodObject for partial operations.
const _SkillBase = (SkillSchema as unknown as { _def: { schema: z.ZodObject<z.ZodRawShape> } })._def.schema
export const UpdateSkillSchema = _SkillBase.partial().omit({ skill_id: true })

// ─────────────────────────────────────────────
// validateMaskedBlock
// ─────────────────────────────────────────────

/**
 * Validates that no `reason` step exists inside a begin_transaction / end_transaction block.
 *
 * A `reason` step inside a masked block is a design error because it sends user data to an
 * external LLM, which could inadvertently expose sensitive values captured by masked fields.
 * Spec: docs/guias/masked-input.md — "reason step dentro de bloco masked é erro de design".
 *
 * Engine behaviour (from engine.ts):
 *   begin_transaction returns "__transaction_begin__" → engine advances to the
 *   NEXT STEP IN THE ARRAY (position N+1). All subsequent steps reachable via
 *   on_success chains until end_transaction are inside the block.
 *
 * Algorithm:
 *   For each begin_transaction step at array position N:
 *     - Start BFS from position N+1 (the first step inside the block)
 *     - For each visited step, extract "success-edge" step IDs (on_success, choice branches, etc.)
 *     - Stop propagating at end_transaction (block closed) or on_failure exits
 *     - If a reason step is found inside the block, emit an error
 *
 * @returns Array of error strings — empty array when the flow is valid.
 */
export function validateMaskedBlock(flow: SkillFlow): string[] {
  const errors: string[] = []
  const steps = flow.steps
  if (!steps || steps.length === 0) return errors

  // ── Step map and position map ──────────────────────────────────────────────
  const stepById  = new Map<string, FlowStep>()
  const stepIndex = new Map<string, number>()  // stepId → position in array
  for (let i = 0; i < steps.length; i++) {
    stepById.set(steps[i]!.id, steps[i]!)
    stepIndex.set(steps[i]!.id, i)
  }

  // ── Success-edge extractor ─────────────────────────────────────────────────
  // Returns all step IDs that `step` can transition to via "happy path" edges.
  // Excludes on_failure / on_disconnect / on_timeout (exit paths).
  function successors(step: FlowStep): string[] {
    const ids: string[] = []

    if (step.type === "begin_transaction") {
      // begin_transaction has no on_success — engine uses position N+1 (handled by caller)
      return []
    }

    // Generic on_success present on most step types
    const s = step as FlowStep & { on_success?: string }
    if (typeof s.on_success === "string" && stepById.has(s.on_success)) {
      ids.push(s.on_success)
    }

    // choice step: all conditional branches + default
    if (step.type === "choice") {
      for (const cond of step.conditions) {
        if (cond.next && stepById.has(cond.next)) ids.push(cond.next)
      }
      if (step.default && stepById.has(step.default)) ids.push(step.default)
    }

    // suspend step: on_resume.next
    if (step.type === "suspend") {
      const on_resume = (step as { on_resume?: { next?: string } }).on_resume
      if (on_resume?.next && stepById.has(on_resume.next)) ids.push(on_resume.next)
    }

    // collect step: on_response
    if (step.type === "collect") {
      const on_response = (step as { on_response?: { next?: string } }).on_response
      if (on_response?.next && stepById.has(on_response.next)) ids.push(on_response.next)
    }

    return [...new Set(ids)]
  }

  // ── BFS from each begin_transaction ───────────────────────────────────────
  for (let i = 0; i < steps.length; i++) {
    const startStep = steps[i]!
    if (startStep.type !== "begin_transaction") continue

    // The first step inside the block is the one at position i+1
    const firstInBlock = steps[i + 1]
    if (!firstInBlock) continue

    const visited = new Set<string>()
    const queue: string[] = [firstInBlock.id]

    while (queue.length > 0) {
      const stepId = queue.shift()!
      if (visited.has(stepId)) continue
      visited.add(stepId)

      const step = stepById.get(stepId)
      if (!step) continue

      // end_transaction closes the block — stop this path
      if (step.type === "end_transaction") continue

      // Validate: reason step inside masked block is forbidden
      if (step.type === "reason") {
        errors.push(
          `Step "${stepId}" (reason) is inside masked transaction block ` +
          `started by "${startStep.id}". reason steps must not appear inside ` +
          `begin_transaction / end_transaction blocks — they send data to an external LLM ` +
          `and could expose sensitive values captured by masked fields. ` +
          `Move the reason step before begin_transaction or after end_transaction.`
        )
        // Don't propagate further from a reason step — one error per step is enough
        continue
      }

      // Propagate through success edges
      for (const next of successors(step)) {
        if (!visited.has(next)) queue.push(next)
      }
    }
  }

  return errors
}

// ─────────────────────────────────────────────────────────────────────────────
// T5 — portão de DEPLOY do `masked` tipado (ADR adr-masked-typed-declaration, D3)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Colhe as declarações TIPADAS de `masked` no flow — só as strings.
 *
 * `true`/`false` não entram: `true` resolve para `opaque`, que é um tipo real do
 * catálogo e não precisa de conferência; `false` não declara nada.
 *
 * Exportada porque o gate ancora nela: um predicado com nome é conferível, uma
 * condição inline no meio da rota não é.
 */
export function collectMaskedTypeRefs(flow: SkillFlow): Array<{ step: string; field: string; type: string }> {
  const out: Array<{ step: string; field: string; type: string }> = []
  const typed = (m: unknown): string | null =>
    (typeof m === "string" && m.trim().length > 0) ? m.trim() : null

  for (const step of flow.steps ?? []) {
    const s = step as FlowStep & {
      masked?: unknown
      fields?: Array<{ id?: string; masked?: unknown }>
      output_as?: string
    }
    const stepType = typed(s.masked)
    if (stepType) out.push({ step: step.id, field: s.output_as ?? step.id, type: stepType })
    for (const f of s.fields ?? []) {
      const ft = typed(f?.masked)
      if (ft) out.push({ step: step.id, field: f?.id ?? "?", type: ft })
    }
  }
  return out
}

/**
 * Confere as referências tipadas contra o catálogo VIVO do tenant.
 *
 * **Fail-closed, e o acoplamento é ESCOPADO**: um flow sem nenhuma declaração
 * tipada não busca o catálogo, logo não depende do config-api para ser salvo.
 * Só quem declara um tipo paga a dependência — e paga porque, sem conferir, o
 * deploy grava um id que ninguém resolve, e o defeito só aparece meses depois,
 * na transcrição, como um `masked_types` que não casa com tipo nenhum.
 *
 * Catálogo inalcançável ⇒ RECUSA. É a postura oposta à de leitura de relatório,
 * e deliberada: aqui não se pode verificar, e mascaramento é a política em que
 * "não sei" nunca pode virar "pode passar".
 *
 * Devolve lista de mensagens; vazia = aprovado.
 */
export async function validateMaskedTypeRefs(
  flow: SkillFlow,
  opts: { tenantId: string; configApiUrl: string; fetchImpl?: typeof fetch },
): Promise<string[]> {
  const refs = collectMaskedTypeRefs(flow)
  if (refs.length === 0) return []            // sem tipado ⇒ sem dependência

  const doFetch = opts.fetchImpl ?? fetch
  let ids: Set<string>
  try {
    const url  = `${opts.configApiUrl}/config/masking?tenant_id=${encodeURIComponent(opts.tenantId)}`
    const resp = await doFetch(url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const body = await resp.json() as { entries?: Record<string, unknown> }
    const raw  = body?.entries?.["types"]
    const cat  = (raw && typeof raw === "object" && "value" in (raw as object))
      ? (raw as { value: unknown }).value
      : raw
    const types = (cat as { types?: Array<{ id?: string }> } | undefined)?.types
    if (!Array.isArray(types) || types.length === 0) throw new Error("catálogo vazio ou ausente")
    ids = new Set(types.map(t => String(t?.id ?? "")).filter(Boolean))
  } catch (err) {
    // Degradação NUNCA silenciosa, e aqui nem sequer degrada: recusa NOMEANDO.
    return [
      `não foi possível conferir os tipos de masking contra o catálogo do tenant ` +
      `(${String(err)}). ${refs.length} referência(s) tipada(s) no flow — o deploy é ` +
      `recusado porque "não sei" não pode virar "pode passar" em mascaramento.`,
    ]
  }

  return refs
    .filter(r => !ids.has(r.type))
    .map(r =>
      `step "${r.step}", campo "${r.field}": masked: "${r.type}" não existe no catálogo ` +
      `masking.types do tenant. Tipos declarados: ${[...ids].sort().join(", ")}.`,
    )
}
