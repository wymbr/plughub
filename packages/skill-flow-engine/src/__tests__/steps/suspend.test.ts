/**
 * steps/suspend.test.ts
 * Unit tests for the suspend step executor (Arc 4 — Workflow Automation).
 *
 * Covers:
 *   1. First execution — suspends, generates token, calls persistSuspend, writes sentinel
 *   2. Idempotency A — sentinel "suspending" (crash after persist, retry suspends again)
 *   3. Idempotency B — sentinel "suspended" (fully suspended, returns __suspended__ immediately)
 *   4. Notification — notify text is sent with interpolated resume_token
 *   5. Notification failure — non-fatal, logs error, still suspends
 *   6. Wall-clock fallback — no persistSuspend → deadline = now + timeout_hours
 *   7. Resume: approved — follows on_resume.next
 *   8. Resume: rejected with on_reject — follows on_reject.next
 *   9. Resume: rejected without on_reject — falls back to on_resume.next
 *  10. Resume: input — follows on_resume.next with payload
 *  11. Resume: timeout — follows on_timeout.next
 *  12. Resume idempotency — decision already stored → uses stored decision
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { executeSuspend }                        from "../../steps/suspend"
import type { StepContext }                      from "../../executor"
import type { SuspendStep, PipelineState }       from "@plughub/schemas"

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function makeState(results: Record<string, unknown> = {}): PipelineState {
  return {
    flow_id:         "wf_approval_v1",
    current_step_id: "aguardar_aprovacao",
    status:          "in_progress",
    started_at:      new Date().toISOString(),
    updated_at:      new Date().toISOString(),
    results,
    retry_counters:  {},
    transitions:     [],
  }
}

function makeCtx(overrides: Partial<StepContext> = {}): StepContext {
  const ctx: StepContext = {
    tenantId:         "tenant-test",
    sessionId:        "session-001",
    customerId:       "customer-001",
    sessionContext:   { order_id: "ORD-123" },
    state:            makeState(),
    redis:            {} as never,
    mcpCall:          vi.fn().mockResolvedValue({ ok: true }),
    aiGatewayCall:    vi.fn(),
    saveState:        vi.fn().mockImplementation(async (s: PipelineState) => {
      ctx.state = s  // simulate what engine._buildContext does
    }),
    retryStep:        vi.fn(),
    executeFallback:  vi.fn(),
    getJobId:         vi.fn().mockResolvedValue(null),
    setJobId:         vi.fn().mockResolvedValue(undefined),
    clearJobId:       vi.fn().mockResolvedValue(undefined),
    renewLock:        vi.fn().mockResolvedValue(true),
    persistSuspend:       vi.fn().mockResolvedValue({
      resume_expires_at: "2026-04-29T08:00:00.000Z",
    }),
    maskedScope:          {},
    transactionOnFailure: null,
    ...overrides,
  }
  return ctx
}

const step: SuspendStep = {
  type:           "suspend",
  id:             "aguardar_aprovacao",
  reason:         "approval",
  timeout_hours:  48,
  business_hours: true,
  on_resume:      { next: "processar_aprovacao" },
  on_timeout:     { next: "escalar_timeout" },
  on_reject:      { next: "notificar_rejeicao" },
}

// ─────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────

describe("executeSuspend — initial suspension", () => {
  it("1. generates token, calls persistSuspend, writes sentinels, returns __suspended__", async () => {
    const ctx = makeCtx()
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("__suspended__")
    expect(result.transition_reason).toBe("suspended")

    // persistSuspend called with correct params
    expect(ctx.persistSuspend).toHaveBeenCalledWith(
      expect.objectContaining({
        step_id:        "aguardar_aprovacao",
        reason:         "approval",
        timeout_hours:  48,
        business_hours: true,
      })
    )
    expect(ctx.persistSuspend).toHaveBeenCalledTimes(1)

    // resume_token is a UUID stored in results
    const token = ctx.state.results["aguardar_aprovacao:__resume_token__"]
    expect(typeof token).toBe("string")
    expect(token).toMatch(/^[0-9a-f-]{36}$/)

    // deadline stored
    expect(ctx.state.results["aguardar_aprovacao:__expires_at__"]).toBe("2026-04-29T08:00:00.000Z")

    // sentinel = "suspended" after full execution
    expect(ctx.state.results["aguardar_aprovacao:__suspended__"]).toBe("suspended")

    // saveState called multiple times (phase 1, deadline, sentinel)
    expect((ctx.saveState as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it("2. idempotency A — sentinel 'suspending' (crash before final sentinel): re-suspends", async () => {
    // State has token and 'suspending' sentinel but NO 'suspended' — simulates crash
    const ctx = makeCtx({
      state: makeState({
        "aguardar_aprovacao:__resume_token__": "existing-token-uuid",
        "aguardar_aprovacao:__suspended__":   "suspending",
        "aguardar_aprovacao:__expires_at__":  "2026-04-29T08:00:00.000Z",
      }),
    })
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("__suspended__")
    // Token is reused from results (not regenerated)
    expect(ctx.state.results["aguardar_aprovacao:__resume_token__"]).toBe("existing-token-uuid")
    // Final sentinel written
    expect(ctx.state.results["aguardar_aprovacao:__suspended__"]).toBe("suspended")
    // persistSuspend NOT called again (deadline already in results)
    expect(ctx.persistSuspend).not.toHaveBeenCalled()
  })

  it("3. idempotency B — sentinel 'suspended': returns __suspended__ immediately, no MCP call", async () => {
    const ctx = makeCtx({
      state: makeState({
        "aguardar_aprovacao:__resume_token__": "tok-abc",
        "aguardar_aprovacao:__suspended__":   "suspended",
        "aguardar_aprovacao:__expires_at__":  "2026-04-29T08:00:00.000Z",
      }),
    })
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("__suspended__")
    // No side effects
    expect(ctx.persistSuspend).not.toHaveBeenCalled()
    expect(ctx.mcpCall).not.toHaveBeenCalled()
    expect(ctx.saveState).not.toHaveBeenCalled()
  })
})

describe("executeSuspend — notification", () => {
  it("4. sends notification with interpolated resume_token", async () => {
    const stepWithNotify: SuspendStep = {
      ...step,
      notify: {
        visibility: "agents_only",
        text:       "Aguardando aprovação. Token: {{resume_token}}",
      },
    }
    const ctx = makeCtx()
    await executeSuspend(stepWithNotify, ctx)

    expect(ctx.mcpCall).toHaveBeenCalledWith(
      "notification_send",
      expect.objectContaining({
        session_id:  "session-001",
        visibility:  "agents_only",
        message:     expect.stringMatching(/^Aguardando aprovação\. Token: [0-9a-f-]{36}$/),
      })
    )
  })

  it("5. notification failure is non-fatal — error stored, still suspends", async () => {
    const stepWithNotify: SuspendStep = {
      ...step,
      notify: { visibility: "all", text: "Token: {{resume_token}}" },
    }
    const ctx = makeCtx({
      mcpCall: vi.fn().mockRejectedValue(new Error("MCP timeout")),
    })
    const result = await executeSuspend(stepWithNotify, ctx)

    expect(result.next_step_id).toBe("__suspended__")
    // Error recorded
    expect(ctx.state.results["aguardar_aprovacao:__notify_error__"]).toContain("MCP timeout")
    // Still fully suspended
    expect(ctx.state.results["aguardar_aprovacao:__suspended__"]).toBe("suspended")
  })
})

describe("executeSuspend — wall-clock fallback", () => {
  it("6. no persistSuspend → deadline is ~timeout_hours from now", async () => {
    // Omit persistSuspend entirely to exercise wall-clock fallback
    const { persistSuspend: _omit, ...ctxWithoutPersist } = makeCtx()
    const ctx = { ...ctxWithoutPersist }

    const before = Date.now()
    const result = await executeSuspend(step, ctx)
    const after  = Date.now()

    expect(result.next_step_id).toBe("__suspended__")

    const expires = new Date(ctx.state.results["aguardar_aprovacao:__expires_at__"] as string).getTime()
    const expectedMs = 48 * 3_600_000
    // Deadline should be within 1 second of expected
    expect(expires).toBeGreaterThanOrEqual(before + expectedMs - 1000)
    expect(expires).toBeLessThanOrEqual(after  + expectedMs + 1000)
  })
})

describe("executeSuspend — resume paths", () => {
  const suspendedResults = {
    "aguardar_aprovacao:__resume_token__": "tok-resume",
    "aguardar_aprovacao:__suspended__":   "suspended",
    "aguardar_aprovacao:__expires_at__":  "2026-04-29T08:00:00.000Z",
  }

  it("7. resume: approved → on_resume.next with payload", async () => {
    const ctx = makeCtx({
      state:         makeState(suspendedResults),
      resumeContext: { decision: "approved", step_id: "aguardar_aprovacao", payload: { approved_by: "maria" } },
    })
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("processar_aprovacao")
    expect(result.transition_reason).toBe("resumed")
    expect(result.output_as).toBe("aguardar_aprovacao")
    expect(result.output_value).toEqual({ approved_by: "maria" })
    // decision stored for idempotency
    expect(ctx.state.results["aguardar_aprovacao:__resume_decision__"]).toBe("approved")
  })

  it("8. resume: rejected with on_reject → on_reject.next", async () => {
    const ctx = makeCtx({
      state:         makeState(suspendedResults),
      resumeContext: { decision: "rejected", step_id: "aguardar_aprovacao", payload: { reason: "budget" } },
    })
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("notificar_rejeicao")
    expect(result.transition_reason).toBe("on_failure")
  })

  it("9. resume: rejected without on_reject → falls back to on_resume.next", async () => {
    const stepNoReject: SuspendStep = { ...step, on_reject: undefined }
    const ctx = makeCtx({
      state:         makeState(suspendedResults),
      resumeContext: { decision: "rejected", step_id: "aguardar_aprovacao", payload: {} },
    })
    const result = await executeSuspend(stepNoReject, ctx)

    expect(result.next_step_id).toBe("processar_aprovacao")
    expect(result.transition_reason).toBe("resumed")
  })

  it("10. resume: input → on_resume.next with form payload", async () => {
    const ctx = makeCtx({
      state:         makeState(suspendedResults),
      resumeContext: { decision: "input", step_id: "aguardar_aprovacao", payload: { amount: 5000 } },
    })
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("processar_aprovacao")
    expect(result.output_value).toEqual({ amount: 5000 })
  })

  it("11. resume: timeout → on_timeout.next", async () => {
    const ctx = makeCtx({
      state:         makeState(suspendedResults),
      resumeContext: { decision: "timeout", step_id: "aguardar_aprovacao", payload: {} },
    })
    const result = await executeSuspend(step, ctx)

    expect(result.next_step_id).toBe("escalar_timeout")
    expect(result.transition_reason).toBe("on_failure")
  })

  it("12. resume idempotency — stored decision takes precedence over context decision", async () => {
    // Simulate crash mid-resume: decision was stored but step didn't complete
    const ctx = makeCtx({
      state: makeState({
        ...suspendedResults,
        "aguardar_aprovacao:__resume_decision__": "approved",  // stored in prior run
      }),
      // Different decision in context (should be ignored)
      resumeContext: { decision: "rejected", step_id: "aguardar_aprovacao", payload: {} },
    })
    const result = await executeSuspend(step, ctx)

    // Should follow "approved" (stored), not "rejected" (context)
    expect(result.next_step_id).toBe("processar_aprovacao")
    expect(result.transition_reason).toBe("resumed")
    // saveState not called again for storing decision (already stored)
    const saveStateCalls = (ctx.saveState as ReturnType<typeof vi.fn>).mock.calls.length
    expect(saveStateCalls).toBe(0)  // no state write needed — just return the path
  })

  it("resumeContext for different step_id does NOT trigger resume", async () => {
    const ctx = makeCtx({
      state:         makeState(),
      resumeContext: { decision: "approved", step_id: "outro_step", payload: {} },
    })
    const result = await executeSuspend(step, ctx)

    // Should suspend normally (resumeContext.step_id does not match step.id)
    expect(result.next_step_id).toBe("__suspended__")
  })
})
