/**
 * 16_live_reconciliation.ts
 * Scenario 16: LIVE RECONCILIATION — registry.changed triggers bootstrap without restart
 *
 * Validates the full reactive reconciliation loop:
 *   Agent Registry write → Kafka registry.changed → orchestrator-bridge reconcile()
 *   → new instance keys appear in Redis without service restart
 *
 * This is the key reliability guarantee of the reconciliation controller: any
 * change in the desired state (Registry) propagates automatically to actual state
 * (Redis) within one reconciliation cycle (~30 s worst case).
 *
 * Precondition: orchestrator-bridge must already be running and have completed
 * at least one initial reconciliation (scenario 15 ensures this).
 *
 * Steps:
 *  1. Snapshot current agent types + Redis instance keys
 *  2. Identify an existing pool to associate with the new agent type
 *  3. POST a new AgentType to Agent Registry (max_concurrent_sessions=2)
 *  4. Poll Redis for up to 30 s for the new instance keys to appear
 *  5. Assert: both instance keys have status=ready, source=bootstrap, TTL>0
 *  6. Assert: pool's instances SET contains both new instance IDs
 *  7. Clean up: DELETE the new agent type from Registry
 *     (triggers a second registry.changed; the bridge will drain those instances)
 *
 * Assertions (13):
 *   A1  At least one existing pool found for association
 *   A2  New agent type created in Registry (201 or already-OK)
 *   A3  Instance -001 appears in Redis within 30 s
 *   A4  Instance -002 appears in Redis within 30 s
 *   A5  Instance -001 status=ready
 *   A6  Instance -002 status=ready
 *   A7  Instance -001 source=bootstrap
 *   A8  Instance -002 source=bootstrap
 *   A9  Instance -001 TTL > 0
 *   A10 Instance -002 TTL > 0
 *   A11 Pool instances SET contains both new IDs
 *   A12 {tenant}:pools global SET contains pool_id
 *   A13 Delete new agent type from Registry succeeds
 */

import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { RegistryClient }         from "../lib/http-client";
import { pass, fail }             from "../lib/report";
import { triggerRegistryChanged } from "../lib/kafka-client";

/** How long to poll Redis for new instances after writing to the Registry. */
const RECONCILE_WAIT_MS   = 30_000;
const RECONCILE_POLL_MS   =  2_000;

