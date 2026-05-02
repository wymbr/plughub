/**
 * validators/skill.ts
 * Validação Zod dos payloads de skill + validações cross-step.
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
