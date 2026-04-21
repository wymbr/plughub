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
import { writeAgentInstanceDirect } from "../lib/redis-client";
import {
  publishInboundEventsBatch,
  waitForRoutedEventsBatch,
} from "../lib/kafka-client";
import { seedPerfFixtures } from "../fixtures/seed";
import { pass, fail } from "../lib/report";

const CONCURRENT_REQUESTS = 10;
const POOLS_COUNT = 5;
const INSTANCES_PER_POOL = 10; // 50 total agent instances across 5 pools

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt = Date.now();
  const assertions: Assertion[] = [];

  // ── Seed perf fixtures (50 agent types, 5 pools) ──────────────────────────
  try {
    await seedPerfFixtures({
      agentRegistryUrl: ctx.agentRegistryUrl,
      tenantId: ctx.tenantId,
    });
    assertions.push(pass("Perf fixtures seeded (50 agent types, 5 pools)"));
    // Allow routing engine to update its cache from registry.changed Kafka events
    await new Promise((r) => setTimeout(r, 1500));
  } catch (err) {
    assertions.push(fail("Perf fixtures seeded", String(err)));
    return buildResult(assertions, startAt, "Seed failed: " + String(err));
  }

  // ── Seed 50 agent instances directly in Redis (fast path) ─────────────────
  // Bypass MCP to avoid MCP overhead in performance setup.
  try {
    const writePromises: Promise<void>[] = [];
    for (let poolIndex = 0; poolIndex < POOLS_COUNT; poolIndex++) {
      const poolId = `pool_perf_${poolIndex}`;
      for (let instIndex = 0; instIndex < INSTANCES_PER_POOL; instIndex++) {
        const instanceId = `perf-inst-${poolIndex}-${instIndex}`;
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
        pool_id:          `pool_perf_${i % POOLS_COUNT}`,
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

  // ── Wait for all routed events ────────────────────────────────────────────
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

  try {
    routingResults = await waitForRoutedEventsBatch(
      ctx.kafka,
      sessionIds,
      5000 // 5s total timeout for all responses
    );
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

  // Calculate per-request latency: time from publish to receive
  const latencies: number[] = [];
  for (const sessionId of sessionIds) {
    const result = routingResults.get(sessionId);
    if (result) {
      // latency = time from batch publish to event receive
      const latency = result.latency_ms > 0
        ? result.latency_ms
        : Date.now() - publishedAt;
      latencies.push(latency);
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
