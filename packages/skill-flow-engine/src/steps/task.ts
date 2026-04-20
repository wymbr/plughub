/**
 * steps/task.ts
 * Executor do step type: task
 * Spec: PlugHub v24.0 seção 4.7 + 9.5i
 *
 * Idempotência:
 *   Antes de chamar agent_delegate, verifica se já existe um job_id ativo
 *   para este step (persitido em Redis). Se sim, pula o delegate e retoma
 *   o polling — nunca duplica uma delegação.
 *
 * execution_mode:
 *   sync  — fire-and-poll: aguarda conclusão na mesma chamada do engine.
 *   async — fire-and-return: persiste job_id e retorna __awaiting_task__;
 *           quando chamado novamente, verifica status uma vez.
 */

import { JSONPath } from "jsonpath-plus"
import type { TaskStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

/** Intervalo e limite de polling para agent_delegate_status (modo sync). */
const POLL_INTERVAL_MS  = 2_000
const POLL_MAX_ATTEMPTS = 150   // 5 min máximo

export async function executeTask(
  step: TaskStep,
  ctx:  StepContext,
): Promise<StepResult> {
  // ── Idempotência: verificar job_id existente ──────────────────────────────
  let jobId = await ctx.getJobId(step.id)

  if (!jobId) {
    // Primeira execução — disparar agent_delegate
    // target.skill_id pode ser literal ou referência JSONPath ($.pipeline_state.xxx)
    const evalContext = { pipeline_state: ctx.state.results, session: ctx.sessionContext }
    const skillId = step.target.skill_id.startsWith("$.")
      ? String(JSONPath({ path: step.target.skill_id, json: evalContext as object, wrap: false }) ?? step.target.skill_id)
      : step.target.skill_id

    const delegateResult = await ctx.mcpCall("agent_delegate", {
      session_id:    ctx.sessionId,
      target_skill:  skillId,
      payload: {
        customer_id:      ctx.customerId,
        pipeline_step:    step.id,
        pipeline_context: ctx.state.results,
      },
      delegation_mode: "silent",
    }) as { job_id: string; status: string }

    jobId = delegateResult.job_id

    // Persistir job_id ANTES do polling — garante retomada sem re-delegação
    await ctx.setJobId(step.id, jobId)
  }
  // Else: retomando após falha do engine — job_id já registrado, pular delegate

  // ── Execução pelo modo declarado no step ──────────────────────────────────
  if (step.execution_mode === "async") {
    return executeTaskAsync(step, ctx, jobId)
  }

  return executeTaskSync(step, ctx, jobId)
}

// ─────────────────────────────────────────────
// Modo sync — fire-and-poll
// ─────────────────────────────────────────────

async function executeTaskSync(
  step:  TaskStep,
  ctx:   StepContext,
  jobId: string,
): Promise<StepResult> {
  for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
    await sleep(POLL_INTERVAL_MS)

    const pollResult = await ctx.mcpCall("agent_delegate_status", {
      job_id:     jobId,
      session_id: ctx.sessionId,
    }) as { status: string; outcome?: string; result?: unknown }

    if (pollResult.status === "completed") {
      await ctx.clearJobId(step.id)
      return makeCompletedResult(step, pollResult)
    }

    if (pollResult.status === "failed") {
      await ctx.clearJobId(step.id)
      return makeFailedResult(step, jobId)
    }
    // status === "queued" | "running" → continuar polling
  }

  // Timeout de polling — tratar como falha (job_id permanece para debug)
  return {
    next_step_id:      step.on_failure,
    output_as:         step.id,
    output_value:      { error: "poll_timeout", job_id: jobId },
    transition_reason: "on_failure",
  }
}

// ─────────────────────────────────────────────
// Modo async — fire-and-return
// ─────────────────────────────────────────────
//
// O engine persiste o pipeline_state com o job_id em results e retorna
// imediatamente com outcome "awaiting_task".
//
// Quando o agente delegado conclui, o webhook atualiza o Redis via
// Kafka consumer (nunca direto ao MCP). O engine é reacionado e, ao
// retomar o mesmo step, encontra o job_id e checa o status uma única vez.

async function executeTaskAsync(
  step:  TaskStep,
  ctx:   StepContext,
  jobId: string,
): Promise<StepResult> {
  const pollResult = await ctx.mcpCall("agent_delegate_status", {
    job_id:     jobId,
    session_id: ctx.sessionId,
  }) as { status: string; outcome?: string; result?: unknown }

  if (pollResult.status === "completed") {
    await ctx.clearJobId(step.id)
    return makeCompletedResult(step, pollResult)
  }

  if (pollResult.status === "failed") {
    await ctx.clearJobId(step.id)
    return makeFailedResult(step, jobId)
  }

  // Ainda em execução — persistir job_id no pipeline_state para retomada
  // e sinalizar ao engine que deve aguardar
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [`${step.id}:__job_id__`]: jobId },
  }
  await ctx.saveState(ctx.state)

  return {
    next_step_id:      "__awaiting_task__",
    transition_reason: "on_success",
  }
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function makeCompletedResult(
  step:   TaskStep,
  result: { outcome?: string; result?: unknown },
): StepResult {
  const outcome = result.outcome ?? "resolved"
  return {
    next_step_id:      outcome === "resolved" ? step.on_success : step.on_failure,
    output_as:         step.id,
    output_value:      result.result ?? null,
    transition_reason: outcome === "resolved" ? "on_success" : "on_failure",
  }
}

function makeFailedResult(step: TaskStep, jobId: string): StepResult {
  return {
    next_step_id:      step.on_failure,
    output_as:         step.id,
    output_value:      { error: "agent_delegate_failed", job_id: jobId },
    transition_reason: "on_failure",
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
