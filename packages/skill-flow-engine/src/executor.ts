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
import { executeSuspend }  from "./steps/suspend"
import { executeCollect }  from "./steps/collect"

// ─────────────────────────────────────────────
// Tipos de contexto e resultado de step
// ─────────────────────────────────────────────

export interface StepContext {
  tenantId:       string
  sessionId:      string
  /** Agent instance_id — used by menu step to set the active_instance flag for CrashDetector. Optional for backward compat. */
  instanceId?:    string
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
   *
   * Opcional — apenas o menu step utiliza. Outros steps não precisam implementar.
   * Se ausente, o menu step assume que o lock não expira (safe default).
   */
  renewLock?(ttlSeconds: number): Promise<boolean>

  // ── Arc 4: Workflow suspend / resume ──────────────────────────────────────

  /**
   * Persists the WorkflowInstance to PostgreSQL and calculates the deadline.
   * Called by the suspend step. Caller (workflow-api) wires this up.
   * If absent, the suspend step falls back to wall-clock hours.
   */
  persistSuspend?(params: {
    step_id:       string
    resume_token:  string
    reason:        string
    timeout_hours: number
    business_hours: boolean
    calendar_id?:  string
    metadata?:     Record<string, unknown>
  }): Promise<{ resume_expires_at: string }>

  /**
   * Creates a collect_instance in PostgreSQL, calculates send_at and expires_at
   * using the calendar-api, and publishes collect.requested to Kafka.
   * Called by the collect step. Caller (workflow-api worker) wires this up.
   * If absent, the collect step falls back to wall-clock times.
   */
  persistCollect?(params: {
    step_id:        string
    collect_token:  string
    target:         { type: string; id: string }
    channel:        string
    interaction:    string
    prompt:         string
    options?:       Array<{ id: string; label: string }>
    fields?:        Array<{ id: string; label: string; type: string }>
    scheduled_at?:  string
    delay_hours?:   number
    timeout_hours:  number
    business_hours: boolean
    calendar_id?:   string
    campaign_id?:   string
  }): Promise<{ send_at: string; expires_at: string }>

  /**
   * When set, indicates this is a resume run rather than a fresh suspend.
   * The suspend step reads this instead of suspending again.
   */
  resumeContext?: {
    decision:  "approved" | "rejected" | "input" | "timeout"
    step_id:   string   // which suspend step is being resumed
    payload:   Record<string, unknown>
  }
}

export interface StepResult {
  /**
   * ID do próximo step.
   * Special values: "__complete__", "__awaiting_task__", "__awaiting_escalation__", "__suspended__"
   */
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
    case "suspend":  return executeSuspend(step, ctx)
    case "collect":  return executeCollect(step, ctx)
    default:
      // TypeScript garante exhaustiveness via discriminated union
      throw new Error(`Tipo de step desconhecido: ${(step as FlowStep).type}`)
  }
}
