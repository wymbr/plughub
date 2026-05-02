/**
 * 18_workflow_worker_chain.ts
 * Scenario 18: WORKFLOW WORKER CHAIN — Kafka→worker→engine chain completo
 *
 * Exercises the full end-to-end arc that scenarios 13/14 deliberately skip.
 * Scenarios 13 and 14 simulate the skill-flow-worker via direct REST calls to
 * workflow-api (mirroring what the worker would do). Scenario 18 instead relies
 * on the *real* worker consuming Kafka events and calling the engine.
 *
 * Chain under test:
 *   REST trigger → workflow.started (Kafka)
 *     → skill-flow-worker consumes → engine.run() → suspend step
 *       → persist-suspend callback → workflow.suspended (Kafka)
 *         → test observes suspension via GET + Kafka event
 *   REST resume → workflow.resumed (Kafka)
 *     → skill-flow-worker consumes → engine.run(resumeContext)
 *       → complete step → workflowClient.complete() → workflow.completed (Kafka)
 *         → test observes completion via GET + Kafka event
 *
 * Flow definition embedded in metadata.flow_definition:
 *   entry: aguardar_aprovacao (suspend step, reason=approval, timeout_hours=1)
 *   on_resume → finalizar (complete, outcome=approved)
 *   on_timeout → finalizar_timeout (complete, outcome=timed_out)
 *
 * Part A — Health check (best-effort, workflow-api only):
 *   workflow-api /v1/health → status=ok
 *
 * Part B — Setup Kafka consumer for workflow.events (two-phase snapshot):
 *   Admin fetches end-offsets → consumer subscribes → GROUP_JOIN → seek()
 *
 * Part C — Trigger workflow:
 *   POST /v1/workflow/trigger with flow_definition in metadata
 *   Asserts: instance created, status=active, id present
 *
 * Part D — Wait for workflow.suspended (≤30s):
 *   Proves worker consumed workflow.started, ran engine, hit suspend step,
 *   called persistSuspend callback, workflow-api emitted workflow.suspended.
 *   Asserts: event received, instance_id matches, suspend_reason=approval
 *
 * Part E — Verify suspension state via GET:
 *   Asserts: status=suspended, resume_token present, resume_expires_at set
 *
 * Part F — Resume via REST:
 *   POST /v1/workflow/resume with token + decision=approved
 *   Asserts: resumed instance has status=active
 *
 * Part G — Wait for workflow.completed (≤30s):
 *   Proves worker consumed workflow.resumed, ran engine with resumeContext,
 *   complete step executed, workflowClient.complete() was called.
 *   Asserts: event received, instance_id matches, outcome=approved
 *
 * Part H — Verify final state via GET:
 *   Asserts: status=completed, outcome=approved
 *
 * Part I — elapsed_ms sanity:
 *   Asserts: wait_duration_ms on workflow.resumed event is a positive number
 *   (measures actual wall-clock time between suspend and resume)
 *
 * Assertions: 16
 * Timeout:    120s (Kafka consumer setup + worker processing + two engine runs)
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { WorkflowClient } from "../lib/http-client";
import { pass, fail } from "../lib/report";
import { Kafka } from "kafkajs";

// ─────────────────────────────────────────────────────────────────────────────
// Flow definition — minimal suspend flow used by the worker in this scenario
// ─────────────────────────────────────────────────────────────────────────────

const FLOW_DEFINITION = {
  entry: "aguardar_aprovacao",
  steps: [
    {
      id:            "aguardar_aprovacao",
      type:          "suspend",
      reason:        "approval",
      timeout_hours: 1,
      business_hours: false,
      on_resume:  { next: "finalizar" },
      on_timeout: { next: "finalizar_timeout" },
    },
    {
      id:      "finalizar",
      type:    "complete",
      outcome: "approved",
    },
    {
      id:      "finalizar_timeout",
      type:    "complete",
      outcome: "timed_out",
    },
  ],
};

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Two-phase Kafka consumer that watches workflow.events for a specific
 * instance_id + event_type combination.
 *
 * Uses the Admin-offset snapshot pattern (same as waitForInboundEvent in
 * kafka-client.ts) to eliminate the race between GROUP_JOIN and event emission.
 *
 * Returns { ready, result }:
 *   ready  — resolves after seek() is done; caller publishes the REST call.
 *   result — resolves with the matching event object (or null on timeout).
 */
