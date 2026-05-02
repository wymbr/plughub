/**
 * 05_routing_latency.ts
 * Scenario 5: TIMEOUT DO ROUTING ENGINE
 *
 * Performance test — validates Routing Engine latency under load.
 * Seeds 50 agent types + 5 pools, sets up 50 ready instances via direct Redis writes,
 * fires 10 concurrent routing requests and measures p95/p99 latency.
 *
 * Assertions:
 * - p95 of routing latencies < 150ms
 * - p99 of routing latencies < 200ms
 * - No routing errors when capacity is available
 * - All 10 routing requests get a response within 2s
 *
 * NOTE: This scenario is performance-only and runs separately from CI (--perf flag).
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { writeAgentInstanceDirect, writePoolConfigDirect } from "../lib/redis-client";
import {
  publishInboundEventsBatch,
  waitForRoutedEventsBatch,
} from "../lib/kafka-client";
import { pass, fail } from "../lib/report";

const CONCURRENT_REQUESTS = 10;
const POOLS_COUNT = 5;
const INSTANCES_PER_POOL = 10; // 50 total agent instances across 5 pools

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt = Date.now();
  const assertions: Assertion[] = [];

  // ── Generate a unique run ID to isolate Redis keys from previous runs ──────
  // This prevents stale queued sessions from earlier runs from consuming the
  // capacity of instances freshly seeded for this run (periodic drain races).
  const runId = randomUUID().replace(/-/g, "").substring(0, 8);
  const poolIds = Array.from({ length: POOLS_COUNT }, (_, i) => `pool_perf_${runId}_${i}`);

  // ── Flush ALL stale routing state for this tenant ────────────────────────
  // Previous failed runs leave sessions queued in pool drain loops forever.
  // The drain cycle processes those old sessions before new ones, causing
  // the consumer to time out waiting for the current run's routed events.
  // Wiping all queue/instance keys gives us a clean slate every run.
  try {
    const patterns = [
      `${ctx.tenantId}:pool:*:queue`,
      `${ctx.tenantId}:pool:*:instances`,
      `${ctx.tenantId}:instance:*`,
      `${ctx.tenantId}:queue_contact:*`,
    ];
    const keysToFlush: string[] = [];
    for (const pattern of patterns) {
      let cursor = "0";
      do {
        const [nextCursor, keys] = await ctx.redis.scan(
          cursor, "MATCH", pattern, "COUNT", "200"
        );
        cursor = nextCursor;
        keysToFlush.push(...keys);
      } while (cursor !== "0");
    }
    if (keysToFlush.length > 0) {
      // DEL accepts up to ~1M keys; split into chunks to be safe
      for (let i = 0; i < keysToFlush.length; i += 500) {
        await ctx.redis.del(...keysToFlush.slice(i, i + 500));
      }
    }
    console.log(`[05] Flushed ${keysToFlush.length} stale routing keys for tenant ${ctx.tenantId}`);
  } catch (err) {
    // Non-fatal: log and continue — worst case old sessions slow things down
    console.warn(`[05] Warning: stale key flush failed: ${String(err)}`);
  }

  // ── Seed pool configs and agent instances directly in Redis ───────────────
  try {
    // Seed pool_config directly in Redis so the routing engine can resolve pools
    // without waiting for registry.changed Kafka events (may lag on test infra).
    for (const poolId of poolIds) {
      await writePoolConfigDirect(
        ctx.redis,
        ctx.tenantId,
        poolId,
        ["webchat"],
        30000
      );
    }
    assertions.push(pass(`Perf pool configs seeded (${POOLS_COUNT} pools, runId=${runId})`));
  } catch (err) {
    assertions.push(fail("Perf pool configs seeded", String(err)));
    return buildResult(assertions, startAt, "Seed failed: " + String(err));
  }

  // ── Seed 50 agent instances directly in Redis (fast path) ─────────────────
  // Bypass registry/MCP to avoid overhead and use run-isolated pool IDs.
  try {
    const writePromises: Promise<void>[] = [];
    for (let poolIndex = 0; poolIndex < POOLS_COUNT; poolIndex++) {
      const poolId = poolIds[poolIndex]!;
      for (let instIndex = 0; instIndex < INSTANCES_PER_POOL; instIndex++) {
        const instanceId = `perf-${runId}-${poolIndex}-${instIndex}`;
        const agentTypeId = `agent_perf_${poolIndex * INSTANCES_PER_POOL + instIndex}_v1`;
        writePromises.push(
          writeAgentInstanceDirect(
            ctx.redis,
            ctx.tenantId,
            instanceId,
            agentTypeId,
            [poolId],
            5, // max_concurrent_sessions
            3600
          )
        );
      }
    }
    await Promise.all(writePromises);
    assertions.push(
      pass(`${POOLS_COUNT * INSTANCES_PER_POOL} agent instances written to Redis directly`)
    );
  } catch (err) {
    assertions.push(
      fail("Agent instances written to Redis", String(err))
    );
    return buildResult(assertions, startAt, "Instance setup failed: " + String(err));
  }

  // ── Generate 10 unique session IDs for concurrent routing ────────────────
  const sessionIds = Array.from({ length: CONCURRENT_REQUESTS }, () => randomUUID());
  const customerId = randomUUID(); // shared customer for all requests

  // ── Start Kafka consumer — two-phase: await GROUP_JOIN before publishing ──
  // waitForRoutedEventsBatch returns { ready, result }.  We await `ready` (GROUP_JOIN
  // or 4 s fallback) before publishing to conversations.inbound so the routing
  // engine's response to conversations.routed is guaranteed to be captured.
  let routingResults: Map<
    string,
    {
      allocated: boolean;
      instance_id?: string;
      pool_id?: string;
      latency_ms: number;
      publishedAt: number;
    }
  >;

  // 20 s budget: up to 5 s for GROUP_JOIN + 15 s for the routing engine to respond.
  const routedWaiter = waitForRoutedEventsBatch(ctx.kafka, sessionIds, 20000);

  // Wait for consumer to join the group before publishing inbound events
  await routedWaiter.ready;

  // ── Publish all 10 events simultaneously ──────────────────────────────────
  const publishStart = Date.now();
  try {
    await publishInboundEventsBatch(
      ctx.kafka,
      sessionIds.map((sessionId, i) => ({
        session_id:       sessionId,
        tenant_id:        ctx.tenantId,
        channel:          "webchat",
        customer_id:      customerId,
        pool_id:          poolIds[i % POOLS_COUNT],
        intent:           "perf_test",
        confidence:       0.9,
        customer_profile: { tier: "standard" },
      }))
    );
    assertions.push(
      pass(`${CONCURRENT_REQUESTS} inbound events published simultaneously`)
    );
  } catch (err) {
    assertions.push(
      fail(`${CONCURRENT_REQUESTS} inbound events published simultaneously`, String(err))
    );
    return buildResult(assertions, startAt, "Publish failed: " + String(err));
  }

  // Record publish timestamp for each session
  const publishedAt = Date.now();

  try {
    routingResults = await routedWaiter.result;
  } catch (err) {
    assertions.push(
      fail("All routing responses received within 5s", String(err))
    );
    return buildResult(assertions, startAt, "Routing wait failed: " + String(err));
  }

  // ── Measure latencies ─────────────────────────────────────────────────────
  const receivedCount = routingResults.size;
  assertions.push(
    receivedCount === CONCURRENT_REQUESTS
      ? pass(`All ${CONCURRENT_REQUESTS} routing responses received`, { count: receivedCount })
      : fail(`All ${CONCURRENT_REQUESTS} routing responses received`, {
          received: receivedCount,
          expected: CONCURRENT_REQUESTS,
          missing: sessionIds.filter((id) => !routingResults.has(id)),
        })
  );

  // Calculate per-request latency: time from publish to receive.
  // waitForRoutedEventsBatch stores the absolute receive timestamp in latency_ms;
  // subtract publishedAt (captured right after publishInboundEventsBatch) to get
  // the true routing latency per event.
  const latencies: number[] = [];
  for (const sessionId of sessionIds) {
    const result = routingResults.get(sessionId);
    if (result) {
      const latency = result.latency_ms > 0
        ? result.latency_ms - publishedAt   // absolute receive time − publish time
        : Date.now() - publishedAt;
      latencies.push(Math.max(0, latency));
    }
  }

  if (latencies.length === 0) {
    assertions.push(fail("Latency data available for analysis", { count: 0 }));
    return buildResult(assertions, startAt);
  }

  latencies.sort((a, b) => a - b);

  const p50 = percentile(latencies, 50);
  const p95 = percentile(latencies, 95);
  const p99 = percentile(latencies, 99);
  const maxLatency = latencies[latencies.length - 1] ?? 0;
  const minLatency = latencies[0] ?? 0;

  console.log(
    `[05] Routing latencies — min:${minLatency}ms  p50:${p50}ms  p95:${p95}ms  p99:${p99}ms  max:${maxLatency}ms`
  );

  // p95 < 150ms
  assertions.push(
    p95 < 150
      ? pass("p95 routing latency < 150ms", { p95, p50, p99 }, p95)
      : fail("p95 routing latency < 150ms", { p95, p50, p99 }, p95)
  );

  // p99 < 200ms
  assertions.push(
    p99 < 200
      ? pass("p99 routing latency < 200ms", { p99 }, p99)
      : fail("p99 routing latency < 200ms", { p99 }, p99)
  );

  // No all_at_capacity errors when capacity is available
  const errors = Array.from(routingResults.values()).filter(
    (r) => !r.allocated
  );
  assertions.push(
    errors.length === 0
      ? pass("No routing errors (all_at_capacity) when capacity is available")
      : fail("No routing errors when capacity is available", {
          not_allocated: errors.length,
          total: routingResults.size,
        })
  );

  // All responses within 2s
  const tooSlow = latencies.filter((l) => l > 2000);
  assertions.push(
    tooSlow.length === 0
      ? pass("All routing requests responded within 2s")
      : fail("All routing requests responded within 2s", {
          too_slow: tooSlow.length,
          max_ms: maxLatency,
        })
  );

  return buildResult(assertions, startAt);
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, Math.min(idx, sorted.length - 1))] ?? 0;
}

function buildResult(
  assertions: Assertion[],
  startAt: number,
  error?: string
): ScenarioResult {
  return {
    scenario_id: "05",
    name: "Timeout do Routing Engine (Performance)",
    passed: assertions.every((a) => a.passed) && !error,
    assertions,
    duration_ms: Date.now() - startAt,
    error,
  };
}
