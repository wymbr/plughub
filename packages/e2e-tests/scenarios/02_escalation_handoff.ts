/**
 * 02_escalation_handoff.ts
 * Scenario 2: ESCALAÇÃO COM HANDOFF
 *
 * Validates that agent_done with outcome !== "resolved" REJECTS
 * when handoff_reason is absent, and ACCEPTS when present.
 *
 * Assertions:
 * - First agent_done (without handoff_reason) is rejected (isError: true)
 * - Error message references handoff_reason
 * - Second agent_done (with handoff_reason) is accepted (acknowledged: true)
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { McpTestClient } from "../lib/mcp-client";
import { pass, fail } from "../lib/report";

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt = Date.now();
  const assertions: Assertion[] = [];
  const instanceId = `e2e-instance-${randomUUID()}`;
  const conversationId = randomUUID();

  const mcp = new McpTestClient(ctx.mcpServerUrl);
  let sessionToken = "";

  try {
    await mcp.connect();

    // ── Login + Ready ────────────────────────────────────────────────────────
    try {
      const loginResult = await mcp.agentLogin(
        ctx.tenantId,
        "agente_retencao_v1",
        instanceId
      );
      sessionToken = loginResult.session_token;
      await mcp.agentReady(sessionToken);
    } catch (err) {
      return buildResult(
        [fail("login and ready setup", String(err))],
        startAt,
        "Setup failed: " + String(err)
      );
    }

    // ── Simulate routing (agent_busy) ────────────────────────────────────────
    try {
      await mcp.agentBusy(sessionToken, conversationId);
    } catch (err) {
      return buildResult(
        [fail("agent_busy setup", String(err))],
        startAt,
        "Setup failed: " + String(err)
      );
    }

    // ── Test 1: agent_done WITHOUT handoff_reason ─────────────────────────────
    // outcome = "escalated_human" — handoff_reason is REQUIRED
    const doneWithoutHandoff = await mcp.agentDone({
      session_token: sessionToken,
      conversation_id: conversationId,
      outcome: "escalated_human",
      issue_status: [
        {
          issue_id: "issue-2",
          description: "Retenção não concluída",
          status: "unresolved",
        },
      ],
      // handoff_reason intentionally omitted
    });

    const isRejected =
      "isError" in doneWithoutHandoff && doneWithoutHandoff.isError === true;
    assertions.push(
      isRejected
        ? pass("agent_done WITHOUT handoff_reason is rejected (isError:true)")
        : fail("agent_done WITHOUT handoff_reason is rejected (isError:true)", {
            result: doneWithoutHandoff,
          })
    );

    // Error message should reference handoff_reason
    if (isRejected) {
      const errorMsg =
        "error" in doneWithoutHandoff ? doneWithoutHandoff.error : "";
      const mentionsHandoff =
        errorMsg.toLowerCase().includes("handoff_reason") ||
        errorMsg.toLowerCase().includes("handoff");
      assertions.push(
        mentionsHandoff
          ? pass("rejection error message references handoff_reason", {
              error: errorMsg,
            })
          : fail(
              "rejection error message references handoff_reason",
              { error: errorMsg }
            )
      );
    } else {
      assertions.push(
        fail("rejection error message references handoff_reason (skipped — not rejected)")
      );
    }

    // ── Re-busy for the second attempt ───────────────────────────────────────
    // If the first agent_done was rejected, the agent is still busy with the conversation.
    // We can proceed directly to the second agent_done attempt.

    // ── Test 2: agent_done WITH handoff_reason ───────────────────────────────
    const doneWithHandoff = await mcp.agentDone({
      session_token: sessionToken,
      conversation_id: conversationId,
      outcome: "escalated_human",
      issue_status: [
        {
          issue_id: "issue-2",
          description: "Retenção não concluída — aguardando humano",
          status: "transferred",
        },
      ],
      handoff_reason: "Cliente solicitou falar com humano",
      resolution_summary: "Transferindo para atendimento humano",
    });

    const isAccepted =
      !("isError" in doneWithHandoff) ||
      !doneWithHandoff.isError;
    const acknowledged =
      isAccepted &&
      "acknowledged" in doneWithHandoff &&
      doneWithHandoff.acknowledged === true;

    assertions.push(
      acknowledged
        ? pass("agent_done WITH handoff_reason is accepted (acknowledged:true)")
        : fail("agent_done WITH handoff_reason is accepted (acknowledged:true)", {
            result: doneWithHandoff,
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
    scenario_id: "02",
    name: "Escalação com Handoff",
    passed: assertions.every((a) => a.passed) && !error,
    assertions,
    duration_ms: Date.now() - startAt,
    error,
  };
}
