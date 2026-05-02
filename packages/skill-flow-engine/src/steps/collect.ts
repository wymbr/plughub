/**
 * steps/collect.ts
 * Executor for step type: collect (Arc 4 extension)
 *
 * Contacts a target (customer / agent / external) via any channel, presents
 * a structured interaction (text / button / form), and suspends the workflow
 * until the target responds or the deadline expires.
 *
 * Timing:
 *   send_at   = scheduled_at ?? (now + delay_hours) ?? now
 *   expires_at = send_at + timeout_hours (calendar-aware when business_hours=true)
 *
 * Both are delegated to ctx.persistCollect — the workflow-api calculates the
 * actual datetimes using the calendar-api and persists the collect_instance.
 *
 * Idempotency follows the same two-stage sentinel pattern as suspend.ts:
 *   1. Write collect_token + sentinel "collecting" → saveState
 *   2. Call persistCollect (creates collect_instance, publishes collect.requested)
 *   3. Write sentinel "collected" → saveState → return __suspended__
 *
 * Resume:
 *   When channel-gateway closes the collect session it publishes collect.responded.
 *   workflow-api receives it, looks up the instance by collect_token, and calls
 *   engine.run() with resumeContext { step_id, decision: "input", payload: response_data }.
 *   On timeout: workflow-api scanner fires engine.run() with decision: "timeout".
 */

import { randomUUID }     from "crypto"
import type { CollectStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeCollect(
  step: CollectStep,
  ctx:  StepContext
): Promise<StepResult> {
  const sentinelKey  = `${step.id}:__collected__`
  const tokenKey     = `${step.id}:__collect_token__`
  const sendAtKey    = `${step.id}:__send_at__`
  const expiresKey   = `${step.id}:__expires_at__`
  const decisionKey  = `${step.id}:__collect_decision__`

  // ── Resume path — response or timeout signal received ─────────────────────
  if (ctx.resumeContext?.step_id === step.id) {
    const { decision, payload } = ctx.resumeContext

    // Idempotency: if decision already recorded, follow the same path
    const storedDecision = ctx.state.results[decisionKey] as string | undefined
    const effectiveDecision = storedDecision ?? decision

    if (!storedDecision) {
      ctx.state = {
        ...ctx.state,
        results: {
          ...ctx.state.results,
          [decisionKey]: effectiveDecision,
          [`${step.id}:__collect_response__`]: payload,
        },
      }
      await ctx.saveState(ctx.state)
    }

    if (effectiveDecision === "input") {
      return {
        next_step_id:      step.on_response.next,
        output_as:         step.output_as,
        output_value:      payload,
        transition_reason: "resumed",
      }
    }
    // timeout
    return {
      next_step_id:      step.on_timeout.next,
      output_as:         step.output_as,
      output_value:      payload,
      transition_reason: "on_failure",
    }
  }

  // ── Already suspended (idempotency check) ────────────────────────────────
  if (ctx.state.results[sentinelKey] === "collected") {
    return { next_step_id: "__suspended__", transition_reason: "suspended" }
  }

  // ── Generate collect token ─────────────────────────────────────────────
  const collectToken: string =
    (ctx.state.results[tokenKey] as string | undefined) ?? randomUUID()

  // ── Phase 1: write token + sentinel "collecting" ──────────────────────────
  ctx.state = {
    ...ctx.state,
    results: {
      ...ctx.state.results,
      [tokenKey]:    collectToken,
      [sentinelKey]: "collecting",
    },
  }
  await ctx.saveState(ctx.state)

  // ── Delegate to workflow-api (or fall back to wall-clock) ─────────────────
  let sendAt:    string
  let expiresAt: string

  if (ctx.state.results[sendAtKey] && ctx.state.results[expiresKey]) {
    // Already calculated in a previous crashed attempt — reuse
    sendAt    = ctx.state.results[sendAtKey]    as string
    expiresAt = ctx.state.results[expiresKey]   as string
  } else if (ctx.persistCollect) {
    const result = await ctx.persistCollect({
      step_id:        step.id,
      collect_token:  collectToken,
      target:         step.target,
      channel:        step.channel,
      interaction:    step.interaction,
      prompt:         step.prompt,
      ...(step.options    ? { options:      step.options }    : {}),
      ...(step.fields     ? { fields:       step.fields }     : {}),
      ...(step.scheduled_at ? { scheduled_at: step.scheduled_at } : {}),
      ...(step.delay_hours !== undefined ? { delay_hours: step.delay_hours } : {}),
      timeout_hours:  step.timeout_hours,
      business_hours: step.business_hours,
      ...(step.calendar_id ? { calendar_id: step.calendar_id } : {}),
      ...(step.campaign_id ? { campaign_id: step.campaign_id } : {}),
    })
    sendAt    = result.send_at
    expiresAt = result.expires_at
  } else {
    // Fallback: wall-clock (no calendar-api available)
    const delaySec = ((step.delay_hours ?? 0) * 3_600_000)
    sendAt    = new Date(Date.now() + delaySec).toISOString()
    expiresAt = new Date(Date.now() + delaySec + step.timeout_hours * 3_600_000).toISOString()
  }

  // Persist send_at and expires_at so a crash+retry reuses the same schedule
  ctx.state = {
    ...ctx.state,
    results: {
      ...ctx.state.results,
      [sendAtKey]:  sendAt,
      [expiresKey]: expiresAt,
    },
  }
  await ctx.saveState(ctx.state)

  // ── Phase 2: write sentinel "collected" (idempotency on retry) ───────────
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [sentinelKey]: "collected" },
  }
  await ctx.saveState(ctx.state)

  return { next_step_id: "__suspended__", transition_reason: "suspended" }
}
