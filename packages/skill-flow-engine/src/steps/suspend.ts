/**
 * steps/suspend.ts
 * Executor for step type: suspend (Arc 4 — Workflow Automation)
 * Spec: PlugHub Platform Arc 4
 *
 * Pauses a Skill Flow execution indefinitely, waiting for an external signal.
 * Generates a resume_token, calculates deadline (business or wall-clock hours),
 * persists the WorkflowInstance via ctx.persistSuspend, and optionally sends
 * a notification with the token interpolated.
 *
 * Returns next_step_id: "__suspended__" — handled specially by engine.ts.
 *
 * Resume flow:
 *   When the workflow-api receives a valid resume_token, it calls engine.run()
 *   with resumeContext set. The suspend step detects ctx.resumeContext.step_id
 *   matches its own id and follows the appropriate on_resume / on_reject /
 *   on_timeout path — without suspending again.
 *
 * Idempotency (two-stage sentinel):
 *   - sentinel "suspended": already suspended → return __suspended__ immediately.
 *   - sentinel "suspending": crash after persist but before sentinel write.
 *     On retry: re-persists the final sentinel and returns __suspended__.
 *   - no sentinel: first execution — normal path.
 *
 * Resume sentinel:
 *   When resuming, the step stores the decision in results so subsequent
 *   replays (e.g. crash mid-resume) follow the same path.
 */

import { randomUUID }      from "crypto"
import type { SuspendStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

/** Regex for {{resume_token}} and {{$.pipeline_state.*}} interpolation */
const INTERPOLATION_REGEX = /\{\{([\$\.a-zA-Z_][^}]*)\}\}/g

export async function executeSuspend(
  step: SuspendStep,
  ctx:  StepContext
): Promise<StepResult> {
  const sentinelKey   = `${step.id}:__suspended__`
  const tokenKey      = `${step.id}:__resume_token__`
  const expiresKey    = `${step.id}:__expires_at__`
  const decisionKey   = `${step.id}:__resume_decision__`

  // ── Resume path — external signal received ────────────────────────────────
  if (ctx.resumeContext?.step_id === step.id) {
    const { decision, payload } = ctx.resumeContext

    // Idempotency: if already resumed in a prior run, follow the same path
    const storedDecision = ctx.state.results[decisionKey] as string | undefined
    const effectiveDecision = storedDecision ?? decision

    // Persist the decision (in case we crash here — next replay uses same path)
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
        // No on_reject configured — treat as resume
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

  // ── Suspend path ──────────────────────────────────────────────────────────

  // Idempotency: already fully suspended (sentinel written)
  if (ctx.state.results[sentinelKey] === "suspended") {
    return {
      next_step_id:      "__suspended__",
      transition_reason: "suspended",
    }
  }

  // Retrieve or generate resume token (crash-safe: token is written first)
  const resumeToken: string = (ctx.state.results[tokenKey] as string | undefined) ?? randomUUID()

  // ── Phase 1: write token + sentinel "suspending" before persist ───────────
  ctx.state = {
    ...ctx.state,
    results: {
      ...ctx.state.results,
      [tokenKey]:   resumeToken,
      [sentinelKey]: "suspending",
    },
  }
  await ctx.saveState(ctx.state)

  // ── Calculate deadline ────────────────────────────────────────────────────
  let resumeExpiresAt: string
  if (ctx.state.results[expiresKey]) {
    // Already calculated in a previous (crashed) attempt — reuse
    resumeExpiresAt = ctx.state.results[expiresKey] as string
  } else if (ctx.persistSuspend) {
    const persistParams: Parameters<NonNullable<typeof ctx.persistSuspend>>[0] = {
      step_id:        step.id,
      resume_token:   resumeToken,
      reason:         step.reason,
      timeout_hours:  step.timeout_hours,
      business_hours: step.business_hours,
      ...(step.calendar_id  ? { calendar_id: step.calendar_id }  : {}),
      ...(step.metadata     ? { metadata:    step.metadata }     : {}),
    }
    const result = await ctx.persistSuspend(persistParams)
    resumeExpiresAt = result.resume_expires_at
  } else {
    // Fallback: wall-clock hours
    const deadline = new Date(Date.now() + step.timeout_hours * 3_600_000)
    resumeExpiresAt = deadline.toISOString()
  }

  // Persist deadline in results so a crash+retry reuses the same expiry
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [expiresKey]: resumeExpiresAt },
  }
  await ctx.saveState(ctx.state)

  // ── Send notification if configured ──────────────────────────────────────
  if (step.notify) {
    try {
      const message = _interpolate(step.notify.text, ctx, resumeToken)
      await ctx.mcpCall("notification_send", {
        session_id:  ctx.sessionId,
        message,
        visibility:  step.notify.visibility ?? "agents_only",
      })
    } catch (err) {
      // Non-fatal — workflow is already persisted; log the failure and continue
      ctx.state = {
        ...ctx.state,
        results: {
          ...ctx.state.results,
          [`${step.id}:__notify_error__`]: String(err),
        },
      }
    }
  }

  // ── Phase 2: write sentinel "suspended" (signals idempotency on retry) ────
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [sentinelKey]: "suspended" },
  }
  await ctx.saveState(ctx.state)

  return {
    next_step_id:      "__suspended__",
    transition_reason: "suspended",
  }
}

/**
 * Interpolates {{resume_token}} and {{$.pipeline_state.*}} in notify text.
 *
 * Supported patterns:
 *   {{resume_token}}            → the generated UUID token
 *   {{$.resume_token}}          → same
 *   {{$.pipeline_state.field}}  → value from pipeline_state.results
 *   {{$.session.field}}         → value from sessionContext
 */
function _interpolate(template: string, ctx: StepContext, resumeToken: string): string {
  return template.replace(INTERPOLATION_REGEX, (_, path: string) => {
    const normalised = path.replace(/^\$\./, "")
    if (normalised === "resume_token") return resumeToken

    const parts = normalised.split(".")
    let current: unknown = {
      pipeline_state: ctx.state.results,
      session:        ctx.sessionContext,
    }
    for (const part of parts) {
      if (current == null || typeof current !== "object") return ""
      current = (current as Record<string, unknown>)[part]
    }
    return current != null ? String(current) : ""
  })
}