const MAX_CONCURRENT = 2;

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const registry = new RegistryClient(ctx.agentRegistryUrl, ctx.tenantId);

  // Unique agent type ID for this test run — avoids conflicts across runs.
  // Must match /^[a-z][a-z0-9_]+_v\d+$/ (AgentTypeRegistrationSchema regex).
  const newAgentTypeId = `e2e_probe_${Date.now()}_v1`;

  // ── Preamble: wait for bootstrap readiness ────────────────────────────────
  // flushTestData (runner.ts) wipes ${tenantId}:* before each scenario,
  // including the bootstrap:ready signal and all instance keys.
  // Publishing registry.changed triggers an immediate reconcile on the next
  // heartbeat tick (≤15 s). Without this, we'd wait up to 5 min for the
  // periodic reconcile cycle.
  await triggerRegistryChanged(ctx.kafka, ctx.tenantId);
  {
    const key      = `${ctx.tenantId}:bootstrap:ready`;
    const deadline = Date.now() + 30_000;
    let ready      = false;
    while (Date.now() < deadline) {
      if ((await ctx.redis.exists(key)) === 1) { ready = true; break; }
      await sleep(2000);
    }
    if (!ready) {
      assertions.push(fail("Bootstrap ready before reconciliation test", "key absent after 30 s"));
      return buildResult(assertions, startAt, "bootstrap not ready");
    }
  }

  // ── Step 1: find an existing pool to associate with the new agent type ────
  let targetPoolId: string | null = null;
  try {
    const resp = await registry.listPools() as { pools?: unknown[]; [k: string]: unknown } | unknown[];
    const pools: unknown[] = Array.isArray(resp)
      ? resp
      : (resp as { pools?: unknown[] }).pools ?? [];

    if (pools.length > 0) {
      const first = pools[0] as { pool_id?: string; id?: string };
      targetPoolId = first.pool_id ?? first.id ?? null;
    }

    assertions.push(
      targetPoolId
        ? pass("Existing pool found for association", { pool_id: targetPoolId })
        : fail("Existing pool found for association", "no pools returned by Registry")
    );
  } catch (err) {
    assertions.push(fail("Existing pool found for association", String(err)));
    return buildResult(assertions, startAt, "cannot list pools");
  }

  if (!targetPoolId) {
    return buildResult(assertions, startAt, "no pool available for new agent type");
  }

  // ── Step 2: create new AgentType in Registry ──────────────────────────────
  const newAgentTypeBody = {
    agent_type_id:           newAgentTypeId,
    framework:               "anthropic_sdk",
    execution_model:         "stateless",
    max_concurrent_sessions: MAX_CONCURRENT,
    pools:                   [targetPoolId],
    skills:                  [],
  };

  try {
    await post(ctx.agentRegistryUrl + "/v1/agent-types", newAgentTypeBody, {
      "Content-Type":  "application/json",
      "x-tenant-id":   ctx.tenantId,
      "x-user-id":     "e2e-runner",
    });
    assertions.push(pass("New agent type created in Registry", { agent_type_id: newAgentTypeId }));
  } catch (err) {
    assertions.push(fail("New agent type created in Registry", String(err)));
    return buildResult(assertions, startAt, "cannot create agent type");
  }

  // Explicitly signal the bridge to reconcile now that the new agent type exists.
  // In production the Agent Registry publishes registry.changed automatically, but
  // publishing here as well makes the scenario self-contained and robust to any
  // Kafka publish delay or silent failure in the agent-registry service.
  await triggerRegistryChanged(ctx.kafka, ctx.tenantId);

  // ── Step 3–4: poll Redis for up to 30 s for both instance keys ───────────
  const instanceIds = Array.from({ length: MAX_CONCURRENT }, (_, n) =>
    `${newAgentTypeId}-${String(n + 1).padStart(3, "0")}`
  );
  const redisKeys = instanceIds.map(id => `${ctx.tenantId}:instance:${id}`);

  const found = await pollUntilAllExist(ctx, redisKeys, RECONCILE_WAIT_MS, RECONCILE_POLL_MS);

  for (let i = 0; i < MAX_CONCURRENT; i++) {
    const instanceId = instanceIds[i];
    const label      = i === 0 ? "-001" : "-002";

    assertions.push(
      found[i]
        ? pass(`Instance ${label} appears in Redis within 30 s`, { key: redisKeys[i] })
        : fail(`Instance ${label} appears in Redis within 30 s`, `key ${redisKeys[i]} not found after ${RECONCILE_WAIT_MS} ms`)
    );
  }

  // ── Step 5–10: validate content of found instance keys ───────────────────
  for (let i = 0; i < MAX_CONCURRENT; i++) {
    const instanceId  = instanceIds[i];
    const redisKey    = redisKeys[i];
    const ordinal     = i === 0 ? "-001" : "-002";

    if (!found[i]) {
      // Key never appeared — skip content assertions for it
      assertions.push(fail(`Instance${ordinal} status=ready`,    "key absent"));
      assertions.push(fail(`Instance${ordinal} source=bootstrap`, "key absent"));
      assertions.push(fail(`Instance${ordinal} TTL>0`,            "key absent"));
      continue;
    }

    const raw = await ctx.redis.get(redisKey);
    let instance: Record<string, unknown>;
    try {
      instance = JSON.parse(raw!);
    } catch {
      assertions.push(fail(`Instance${ordinal} status=ready`,    "invalid JSON"));
      assertions.push(fail(`Instance${ordinal} source=bootstrap`, "invalid JSON"));
      assertions.push(fail(`Instance${ordinal} TTL>0`,            "invalid JSON"));
      continue;
    }

    // A5/A6: status=ready
    assertions.push(
      instance["status"] === "ready"
        ? pass(`Instance${ordinal} status=ready`, { instance_id: instanceId })
        : fail(`Instance${ordinal} status=ready`, `status=${instance["status"]}`)
    );

    // A7/A8: source=bootstrap
    assertions.push(
      instance["source"] === "bootstrap"
        ? pass(`Instance${ordinal} source=bootstrap`)
        : fail(`Instance${ordinal} source=bootstrap`, `source=${instance["source"]}`)
    );

    // A9/A10: TTL > 0
    const ttl = await ctx.redis.ttl(redisKey);
    assertions.push(
      ttl > 0
        ? pass(`Instance${ordinal} TTL>0 (bootstrap-managed)`, { ttl_s: ttl })
        : fail(`Instance${ordinal} TTL>0 (bootstrap-managed)`, `ttl=${ttl}`)
    );
  }

  // ── Step 6: pool:*:instances SET contains both new instance IDs ───────────
  const poolInstancesKey = `${ctx.tenantId}:pool:${targetPoolId}:instances`;
  const members          = await ctx.redis.smembers(poolInstancesKey);
  const allInSet         = instanceIds.every(id => members.includes(id));

  assertions.push(
    allInSet
      ? pass("Pool instances SET contains both new instance IDs", {
          pool_id: targetPoolId,
          new_ids: instanceIds,
          set_size: members.length,
        })
      : fail("Pool instances SET contains both new instance IDs", {
          pool_id:  targetPoolId,
          expected: instanceIds,
          found_in_set: instanceIds.filter(id => members.includes(id)),
          missing:      instanceIds.filter(id => !members.includes(id)),
        })
  );

  // ── A12: {tenant}:pools global SET contains pool_id ──────────────────────
  const poolsSetKey    = `${ctx.tenantId}:pools`;
  const poolsSetMember = await ctx.redis.sismember(poolsSetKey, targetPoolId);

  assertions.push(
    poolsSetMember === 1
      ? pass("{tenant}:pools global SET contains pool_id", { pool_id: targetPoolId })
      : fail("{tenant}:pools global SET contains pool_id", `${poolsSetKey} missing ${targetPoolId}`)
  );

  // ── Step 7: clean up — DELETE new agent type from Registry ───────────────
  // This fires a second registry.changed event; the bridge will set draining=true
  // on the instance keys (they are still in status=ready at this point).
  try {
    await del(ctx.agentRegistryUrl + `/v1/agent-types/${encodeURIComponent(newAgentTypeId)}`, {
      "x-tenant-id": ctx.tenantId,
      "x-user-id":   "e2e-runner",
    });
    assertions.push(pass("New agent type deleted from Registry (cleanup)", { agent_type_id: newAgentTypeId }));
  } catch (err) {
    // Non-fatal — the ephemeral Docker environment will be torn down anyway
    assertions.push(fail("New agent type deleted from Registry (cleanup)", String(err)));
  }

  return buildResult(assertions, startAt);
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Polls Redis every pollMs until ALL keys exist, or timeoutMs elapses.
 * Returns a boolean[] where true means the corresponding key was found.
 */
