/**
 * steps/delegate.ts
 * Executor for step type: delegate.
 *
 * Architecture: docs/arcos/delegate-workflow-io.md
 *
 * The delegate step suspends the workflow and dispatches a specialist agent
 * to handle I/O with the customer. The agent calls workflow_resume (MCP tool)
 * when done, which triggers the webhook resume endpoint and resumes this step.
 *
 * Lifecycle:
 *   1. Generate resume_token (UUID)
 *   2. Write token to {tenant}:resume_tokens via persistSuspendWebhook
 *   3. Call persistDelegate → bridge creates child session in target pool
 *      with workflow_resume_token + caller context in ContextStore
 *      origin_session_id is propagated (Session A root)
 *   4. Return __suspended__
 *
 * Resume:
 *   When agent calls workflow_resume → POST /channels/webhook/resume/{token}
 *   → engine resumes with resumeContext.decision:
 *     "input" | "approved" → on_resume.next
 *     "rejected"            → on_reject.next (falls back to on_resume.next)
 *     "timeout"             → on_timeout.next
 *
 * Idempotency (same pattern as suspend):
 *   Sentinel key {step_id}:__delegated__ prevents double-dispatch on retry.
 *   On resume: {step_id}:__resume_decision__ stores the decision.
 */

import { randomUUID }    from "crypto"
import type { DelegateStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { resolveInputMap } from "../interpolate"

export async function executeDelegate(
  step: DelegateStep,
  ctx:  StepContext
): Promise<StepResult> {
  const sentinelKey    = `${step.id}:__delegated__`
  const tokenKey       = `${step.id}:__resume_token__`
  const expiresKey     = `${step.id}:__expires_at__`
  const decisionKey    = `${step.id}:__resume_decision__`

  // ── Resume path — agent called workflow_resume ──────────────────────────────
  if (ctx.resumeContext?.step_id === step.id) {
    const { decision, payload } = ctx.resumeContext

    const storedDecision = ctx.state.results[decisionKey] as string | undefined
    const effectiveDecision = storedDecision ?? decision

    if (!storedDecision) {
      ctx.state = {
        ...ctx.state,
        results: {
          ...ctx.state.results,
          [decisionKey]: effectiveDecision,
          [`${step.id}:__resume_payload__`]: payload,
        },
      }
      await ctx.saveState(ctx.state)
    }

    switch (effectiveDecision) {
      case "approved":
      case "input":
        return {
          next_step_id:      step.on_resume.next,
          output_as:         step.id,
          output_value:      payload,
          transition_reason: "resumed",
        }
      case "rejected":
        if (step.on_reject) {
          return {
            next_step_id:      step.on_reject.next,
            output_as:         step.id,
            output_value:      payload,
            transition_reason: "on_failure",
          }
        }
        // No on_reject — fall back to on_resume
        return {
          next_step_id:      step.on_resume.next,
          output_as:         step.id,
          output_value:      payload,
          transition_reason: "resumed",
        }
      case "timeout":
        return {
          next_step_id:      step.on_timeout.next,
          output_as:         step.id,
          output_value:      payload,
          transition_reason: "on_failure",
        }
    }
  }

  // ── Already delegated in a prior run (replay idempotency) ──────────────────
  const storedDecision = ctx.state.results[decisionKey] as string | undefined
  if (storedDecision) {
    const payload = ctx.state.results[`${step.id}:__resume_payload__`] ?? {}
    if (storedDecision === "rejected" && step.on_reject) {
      return { next_step_id: step.on_reject.next, output_as: step.id, output_value: payload, transition_reason: "on_failure" }
    }
    if (storedDecision === "timeout") {
      return { next_step_id: step.on_timeout.next, output_as: step.id, output_value: payload, transition_reason: "on_failure" }
    }
    return { next_step_id: step.on_resume.next, output_as: step.id, output_value: payload, transition_reason: "resumed" }
  }

  // ── Already suspended (sentinel set) — idempotent re-entry ─────────────────
  if (ctx.state.results[sentinelKey] === "delegated") {
    return { next_step_id: "__suspended__", transition_reason: "suspended" }
  }

  // ── First execution — suspend + dispatch agent ──────────────────────────────

  // 0. Resolve the TARGET POOL. Ele aceita ref (@ctx.* / $.pipeline_state.*) porque o
  // alvo pode ser fato do CHAMADOR, não do skill: um skill de hook genérico (wrap-up)
  // precisa delegar na fila interna do pool que o disparou, e lê "@ctx.hook.wrapup_pool"
  // — escrito pelo bridge em _fire_detached_hook. Ver ADR
  // adr-internal-work-queue-author-bound.
  //
  // Resolvido ANTES de qualquer efeito colateral (o resume_token e o persistSuspendWebhook
  // vêm depois): falhar aqui não deixa token órfão nem sentinela pela metade.
  //
  // Ref não resolvida é FALHA DURA — nunca passa adiante como literal. Roteando para um
  // pool chamado "@ctx.hook.wrapup_pool" o erro apareceria como "pool not found",
  // apontando para o registry em vez de para a tag ausente, que é a causa real.
  let targetPool = step.pool
  if (step.pool.startsWith("@") || step.pool.startsWith("$.")) {
    const m = await resolveInputMap({ v: step.pool }, ctx, ctx.contextStore)
    const v = m["v"]
    const resolved = v === undefined || v === null ? "" : String(v).trim()
    if (resolved === "" || resolved === step.pool) {
      const msg =
        `[delegate] step=${step.id}: pool ref "${step.pool}" não resolveu ` +
        `(session=${ctx.sessionId}). O delegate NÃO foi despachado.`
      console.error(msg)
      return {
        next_step_id:      step.on_timeout.next,
        output_as:         step.id,
        output_value:      { error: "pool_ref_unresolved", ref: step.pool },
        transition_reason: "on_failure",
      }
    }
    targetPool = resolved
  }

  // 0b. Resolve o PRAZO. Aceita ref porque pode ser fato do CHAMADOR: o prazo de ACW
  // é do POOL DE ORIGEM (@ctx.hook.acw_timeout_hours, via PoolHookEntry.context), não
  // deste skill genérico.
  //
  // Ao contrário do `pool`, ref não resolvida aqui NÃO é falha dura — um prazo tem
  // default seguro, um pool não tem para onde rotear. Mas degrada COM log: prazo
  // errado em silêncio é justamente o valor plausível que nunca denuncia nada.
  const DEFAULT_TIMEOUT_HOURS = 24
  let timeoutHours = DEFAULT_TIMEOUT_HOURS
  if (typeof step.timeout_hours === "number") {
    timeoutHours = step.timeout_hours
  } else {
    const m = await resolveInputMap({ v: step.timeout_hours }, ctx, ctx.contextStore)
    const n = Number(m["v"])
    if (Number.isFinite(n) && n > 0) {
      timeoutHours = n
    } else {
      console.warn(
        `[delegate] step=${step.id}: timeout_hours ref "${step.timeout_hours}" não ` +
        `resolveu para um número positivo (valor=${JSON.stringify(m["v"])}); ` +
        `usando o default de ${DEFAULT_TIMEOUT_HOURS}h (session=${ctx.sessionId}).`
      )
    }
  }

  const resume_token = randomUUID()

  // 1. Persist resume_token (extends Redis TTLs + writes to resume_tokens hash)
  let expires_at = new Date(
    Date.now() + timeoutHours * 3600 * 1000
  ).toISOString()

  if (ctx.persistSuspendWebhook) {
    try {
      const result = await ctx.persistSuspendWebhook({
        step_id:       step.id,
        resume_token,
        timeout_hours: timeoutHours,
        ...(step.business_hours !== undefined ? { business_hours: step.business_hours } : {}),
        ...(step.calendar_id                 ? { calendar_id:    step.calendar_id }    : {}),
      })
      expires_at = result.resume_expires_at
    } catch (err) {
      // Non-fatal — fall back to wall-clock deadline
      console.warn(`[delegate] persistSuspendWebhook failed (step=${step.id}):`, err)
    }
  }

  // 2. Resolve context entries — interpolate @ctx.* and $.pipeline_state.*
  // Uses the same resolveInputMap used by the invoke step so @ctx.* and
  // $.pipeline_state.* references are properly resolved before being written
  // to the child session's ContextStore.
  const resolvedContext: Record<string, string> = {}
  if (step.context) {
    const resolved = await resolveInputMap(
      step.context as Record<string, unknown>,
      ctx,
      ctx.contextStore,
    )
    for (const [key, value] of Object.entries(resolved)) {
      if (value !== undefined && value !== null) {
        resolvedContext[key] = String(value)
      }
    }
  }

  // Camada B (pull direcionado) — resolve assigned_to (aceita ref @ctx.*/$.* via o
  // mesmo resolveInputMap do context; o wrap-up usa @ctx.session.surveyed_agent_key).
  let resolvedAssignedTo: string | undefined
  if (step.assigned_to) {
    const m = await resolveInputMap({ v: step.assigned_to }, ctx, ctx.contextStore)
    const v = m["v"]
    if (v !== undefined && v !== null && String(v).trim() !== "") {
      resolvedAssignedTo = String(v)
    }
  }

  // Wrap-up unificado (Camada E2) — resolve auto_attend (ref/literal → booleano).
  // O bridge seta @ctx.session.wrap_up_auto_attend="true" quando o hook é inline;
  // o Console usa este flag no item de pull para auto-reivindicar (auto-atendimento).
  let resolvedAutoAttend = false
  if (step.auto_attend) {
    const m = await resolveInputMap({ v: step.auto_attend }, ctx, ctx.contextStore)
    const v = m["v"]
    resolvedAutoAttend = v === true || String(v).trim().toLowerCase() === "true"
  }

  // 3. Dispatch child session via persistDelegate callback (wired by bridge)
  let child_session_id = ""
  if (ctx.persistDelegate) {
    try {
      const result = await ctx.persistDelegate({
        step_id:           step.id,
        resume_token,
        pool:              targetPool,
        context:           resolvedContext,
        timeout_hours:     timeoutHours,
        origin_session_id: (ctx.sessionContext["origin_session_id"] as string | undefined)
                           ?? ctx.sessionId,  // propagate root or self as root
        // Identity Resolver (nível b) — forward retomada policy so the
        // channel-gateway gates the pending_by_customer dual-write.
        customer_resumable: step.customer_resumable,
        resume_policy:      step.resume_policy,
        // Camada B (pull direcionado / "ramal") — reserva do work item ao recurso.
        ...(resolvedAssignedTo ? { assigned_to: resolvedAssignedTo } : {}),
        ...(step.fallback_to_pool_after_s !== undefined
          ? { fallback_to_pool_after_s: step.fallback_to_pool_after_s } : {}),
        // Wrap-up unificado (Camada E2) — auto-atendimento no Console (inline).
        ...(resolvedAutoAttend ? { auto_attend: true } : {}),
      })
      child_session_id = result.child_session_id
    } catch (err) {
      // Dispatch failed — cannot delegate; go to on_timeout as safe fallback
      console.error(`[delegate] persistDelegate failed (step=${step.id}):`, err)
      return {
        next_step_id:      step.on_timeout.next,
        output_as:         step.id,
        output_value:      { error: String(err) },
        transition_reason: "on_failure",
      }
    }
  } else {
    // No persistDelegate wired — cannot create child session; this is a
    // configuration error. Return on_timeout so the workflow does not hang.
    console.error(
      `[delegate] ctx.persistDelegate not wired — step=${step.id}. ` +
      "Wire persistDelegate in orchestrator-bridge for webhook sessions."
    )
    return {
      next_step_id:      step.on_timeout.next,
      output_as:         step.id,
      output_value:      { error: "persistDelegate_not_wired" },
      transition_reason: "on_failure",
    }
  }

  // 4. Save sentinel + token metadata
  ctx.state = {
    ...ctx.state,
    results: {
      ...ctx.state.results,
      [tokenKey]:   resume_token,
      [expiresKey]: expires_at,
      [`${step.id}:__child_session_id__`]: child_session_id,
      [sentinelKey]: "delegated",
    },
  }
  await ctx.saveState(ctx.state)

  return { next_step_id: "__suspended__", transition_reason: "suspended" }
}
