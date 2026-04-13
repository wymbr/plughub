/**
 * executor.ts
 * Executa um step individual e retorna o id do próximo step.
 * Spec: PlugHub v24.0 seções 4.7 e 9.5i
 */

import type { Redis } from "ioredis"
import type {
  FlowStep,
  PipelineState,
  CatchStrategy,
} from "@plughub/schemas"

import { executeTask }     from "./steps/task"
import { executeChoice }   from "./steps/choice"
import { executeCatch }    from "./steps/catch"
import { executeEscalate } from "./steps/escalate"
import { executeComplete } from "./steps/complete"
import { executeInvoke }   from "./steps/invoke"
import { executeReason }   from "./steps/reason"
import { executeNotify }   from "./steps/notify"
import { executeMenu }     from "./steps/menu"

// ─────────────────────────────────────────────
// Tipos de contexto e resultado de step
// ─────────────────────────────────────────────

export interface StepContext {
  tenantId:       string
  sessionId:      string
  customerId:     string
  sessionContext: Record<string, unknown>
  state:          PipelineState

  /** Redis client — used by menu step for BLPOP (awaiting customer reply) */
  redis:          Redis

  /** Chama uma tool no mcp-server-plughub */
  mcpCall(tool: string, input: unknown, mcpServer?: string): Promise<unknown>

  /** Chama o AI Gateway para steps reason */
  aiGatewayCall(payload: {
    prompt_id:     string
    input:         Record<string, unknown>
    output_schema: Record<string, unknown>
    session_id:    string
    attempt:       number
  }): Promise<unknown>

  /** Persiste o pipeline_state atual */
  saveState(state: PipelineState): Promise<void>

  /** Reexecuta o step referenciado (para retry em catch) */
  retryStep(stepId: string): Promise<StepResult>

  /** Executa um fallback alternativo (para fallback em catch) */
  executeFallback(strategy: CatchStrategy & { type: "fallback" }): Promise<StepResult>

  /** Retorna o job_id ativo de um step (idempotência do agent_delegate). */
  getJobId(stepId: string): Promise<string | null>

  /** Persiste o job_id de um step antes de iniciar o polling. */
  setJobId(stepId: string, jobId: string): Promise<void>

  /** Remove o job_id após conclusão do step. */
  clearJobId(stepId: string): Promise<void>

  /**
   * Renova o TTL do execution lock para esta instância.
   * Deve ser chamado por steps de longa duração (ex: menu) antes de bloquear
   * para garantir que o lock não expira durante o BLPOP.
   *
   * Retorna false se o lock foi tomado por outra instância (crash recovery):
   * o step deve abortar graciosamente e retornar on_failure.
   *
   * @param ttlSeconds - novo TTL em segundos a partir de agora
   */
  renewLock(ttlSeconds: number): Promise<boolean>
}

export interface StepResult {
  /** ID do próximo step. "__complete__" = pipeline encerrado. "__awaiting_escalation__" = aguardando. */
  next_step_id:      string
  /** Chave para persistir output no pipeline_state (steps que produzem resultado) */
  output_as?:        string
  output_value?:     unknown
  transition_reason: PipelineState["transitions"][number]["reason"]
  /** Outcome final — apenas steps complete */
  outcome?:          string
}

// ─────────────────────────────────────────────
// executeStep — dispatch por tipo
// ─────────────────────────────────────────────

export async function executeStep(
  step: FlowStep,
  ctx:  StepContext
): Promise<StepResult> {
  switch (step.type) {
    case "task":     return executeTask(step, ctx)
    case "choice":   return executeChoice(step, ctx)
    case "catch":    return executeCatch(step, ctx)
    case "escalate": return executeEscalate(step, ctx)
    case "complete": return executeComplete(step, ctx)
    case "invoke":   return executeInvoke(step, ctx)
    case "reason":   return executeReason(step, ctx)
    case "notify":   return executeNotify(step, ctx)
    case "menu":     return executeMenu(step, ctx)
    default:
      // TypeScript garante exhaustiveness via discriminated union
      throw new Error(`Tipo de step desconhecido: ${(step as FlowStep).type}`)
  }
}
