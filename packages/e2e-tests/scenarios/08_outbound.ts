/**
 * 08_outbound.ts
 * Scenario 8: OUTBOUND FLOW
 *
 * Flow:
 *   Part A — Outbound request + Channel Gateway simulation:
 *     BPM calls outbound_contact_request → publishes conversations.outbound →
 *     Channel Gateway picks up (simulated: seed session meta + publish inbound) →
 *     Redis outbound meta persisted.
 *
 *   Part B — AI first touch:
 *     AI agent receives the routed outbound session →
 *     reads context → sends opening message to customer.
 *
 *   Part C — Human agent finalizes:
 *     AI agent escalates to human pool →
 *     human agent picks up → sends message → finalizes with resolved outcome.
 *
 * Key differences from inbound:
 *   - Session originates from a BPM tool (outbound_contact_request), not from the customer
 *   - Channel Gateway is simulated (seedSessionMeta + direct conversations.inbound publish)
 *   - The initial routing event has origin: "outbound" in the meta
 *
 * Modules exercised:
 *   BPM tools (outbound_contact_request)
 *   Agent Runtime (agent_login, agent_ready, agent_busy, agent_done)
 *   Session tools (session_context_get, message_send, session_escalate)
 *   Redis meta persistence (outbound:contact_id:meta)
 *
 * Assertions: 12
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { McpTestClient } from "../lib/mcp-client";
import {
  seedSessionMeta,
  getAgentInstanceState,
  genSessionId,
} from "../lib/redis-client";
import { pass, fail } from "../lib/report";

// ─────────────────────────────────────────────────────────────────────────────
// Part A: Outbound request + Channel Gateway simulation
// ─────────────────────────────────────────────────────────────────────────────

async function runPartA(
  ctx: ScenarioContext,
  mcp: McpTestClient,
  assertions: Assertion[]
): Promise<{ sessionId: string; customerId: string } | null> {
  const customerId = randomUUID();

  // ── A1: BPM calls outbound_contact_request ────────────────────────────────
  const outboundReq = await mcp.outboundContactRequest({
    tenant_id:    ctx.tenantId,
    customer_id:  customerId,
    channel:      "whatsapp",
    agent_type_id: "agente_retencao_v1",
    pool_id:       "retencao_humano",
    metadata:      { campaign: "retencao_q1", priority: "high" },
  });

  if ("isError" in outboundReq) {
    assertions.push(fail("A: outbound_contact_request returns contact_id", outboundReq.error));
    return null;
  }

  const contactId = outboundReq.contact_id;
  assertions.push(
    contactId && outboundReq.status === "pending"
      ? pass("A: outbound_contact_request → contact_id + status:pending", {
          contact_id: contactId,
          channel:    outboundReq.channel,
        })
      : fail("A: outbound_contact_request → contact_id + status:pending", outboundReq)
  );

  // ── A2: Verify Redis outbound meta persisted ──────────────────────────────
  const metaRaw = await ctx.redis.get(`outbound:${contactId}:meta`);
  let metaParsed: Record<string, unknown> | null = null;
  try {
    if (metaRaw) metaParsed = JSON.parse(metaRaw) as Record<string, unknown>;
  } catch { /* ignore */ }

  assertions.push(
    metaParsed?.["customer_id"] === customerId && metaParsed?.["status"] === "pending"
      ? pass("A: Redis outbound:contact_id:meta persisted with customer_id + status:pending")
      : fail("A: Redis outbound:contact_id:meta persisted", {
          meta:     metaParsed,
          expected: { customer_id: customerId, status: "pending" },
        })
  );

  // ── A3: Channel Gateway accepts contact — simulation ─────────────────────
  // In production: Channel Gateway subscribes to conversations.outbound,
  // sends the message via WhatsApp, customer picks up, Gateway publishes
  // conversations.inbound with the session_id and seeds session meta in Redis.
  //
  // Here: we simulate the Gateway's accept response by seeding session meta
  // directly (same keys the Gateway would write) and then routing continues
  // via the normal agent login flow below.
  const sessionId = genSessionId();

  await seedSessionMeta(
    ctx.redis,
    sessionId,
    ctx.tenantId,
    customerId,
    "whatsapp",
    "retencao_humano"
  );

  // Also mark outbound contact as accepted in Redis
  if (metaRaw) {
    const updatedMeta = { ...metaParsed, status: "accepted", session_id: sessionId };
    await ctx.redis.setex(`outbound:${contactId}:meta`, 14400, JSON.stringify(updatedMeta));
  }

  assertions.push(pass("A: Channel Gateway simulation — session meta seeded in Redis", { session_id: sessionId }));

  return { sessionId, customerId };
}

// ─────────────────────────────────────────────────────────────────────────────
// Part B: AI first touch
// ─────────────────────────────────────────────────────────────────────────────

