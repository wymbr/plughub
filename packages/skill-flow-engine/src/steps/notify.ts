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

import type { NotifyStep, ContextTagEntry } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { interpolate, resolveVisibility } from "../interpolate"
import { extractOutputsToCtx } from "../context-accumulator-util"

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

  const message = await interpolate(step.message, ctx, ctx.contextStore)
  const resolvedVisibility = await resolveVisibility(step.visibility ?? "all", ctx, ctx.contextStore)

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
      visibility: resolvedVisibility,
      ...(ctx.segmentId ? { segment_id: ctx.segmentId } : {}),
      ...(ctx.instanceId ? { instance_id: ctx.instanceId } : {}),
    })

    // ── Fase 2: gravar sentinel "completed" após envio ───────────────────
    ctx.state = {
      ...ctx.state,
      results: { ...ctx.state.results, [sentinelKey]: "completed" },
    }
    await ctx.saveState(ctx.state)

    // ── context_tags.outputs: escrever valores do pipeline_state no ContextStore ──
    // F1.4b (bancada de agentes): o notify NUNCA implementou context_tags — os
    // YAMLs (agente_wrapup_v1, agente_nps_v1) declaravam e o engine ignorava em
    // silêncio. Semântica: o dotPath resolve sobre pipeline_state.results (ex.:
    // o `output_as` de um menu anterior), espelhando o uso documentado nos YAMLs
    // ("dotPath key deve coincidir com output_as do menu anterior").
    // Fire-and-forget — não bloqueia a transição do step (mesmo padrão do invoke).
    if (step.context_tags?.outputs && ctx.contextStore) {
      extractOutputsToCtx(
        ctx.contextStore,
        ctx.sessionId,
        ctx.customerId,
        step.context_tags.outputs as Record<string, ContextTagEntry>,
        ctx.state.results,
        `notify:${step.id}`,
        ctx.segmentId,
        ctx.journeyId,
      ).catch(err => {
        console.error("[notify] CTX_OUTPUT_EXTRACTION_FAILED", String(err))
      })
    }

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