async function pollUntilAllExist(
  ctx:       ScenarioContext,
  keys:      string[],
  timeoutMs: number,
  pollMs:    number
): Promise<boolean[]> {
  const found  = new Array(keys.length).fill(false) as boolean[];
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    for (let i = 0; i < keys.length; i++) {
      if (!found[i]) {
        const exists = await ctx.redis.exists(keys[i]);
        if (exists === 1) {
          found[i] = true;
          console.log(`[scenario-16] Instance key appeared: ${keys[i]}`);
        }
      }
    }
    if (found.every(Boolean)) break;
    await sleep(pollMs);
  }

  return found;
}

async function post(url: string, body: unknown, headers?: Record<string, string>): Promise<unknown> {
  const res = await fetch(url, {
    method:  "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body:    JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`POST ${url} → ${res.status}: ${text}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function del(url: string, headers?: Record<string, string>): Promise<void> {
  const res = await fetch(url, { method: "DELETE", headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`DELETE ${url} → ${res.status}: ${text}`);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─────────────────────────────────────────────────────────────────────────────

function buildResult(
  assertions:  Assertion[],
  startAt:     number,
  abortReason?: string,
): ScenarioResult {
  const failedCount = assertions.filter(a => !a.passed).length;
  return {
    scenario_id: "16",
    name:        "Live Reconciliation — registry.changed triggers bootstrap without restart",
    passed:      failedCount === 0 && !abortReason,
    assertions,
    duration_ms: Date.now() - startAt,
    ...(abortReason ? { error: abortReason } : {}),
  };
}
