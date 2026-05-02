/**
 * steps/complete.ts
 * Executor do step type: complete
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Encerra o pipeline com o outcome declarado.
 * O engine sinaliza agent_done com o outcome do step.
 */

import type { CompleteStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export function executeComplete(
  step: CompleteStep,
  _ctx: StepContext
): StepResult {
  return {
    next_step_id:      "__complete__",
    outcome:           step.outcome,
    transition_reason: "on_success",
  }
}
