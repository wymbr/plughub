/**
 * runner.ts
 * Main entry point for the PlugHub E2E black-box test suite.
 *
 * Usage:
 *   ts-node runner.ts                 — run scenarios 01–04
 *   ts-node runner.ts --only 03       — run only scenario 03
 *   ts-node runner.ts --perf          — run scenarios 01–04 + 05 (performance)
 *   ts-node runner.ts --only 05       — run only scenario 05 (performance)
 *   ts-node runner.ts --conference    — run scenarios 01–04 + 06 (conference + reconnect)
 *   ts-node runner.ts --only 06       — run only scenario 06 (conference + reconnect)
 *   ts-node runner.ts --demo          — run all scenarios 01–12 (full demo suite)
 *   ts-node runner.ts --only 07       — run only scenario 07 (full inbound flow)
 *   ts-node runner.ts --only 08       — run only scenario 08 (outbound flow)
 *   ts-node runner.ts --only 09       — run only scenario 09 (session replayer)
 *   ts-node runner.ts --only 10       — run only scenario 10 (message masking)
 *   ts-node runner.ts --only 11       — run only scenario 11 (comparison mode)
 *   ts-node runner.ts --only 12       — run only scenario 12 (webchat channel)
 *   ts-node runner.ts --webchat       — run scenarios 01–04 + 12 (includes webchat)
 *   ts-node runner.ts --workflow      — run scenarios 01–04 + 13 (workflow automation)
 *   ts-node runner.ts --only 13       — run only scenario 13 (workflow automation)
 *
 * Environment variables (all optional — defaults work with docker-compose.test.yml):
 *   MCP_SERVER_URL            (default: http://localhost:3100)
 *   AGENT_REGISTRY_URL        (default: http://localhost:3300)
 *   SKILL_FLOW_URL            (default: http://localhost:3400)
 *   RULES_ENGINE_URL          (default: http://localhost:3201)
 *   AI_GATEWAY_URL            (default: http://localhost:3200)
 *   CHANNEL_GATEWAY_WS_URL    (default: ws://localhost:8010)
 *   CHANNEL_GATEWAY_HTTP_URL  (default: http://localhost:8010)
 *   WORKFLOW_API_URL          (default: http://localhost:3800)
 *   CALENDAR_API_URL          (default: http://localhost:3700)
 *   REDIS_URL                 (default: redis://localhost:6379)
 *   KAFKA_BROKERS             (default: localhost:9092)
 *   TENANT_ID                 (default: tenant_e2e_test)
 *   JWT_SECRET                (default: test_e2e_secret_32chars)
 *   WEBCHAT_JWT_SECRET        (default: test_e2e_webchat_secret_32chars)
 *   E2E_REPORT_PATH           (default: ./e2e-report.json)
 */

import { join } from "path";
import { createTestRedis, flushTestData } from "./lib/redis-client";
import { createTestKafka } from "./lib/kafka-client";
import {
  waitForService,
  waitForRedis,
  waitForKafka,
} from "./lib/wait-for";
import { ReportBuilder } from "./lib/report";
import { seedBaseFixtures } from "./fixtures/seed";
import type { ScenarioContext, ScenarioResult } from "./scenarios/types";

// Scenarios
import { run as scenario01 } from "./scenarios/01_happy_path";
import { run as scenario02 } from "./scenarios/02_escalation_handoff";
import { run as scenario03 } from "./scenarios/03_resume_after_failure";
import { run as scenario04 } from "./scenarios/04_rules_engine";
import { run as scenario05 } from "./scenarios/05_routing_latency";
import { run as scenario06 } from "./scenarios/06_conference";
import { run as scenario07 } from "./scenarios/07_inbound_full";
import { run as scenario08 } from "./scenarios/08_outbound";
import { run as scenario09 } from "./scenarios/09_session_replayer";
import { run as scenario10 } from "./scenarios/10_masking";
import { run as scenario11 } from "./scenarios/11_comparison_mode";
import { run as scenario12 } from "./scenarios/12_webchat_channel";
import { run as scenario13 } from "./scenarios/13_workflow_automation";

// ─────────────────────────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────────────────────────

