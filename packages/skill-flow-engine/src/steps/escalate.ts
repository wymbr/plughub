/**
 * steps/escalate.ts
 * Executor do step type: escalate
 * Spec: PlugHub v24.0 seção 4.7 + 9.5i
 *
 * Deriva para pool via Rules Engine com pipeline_state como contexto.
 * O Rules Engine aloca o agente do pool, que recebe o pipeline_state
 * no context_package, executa, e retorna o controle ao orquestrador.
 */

import type { EscalateStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeEscalate(
  step: EscalateStep,
  ctx:  StepContext
): Promise<StepResult> {
  // Deriva para pool via conversation_escalate com pipeline_state completo
  await ctx.mcpCall("conversation_escalate", {
    session_id:     ctx.sessionId,
    target_pool:    step.target.pool,
    pipeline_state: ctx.state,
    error_reason:   step.error_reason,
  })

  // O Rules Engine atualiza o pipeline_state quando o agente do pool
  // sinaliza agent_done. O engine detecta isso via polling do pipeline_state.
  // Quando retornar, o step escalate terá seu resultado em state.results.
  return {
    next_step_id:      "__awaiting_escalation__",
    transition_reason: "on_success",
  }
}
