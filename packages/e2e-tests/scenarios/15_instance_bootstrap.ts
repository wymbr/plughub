/**
 * 15_instance_bootstrap.ts
 * Scenario 15: INSTANCE BOOTSTRAP — configuration-driven agent registration
 *
 * Validates that the orchestrator-bridge InstanceBootstrap module correctly
 * read all active AgentTypes from the Agent Registry and registered the
 * expected instance slots in Redis.
 *
 * Principle: billing is per configured instance → Agent Registry is the
 * source of truth. No Redis seed writes should be needed for instances.
 *
 * Assertions:
 *  1. Agent Registry returns the expected non-human agent types
 *  2. For each non-human agent type, Redis contains exactly
 *     max_concurrent_sessions instance keys ({agent_type_id}-{n+1:03d})
 *  3. Each instance key has status=ready and a positive TTL (≥ 1s)
 *  4. Each pool's instances SET contains all expected instance IDs
 *  5. Pool configs are cached in Redis for each pool
 *  6. Bootstrapped instances carry channel_types derived from their pools
 */

import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { RegistryClient }         from "../lib/http-client";
import { pass, fail }             from "../lib/report";
import { triggerRegistryChanged } from "../lib/kafka-client";

const SCENARIO_TIMEOUT_MS = 30_000;