const config = {
  mcpServerUrl:          process.env["MCP_SERVER_URL"]           ?? "http://localhost:3100",
  agentRegistryUrl:      process.env["AGENT_REGISTRY_URL"]       ?? "http://localhost:3300",
  skillFlowUrl:          process.env["SKILL_FLOW_URL"]           ?? "http://localhost:3400",
  rulesEngineUrl:        process.env["RULES_ENGINE_URL"]         ?? "http://localhost:3201",
  aiGatewayUrl:          process.env["AI_GATEWAY_URL"]           ?? "http://localhost:3200",
  channelGatewayWsUrl:   process.env["CHANNEL_GATEWAY_WS_URL"]   ?? "ws://localhost:8010",
  channelGatewayHttpUrl: process.env["CHANNEL_GATEWAY_HTTP_URL"] ?? "http://localhost:8010",
  workflowApiUrl:        process.env["WORKFLOW_API_URL"]         ?? "http://localhost:3800",
  calendarApiUrl:        process.env["CALENDAR_API_URL"]         ?? "http://localhost:3700",
  redisUrl:              process.env["REDIS_URL"]                ?? "redis://localhost:6379",
  kafkaBrokers:          (process.env["KAFKA_BROKERS"]           ?? "localhost:9092").split(","),
  tenantId:              process.env["TENANT_ID"]                ?? "tenant_e2e_test",
  jwtSecret:             process.env["JWT_SECRET"]               ?? "test_e2e_secret_32chars",
  webchatJwtSecret:      process.env["WEBCHAT_JWT_SECRET"]       ?? "test_e2e_webchat_secret_32chars",
  reportPath:            process.env["E2E_REPORT_PATH"]          ?? join(__dirname, "e2e-report.json"),
};

// ─────────────────────────────────────────────────────────────────────────────
// CLI argument parsing
// ─────────────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const onlyFlag = args.indexOf("--only");
const onlyScenario = onlyFlag >= 0 ? args[onlyFlag + 1] : null;
const runPerf       = args.includes("--perf")       || onlyScenario === "05";
const runConference = args.includes("--conference") || onlyScenario === "06";
const runWebchat    = args.includes("--webchat")    || onlyScenario === "12";
const runWorkflow   = args.includes("--workflow")   || onlyScenario === "13";
const runDemo       = args.includes("--demo");  // runs all scenarios 01–13

// ─────────────────────────────────────────────────────────────────────────────
// Scenario registry
// ─────────────────────────────────────────────────────────────────────────────

const ALL_SCENARIOS: Array<{ id: string; fn: (ctx: ScenarioContext) => Promise<ScenarioResult> }> = [
  { id: "01", fn: scenario01 },
  { id: "02", fn: scenario02 },
  { id: "03", fn: scenario03 },
  { id: "04", fn: scenario04 },
];

if (runPerf) {
  ALL_SCENARIOS.push({ id: "05", fn: scenario05 });
}

if (runConference || runDemo) {
  ALL_SCENARIOS.push({ id: "06", fn: scenario06 });
}

if (runDemo || onlyScenario === "07") {
  ALL_SCENARIOS.push({ id: "07", fn: scenario07 });
}

if (runDemo || onlyScenario === "08") {
  ALL_SCENARIOS.push({ id: "08", fn: scenario08 });
}

if (runDemo || onlyScenario === "09") {
  ALL_SCENARIOS.push({ id: "09", fn: scenario09 });
}

if (runDemo || onlyScenario === "10") {
  ALL_SCENARIOS.push({ id: "10", fn: scenario10 });
}

if (runDemo || onlyScenario === "11") {
  ALL_SCENARIOS.push({ id: "11", fn: scenario11 });
}

if (runDemo || runWebchat || onlyScenario === "12") {
  ALL_SCENARIOS.push({ id: "12", fn: scenario12 });
}

if (runDemo || runWorkflow || onlyScenario === "13") {
  ALL_SCENARIOS.push({ id: "13", fn: scenario13 });
}

