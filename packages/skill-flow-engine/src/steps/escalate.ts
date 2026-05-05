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
  try {
    await ctx.mcpCall("conversation_escalate", {
      session_id:     ctx.sessionId,
      target_pool:    step.target.pool,
      pipeline_state: ctx.state,
      error_reason:   step.error_reason,
    })
  } catch (err) {
    // Graceful degradation — same pattern as notify/invoke steps.
    // Without this, an MCP failure would propagate as an uncaught exception,
    // crashing the engine (500) and causing the bridge to close the session.
    const msg = err instanceof Error ? err.message : String(err)
    console.error(
      "[escalate] conversation_escalate failed for session=" + ctx.sessionId +
      " target_pool=" + step.target.pool + ": " + msg,
    )
    // If on_failure is defined, transition there; otherwise complete with error
    // to avoid a crash from undefined step id.
    if (step.on_failure) {
      return {
        next_step_id:      step.on_failure,
        transition_reason: "on_failure",
      }
    }
    return {
      next_step_id:      "__complete__",
      outcome:           "error",
      transition_reason: "on_failure",
    }
  }

  // O Rules Engine atualiza o pipeline_state quando o agente do pool
  // sinaliza agent_done. O engine detecta isso via polling do pipeline_state.
  // Quando retornar, o step escalate terá seu resultado em state.results.
  return {
    next_step_id:      "__awaiting_escalation__",
    transition_reason: "on_success",
  }
}