function waitForWorkflowEvent(
  kafka: Kafka,
  instanceId: string,
  eventType: string,
  timeoutMs: number = 30_000
): { ready: Promise<void>; result: Promise<Record<string, unknown> | null> } {
  const TOPIC   = "workflow.events";
  const tag     = `[wf-event:${eventType.split(".")[1]}:${instanceId.slice(0, 8)}]`;
  const groupId = `e2e-wf-${randomUUID()}`;

  const admin    = kafka.admin();
  const consumer = kafka.consumer({ groupId });

  let resolveReady!: () => void;
  const ready = new Promise<void>((res) => { resolveReady = res; });

  // Hard outer fallback for ready — fires regardless of IIFE outcome.
  const readyTimer = setTimeout(() => {
    console.log(`${tag} ready-fallback fired (7s)`);
    resolveReady();
  }, 7_000);

  const result = new Promise<Record<string, unknown> | null>((resolve) => {
    const cleanup = () => {
      consumer.disconnect().catch(() => undefined);
      admin.disconnect().catch(() => undefined);
    };

    // Timeout: resolve null, never reject — keeps the scenario going.
    const timeout = setTimeout(() => {
      console.log(`${tag} result-timeout (${timeoutMs}ms) — resolving null`);
      clearTimeout(readyTimer);
      resolveReady();
      cleanup();
      resolve(null);
    }, timeoutMs);

    (async () => {
      try {
        // Step 1: snapshot end-offsets before subscribing
        console.log(`${tag} fetching topic offsets…`);
        await admin.connect();
        const topicOffsets = await admin.fetchTopicOffsets(TOPIC);
        await admin.disconnect().catch(() => undefined);
        console.log(`${tag} offsets: ${JSON.stringify(topicOffsets)}`);

        // Step 2: connect consumer + subscribe
        await consumer.connect();
        await consumer.subscribe({ topic: TOPIC, fromBeginning: false });
        console.log(`${tag} subscribed — waiting for GROUP_JOIN`);

        // Step 3: on GROUP_JOIN seek every assigned partition to snapshot offset
        consumer.on(consumer.events.GROUP_JOIN, (event: unknown) => {
          const e = event as {
            payload?: { memberAssignment?: Record<string, number[]> };
          };
          const assigned: number[] =
            e?.payload?.memberAssignment?.[TOPIC] ??
            topicOffsets.map((o: { partition: number }) => o.partition);

          for (const partition of assigned) {
            const info = topicOffsets.find(
              (o: { partition: number }) => o.partition === partition
            );
            const offset = info?.offset ?? "0";
            console.log(`${tag} seek partition=${partition} → offset=${offset}`);
            consumer.seek({ topic: TOPIC, partition, offset });
          }
          clearTimeout(readyTimer);
          resolveReady();
          console.log(`${tag} ready — seeked to snapshot offsets`);
        });

        consumer.run({
          eachMessage: async ({ message }) => {
            if (!message.value) return;
            try {
              const parsed = JSON.parse(
                message.value.toString()
              ) as Record<string, unknown>;
              if (
                parsed["instance_id"] !== instanceId ||
                parsed["event_type"]  !== eventType
              ) {
                return;
              }
              console.log(`${tag} event matched — resolving`);
              clearTimeout(timeout);
              clearTimeout(readyTimer);
              resolveReady();
              cleanup();
              resolve(parsed);
            } catch { /* ignore parse errors */ }
          },
        }).catch((err) => {
          console.log(`${tag} consumer.run() error: ${err}`);
          clearTimeout(readyTimer);
          resolveReady();
          clearTimeout(timeout);
          cleanup();
          resolve(null);
        });

      } catch (err) {
        console.log(`${tag} setup error: ${err}`);
        clearTimeout(readyTimer);
        resolveReady();
        clearTimeout(timeout);
        cleanup();
        resolve(null);
      }
    })();
  });

  return { ready, result };
}

