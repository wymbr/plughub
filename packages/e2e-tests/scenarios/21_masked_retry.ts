/**
 * 21_masked_retry.ts
 * Scenario 21: MASKED RETRY — begin_transaction ROLLBACK CYCLE
 *
 * Validates that the begin_transaction / end_transaction retry cycle correctly
 * clears the maskedScope between attempts, preventing any leakage of a failed
 * attempt's sensitive values into subsequent attempts or the final pipeline_state.
 *
 * Flow:
 *   begin_transaction (tx_inicio)
 *     → coletar_pin   (menu, masked:true, interaction: text)
 *     → validar_pin   (invoke mcp-server-auth/validate_pin)
 *     → verificar_pin (choice: validacao_pin.valid eq true → tx_fim | default → tx_inicio)
 *       default → tx_inicio  ← KEY: rewinds to begin_transaction, clearing maskedScope
 *                              choice returns "default" (not "on_failure"), so the engine
 *                              does NOT intercept it as a transaction failure — begin_transaction
 *                              re-runs normally and clears maskedScope itself.
 *     → tx_fim        (end_transaction, result_as: auth_status)
 *   → concluir (complete, outcome: resolved)
 *
 * NOTE on retry mechanism:
 *   invoke.on_failure only fires on MCP transport errors (throws/HTTP failures).
 *   mcp-server-auth/validate_pin returns {valid:false} without throwing, so on_failure
 *   never triggers. A choice step is required to inspect the result and route back to
 *   tx_inicio when validation fails.
 *
 * Sequence:
 *   1st attempt: inject "000000" → validate_pin returns {valid:false} → verificar_pin
 *                routes to tx_inicio via default path → begin_transaction re-runs,
 *                clearing maskedScope
 *   2nd attempt: inject "123456" → validate_pin returns {valid:true} → verificar_pin
 *                routes to tx_fim → end_transaction → resolved
 *
 * Asserts:
 *   C1: outcome=resolved (flow completed after 2nd attempt)
 *   C2: "000000" (failed attempt PIN) absent from full pipeline_state JSON
 *   C3: "123456" (successful attempt PIN) absent from full pipeline_state JSON
 *   C4: validacao_pin.valid=true (successful attempt result persisted)
 *   C5: auth_status.status=ok (end_transaction completed cleanly)
 *
 * Prerequisites: same demo stack as scenario 19 (mcp-server-auth on port 3150)
 *
 * Run:
 *   ts-node runner.ts --masked         — adds scenarios 20 + 21
 *   ts-node runner.ts --only 21        — run only scenario 21
 *
 * Assertions: 5
 * Timeout:    60s
 */

import { randomUUID }     from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { SkillFlowClient } from "../lib/http-client";
import { seedSessionMeta } from "../lib/redis-client";
import { pass, fail }      from "../lib/report";

