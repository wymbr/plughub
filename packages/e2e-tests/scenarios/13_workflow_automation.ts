/**
 * 13_workflow_automation.ts
 * Scenario 13: WORKFLOW AUTOMATION — ciclo completo trigger → suspend → resume → complete
 *
 * Simulates the full Arc 4 workflow lifecycle. The Skill Flow TypeScript worker
 * is simulated directly via REST calls to workflow-api, mirroring what the worker
 * would do when driving engine.run().
 *
 * Part A — Health checks:
 *   workflow-api /v1/health → status=ok, postgres=ok
 *   calendar-api /v1/health → status=ok
 *
 * Part B — Trigger:
 *   POST /v1/workflow/trigger → instance created with status=active
 *
 * Part C — Persist Suspend (simulates Skill Flow engine hitting a suspend step):
 *   POST /v1/workflow/instances/{id}/persist-suspend
 *   Expects: status=suspended, resume_token present, resume_expires_at calculated
 *
 * Part D — Resume (simulates external approver calling REST endpoint with token):
 *   POST /v1/workflow/resume {token, decision="approved"}
 *   Expects: status=active (engine can continue), decision echoed
 *
 * Part E — Complete (simulates engine worker reporting resolved outcome):
 *   POST /v1/workflow/instances/{id}/complete {outcome="approved"}
 *   Expects: status=completed, outcome persisted (verified via GET)
 *
 * Part F — Cancel path:
 *   Trigger new instance → persist-suspend → cancel
 *   Expects: status=cancelled (terminal)
 *
 * Assertions: 13
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { WorkflowClient, CalendarClient } from "../lib/http-client";
import { pass, fail } from "../lib/report";

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const wf  = new WorkflowClient(ctx.workflowApiUrl);
  const cal = new CalendarClient(ctx.calendarApiUrl);

  const tenantId = ctx.tenantId;
  const flowId   = "flow_approval_test_v1";

  // ── Part A: Health checks ─────────────────────────────────────────────────

  try {
    const wfHealth = await wf.health();
    assertions.push(
      wfHealth.status === "ok" && wfHealth.postgres === "ok"
        ? pass("A: workflow-api health → ok (postgres ok)")
        : fail("A: workflow-api health", `status=${wfHealth.status}, postgres=${wfHealth.postgres}`)
    );
  } catch (err) {
    assertions.push(fail("A: workflow-api health", String(err)));
  }

  try {
    const calHealth = await cal.health();
    assertions.push(
      calHealth.status === "ok"
        ? pass("A: calendar-api health → ok")
        : fail("A: calendar-api health", `status=${calHealth.status}`)
    );
  } catch (err) {
    assertions.push(fail("A: calendar-api health", String(err)));
  }

  // ── Part B: Trigger ───────────────────────────────────────────────────────

  let instanceId!: string;

  try {
    const instance = await wf.trigger({
      tenant_id:    tenantId,
      flow_id:      flowId,
      trigger_type: "manual",
      context: {
        customer_id:  `cust-${randomUUID()}`,
        request_type: "approval",
        amount:       1500.0,
      },
    });

    instanceId = instance.id;

    assertions.push(
      typeof instanceId === "string" && instanceId.length > 0
        ? pass("B: trigger → instance_id returned")
        : fail("B: trigger instance_id", `unexpected id: ${instanceId}`)
    );
    assertions.push(
      instance.status === "active"
        ? pass("B: trigger → status=active")
        : fail("B: trigger status", `expected active, got '${instance.status}'`)
    );
  } catch (err) {
    assertions.push(fail("B: trigger", String(err)));
    assertions.push(fail("B: trigger status", "skipped — trigger failed"));
    return buildResult(assertions, startAt, "Trigger failed, aborting scenario");
  }

  // ── Part C: Persist Suspend ───────────────────────────────────────────────

  const resumeToken = `tok-${randomUUID()}`;
  const stepId      = "aguardar_aprovacao";

  try {
    const suspendResult = await wf.persistSuspend(instanceId, {
      step_id:        stepId,
      resume_token:   resumeToken,
      reason:         "approval",
      timeout_hours:  48,
      business_hours: false,  // wall-clock — no calendar association needed in test env
      pipeline_state: {
        status:  "in_progress",
        results: {},
        context: { customer_id: `cust-${randomUUID()}` },
      },
    });

    assertions.push(
      suspendResult.instance.status === "suspended"
        ? pass("C: persist-suspend → status=suspended")
        : fail("C: persist-suspend status", `expected suspended, got '${suspendResult.instance.status}'`)
    );
    assertions.push(
      typeof suspendResult.instance.resume_token === "string"
        ? pass("C: persist-suspend → resume_token present")
        : fail("C: persist-suspend resume_token", "resume_token missing from instance")
    );
    assertions.push(
      typeof suspendResult.resume_expires_at === "string" &&
      suspendResult.resume_expires_at.length > 0
        ? pass("C: persist-suspend → resume_expires_at calculated")
        : fail("C: persist-suspend expires", `unexpected value: ${suspendResult.resume_expires_at}`)
    );
  } catch (err) {
    assertions.push(fail("C: persist-suspend", String(err)));
    assertions.push(fail("C: persist-suspend resume_token", "skipped"));
    assertions.push(fail("C: persist-suspend expires",      "skipped"));
    return buildResult(assertions, startAt, "Persist-suspend failed, aborting scenario");
  }

  // ── Part D: Resume ────────────────────────────────────────────────────────

  try {
    const resumeResult = await wf.resume({
      token:    resumeToken,
      decision: "approved",
      payload:  {
        approved_by: "manager@example.com",
        notes:       "Approved via E2E test",
      },
    });

    assertions.push(
      resumeResult.instance_id === instanceId
        ? pass("D: resume → instance_id matches")
        : fail("D: resume instance_id", `expected ${instanceId}, got ${resumeResult.instance_id}`)
    );
    assertions.push(
      resumeResult.instance.status === "active"
        ? pass("D: resume → status=active (engine can continue)")
        : fail("D: resume status", `expected active, got '${resumeResult.instance.status}'`)
    );
  } catch (err) {
    assertions.push(fail("D: resume", String(err)));
    assertions.push(fail("D: resume status", "skipped"));
  }

  // ── Part E: Complete ──────────────────────────────────────────────────────

  try {
    const completed = await wf.complete(instanceId, {
      outcome:        "approved",
      pipeline_state: {
        status:  "completed",
        results: { [stepId]: "approved", final_outcome: "approved" },
      },
    });

    assertions.push(
      completed.status === "completed"
        ? pass("E: complete → status=completed")
        : fail("E: complete status", `expected completed, got '${completed.status}'`)
    );

    // Verify outcome is persisted via GET
    const fetched = await wf.getInstance(instanceId);
    assertions.push(
      fetched.outcome === "approved"
        ? pass("E: complete → outcome=approved persisted (GET verified)")
        : fail("E: complete outcome", `expected approved, got '${fetched.outcome}'`)
    );
  } catch (err) {
    assertions.push(fail("E: complete", String(err)));
    assertions.push(fail("E: complete outcome", "skipped"));
  }

  // ── Part F: Cancel path ───────────────────────────────────────────────────

  let cancelInstanceId!: string;

  try {
    const inst2 = await wf.trigger({
      tenant_id:    tenantId,
      flow_id:      flowId,
      trigger_type: "api",
      context:      { customer_id: `cust-${randomUUID()}` },
    });
    cancelInstanceId = inst2.id;

    await wf.persistSuspend(cancelInstanceId, {
      step_id:        "aguardar_aprovacao",
      resume_token:   `tok-${randomUUID()}`,
      reason:         "approval",
      timeout_hours:  24,
      business_hours: false,
    });

    assertions.push(pass("F: cancel path — trigger + persist-suspend ok"));
  } catch (err) {
    assertions.push(fail("F: cancel path setup", String(err)));
    return buildResult(assertions, startAt);
  }

  try {
    const cancelled = await wf.cancel(cancelInstanceId, {
      cancelled_by: "e2e-runner",
      reason:       "E2E test cancel path validation",
    });
    assertions.push(
      cancelled.status === "cancelled"
        ? pass("F: cancel → status=cancelled (terminal)")
        : fail("F: cancel status", `expected cancelled, got '${cancelled.status}'`)
    );
  } catch (err) {
    assertions.push(fail("F: cancel", String(err)));
  }

  return buildResult(assertions, startAt);
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function buildResult(
  assertions: Assertion[],
  startAt: number,
  error?: string
): ScenarioResult {
  const passed = assertions.every((a) => a.passed) && !error;
  return {
    scenario_id: "13",
    name:        "Workflow Automation — trigger → suspend → resume → complete",
    passed,
    assertions,
    duration_ms: Date.now() - startAt,
    ...(error ? { error } : {}),
  };
}