// ─────────────────────────────────────────────────────────────────────────────
// Scenario
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const wf       = new WorkflowClient(ctx.workflowApiUrl);
  const tenantId = ctx.tenantId;
  const flowId   = `flow_worker_chain_test_${randomUUID().slice(0, 8)}`;

  // ── Part A: Health check (best-effort) ───────────────────────────────────

  try {
    const wfHealth = await wf.health();
    assertions.push(
      wfHealth.status === "ok" && wfHealth.postgres === "ok"
        ? pass("A: workflow-api health → ok (postgres ok)")
        : fail("A: workflow-api health", `status=${wfHealth.status} postgres=${wfHealth.postgres}`)
    );
  } catch (err) {
    assertions.push(fail("A: workflow-api health", String(err)));
  }

  // ── Part B: Setup Kafka consumers ────────────────────────────────────────
  //
  // We need to observe two events for the same instance:
  //   1. workflow.suspended — after the worker runs engine.run() on workflow.started
  //   2. workflow.completed — after the worker runs engine.run() on workflow.resumed
  //
  // Both consumers are wired up BEFORE their respective REST calls to avoid the
  // race between event emission and consumer subscription.

  // Reserve a deterministic instance ID by triggering first, then wiring consumers.
  // We cannot know the instance ID before triggering, so we:
  //   1. Wire the suspended consumer — needs to be ready before we trigger.
  //   2. Trigger — returns instanceId immediately (status=active, no Kafka delay).
  //   3. Wire the completed consumer with the now-known instanceId — needs to be
  //      ready before we resume.
  //
  // The two-phase pattern guarantees that any event published AFTER the consumer
  // seeks to the snapshot offset will be received, so wiring the suspended consumer
  // before the trigger (and therefore before workflow.started fires) is correct.

  // We use a placeholder instanceId for the suspended consumer that we'll replace
  // once we know the real one.  Instead, we create the suspended consumer after
  // triggering (the trigger is a REST call that returns before Kafka fires), which
  // is safe because:
  //   trigger REST → returns instanceId (sync)
  //   workflow.started published (async, aiokafka fire-and-forget)
  //   worker consumes workflow.started → runs engine (~ms to seconds)
  //   persist-suspend callback → workflow.suspended published
  //
  // The two-phase consumer setup takes ~1-2s (admin fetch + GROUP_JOIN), which is
  // well within the window between "trigger returns" and "worker publishes suspended".
  // The snapshot offset acquired by the Admin is taken AFTER the trigger, so any
  // workflow.started / workflow.suspended published after that offset is captured.

  let instanceId!: string;

  // ── Part C: Trigger ───────────────────────────────────────────────────────

  try {
    const instance = await wf.trigger({
      tenant_id:    tenantId,
      flow_id:      flowId,
      trigger_type: "api",
      context: {
        customer_id:  `cust-${randomUUID()}`,
        request_type: "worker_chain_e2e",
      },
      metadata: {
        flow_definition: FLOW_DEFINITION,
      },
    });

    instanceId = instance.id;

    assertions.push(
      typeof instanceId === "string" && instanceId.length > 0
        ? pass("C: trigger → instance_id returned")
        : fail("C: trigger instance_id", `unexpected id: ${instanceId}`)
    );
    assertions.push(
      instance.status === "active"
        ? pass("C: trigger → status=active")
        : fail("C: trigger status", `expected active, got '${instance.status}'`)
    );
  } catch (err) {
    assertions.push(fail("C: trigger", String(err)));
    assertions.push(fail("C: trigger status", "skipped — trigger failed"));
    return buildResult(assertions, startAt, "Trigger failed, aborting scenario");
  }

  // ── Part D: Wait for workflow.suspended ──────────────────────────────────
  //
  // Set up consumer NOW — instance ID is known, worker has not yet had time to
  // process workflow.started (it was published fire-and-forget after the REST
  // response, and the worker needs to fetch the instance, run the engine, and
  // call persistSuspend before emitting workflow.suspended).

  const suspendedWatcher = waitForWorkflowEvent(
    ctx.kafka,
    instanceId,
    "workflow.suspended",
    30_000
  );

  // Wait for the consumer to seek to snapshot offsets before we just let time pass.
  await suspendedWatcher.ready;
  console.log("[18] suspended-watcher ready — waiting for workflow.suspended event…");

  const suspendedEvent = await suspendedWatcher.result;

  if (!suspendedEvent) {
    assertions.push(
      fail(
        "D: workflow.suspended received within 30s",
        "Timeout — worker did not emit workflow.suspended (worker may not be running)"
      )
    );
    assertions.push(fail("D: workflow.suspended suspend_reason=approval", "skipped — event not received"));
  } else {
    assertions.push(
      pass("D: workflow.suspended received within 30s")
    );
    assertions.push(
      suspendedEvent["suspend_reason"] === "approval"
        ? pass("D: workflow.suspended suspend_reason=approval")
        : fail(
            "D: workflow.suspended suspend_reason",
            `expected approval, got '${suspendedEvent["suspend_reason"]}'`
          )
    );
  }

  // ── Part E: Verify suspension state via GET ───────────────────────────────

  let resumeToken: string | null = null;

  try {
    const fetched = await wf.getInstance(instanceId);

    assertions.push(
      fetched.status === "suspended"
        ? pass("E: GET → status=suspended")
        : fail("E: GET status", `expected suspended, got '${fetched.status}'`)
    );

    resumeToken = fetched.resume_token ?? null;

    assertions.push(
      typeof fetched.resume_token === "string" && fetched.resume_token.length > 0
        ? pass("E: GET → resume_token present")
        : fail("E: GET resume_token", "resume_token missing or empty")
    );
    assertions.push(
      typeof fetched.resume_expires_at === "string" && fetched.resume_expires_at.length > 0
        ? pass("E: GET → resume_expires_at set")
        : fail("E: GET resume_expires_at", `unexpected value: ${fetched.resume_expires_at}`)
    );
  } catch (err) {
    assertions.push(fail("E: GET instance",          String(err)));
    assertions.push(fail("E: GET resume_token",      "skipped"));
    assertions.push(fail("E: GET resume_expires_at", "skipped"));
  }

  if (!resumeToken) {
    assertions.push(fail("F: resume", "skipped — resume_token not available"));
    assertions.push(fail("F: resume status=active", "skipped"));
    assertions.push(fail("G: workflow.completed received within 30s", "skipped"));
    assertions.push(fail("G: workflow.completed outcome=approved", "skipped"));
    assertions.push(fail("H: GET → status=completed", "skipped"));
    assertions.push(fail("H: GET → outcome=approved persisted", "skipped"));
    assertions.push(fail("I: wait_duration_ms > 0", "skipped"));
    return buildResult(assertions, startAt, "No resume_token, aborting after Part E");
  }

  // ── Part F: Setup completed-watcher + Resume ─────────────────────────────
  //
  // Wire the completed consumer BEFORE calling resume, so the snapshot offset
  // is taken before workflow.resumed fires. workflow.resumed → worker consumes
  // → engine.run(resumeContext) → complete step → workflowClient.complete() →
  // workflow.completed emitted.

  const completedWatcher = waitForWorkflowEvent(
    ctx.kafka,
    instanceId,
    "workflow.completed",
    30_000
  );

  // Wire the resumed watcher as well — needed for Part I (wait_duration_ms).
  const resumedWatcher = waitForWorkflowEvent(
    ctx.kafka,
    instanceId,
    "workflow.resumed",
    30_000
  );

  // Wait for both consumers to be ready before the REST resume call.
  await Promise.all([completedWatcher.ready, resumedWatcher.ready]);
  console.log("[18] completed-watcher + resumed-watcher ready — calling resume…");

  let resumedStatus: string | undefined;

  try {
    const resumeResult = await wf.resume({
      token:    resumeToken,
      decision: "approved",
      payload:  { approved_by: "e2e-runner", notes: "Scenario 18 resume" },
    });

    resumedStatus = resumeResult.instance?.status;

    assertions.push(
      resumeResult.instance_id === instanceId
        ? pass("F: resume → instance_id matches")
        : fail("F: resume instance_id", `expected ${instanceId}, got ${resumeResult.instance_id}`)
    );
    assertions.push(
      resumedStatus === "active"
        ? pass("F: resume → status=active (engine can continue)")
        : fail("F: resume status", `expected active, got '${resumedStatus}'`)
    );
  } catch (err) {
    assertions.push(fail("F: resume",               String(err)));
    assertions.push(fail("F: resume status=active", "skipped"));
  }

  // ── Part G: Wait for workflow.completed ──────────────────────────────────

  const completedEvent = await completedWatcher.result;
  const resumedEvent   = await resumedWatcher.result;

  if (!completedEvent) {
    assertions.push(
      fail(
        "G: workflow.completed received within 30s",
        "Timeout — worker did not emit workflow.completed after resume"
      )
    );
    assertions.push(fail("G: workflow.completed outcome=approved", "skipped — event not received"));
  } else {
    assertions.push(
      pass("G: workflow.completed received within 30s")
    );
    assertions.push(
      completedEvent["outcome"] === "approved"
        ? pass("G: workflow.completed outcome=approved")
        : fail(
            "G: workflow.completed outcome",
            `expected approved, got '${completedEvent["outcome"]}'`
          )
    );
  }

  // ── Part H: Verify final state via GET ────────────────────────────────────

  try {
    const finalState = await wf.getInstance(instanceId);

    assertions.push(
      finalState.status === "completed"
        ? pass("H: GET → status=completed")
        : fail("H: GET status", `expected completed, got '${finalState.status}'`)
    );
    assertions.push(
      finalState.outcome === "approved"
        ? pass("H: GET → outcome=approved persisted")
        : fail("H: GET outcome", `expected approved, got '${finalState.outcome}'`)
    );
  } catch (err) {
    assertions.push(fail("H: GET final state",           String(err)));
    assertions.push(fail("H: GET outcome persisted",     "skipped"));
  }

  // ── Part I: elapsed_ms sanity ─────────────────────────────────────────────
  //
  // workflow.resumed carries wait_duration_ms — the wall-clock time (in ms)
  // the instance spent in suspended state between persist-suspend and resume.
  // Must be > 0 (some time always elapses between the two REST calls).

  if (resumedEvent) {
    const durationMs = resumedEvent["wait_duration_ms"];
    assertions.push(
      typeof durationMs === "number" && durationMs > 0
        ? pass(`I: workflow.resumed wait_duration_ms > 0 (${durationMs}ms)`)
        : fail("I: wait_duration_ms", `expected positive number, got ${durationMs}`)
    );
  } else {
    assertions.push(
      fail(
        "I: wait_duration_ms > 0",
        "Skipped — workflow.resumed event not received within 30s"
      )
    );
  }

  return buildResult(assertions, startAt);
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function buildResult(
  assertions: Assertion[],
  startAt:    number,
  error?:     string
): ScenarioResult {
  const passed = assertions.every((a) => a.passed) && !error;
  return {
    scenario_id: "18",
    name:        "Workflow Worker Chain — Kafka→worker→engine (suspend→resume→complete)",
    passed,
    assertions,
    duration_ms: Date.now() - startAt,
    ...(error ? { error } : {}),
  };
}
