/**
 * steps/complete.ts
 * Executor do step type: complete
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Encerra o pipeline com o outcome declarado.
 * O engine sinaliza agent_done com o outcome do step.
 *
 * F1.2 (bancada de agentes — docs/arcos/analytics-agents-workbench.md §13):
 * outcome dinâmico. Se `outcome_from` estiver presente, resolve o valor da chave
 * correspondente em pipeline_state (ex.: `output_as` de um menu) e valida contra o
 * domínio canônico SegmentOutcomeSchema. Valor ausente/inválido → cai no `outcome`
 * literal (fallback obrigatório do YAML). Mantém o step síncrono — pipeline_state
 * é in-memory (ctx.state.results), mesmo acesso usado pelo choice step.
 */

import { SegmentOutcomeSchema } from "@plughub/schemas"
import type { CompleteStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export function executeComplete(
  step: CompleteStep,
  ctx:  StepContext
): StepResult {
  let outcome: string = step.outcome

  if (step.outcome_from) {
    const raw = ctx.state?.results?.[step.outcome_from]
    if (typeof raw === "string" && SegmentOutcomeSchema.safeParse(raw).success) {
      outcome = raw
    }
    // else: mantém o literal step.outcome como fallback explícito
  }

  return {
    next_step_id:      "__complete__",
    outcome,
    transition_reason: "on_success",
  }
}