const SCENARIOS_TO_RUN = onlyScenario
  ? ALL_SCENARIOS.filter((s) => s.id === onlyScenario)
  : ALL_SCENARIOS;

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  console.log("\n" + "═".repeat(60));
  console.log("  PlugHub E2E Test Runner");
  console.log("═".repeat(60));
  console.log(`  Tenant:      ${config.tenantId}`);
  console.log(`  MCP Server:  ${config.mcpServerUrl}`);
  console.log(`  Registry:    ${config.agentRegistryUrl}`);
  console.log(`  Workflow:    ${config.workflowApiUrl}`);
  console.log(`  Calendar:    ${config.calendarApiUrl}`);
  console.log(`  Kafka:       ${config.kafkaBrokers.join(",")}`);
  console.log(`  Redis:       ${config.redisUrl}`);
  console.log(
    `  Scenarios:   ${SCENARIOS_TO_RUN.map((s) => s.id).join(", ")}`
  );
  console.log("─".repeat(60) + "\n");

  // ── Wait for services (only those needed by the selected scenarios) ─────────
  console.log("[runner] Waiting for services to be ready...");

  const scenarioIds = new Set(SCENARIOS_TO_RUN.map((s) => s.id));
  const needsCore     = [...scenarioIds].some((id) => !["13"].includes(id));
  const needsWorkflow = scenarioIds.has("13") || runWorkflow;

  const waits: Promise<unknown>[] = [
    waitForRedis(config.redisUrl, 30000),
    waitForKafka(config.kafkaBrokers, 30000),
  ];

  if (needsCore) {
    waits.push(
      waitForService(`${config.agentRegistryUrl}/v1/health`, "agent-registry", 30000),
      waitForService(`${config.mcpServerUrl}/sse`, "mcp-server-plughub", 30000),
      waitForService(`${config.skillFlowUrl}/health`, "skill-flow-service", 30000),
      waitForService(`${config.rulesEngineUrl}/rules?tenant_id=${config.tenantId}`, "rules-engine", 30000),
    );
  }

  if (needsWorkflow) {
    waits.push(
      waitForService(`${config.workflowApiUrl}/v1/health`, "workflow-api", 30000),
      waitForService(`${config.calendarApiUrl}/v1/health`, "calendar-api", 30000),
    );
  }

  await Promise.all(waits);
  console.log("[runner] All services ready.\n");

  // ── Seed base fixtures (only when core services are available) ───────────────
  if (needsCore) {
    console.log("[runner] Seeding base fixtures...");
    await seedBaseFixtures({
      agentRegistryUrl: config.agentRegistryUrl,
      tenantId: config.tenantId,
    });
    console.log("[runner] Fixtures seeded.\n");
  } else {
    console.log("[runner] Skipping base fixtures (no core services needed).\n");
  }

  // ── Run scenarios ──────────────────────────────────────────────────────────
  const report = new ReportBuilder();
  const redis = createTestRedis(config.redisUrl);
  const kafka = createTestKafka(config.kafkaBrokers);

  for (const { id, fn } of SCENARIOS_TO_RUN) {
    console.log(`[runner] ─── Running Scenario ${id} ───`);

    // Flush test data before each scenario for isolation
    await flushTestData(redis, config.tenantId);

    // Build context (fresh Redis per scenario via shared client, flushed above)
    const ctx: ScenarioContext = {
      mcpServerUrl:          config.mcpServerUrl,
      agentRegistryUrl:      config.agentRegistryUrl,
      skillFlowUrl:          config.skillFlowUrl,
      rulesEngineUrl:        config.rulesEngineUrl,
      aiGatewayUrl:          config.aiGatewayUrl,
      channelGatewayWsUrl:   config.channelGatewayWsUrl,
      channelGatewayHttpUrl: config.channelGatewayHttpUrl,
      workflowApiUrl:        config.workflowApiUrl,
      calendarApiUrl:        config.calendarApiUrl,
      redis,
      kafka,
      tenantId:              config.tenantId,
      jwtSecret:             config.jwtSecret,
      webchatJwtSecret:      config.webchatJwtSecret,
    };

    // Run with 60s timeout
    let result: ScenarioResult;
    try {
      result = await runWithTimeout(fn(ctx), 60_000, id);
    } catch (err) {
      result = {
        scenario_id: id,
        name: `Scenario ${id}`,
        passed: false,
        assertions: [],
        duration_ms: 0,
        error: `Scenario timed out or crashed: ${String(err)}`,
      };
    }

    report.addScenario(result);
    const icon = result.passed ? "✅" : "❌";
    console.log(
      `[runner] ${icon} Scenario ${id} — ${result.passed ? "PASSED" : "FAILED"} (${result.duration_ms}ms)\n`
    );
  }

  // ── Cleanup ────────────────────────────────────────────────────────────────
  await redis.quit().catch(() => undefined);

  // ── Write report ───────────────────────────────────────────────────────────
  await report.writeToFile(config.reportPath);
  console.log(`[runner] Report written to ${config.reportPath}`);

  // ── Print summary ──────────────────────────────────────────────────────────
  report.printSummary();

  // ── Exit code ─────────────────────────────────────────────────────────────
  const finalReport = report.build();
  process.exit(finalReport.passed ? 0 : 1);
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

async function runWithTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  scenarioId: string
): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`Scenario ${scenarioId} exceeded ${timeoutMs}ms timeout`)),
      timeoutMs
    );
  });
  try {
    const result = await Promise.race([promise, timeoutPromise]);
    clearTimeout(timer!);
    return result;
  } catch (err) {
    clearTimeout(timer!);
    throw err;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────────────────────

main().catch((err) => {
  console.error("[runner] Fatal error:", err);
  process.exit(1);
});
