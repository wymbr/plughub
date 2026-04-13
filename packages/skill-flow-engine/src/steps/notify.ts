/**
 * steps/notify.ts
 * Executor do step type: notify
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Envia mensagem ao cliente via Notification Agent.
 * Suporta personalização dinâmica com {{$.pipeline_state.*}}.
 * Operação unidirecional — não aguarda resposta do cliente.
 *
 * Idempotência (sentinel de dois estágios):
 *   notify é especialmente crítico — uma mensagem duplicada é visível ao cliente.
 *   O mesmo padrão sentinela de invoke.ts é aplicado aqui:
 *
 *   - sentinel "completed": notificação já enviada → pular sem re-chamar MCP.
 *   - sentinel "dispatched": crash residual antes de salvar "completed" → re-enviar.
 *   - sem sentinel: primeira execução normal.
 */

import type { NotifyStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

/** Regex para interpolação dinâmica: {{$.pipeline_state.campo}} */
const INTERPOLATION_REGEX = /\{\{([\$\.][^}]+)\}\}/g

export async function executeNotify(
  step: NotifyStep,
  ctx:  StepContext
): Promise<StepResult> {
  const sentinelKey = `${step.id}:__notified__`

  // ── Idempotência: mensagem já enviada em execução anterior ────────────────
  if (ctx.state.results[sentinelKey] === "completed") {
    return {
      next_step_id:      step.on_success,
      transition_reason: "on_success",
    }
  }

  const message = interpolateMessage(step.message, ctx)

  // ── Fase 1: gravar sentinel "dispatched" antes de enviar ─────────────────
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [sentinelKey]: "dispatched" },
  }
  await ctx.saveState(ctx.state)

  try {
    await ctx.mcpCall("notification_send", {
      session_id: ctx.sessionId,
      message,
      channel:    step.channel ?? "session",
    })

    // ── Fase 2: gravar sentinel "completed" após envio ───────────────────
    ctx.state = {
      ...ctx.state,
      results: { ...ctx.state.results, [sentinelKey]: "completed" },
    }
    await ctx.saveState(ctx.state)

    return {
      next_step_id:      step.on_success,
      transition_reason: "on_success",
    }
  } catch {
    // Sentinel permanece "dispatched" — na retomada, re-envia a notificação.
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }
}

/**
 * Interpola referências dinâmicas na mensagem.
 * {{$.pipeline_state.protocolo}} → valor do pipeline_state
 */
function interpolateMessage(template: string, ctx: StepContext): string {
  return template.replace(INTERPOLATION_REGEX, (_, path: string) => {
    const parts = path.replace(/^\$\./, "").split(".")
    let current: unknown = { pipeline_state: ctx.state.results, session: ctx.sessionContext }
    for (const part of parts) {
      if (current == null || typeof current !== "object") return ""
      current = (current as Record<string, unknown>)[part]
    }
    return current != null ? String(current) : ""
  })
}
