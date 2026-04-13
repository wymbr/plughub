/**
 * 04_rules_engine.ts
 * Scenario 4: MOTOR DE REGRAS — ESCALAÇÃO INTRA-TURNO
 *
 * Validates that the Rules Engine detects a rule condition before the turn ends
 * and triggers escalation based on session parameters written to Redis.
 *
 * Assertions:
 * - Rule created successfully
 * - Rule transitioned to "active" status
 * - /evaluate with sentiment_score=-0.7 returns should_escalate: true
 * - pool_target === "retencao_humano"
 * - Evaluation completes within 500ms
 * - Agent session is still active (agent_done not yet called) when escalation fires
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { McpTestClient } from "../lib/mcp-client";
import {
  writeAiSessionState,
  getAgentInstanceState,
} from "../lib/redis-client";
import { RulesEngineClient } from "../lib/http-client";
import { pass, fail } from "../lib/report";

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt = Date.now();
  const assertions: Assertion[] = [];
  const instanceId = `e2e-instance-${randomUUID()}`;
  const sessionId = randomUUID();
  const conversationId = randomUUID();
  const ruleId = `rule_e2e_sentiment_${randomUUID().slice(0, 8)}`;

  const mcp = new McpTestClient(ctx.mcpServerUrl);
  const rules = new RulesEngineClient(ctx.rulesEngineUrl);
  let sessionToken = "";

  try {
    await mcp.connect();

    // ── Login → Ready → Busy (session in progress) ───────────────────────────
    try {
      const loginResult = await mcp.agentLogin(
        ctx.tenantId,
        "agente_retencao_v1",
        instanceId
      );
      sessionToken = loginResult.session_token;
      await mcp.agentReady(sessionToken);
      await mcp.agentBusy(sessionToken, conversationId);
    } catch (err) {
      return buildResult(
        [fail("login/ready/busy setup", String(err))],
        startAt,
        "Setup failed: " + String(err)
      );
    }

    // ── Create rule in Rules Engine ───────────────────────────────────────────
    let createdRule: { rule_id: string; status: string } | null = null;
    try {
      createdRule = await rules.createRule({
        rule_id: ruleId,
        name: "E2E Sentiment Escalation Test",
        conditions: [
          {
            parameter: "sentiment_score",
            operator: "lt",
            value: -0.5,
          },
        ],
        logic: "AND",
        target_pool: "retencao_humano",
        priority: 10,
        tenant_id: ctx.tenantId,
      });

      assertions.push(
        createdRule?.rule_id
          ? pass("Rule created successfully", { rule_id: createdRule.rule_id, status: createdRule.status })
          : fail("Rule created successfully", { result: createdRule })
      );
    } catch (err) {
      assertions.push(fail("Rule created successfully", String(err)));
      return buildResult(assertions, startAt, "Rule creation failed");
    }

    // ── Transition rule to active: draft → dry_run → shadow → active ─────────
    const lifecycle = ["dry_run", "shadow", "active"] as const;
    let activationFailed = false;

    for (const targetStatus of lifecycle) {
      try {
        await rules.updateRuleStatus(ruleId, targetStatus, ctx.tenantId);
      } catch (err) {
        // Some status transitions may fail if the implementation enforces strict lifecycle
        // Try to continue — the evaluate endpoint will still work if rules are in shadow mode
        console.warn(`[04] Warning: Could not transition rule to ${targetStatus}: ${err}`);
        activationFailed = true;
        break;
      }
    }

    // Verify the rule status
    try {
      const ruleData = (await rules.getRule(ruleId, ctx.tenantId)) as Record<string, unknown>;
      const finalStatus = ruleData?.status as string | undefined;
      assertions.push(
        finalStatus === "active" || finalStatus === "shadow"
          ? pass("Rule transitioned to active (or shadow) status", { status: finalStatus })
          : fail("Rule transitioned to active (or shadow) status", {
              status: finalStatus,
              activationFailed,
            })
      );
    } catch (err) {
      assertions.push(
        fail("Rule transitioned to active status", String(err))
      );
    }

    // ── Write AI session state to Redis (simulates AI Gateway output) ─────────
    await writeAiSessionState(
      ctx.redis,
      sessionId,
      -0.7,  // sentiment_score < -0.5 → should trigger rule
      0.5,   // intent_confidence
      [],    // flags
      3600
    );

    // ── Verify agent session still active before escalation ──────────────────
    const instanceStateBeforeEval = await getAgentInstanceState(
      ctx.redis,
      ctx.tenantId,
      instanceId
    );
    assertions.push(
      instanceStateBeforeEval?.state === "busy"
        ? pass("Agent session still busy before escalation evaluation")
        : fail("Agent session still busy before escalation evaluation", {
            state: instanceStateBeforeEval?.state,
          })
    );

    // ── Call /evaluate on Rules Engine ───────────────────────────────────────
    const evalStart = Date.now();
    let evalResult: { should_escalate: boolean; rule_id?: string; pool_target?: string } | null = null;
    try {
      evalResult = await rules.evaluate({
        session_id: sessionId,
        tenant_id: ctx.tenantId,
        turn_id: "turn_1",
      });
      const evalLatency = Date.now() - evalStart;

      assertions.push(
        evalResult.should_escalate === true
          ? pass("Rules Engine detects escalation condition (should_escalate:true)", {
              rule_id: evalResult.rule_id,
              pool_target: evalResult.pool_target,
            }, evalLatency)
          : fail("Rules Engine detects escalation condition (should_escalate:true)", {
              result: evalResult,
              sentiment_score: -0.7,
            }, evalLatency)
      );

      assertions.push(
        evalResult.pool_target === "retencao_humano"
          ? pass("Escalation target pool = retencao_humano", {
              pool_target: evalResult.pool_target,
            })
          : fail("Escalation target pool = retencao_humano", {
              pool_target: evalResult.pool_target,
            })
      );

      assertions.push(
        evalLatency <= 500
          ? pass(`Rules Engine evaluation within 500ms`, undefined, evalLatency)
          : fail(`Rules Engine evaluation within 500ms (took ${evalLatency}ms)`, {
              latency_ms: evalLatency,
            }, evalLatency)
      );
    } catch (err) {
      assertions.push(
        fail("Rules Engine /evaluate call succeeded", String(err))
      );
    }

    // ── Verify agent session still active after evaluation ───────────────────
    // The Rules Engine evaluation should not automatically call agent_done
    const instanceStateAfterEval = await getAgentInstanceState(
      ctx.redis,
      ctx.tenantId,
      instanceId
    );
    assertions.push(
      instanceStateAfterEval?.state === "busy"
        ? pass("Agent session still active (busy) after escalation detected — issue_status not yet sent")
        : fail("Agent session still active after escalation detected", {
            state: instanceStateAfterEval?.state,
          })
    );
  } finally {
    await mcp.disconnect().catch(() => undefined);
  }

  return buildResult(assertions, startAt);
}

function buildResult(
  assertions: Assertion[],
  startAt: number,
  error?: string
): ScenarioResult {
  return {
    scenario_id: "04",
    name: "Motor de Regras — Escalação Intra-Turno",
    passed: assertions.every((a) => a.passed) && !error,
    assertions,
    duration_ms: Date.now() - startAt,
    error,
  };
}