/** Bootstrap readiness: poll {tenant}:bootstrap:ready up to timeoutMs. */
async function waitForBootstrapReady(
  ctx:       ScenarioContext,
  timeoutMs: number = 30_000
): Promise<boolean> {
  const key      = `${ctx.tenantId}:bootstrap:ready`;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const exists = await ctx.redis.exists(key);
    if (exists === 1) return true;
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  return false;
}

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const registry = new RegistryClient(ctx.agentRegistryUrl, ctx.tenantId);

  // ── Preamble: wait for bootstrap readiness ────────────────────────────────
  // flushTestData (runner.ts) wipes `${tenantId}:*` before each scenario,
  // including the instance keys and the bootstrap:ready signal.
  // Publishing registry.changed triggers an immediate reconcile on the next
  // heartbeat tick (≤15 s). Without this, we'd wait up to 5 min for the
  // periodic reconcile cycle.
  await triggerRegistryChanged(ctx.kafka, ctx.tenantId);
  const bootstrapReady = await waitForBootstrapReady(ctx, 30_000);
  assertions.push(
    bootstrapReady
      ? pass("Bootstrap readiness signal present in Redis", {
          key: `${ctx.tenantId}:bootstrap:ready`,
        })
      : fail("Bootstrap readiness signal present in Redis", "key absent after 30 s wait")
  );
  if (!bootstrapReady) {
    return buildResult(assertions, startAt, "bootstrap not ready");
  }

  // ── Step 1: fetch all agent types from Agent Registry ─────────────────────
  let agentTypes: Array<{
    agent_type_id:           string;
    framework:               string;
    max_concurrent_sessions: number;
    pools:                   Array<{ pool_id: string } | string>;
  }> = [];

  try {
    const resp = await registry.listAgentTypes();
    agentTypes = resp.agent_types as typeof agentTypes;
    assertions.push(
      agentTypes.length > 0
        ? pass("Agent Registry returns at least one agent type", { total: agentTypes.length })
        : fail("Agent Registry returns at least one agent type", "empty list")
    );
  } catch (err) {
    assertions.push(fail("Agent Registry returns at least one agent type", String(err)));
    return buildResult(assertions, startAt, "registry unreachable");
  }

  // ── Step 2–6: validate Redis state for each non-human agent type ───────────
  const nonHuman = agentTypes.filter(at => at.framework !== "human");

  assertions.push(
    nonHuman.length > 0
      ? pass("At least one non-human agent type found", { count: nonHuman.length })
      : fail("At least one non-human agent type found", "all agent types are human")
  );

  for (const at of nonHuman) {
    const { agent_type_id, max_concurrent_sessions, pools } = at;
    const poolIds: string[] = pools.map(p =>
      typeof p === "string" ? p : (p as { pool_id: string }).pool_id
    );

    for (let n = 0; n < max_concurrent_sessions; n++) {
      const instanceId = `${agent_type_id}-${String(n + 1).padStart(3, "0")}`;
      const redisKey   = `${ctx.tenantId}:instance:${instanceId}`;

      // ── Assertion: instance key exists ──────────────────────────────────
      const raw = await ctx.redis.get(redisKey);
      if (!raw) {
        assertions.push(
          fail(`Instance key exists: ${instanceId}`, `key ${redisKey} not found in Redis`)
        );
        continue;
      }
      assertions.push(pass(`Instance key exists: ${instanceId}`));

      // ── Assertion: instance has status=ready ─────────────────────────────
      let instance: Record<string, unknown>;
      try {
        instance = JSON.parse(raw);
      } catch {
        assertions.push(fail(`Instance JSON valid: ${instanceId}`, "invalid JSON"));
        continue;
      }

      assertions.push(
        instance["status"] === "ready"
          ? pass(`Instance status=ready: ${instanceId}`)
          : fail(`Instance status=ready: ${instanceId}`, `status=${instance["status"]}`)
      );

      // ── Assertion: instance has a positive TTL (bootstrap wrote with TTL) ─
      const ttl = await ctx.redis.ttl(redisKey);
      assertions.push(
        ttl > 0
          ? pass(`Instance TTL>0 (bootstrap-managed): ${instanceId}`, { ttl_s: ttl })
          : fail(`Instance TTL>0 (bootstrap-managed): ${instanceId}`, `ttl=${ttl}`)
      );

      // ── Assertion: instance source = bootstrap ────────────────────────────
      assertions.push(
        instance["source"] === "bootstrap"
          ? pass(`Instance source=bootstrap: ${instanceId}`)
          : fail(`Instance source=bootstrap: ${instanceId}`, `source=${instance["source"]}`)
      );

      // ── Assertion: channel_types present and non-empty ───────────────────
      const channelTypes = instance["channel_types"];
      assertions.push(
        Array.isArray(channelTypes) && (channelTypes as unknown[]).length > 0
          ? pass(`Instance has channel_types: ${instanceId}`, { channel_types: channelTypes })
          : fail(`Instance has channel_types: ${instanceId}`, `got ${JSON.stringify(channelTypes)}`)
      );
    }

    // ── Assertion: pool:*:instances SET contains all expected instance IDs ──
    for (const poolId of poolIds) {
      const setKey = `${ctx.tenantId}:pool:${poolId}:instances`;
      const members = await ctx.redis.smembers(setKey);
      const missingInstances: string[] = [];

      for (let n = 0; n < max_concurrent_sessions; n++) {
        const instanceId = `${agent_type_id}-${String(n + 1).padStart(3, "0")}`;
        if (!members.includes(instanceId)) {
          missingInstances.push(instanceId);
        }
      }

      assertions.push(
        missingInstances.length === 0
          ? pass(`Pool instances SET complete: ${poolId} ← ${agent_type_id}`, {
              members_count: members.length,
            })
          : fail(`Pool instances SET complete: ${poolId} ← ${agent_type_id}`, {
              missing: missingInstances,
            })
      );
    }

    // ── Assertion: pool_config cached for each pool ───────────────────────
    for (const poolId of poolIds) {
      const configKey = `${ctx.tenantId}:pool_config:${poolId}`;
      const configRaw = await ctx.redis.get(configKey);
      assertions.push(
        configRaw
          ? pass(`Pool config cached in Redis: ${poolId}`)
          : fail(`Pool config cached in Redis: ${poolId}`, `key ${configKey} not found`)
      );
    }
  }

  return buildResult(assertions, startAt);
}

// ─────────────────────────────────────────────────────────────────────────────

function buildResult(
  assertions: Assertion[],
  startAt:    number,
  abortReason?: string,
): ScenarioResult {
  const failedCount = assertions.filter(a => !a.passed).length;
  return {
    scenario_id: "15",
    name:        "Instance Bootstrap — configuration-driven agent registration",
    passed:      failedCount === 0 && !abortReason,
    assertions,
    duration_ms: Date.now() - startAt,
    ...(abortReason ? { error: abortReason } : {}),
  };
}
