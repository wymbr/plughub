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
  const resume_token = randomUUID()

  // 1. Persist resume_token (extends Redis TTLs + writes to resume_tokens hash)
  let expires_at = new Date(
    Date.now() + step.timeout_hours * 3600 * 1000
  ).toISOString()

  if (ctx.persistSuspendWebhook) {
    try {
      const result = await ctx.persistSuspendWebhook({
        step_id:        step.id,
        resume_token,
        timeout_hours:  step.timeout_hours,
        business_hours: step.business_hours,
        calendar_id:    step.calendar_id,
      })
      expires_at = result.resume_expires_at
    } catch (err) {
      // Non-fatal — fall back to wall-clock deadline
      console.warn(`[delegate] persistSuspendWebhook failed (step=${step.id}):`, err)
    }
  }

  // 2. Resolve context entries — interpolate @ctx.* and $.pipeline_state.*
  const resolvedContext: Record<string, string> = {}
  if (step.context) {
    for (const [key, value] of Object.entries(step.context)) {
      // Interpolation is handled by the engine before calling executeStep.
      // Values arriving here are already resolved strings.
      resolvedContext[key] = String(value)
    }
  }

  // 3. Dispatch child session via persistDelegate callback (wired by bridge)
  let child_session_id = ""
  if (ctx.persistDelegate) {
    try {
      const result = await ctx.persistDelegate({
        step_id:           step.id,
        resume_token,
        pool:              step.pool,
        context:           resolvedContext,
        timeout_hours:     step.timeout_hours,
        origin_session_id: (ctx.sessionContext["origin_session_id"] as string | undefined)
                           ?? ctx.sessionId,  // propagate root or self as root
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