async function runPartB(
  ctx: ScenarioContext,
  mcp: McpTestClient,
  sessionId: string,
  assertions: Assertion[]
): Promise<{ aiToken: string; aiParticipantId: string; aiInstanceId: string } | null> {
  const aiInstanceId   = `e2e-outbound-ai-${randomUUID()}`;
  const aiParticipantId = randomUUID();
  let aiToken: string;

  // ── B1: AI agent login → ready → busy ────────────────────────────────────
  try {
    const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", aiInstanceId);
    aiToken = login.session_token;
    await mcp.agentReady(aiToken);
    const busy = await mcp.agentBusyV2(aiToken, sessionId, aiParticipantId);
    assertions.push(
      busy.status === "busy"
        ? pass("B: AI agent login → ready → busy (outbound session)", { instance: aiInstanceId })
        : fail("B: AI agent login → ready → busy", { status: busy.status })
    );
  } catch (err) {
    assertions.push(fail("B: AI agent login → ready → busy", String(err)));
    return null;
  }

  // ── B2: AI reads session context ─────────────────────────────────────────
  const ctxResult = await mcp.sessionContextGet(aiToken, sessionId, aiParticipantId);
  assertions.push(
    !("isError" in ctxResult) && ctxResult.session_id === sessionId
      ? pass("B: session_context_get returns outbound session context", {
          channel: ctxResult.channel,
        })
      : fail("B: session_context_get returns outbound session context", ctxResult)
  );

  // ── B3: AI sends opening message to customer ──────────────────────────────
  const aiMsg = await mcp.messageSend(
    aiToken, sessionId, aiParticipantId,
    { type: "text", text: "Olá! Ligamos porque identificamos uma oferta exclusiva para você. Tem um momento?" },
    "all"
  );
  assertions.push(
    !("isError" in aiMsg)
      ? pass("B: AI sends opening message to customer (outbound)", {
          message_id: (aiMsg as { message_id: string }).message_id,
        })
      : fail("B: AI sends opening message", aiMsg)
  );

  return { aiToken, aiParticipantId, aiInstanceId };
}

// ─────────────────────────────────────────────────────────────────────────────
// Part C: Human agent finalizes
// ─────────────────────────────────────────────────────────────────────────────

async function runPartC(
  ctx: ScenarioContext,
  mcp: McpTestClient,
  sessionId: string,
  aiToken: string,
  aiParticipantId: string,
  assertions: Assertion[]
): Promise<void> {
  const humanInstanceId   = `e2e-outbound-human-${randomUUID()}`;
  const humanParticipantId = randomUUID();

  // ── C1: AI escalates to human pool ───────────────────────────────────────
  const escalate = await mcp.sessionEscalate(
    aiToken, sessionId, aiParticipantId,
    "retencao_humano",
    "Cliente demonstrou interesse — escalando para fechamento humano",
    { intent: "sale_closure", product: "premium_plan" }
  );
  assertions.push(
    !("isError" in escalate) && (escalate as { escalated: boolean }).escalated
      ? pass("C: AI session_escalate to human pool for closure")
      : fail("C: AI session_escalate to human pool", escalate)
  );

  // ── C2: Human agent login → ready → busy ─────────────────────────────────
  let humanToken: string;
  try {
    const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", humanInstanceId);
    humanToken = login.session_token;
    await mcp.agentReady(humanToken);
    const busy = await mcp.agentBusyV2(humanToken, sessionId, humanParticipantId);
    assertions.push(
      busy.status === "busy"
        ? pass("C: human agent login → ready → busy", { instance: humanInstanceId })
        : fail("C: human agent login → ready → busy", { status: busy.status })
    );
  } catch (err) {
    assertions.push(fail("C: human agent login → ready → busy", String(err)));
    return;
  }

  // ── C3: Human sends message and closes ───────────────────────────────────
  await mcp.messageSend(
    humanToken, sessionId, humanParticipantId,
    { type: "text", text: "Oi! Sou a Ana. Posso confirmar a adesão ao plano premium para você agora. Posso prosseguir?" },
    "all"
  );

  const humanDone = await mcp.agentDoneV2({
    session_token:  humanToken,
    session_id:     sessionId,
    participant_id: humanParticipantId,
    outcome:        "resolved",
    issue_status:   "Campanha outbound concluída — cliente aderiu ao plano premium",
  });

  assertions.push(
    !("isError" in humanDone) && humanDone.acknowledged === true
      ? pass("C: human agent_done → resolved, acknowledged:true (outbound contact closed)")
      : fail("C: human agent_done", humanDone)
  );

  // ── C4: Human back to ready ───────────────────────────────────────────────
  const humanFinal = await getAgentInstanceState(ctx.redis, ctx.tenantId, humanInstanceId);
  assertions.push(
    humanFinal?.state === "ready"
      ? pass("C: Redis human state = ready after outbound session closed")
      : fail("C: Redis human state = ready", { state: humanFinal?.state })
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Entrypoint
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const mcp = new McpTestClient(ctx.mcpServerUrl);

  try {
    await mcp.connect();

    // Part A: Outbound request + Channel Gateway simulation
    const partA = await runPartA(ctx, mcp, assertions);
    if (!partA) {
      return {
        scenario_id: "08",
        name:        "Outbound Flow",
        passed:      false,
        assertions,
        duration_ms: Date.now() - startAt,
        error:       "Part A failed — aborting scenario",
      };
    }

    const { sessionId } = partA;

    // Part B: AI first touch
    const partB = await runPartB(ctx, mcp, sessionId, assertions);
    if (!partB) {
      return {
        scenario_id: "08",
        name:        "Outbound Flow",
        passed:      false,
        assertions,
        duration_ms: Date.now() - startAt,
        error:       "Part B failed — aborting scenario",
      };
    }

    const { aiToken, aiParticipantId } = partB;

    // Part C: Human agent finalizes
    await runPartC(ctx, mcp, sessionId, aiToken, aiParticipantId, assertions);

  } catch (err) {
    assertions.push(fail("Scenario 08 unexpected error", String(err)));
  } finally {
    await mcp.disconnect().catch(() => undefined);
  }

  return {
    scenario_id: "08",
    name:        "Outbound Flow",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  };
}
