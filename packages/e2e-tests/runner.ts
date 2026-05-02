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
 *   ts-node runner.ts --demo          — run all scenarios 01–16 (full demo suite)
 *   ts-node runner.ts --only 07       — run only scenario 07 (full inbound flow)
 *   ts-node runner.ts --only 08       — run only scenario 08 (outbound flow)
 *   ts-node runner.ts --only 09       — run only scenario 09 (session replayer)
 *   ts-node runner.ts --only 10       — run only scenario 10 (message masking)
 *   ts-node runner.ts --only 11       — run only scenario 11 (comparison mode)
 *   ts-node runner.ts --only 12       — run only scenario 12 (webchat channel)
 *   ts-node runner.ts --webchat       — run scenarios 01–04 + 12 (includes webchat)
 *   ts-node runner.ts --workflow      — run scenarios 01–04 + 13 + 14 (workflow + collect)
 *   ts-node runner.ts --only 13       — run only scenario 13 (workflow automation)
 *   ts-node runner.ts --collect       — run scenarios 01–04 + 14 (collect step)
 *   ts-node runner.ts --only 14       — run only scenario 14 (collect step)
 *   ts-node runner.ts --bootstrap     — run scenario 15 (instance bootstrap)
 *   ts-node runner.ts --only 15       — run only scenario 15 (instance bootstrap)
 *   ts-node runner.ts --reconcile     — run scenarios 01–04 + 15 + 16 (bootstrap + live reconcile)
 *   ts-node runner.ts --only 16       — run only scenario 16 (live reconciliation)
 *   ts-node runner.ts --ctx           — run scenario 17 (ContextStore accumulation + supervisor_state)
 *   ts-node runner.ts --only 17       — run only scenario 17 (ContextStore)
 *   ts-node runner.ts --worker        — run scenario 18 (Kafka→worker→engine chain)
 *   ts-node runner.ts --only 18       — run only scenario 18 (workflow worker chain)
 *   ts-node runner.ts --mention       — run scenario 19 (@mention co-pilot + masked PIN auth)
 *   ts-node runner.ts --only 19       — run only scenario 19 (@mention + masked PIN)
 *   ts-node runner.ts --masked        — run scenarios 20 + 21 (masked form + retry cycle)
 *   ts-node runner.ts --only 20       — run only scenario 20 (masked form field-level policy)
 *   ts-node runner.ts --only 21       — run only scenario 21 (masked retry / rollback cycle)
 *   ts-node runner.ts --hooks         — run scenario 22 (pool lifecycle hooks Fase B + C)
 *   ts-node runner.ts --only 22       — run only scenario 22 (on_human_end + post_human + participation)
 *   ts-node runner.ts --segments      — run scenario 23 (Arc 5 ContactSegment analytics pipeline)
 *   ts-node runner.ts --only 23       — run only scenario 23 (ContactSegment lifecycle + topology)
 *   ts-node runner.ts --evaluation    — run scenario 24 (Arc 6 Evaluation Campaign pipeline)
 *   ts-node runner.ts --only 24       — run only scenario 24 (Form+Campaign CRUD, Kafka→analytics)
 *   ts-node runner.ts --contestation  — run scenario 25 (Arc 6 Contestation + locked result)
 *   ts-node runner.ts --only 25       — run only scenario 25 (contestation + adjudication + locked)
 *   ts-node runner.ts --fallback         — run scenario 26 (AI Gateway 429 fallback + account rotation)
 *   ts-node runner.ts --only 26          — run only scenario 26 (AI Gateway provider fallback chain)
 *   ts-node runner.ts --permissions      — run scenario 27 (Arc 6 v2 — 2D permission model)
 *   ts-node runner.ts --only 27          — run only scenario 27 (grant/list/update/resolve/revoke perms)
 *   ts-node runner.ts --workflow-review  — run scenario 28 (Arc 6 v2 — workflow review/contestation cycle)
 *   ts-node runner.ts --only 28          — run only scenario 28 (workflow motor, anti-replay, ContextStore lock)
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
 *   ANALYTICS_API_URL         (default: http://localhost:3500)
 *   EVALUATION_API_URL        (default: http://localhost:3400)
 *   CONFIG_API_URL            (default: http://localhost:3600)
 *   CONFIG_API_ADMIN_TOKEN    (default: test_e2e_admin_token)
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
  waitForBootstrap,
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
import { run as scenario14 } from "./scenarios/14_collect_step";
import { run as scenario15 } from "./scenarios/15_instance_bootstrap";
import { run as scenario16 } from "./scenarios/16_live_reconciliation";
import { run as scenario17 } from "./scenarios/17_context_store";
import { run as scenario18 } from "./scenarios/18_workflow_worker_chain";
import { run as scenario19 } from "./scenarios/19_mention_copilot_auth";
import { run as scenario20 } from "./scenarios/20_masked_form";
import { run as scenario21 } from "./scenarios/21_masked_retry";
import { run as scenario22 } from "./scenarios/22_pool_hooks_fase_b";
import { run as scenario23 } from "./scenarios/23_contact_segments";
import { run as scenario24 } from "./scenarios/24_evaluation_campaign";
import { run as scenario25 } from "./scenarios/25_evaluation_contestation";
import { run as scenario26 } from "./scenarios/26_ai_gateway_fallback";
import { run as scenario27 } from "./scenarios/27_evaluation_permissions";
import { run as scenario28 } from "./scenarios/28_evaluation_workflow_cycle";

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
  analyticsApiUrl:       process.env["ANALYTICS_API_URL"]        ?? "http://localhost:3500",
  evaluationApiUrl:      process.env["EVALUATION_API_URL"]       ?? "http://localhost:3400",
  configApiUrl:          process.env["CONFIG_API_URL"]           ?? "http://localhost:3600",
  redisUrl:              process.env["REDIS_URL"]                ?? "redis://localhost:6379",
  kafkaBrokers:          (process.env["KAFKA_BROKERS"]           ?? "localhost:9092").split(","),
  tenantId:              process.env["TENANT_ID"]                ?? "tenant_e2e_test",
  jwtSecret:             process.env["JWT_SECRET"]               ?? "test_e2e_secret_32chars",
  webchatJwtSecret:      process.env["WEBCHAT_JWT_SECRET"]       ?? "test_e2e_webchat_secret_32chars",
  configApiAdminToken:   process.env["CONFIG_API_ADMIN_TOKEN"]   ?? "test_e2e_admin_token",
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
const runCollect    = args.includes("--collect")    || onlyScenario === "14";
const runBootstrap  = args.includes("--bootstrap")  || onlyScenario === "15";
const runReconcile  = args.includes("--reconcile") || onlyScenario === "16";
const runCtx        = args.includes("--ctx")        || onlyScenario === "17";
const runWorker     = args.includes("--worker")     || onlyScenario === "18";
const runMention    = args.includes("--mention")    || onlyScenario === "19";
const runMasked     = args.includes("--masked")     || onlyScenario === "20" || onlyScenario === "21";
const runHooks      = args.includes("--hooks")      || onlyScenario === "22";
const runSegments   = args.includes("--segments")   || onlyScenario === "23";
const runEvaluation = args.includes("--evaluation") || onlyScenario === "24";
const runContestation = args.includes("--contestation") || onlyScenario === "25";
const runFallback         = args.includes("--fallback")         || onlyScenario === "26";
const runPermissions      = args.includes("--permissions")      || onlyScenario === "27";
const runWorkflowReview   = args.includes("--workflow-review")  || onlyScenario === "28";
const runDemo             = args.includes("--demo");  // runs all scenarios 01–18

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

if (runDemo || runWorkflow || runCollect || onlyScenario === "14") {
  ALL_SCENARIOS.push({ id: "14", fn: scenario14 });
}

if (runDemo || runBootstrap || onlyScenario === "15") {
  ALL_SCENARIOS.push({ id: "15", fn: scenario15 });
}

if (runDemo || runReconcile || onlyScenario === "16") {
  ALL_SCENARIOS.push({ id: "16", fn: scenario16 });
}

if (runDemo || runCtx || onlyScenario === "17") {
  ALL_SCENARIOS.push({ id: "17", fn: scenario17 });
}

if (runDemo || runWorker || onlyScenario === "18") {
  ALL_SCENARIOS.push({ id: "18", fn: scenario18 });
}

if (runMention || onlyScenario === "19") {
  ALL_SCENARIOS.push({ id: "19", fn: scenario19 });
}

if (runMasked || onlyScenario === "20") {
  ALL_SCENARIOS.push({ id: "20", fn: scenario20 });
}

if (runMasked || onlyScenario === "21") {
  ALL_SCENARIOS.push({ id: "21", fn: scenario21 });
}

if (runHooks || onlyScenario === "22") {
  ALL_SCENARIOS.push({ id: "22", fn: scenario22 });
}

if (runSegments || onlyScenario === "23") {
  ALL_SCENARIOS.push({ id: "23", fn: scenario23 });
}

if (runEvaluation || onlyScenario === "24") {
  ALL_SCENARIOS.push({ id: "24", fn: scenario24 });
}

if (runContestation || onlyScenario === "25") {
  ALL_SCENARIOS.push({ id: "25", fn: scenario25 });
}

if (runFallback || onlyScenario === "26") {
  ALL_SCENARIOS.push({ id: "26", fn: scenario26 });
}

if (runPermissions || onlyScenario === "27") {
  ALL_SCENARIOS.push({ id: "27", fn: scenario27 });
}

if (runWorkflowReview || onlyScenario === "28") {
  ALL_SCENARIOS.push({ id: "28", fn: scenario28 });
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
  console.log(`  Analytics:   ${config.analyticsApiUrl}`);
  console.log(`  Kafka:       ${config.kafkaBrokers.join(",")}`);
  console.log(`  Redis:       ${config.redisUrl}`);
  console.log(
    `  Scenarios:   ${SCENARIOS_TO_RUN.map((s) => s.id).join(", ")}`
  );
  console.log("─".repeat(60) + "\n");

  // ── Wait for services (only those needed by the selected scenarios) ─────────
  console.log("[runner] Waiting for services to be ready...");

  const scenarioIds = new Set(SCENARIOS_TO_RUN.map((s) => s.id));
  const needsCore      = [...scenarioIds].some((id) => !["13", "14", "15", "16"].includes(id));
  const needsRegistry  = scenarioIds.has("15") || scenarioIds.has("16") || needsCore;
  const needsBootstrap = scenarioIds.has("15") || scenarioIds.has("16");
  const needsConfigApi = scenarioIds.has("16");
  const needsWorkflow  = scenarioIds.has("13") || scenarioIds.has("14") || scenarioIds.has("18") || runWorkflow || runCollect || runWorker;

  const waits: Promise<unknown>[] = [
    waitForRedis(config.redisUrl, 30000),
    waitForKafka(config.kafkaBrokers, 30000),
  ];

  if (needsRegistry) {
    waits.push(
      waitForService(`${config.agentRegistryUrl}/v1/health`, "agent-registry", 30000),
    );
  }

  if (needsConfigApi) {
    waits.push(
      waitForService(`${config.configApiUrl}/v1/health`, "config-api", 30000),
    );
  }

  if (needsCore) {
    waits.push(
      waitForService(`${config.mcpServerUrl}/health`, "mcp-server-plughub", 30000),
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

  // ── Wait for bootstrap readiness (scenarios 15 and 16) ──────────────────────
  // The orchestrator-bridge has no HTTP health endpoint — it signals readiness
  // by writing {tenant}:bootstrap:ready to Redis after its first reconciliation.
  if (needsBootstrap) {
    console.log("[runner] Waiting for orchestrator-bridge initial reconciliation...");
    await waitForBootstrap(config.redisUrl, config.tenantId, 60000);
    console.log("[runner] Bootstrap ready.\n");
  }

  // ── Seed base fixtures (only when registry is available) ────────────────────
  if (needsRegistry) {
    console.log("[runner] Seeding base fixtures...");
    await seedBaseFixtures({
      agentRegistryUrl: config.agentRegistryUrl,
      tenantId: config.tenantId,
    });
    console.log("[runner] Fixtures seeded.\n");
  } else {
    console.log("[runner] Skipping base fixtures (no registry-dependent scenarios).\n");
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
      analyticsApiUrl:       config.analyticsApiUrl,
      evaluationApiUrl:      config.evaluationApiUrl,
      configApiUrl:          config.configApiUrl,
      configApiAdminToken:   config.configApiAdminToken,
      redis,
      kafka,
      tenantId:              config.tenantId,
      jwtSecret:             config.jwtSecret,
      webchatJwtSecret:      config.webchatJwtSecret,
    };

    // Scenario 18 involves two Kafka consumer setups + two worker processing cycles.
    // The worker needs to consume workflow.started, run engine (suspend), call
    // persist-suspend, then consume workflow.resumed and run engine again (complete).
    // Each Kafka consumer setup adds ~1-2s, and the worker may take a few seconds
    // to process each event.  120s provides a comfortable window.
    // Scenario 18: two Kafka round-trips + two worker engine runs → 120s
    // Scenario 19: LLM reason step (real API) + BLPOP cycles → 90s
    // Scenarios 20 + 21: BLPOP cycles, two form/text menu interactions → 60s
    const timeoutMs = id === "18" ? 120_000 : id === "19" ? 90_000 : 60_000;

    let result: ScenarioResult;
    try {
      result = await runWithTimeout(fn(ctx), timeoutMs, id);
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
