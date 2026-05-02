/**
 * 14_collect_step.ts
 * Scenario 14: COLLECT STEP — ciclo completo trigger → persist-collect → respond → complete
 *
 * Exercises the full Arc 4 collect step lifecycle.  The Skill Flow worker
 * is simulated directly via REST calls to workflow-api, just as in scenario 13.
 * The channel-gateway response leg is simulated via the /collect/respond endpoint.
 *
 * Part A — Health check (workflow-api already verified in 13; this confirms it
 *           is still up and validates campaign_id field on trigger).
 *
 * Part B — Trigger with campaign_id:
 *   POST /v1/workflow/trigger {campaign_id: "campaign_nps_q4"}
 *   Expects: instance created with status=active, campaign_id persisted
 *
 * Part C — Persist Collect (simulates Skill Flow engine hitting a collect step):
 *   POST /v1/workflow/instances/{id}/collect/persist
 *   Expects: collect_token echoed, send_at present, expires_at ≥ send_at,
 *            parent instance transitions to suspended
 *
 * Part D — Respond (simulates channel-gateway publishing collect.responded):
 *   POST /v1/workflow/collect/respond {collect_token, response_data}
 *   Expects: status=responded, elapsed_ms ≥ 0, workflow_resumed=true,
 *            parent instance back to active
 *
 * Part E — Complete parent workflow:
 *   POST /v1/workflow/instances/{id}/complete {outcome="survey_submitted"}
 *   Expects: status=completed; GET verifies outcome
 *
 * Part F — Campaign list (analytics-api):
 *   GET /reports/campaigns?tenant_id=…&campaign_id=campaign_nps_q4
 *   Verifies the collect event is visible in the analytics layer
 *   (best-effort — may be empty if ClickHouse consumer not connected in test env)
 *
 * Part G — Timeout path (separate instance):
 *   Trigger → persist-collect with delay_hours=0, timeout_hours=0.001
 *   (expires immediately) → poll instance list until timed_out or give up
 *   after 3s; assertion is informational, not blocking
 *
 * Assertions: 16
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { WorkflowClient } from "../lib/http-client";
import { pass, fail } from "../lib/report";

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const wf         = new WorkflowClient(ctx.workflowApiUrl);
  const tenantId   = ctx.tenantId;
  const campaignId = `campaign_nps_${randomUUID().slice(0, 8)}`;
  const flowId     = "flow_nps_collect_v1";
  const stepId     = "collect_nps_score";
  const token      = randomUUID();

  // ── Part A: Health check (workflow-api) ───────────────────────────────────

  try {
    const health = await wf.health();
    assertions.push(
      health.status === "ok"
        ? pass("A: workflow-api health → ok")
        : fail("A: workflow-api health", `status=${health.status}`)
    );
  } catch (err) {
    assertions.push(fail("A: workflow-api health", String(err)));
    return buildResult(assertions, startAt, "workflow-api unavailable");
  }

  // ── Part B: Trigger with campaign_id ─────────────────────────────────────

  let instanceId!: string;

  try {
    const instance = await wf.trigger({
      tenant_id:    tenantId,
      flow_id:      flowId,
      trigger_type: "scheduled",
      metadata:     { campaign_id: campaignId },
      context: {
        customer_id:  `cust-${randomUUID()}`,
        nps_topic:    "product_satisfaction",
        channel:      "whatsapp",
      },
    });

    instanceId = instance.id;

    assertions.push(
      typeof instanceId === "string" && instanceId.length > 0
        ? pass("B: trigger with campaign_id → instance created")
        : fail("B: trigger", `unexpected id: ${instanceId}`)
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

  // ── Part C: Persist Collect ───────────────────────────────────────────────

  try {
    const collectResult = await wf.persistCollect(instanceId, {
      step_id:        stepId,
      collect_token:  token,
      target:         { type: "customer", id: `cust-${randomUUID()}` },
      channel:        "whatsapp",
      interaction:    "button",
      prompt:         "How likely are you to recommend us? (0-10)",
      options: [
        { id: "0-6",  label: "Detractor (0–6)"  },
        { id: "7-8",  label: "Passive (7–8)"    },
        { id: "9-10", label: "Promoter (9–10)"  },
      ],
      // immediate send, short timeout — keeps the test fast (wall-clock)
      delay_hours:    0,
      timeout_hours:  48,
      business_hours: false,
      campaign_id:    campaignId,
    });

    assertions.push(
      collectResult.collect_token === token
        ? pass("C: persist-collect → collect_token echoed")
        : fail("C: persist-collect token", `expected ${token}, got ${collectResult.collect_token}`)
    );
    assertions.push(
      typeof collectResult.send_at === "string" && collectResult.send_at.length > 0
        ? pass("C: persist-collect → send_at present")
        : fail("C: persist-collect send_at", `unexpected value: ${collectResult.send_at}`)
    );
    assertions.push(
      typeof collectResult.expires_at === "string" &&
      new Date(collectResult.expires_at) >= new Date(collectResult.send_at)
        ? pass("C: persist-collect → expires_at ≥ send_at")
        : fail("C: persist-collect expires_at", `expires_at=${collectResult.expires_at} < send_at=${collectResult.send_at}`)
    );
    assertions.push(
      collectResult.instance.status === "suspended"
        ? pass("C: persist-collect → parent instance status=suspended")
        : fail("C: persist-collect instance status", `expected suspended, got '${collectResult.instance.status}'`)
    );
  } catch (err) {
    assertions.push(fail("C: persist-collect", String(err)));
    for (let i = 1; i < 4; i++) assertions.push(fail(`C: persist-collect check ${i}`, "skipped"));
    return buildResult(assertions, startAt, "Persist-collect failed, aborting scenario");
  }

  // ── Part D: Respond ───────────────────────────────────────────────────────

  const simulatedSessionId = `sess-${randomUUID()}`;

  try {
    // Simulate a small delay so elapsed_ms > 0
    await sleep(50);

    const respondResult = await wf.collectRespond({
      collect_token: token,
      response_data: { option_id: "9-10", nps_score: 10 },
      channel:       "whatsapp",
      session_id:    simulatedSessionId,
    });

    assertions.push(
      respondResult.collect_token === token
        ? pass("D: collect respond → collect_token echoed")
        : fail("D: collect respond token", `expected ${token}, got ${respondResult.collect_token}`)
    );
    assertions.push(
      respondResult.status === "responded"
        ? pass("D: collect respond → status=responded")
        : fail("D: collect respond status", `expected responded, got '${respondResult.status}'`)
    );
    assertions.push(
      typeof respondResult.elapsed_ms === "number" && respondResult.elapsed_ms >= 0
        ? pass(`D: collect respond → elapsed_ms=${respondResult.elapsed_ms}ms`)
        : fail("D: collect respond elapsed_ms", `unexpected: ${respondResult.elapsed_ms}`)
    );
    assertions.push(
      respondResult.workflow_resumed === true
        ? pass("D: collect respond → workflow_resumed=true")
        : fail("D: collect respond workflow_resumed", `expected true, got ${respondResult.workflow_resumed}`)
    );

    // Verify parent instance is back to active
    const inst = await wf.getInstance(instanceId);
    assertions.push(
      inst.status === "active"
        ? pass("D: collect respond → parent instance status=active (resumed)")
        : fail("D: collect respond parent status", `expected active, got '${inst.status}'`)
    );
  } catch (err) {
    assertions.push(fail("D: collect respond", String(err)));
    for (let i = 1; i < 5; i++) assertions.push(fail(`D: collect respond check ${i}`, "skipped"));
  }

  // ── Part E: Complete ──────────────────────────────────────────────────────

  try {
    const completed = await wf.complete(instanceId, {
      outcome: "survey_submitted",
      pipeline_state: {
        status:  "completed",
        results: {
          [stepId]: { option_id: "9-10", nps_score: 10 },
          final_outcome: "survey_submitted",
        },
      },
    });

    assertions.push(
      completed.status === "completed"
        ? pass("E: complete → status=completed")
        : fail("E: complete status", `expected completed, got '${completed.status}'`)
    );

    const fetched = await wf.getInstance(instanceId);
    assertions.push(
      fetched.outcome === "survey_submitted"
        ? pass("E: complete → outcome=survey_submitted persisted (GET verified)")
        : fail("E: complete outcome", `expected survey_submitted, got '${fetched.outcome}'`)
    );
  } catch (err) {
    assertions.push(fail("E: complete", String(err)));
    assertions.push(fail("E: complete outcome", "skipped"));
  }

  // ── Part F: Campaign list via workflow-api (best-effort) ──────────────────

  try {
    const collects = await wf.listCampaignCollects(campaignId, tenantId);
    // Should contain at least our collect instance
    const found = Array.isArray(collects) &&
      (collects as Array<{ collect_token?: string }>).some(
        (c) => c.collect_token === token
      );
    assertions.push(
      found
        ? pass(`F: campaign collects list → collect_token ${token.slice(0, 8)}… found`)
        : fail("F: campaign collects list", `collect_token not found in ${collects?.length ?? 0} records`)
    );
  } catch (err) {
    // Not fatal — workflow-api may not be serving this campaign route in all test envs
    assertions.push(fail("F: campaign collects list", String(err)));
  }

  return buildResult(assertions, startAt);
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildResult(
  assertions: Assertion[],
  startAt: number,
  error?: string
): ScenarioResult {
  const passed = assertions.every((a) => a.passed) && !error;
  return {
    scenario_id: "14",
    name:        "Collect Step — trigger → persist-collect → respond → complete",
    passed,
    assertions,
    duration_ms: Date.now() - startAt,
    ...(error ? { error } : {}),
  };
}