// ─────────────────────────────────────────────────────────────────────────────
// Retry flow — identical to agente_auth_ia_v1 structure (inline, no LLM)
// mcp-server-auth/validate_pin: valid if pin starts with "1", invalid otherwise
// ─────────────────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const RETRY_FLOW: Record<string, any> = {
  entry: "tx_inicio",
  steps: [
    {
      id:         "tx_inicio",
      type:       "begin_transaction",
      on_failure: "falha",
    },
    {
      id:          "coletar_pin",
      type:        "menu",
      interaction: "text",
      prompt:      "Informe seu PIN:",
      timeout_s:   120,
      masked:      true,
      output_as:   "pin_input",
      on_success:  "validar_pin",
      on_failure:  "tx_inicio",
    },
    {
      id:        "validar_pin",
      type:      "invoke",
      target:    { mcp_server: "mcp-server-auth", tool: "validate_pin" },
      input:     { customer_id: "e2e-retry", pin: "@masked.pin_input" },
      output_as: "validacao_pin",
      on_success: "verificar_pin",  // always goes to choice — mcp-server-auth never throws for {valid:false}
      on_failure: "tx_inicio",      // only for MCP transport errors
    },
    {
      id:   "verificar_pin",
      type: "choice",
      conditions: [
        {
          // evalContext.pipeline_state === ctx.state.results (no nested "results" key)
          field:    "$.pipeline_state.validacao_pin.valid",
          operator: "eq",
          value:    true,
          next:     "tx_fim",
        },
      ],
      default: "tx_inicio",  // invalid PIN → rewind to begin_transaction, clears maskedScope
    },
    {
      id:         "tx_fim",
      type:       "end_transaction",
      result_as:  "auth_status",
      on_success: "concluir",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "falha",    type: "complete", outcome: "escalated_human" },
  ],
};

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// ─────────────────────────────────────────────────────────────────────────────
// Scenario: inject "000000" (fails), then "123456" (succeeds)
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const sessionId  = `sess_${Date.now()}T000000_RETRYM${randomUUID().replace(/-/g,"").slice(0,8).toUpperCase()}`;
  const customerId = randomUUID();
  const client     = new SkillFlowClient(ctx.skillFlowUrl);

  try {
    await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId, "webchat", "auth_ia");

    const executePromise = client.execute({
      tenant_id:       ctx.tenantId,
      session_id:      sessionId,
      customer_id:     customerId,
      skill_id:        "skill_masked_retry_v1",
      flow:            RETRY_FLOW,
      session_context: {},
    });

    // Wait for flow to reach first coletar_pin BLPOP
    // (begin_transaction is instant, menu step blocks on BLPOP)
    await sleep(1000);

    // ── 1st attempt: inject invalid PIN (starts with "0" → validate_pin returns {valid:false})
    // mcp-server-auth/validate_pin returns {valid:false} for PINs not starting with "1" (no throw)
    // verificar_pin (choice) routes to tx_inicio via default path → begin_transaction re-runs,
    // clearing maskedScope → coletar_pin BLPOP again
    await ctx.redis.lpush(`menu:result:${sessionId}`, "000000");

    // Wait for: invoke(validate_pin) + choice(verificar_pin) + begin_transaction(rewind) + menu(BLPOP)
    await sleep(3000);

    // ── 2nd attempt: inject valid PIN (starts with "1" → validate_pin returns {valid:true})
    await ctx.redis.lpush(`menu:result:${sessionId}`, "123456");

    let result: Awaited<typeof executePromise>;
    try {
      result = await executePromise;
    } catch (err) {
      assertions.push(fail("retry flow /execute completes without HTTP error", String(err)));
      return {
        scenario_id: "21",
        name:        "Masked Retry — begin_transaction rollback cycle",
        passed:      false,
        assertions,
        duration_ms: Date.now() - startAt,
      };
    }

    // C1: outcome=resolved (2nd attempt succeeded)
    assertions.push(
      "outcome" in result && result.outcome === "resolved"
        ? pass("C: retry cycle — outcome=resolved (2nd attempt with valid PIN)")
        : fail("C: retry cycle — outcome=resolved", { outcome: (result as any).outcome })
    );

    if (!("pipeline_state" in result)) {
      assertions.push(fail("C: pipeline_state present in result", { result }));
      return {
        scenario_id: "21",
        name:        "Masked Retry — begin_transaction rollback cycle",
        passed:      assertions.every((a) => a.passed),
        assertions,
        duration_ms: Date.now() - startAt,
      };
    }

    const ps     = result.pipeline_state as Record<string, unknown>;
    const psJson = JSON.stringify(ps);

    // C2: failed attempt PIN "000000" absent from pipeline_state
    assertions.push(
      !psJson.includes("000000")
        ? pass("C: failed attempt PIN '000000' absent from pipeline_state (maskedScope cleared on rewind)")
        : fail("C: failed attempt PIN '000000' leaked into pipeline_state!", {
            found_in: Object.keys((ps["results"] ?? {}) as Record<string, unknown>)
              .filter(k => JSON.stringify(((ps["results"] ?? {}) as Record<string, unknown>)[k]).includes("000000")),
          })
    );

    // C3: successful attempt PIN "123456" absent from pipeline_state
    assertions.push(
      !psJson.includes("123456")
        ? pass("C: successful attempt PIN '123456' absent from pipeline_state (masked, not written to output)")
        : fail("C: successful attempt PIN '123456' leaked into pipeline_state!", {
            found_in: Object.keys((ps["results"] ?? {}) as Record<string, unknown>)
              .filter(k => JSON.stringify(((ps["results"] ?? {}) as Record<string, unknown>)[k]).includes("123456")),
          })
    );

    const psResults = (ps["results"] ?? {}) as Record<string, unknown>;

    // C4: validacao_pin.valid=true (successful attempt result is in pipeline_state)
    const validacao = psResults["validacao_pin"] as Record<string, unknown> | undefined;
    assertions.push(
      validacao !== undefined && validacao["valid"] === true
        ? pass("C: validacao_pin.valid=true — 2nd attempt PIN correctly forwarded to invoke")
        : fail("C: validacao_pin.valid=true", { validacao_pin: validacao })
    );

    // C5: auth_status.status=ok (end_transaction completed successfully)
    const authStatus = psResults["auth_status"] as Record<string, unknown> | undefined;
    assertions.push(
      authStatus?.["status"] === "ok"
        ? pass("C: auth_status.status=ok — end_transaction completed after retry")
        : fail("C: auth_status.status=ok", { auth_status: authStatus })
    );

  } catch (err) {
    assertions.push(fail("Scenario 21 unexpected error", String(err)));
  }

  return {
    scenario_id: "21",
    name:        "Masked Retry — begin_transaction rollback cycle",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  };
}
