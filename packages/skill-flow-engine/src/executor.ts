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
import type { IContextStore } from "./context-types"

import { executeTask }             from "./steps/task"
import { executeChoice }           from "./steps/choice"
import { executeCatch }            from "./steps/catch"
import { executeEscalate }         from "./steps/escalate"
import { executeComplete }         from "./steps/complete"
import { executeInvoke }           from "./steps/invoke"
import { executeReason }           from "./steps/reason"
import { executeNotify }           from "./steps/notify"
import { executeMenu }             from "./steps/menu"
import { executeSuspend }          from "./steps/suspend"
import { executeCollect }          from "./steps/collect"
import { executeBeginTransaction } from "./steps/begin-transaction"
import { executeEndTransaction }   from "./steps/end-transaction"
import { executeResolve }          from "./steps/resolve"
import { executeReceive }          from "./steps/receive"
import { executeDelegate }         from "./steps/delegate"

// ─────────────────────────────────────────────
// Tipos de contexto e resultado de step
// ─────────────────────────────────────────────

export interface StepContext {
  tenantId:       string
  sessionId:      string
  /** Agent instance_id — used by menu step to set the active_instance flag for CrashDetector. Optional for backward compat. */
  instanceId?:    string
  /** Segment UUID for segment-scoped ContextStore writes (scope: segment in YAML). Optional. */
  segmentId?:     string
  /**
   * Arc 16 — Journey ContextStore namespace.
   * When present, @ctx.journey.* reads/writes target {tenant}:ctx:journey:{journeyId}
   * instead of the session hash. Provided by the skill-flow-worker when the
   * workflow instance has a journey_id.
   */
  journeyId?:     string
  customerId:     string
  sessionContext: Record<string, unknown>
  state:          PipelineState

  /** Redis client — used by menu step for BLPOP (awaiting customer reply) */
  redis:          Redis

  /**
   * ContextStore unificado — acesso a @ctx.namespace.campo.
   * Opcional para retrocompatibilidade; steps que usam @ctx.* requerem este campo.
   */
  contextStore?:  IContextStore

  /** Chama uma tool no mcp-server-plughub */
  mcpCall(tool: string, input: unknown, mcpServer?: string): Promise<unknown>

  /** Chama o AI Gateway para steps reason */
  aiGatewayCall(payload: {
    prompt_id:     string
    input:         Record<string, unknown>
    output_schema: Record<string, unknown>
    session_id:    string
    attempt:       number
    json_schema?:  Record<string, unknown>   // T7b — tool-use nativo quando presente
    model_profile?: string                    // R8d — perfil de modelo (revisor heterogêneo)
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
   * Arc 19 — Optional. Wired by orchestrator-bridge for webhook (workflow) sessions.
   * Replaces the wall-clock fallback for webhook pools: instead of writing to
   * PostgreSQL, this callback:
   *   1. Extends the TTL of all Redis session keys (stream, ctx, pipeline,
   *      resume_tokens) by (timeout_hours * 3600 + 3600) seconds (+1h buffer).
   *   2. Writes the resume_token to the {tenant}:resume_tokens hash:
   *      field = resume_token, value = "{session_id}:{step_id}:{expires_at}"
   *   3. Returns the wall-clock deadline as resume_expires_at.
   *
   * Priority: persistSuspend (Arc 4, PostgreSQL) → persistSuspendWebhook (Arc 19,
   * Redis-only) → wall-clock fallback. Used when persistSuspend is absent.
   */
  persistSuspendWebhook?(params: {
    step_id:        string
    resume_token:   string
    timeout_hours:  number
    // Arc 19 Fase D: forwarded from the suspend step so the skill-flow-service
    // can call the calendar-api for business-hours deadline calculation.
    business_hours?: boolean
    calendar_id?:    string
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
    channel?:       string   // optional — channel-gateway selects by requires[] when absent
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
   * Arc 19 delegate step — wired by orchestrator-bridge for webhook sessions.
   * Called when a delegate step executes for the first time.
   * Responsibilities:
   *   1. Create a child session in the target pool via routing engine
   *      (publishes conversations.inbound with pool_id, origin_session_id)
   *   2. Write context entries + workflow_resume_token to child ContextStore
   *   3. origin_session_id is set to the root session (Session A) by the caller
   * Returns the child_session_id for tracing.
   * If absent, the delegate step falls back to on_timeout (config error).
   */
  persistDelegate?(params: {
    step_id:           string
    resume_token:      string
    pool:              string
    context:           Record<string, string>
    timeout_hours:     number
    origin_session_id: string
  }): Promise<{ child_session_id: string }>

  /**
   * When set, indicates this is a resume run rather than a fresh suspend.
   * The suspend step reads this instead of suspending again.
   */
  resumeContext?: {
    decision:  "approved" | "rejected" | "input" | "timeout"
    step_id:   string   // which suspend step is being resumed
    payload:   Record<string, unknown>
  }

  // ── Masked input — transação atômica ──────────────────────────────────────

  /**
   * Escopo em memória para valores sensíveis capturados em steps masked.
   * Nunca escrito em Redis, pipeline_state ou stream.
   * Limpo pelo end_transaction (sucesso) ou pelo engine (falha → rewind).
   * Chave = field_id do FormField; valor = dado sensível em texto claro.
   * Sempre presente (objeto vazio quando fora de transação).
   */
  maskedScope: Record<string, string>

  /**
   * Step de rewind declarado no begin_transaction.on_failure.
   * Presente enquanto estamos dentro de um bloco begin/end_transaction.
   * Usado pelo engine para detectar falha dentro de transação e fazer rewind.
   * Limpo pelo end_transaction (sucesso) ou pelo engine (rewind executado).
   * null quando fora de bloco de transação.
   */
  transactionOnFailure: string | null
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
    case "suspend":           return executeSuspend(step, ctx)
    case "collect":           return executeCollect(step, ctx)
    case "begin_transaction": return executeBeginTransaction(step, ctx)
    case "end_transaction":   return executeEndTransaction(step, ctx)
    case "resolve":           return executeResolve(step, ctx)
    case "receive":           return executeReceive(step, ctx)
    case "delegate":          return executeDelegate(step, ctx)
    default:
      // TypeScript garante exhaustiveness via discriminated union
      throw new Error(`Tipo de step desconhecido: ${(step as FlowStep).type}`)
  }
}
