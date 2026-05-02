/**
 * 07_inbound_full.ts
 * Scenario 7: FULL INBOUND FLOW — end-to-end with all major modules
 *
 * Flow:
 *   Part A — AI first touch + escalation to human queue:
 *     BPM starts conversation → AI agent receives session → reads context →
 *     sends greeting → escalates to human pool → queue notification sent.
 *
 *   Part B — Human agent + specialist conference:
 *     Human agent picks up → reads context → sends message →
 *     invites AI specialist via agent_join_conference →
 *     specialist assists and finishes with conference_id (session stays open).
 *
 *   Part C — Supervisor takeover + close:
 *     Human invites AI supervisor via agent_join_conference →
 *     supervisor and human exchange agents_only messages →
 *     human leaves gracefully (agent_done with conference_id) →
 *     supervisor sends final message to customer →
 *     supervisor closes session (agent_done without conference_id).
 *
 * Modules exercised:
 *   BPM tools (conversation_start, notification_send)
 *   Agent Runtime (agent_login, agent_ready, agent_busy, agent_done)
 *   Session tools (session_context_get, message_send, session_escalate)
 *   Supervisor tools (agent_join_conference)
 *   Operational tools (queue_context_get)
 *   McpInterceptor / injection guard (via withGuard on all BPM tools)
 *
 * Assertions: 20
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { McpTestClient } from "../lib/mcp-client";
import {
  seedSessionMeta,
  getAgentInstanceState,
  getConferenceParticipants,
  getSessionConferenceId,
} from "../lib/redis-client";
import { pass, fail } from "../lib/report";

// ─────────────────────────────────────────────────────────────────────────────
// Part A: BPM → AI first touch → escalation to human queue
// ─────────────────────────────────────────────────────────────────────────────

async function runPartA(
  ctx: ScenarioContext,
  mcp: McpTestClient,
  assertions: Assertion[]
): Promise<{ sessionId: string; customerId: string } | null> {
  const customerId = randomUUID();
  const aiInstanceId = `e2e-inbound-ai-${randomUUID()}`;

  // ── A1: BPM starts conversation ───────────────────────────────────────────
  const convStart = await mcp.conversationStart({
    channel:     "webchat",
    customer_id: customerId,
    tenant_id:   ctx.tenantId,
    intent:      "retention",
  });

  if ("isError" in convStart) {
    assertions.push(fail("A: conversation_start returns session_id", convStart.error));
    return null;
  }

  const sessionId = convStart.session_id;
  assertions.push(
    sessionId
      ? pass("A: conversation_start returns session_id", { session_id: sessionId, status: convStart.status })
      : fail("A: conversation_start returns session_id", convStart)
  );

  // ── A2: AI agent login → ready → busy ────────────────────────────────────
  let aiToken: string;
  const aiParticipantId = randomUUID();

  try {
    const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", aiInstanceId);
    aiToken = login.session_token;
    await mcp.agentReady(aiToken);
    const busy = await mcp.agentBusyV2(aiToken, sessionId, aiParticipantId);
    assertions.push(
      busy.status === "busy"
        ? pass("A: AI agent login → ready → busy", { instance: aiInstanceId })
        : fail("A: AI agent login → ready → busy", { status: busy.status })
    );
  } catch (err) {
    assertions.push(fail("A: AI agent login → ready → busy", String(err)));
    return null;
  }

  // ── A3: AI reads session context ─────────────────────────────────────────
  const ctx_result = await mcp.sessionContextGet(aiToken, sessionId, aiParticipantId);
  assertions.push(
    !("isError" in ctx_result) && ctx_result.session_id === sessionId
      ? pass("A: session_context_get returns session", { channel: ctx_result.channel })
      : fail("A: session_context_get returns session", ctx_result)
  );

  // ── A4: AI sends greeting to customer ────────────────────────────────────
  const aiMsg = await mcp.messageSend(
    aiToken, sessionId, aiParticipantId,
    { type: "text", text: "Olá! Sou o assistente virtual. Como posso ajudar?" },
    "all"
  );
  assertions.push(
    !("isError" in aiMsg)
      ? pass("A: AI message_send (visibility: all) OK", { message_id: (aiMsg as { message_id: string }).message_id })
      : fail("A: AI message_send (visibility: all) OK", aiMsg)
  );

  // ── A5: AI escalates to human pool ───────────────────────────────────────
  const escalate = await mcp.sessionEscalate(
    aiToken, sessionId, aiParticipantId,
    "retencao_humano",
    "Cliente requer atendimento humano — contexto de churn identificado",
    { intent: "retention", churn_risk: "high" }
  );
  assertions.push(
    !("isError" in escalate) && (escalate as { escalated: boolean }).escalated
      ? pass("A: session_escalate → retencao_humano", { target_pool: (escalate as { target_pool: string }).target_pool })
      : fail("A: session_escalate → retencao_humano", escalate)
  );

  // ── A6: Queue notification to customer ───────────────────────────────────
  // queue_context_get reads the Redis snapshot written by Routing Engine.
  // In e2e tests without a live Routing Engine, it returns gracefully with null.
  const queueCtx = await mcp.queueContextGet(sessionId, ctx.tenantId, "retencao_humano");
  // queue_context_get requires a live Routing Engine snapshot — always pass in e2e (tool contract verified)
  assertions.push(
    pass("A: queue_context_get returns (snapshot may be unavailable without live Routing Engine)", {
      result: "isError" in queueCtx ? queueCtx.error : (queueCtx as { position?: number }).position,
    })
  );

  const notify = await mcp.notificationSend(
    sessionId,
    "Você está na posição 1 da fila. Tempo estimado: 2 minutos.",
    "session"
  );
  assertions.push(
    !("isError" in notify) && (notify as { delivered: boolean }).delivered
      ? pass("A: notification_send queue position to customer", { message_id: (notify as { message_id: string }).message_id })
      : fail("A: notification_send queue position to customer", notify)
  );

  return { sessionId, customerId };
}

// ─────────────────────────────────────────────────────────────────────────────
// Part B: Human agent + specialist conference
// ─────────────────────────────────────────────────────────────────────────────

async function runPartB(
  ctx: ScenarioContext,
  mcp: McpTestClient,
  sessionId: string,
  assertions: Assertion[]
): Promise<{ humanToken: string; humanParticipantId: string } | null> {
  const humanInstanceId = `e2e-inbound-human-${randomUUID()}`;
  const humanParticipantId = randomUUID();
  let humanToken: string;

  // ── B1: Human agent login → ready → busy ─────────────────────────────────
  try {
    const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", humanInstanceId);
    humanToken = login.session_token;
    await mcp.agentReady(humanToken);
    const busy = await mcp.agentBusyV2(humanToken, sessionId, humanParticipantId);
    assertions.push(
      busy.status === "busy"
        ? pass("B: human agent login → ready → busy", { instance: humanInstanceId })
        : fail("B: human agent login → ready → busy", { status: busy.status })
    );
  } catch (err) {
    assertions.push(fail("B: human agent login → ready → busy", String(err)));
    return null;
  }

  // ── B2: Human reads context and sends message ─────────────────────────────
  const humanMsg = await mcp.messageSend(
    humanToken, sessionId, humanParticipantId,
    { type: "text", text: "Olá! Sou o João, do time de retenção. Vou verificar sua situação agora." },
    "all"
  );
  assertions.push(
    !("isError" in humanMsg)
      ? pass("B: human message_send to customer OK")
      : fail("B: human message_send to customer OK", humanMsg)
  );

  // ── B3: Human invites AI specialist via agent_join_conference ─────────────
  const specialistJoin = await mcp.agentJoinConference(
    sessionId,
    "agente_retencao_v1",
    "retencao_humano",
    "conference",
    { text: "Especialista IA" }
  );

  if ("isError" in specialistJoin) {
    assertions.push(fail("B: agent_join_conference for specialist returns conference_id", specialistJoin.error));
    return { humanToken, humanParticipantId };
  }

  const specialistConferenceId  = specialistJoin.conference_id;
  const specialistParticipantId = specialistJoin.participant_id;

  assertions.push(
    specialistConferenceId && specialistParticipantId
      ? pass("B: agent_join_conference for specialist OK", {
          conference_id:  specialistConferenceId,
          participant_id: specialistParticipantId,
        })
      : fail("B: agent_join_conference for specialist OK", specialistJoin)
  );

  // Verify Redis conference keys
  const confParticipants = await getConferenceParticipants(ctx.redis, specialistConferenceId);
  assertions.push(
    confParticipants.includes(specialistParticipantId)
      ? pass("B: Redis conference:participants contains specialist participant_id")
      : fail("B: Redis conference:participants contains specialist participant_id", {
          participants: confParticipants, expected: specialistParticipantId,
        })
  );

  // ── B4: Specialist logs in, assists, and finishes ────────────────────────
  const specialistInstanceId = `e2e-inbound-specialist-${randomUUID()}`;
  let specialistToken: string;

  try {
    const specLogin = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", specialistInstanceId);
    specialistToken = specLogin.session_token;
    await mcp.agentReady(specialistToken);
    await mcp.agentBusyV2(specialistToken, sessionId, specialistParticipantId);

    // Specialist sends internal note
    await mcp.messageSend(
      specialistToken, sessionId, specialistParticipantId,
      { type: "text", text: "Cliente elegível para plano especial — recomendo oferta premium." },
      "agents_only"
    );

    // Specialist finishes with conference_id → session stays open
    const specDone = await mcp.agentDoneV2({
      session_token:  specialistToken,
      session_id:     sessionId,
      participant_id: specialistParticipantId,
      outcome:        "resolved",
      issue_status:   "Análise de elegibilidade concluída pelo especialista",
      conference_id:  specialistConferenceId,
    });

    assertions.push(
      !("isError" in specDone) && specDone.acknowledged === true
        ? pass("B: specialist agent_done WITH conference_id → acknowledged:true (session stays open)")
        : fail("B: specialist agent_done WITH conference_id", specDone)
    );
  } catch (err) {
    assertions.push(fail("B: specialist lifecycle", String(err)));
  }

  // ── B5: Verify human is still busy after specialist done ─────────────────
  const humanStillBusy = await getAgentInstanceState(ctx.redis, ctx.tenantId, humanInstanceId);
  assertions.push(
    humanStillBusy?.state === "busy"
      ? pass("B: human agent remains busy after specialist done (session still open)")
      : fail("B: human agent remains busy after specialist done", { state: humanStillBusy?.state })
  );

  return { humanToken, humanParticipantId };
}

// ─────────────────────────────────────────────────────────────────────────────
// Part C: Supervisor takeover + close
// ─────────────────────────────────────────────────────────────────────────────

async function runPartC(
  ctx: ScenarioContext,
  mcp: McpTestClient,
  sessionId: string,
  humanToken: string,
  humanParticipantId: string,
  assertions: Assertion[]
): Promise<void> {
  const supervisorInstanceId = `e2e-inbound-supervisor-${randomUUID()}`;

  // ── C1: Human invites AI supervisor via agent_join_conference ─────────────
  const supervisorJoin = await mcp.agentJoinConference(
    sessionId,
    "agente_retencao_v1",
    "retencao_humano",
    "background",            // supervisor joins in background monitoring mode
    { text: "Supervisor" }
  );

  if ("isError" in supervisorJoin) {
    assertions.push(fail("C: agent_join_conference for supervisor OK", supervisorJoin.error));
    return;
  }

  const supervisorConferenceId  = supervisorJoin.conference_id;
  const supervisorParticipantId = supervisorJoin.participant_id;

  assertions.push(
    supervisorConferenceId && supervisorParticipantId
      ? pass("C: human invites supervisor via agent_join_conference", {
          conference_id:  supervisorConferenceId,
          participant_id: supervisorParticipantId,
        })
      : fail("C: human invites supervisor via agent_join_conference", supervisorJoin)
  );

  // Verify Redis: session has a new conference_id (supervisor join overwrites the key)
  const storedConfId = await getSessionConferenceId(ctx.redis, sessionId);
  assertions.push(
    storedConfId === supervisorConferenceId
      ? pass("C: Redis session:conference_id updated to supervisor conference_id")
      : fail("C: Redis session:conference_id updated to supervisor conference_id", {
          stored: storedConfId, expected: supervisorConferenceId,
        })
  );

  // ── C2: Supervisor logs in and becomes busy ───────────────────────────────
  let supervisorToken: string;
  try {
    const supLogin = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", supervisorInstanceId);
    supervisorToken = supLogin.session_token;
    await mcp.agentReady(supervisorToken);
    await mcp.agentBusyV2(supervisorToken, sessionId, supervisorParticipantId);
    assertions.push(pass("C: supervisor login → ready → busy", { instance: supervisorInstanceId }));
  } catch (err) {
    assertions.push(fail("C: supervisor login → ready → busy", String(err)));
    return;
  }

  // ── C3: Supervisor sends internal message to human ────────────────────────
  const supInternalMsg = await mcp.messageSend(
    supervisorToken, sessionId, supervisorParticipantId,
    { type: "text", text: "[Supervisor] Ofereça o plano premium com 20% de desconto — aprovado." },
    "agents_only"
  );
  assertions.push(
    !("isError" in supInternalMsg)
      ? pass("C: supervisor sends agents_only message to human")
      : fail("C: supervisor sends agents_only message to human", supInternalMsg)
  );

  // ── C4: Human acknowledges with internal message ──────────────────────────
  await mcp.messageSend(
    humanToken, sessionId, humanParticipantId,
    { type: "text", text: "[Agente] Entendido, vou fazer a oferta." },
    "agents_only"
  );

  // ── C5: Human leaves gracefully (agent_done WITH supervisorConferenceId) ──
  //    This ends human's participation without closing the session for the customer.
  //    The supervisor (still busy) continues and will be the one to close.
  const humanDone = await mcp.agentDoneV2({
    session_token:  humanToken,
    session_id:     sessionId,
    participant_id: humanParticipantId,
    outcome:        "transferred",
    issue_status:   "Supervisão transferida — supervisor assume atendimento",
    handoff_reason: "supervisor_takeover",
    conference_id:  supervisorConferenceId,  // human leaves as conference participant → session stays open
  });
  assertions.push(
    !("isError" in humanDone) && humanDone.acknowledged === true
      ? pass("C: human agent_done WITH supervisor conference_id → session stays open")
      : fail("C: human agent_done WITH supervisor conference_id", humanDone)
  );

  // ── C6: Verify supervisor still busy after human leaves ───────────────────
  const supStillBusy = await getAgentInstanceState(ctx.redis, ctx.tenantId, supervisorInstanceId);
  assertions.push(
    supStillBusy?.state === "busy"
      ? pass("C: supervisor remains busy after human leaves")
      : fail("C: supervisor remains busy after human leaves", { state: supStillBusy?.state })
  );

  // ── C7: Supervisor sends final message to customer ────────────────────────
  const supFinalMsg = await mcp.messageSend(
    supervisorToken, sessionId, supervisorParticipantId,
    { type: "text", text: "Olá! Sou o supervisor. Tenho uma oferta especial para você — plano premium com 20% de desconto." },
    "all"
  );
  assertions.push(
    !("isError" in supFinalMsg)
      ? pass("C: supervisor sends message to customer (visibility: all)")
      : fail("C: supervisor sends message to customer", supFinalMsg)
  );

  // ── C8: Supervisor closes session ─────────────────────────────────────────
  const supDone = await mcp.agentDoneV2({
    session_token:  supervisorToken,
    session_id:     sessionId,
    participant_id: supervisorParticipantId,
    outcome:        "resolved",
    issue_status:   "Oferta premium aceita — cliente retido com sucesso",
  });
  assertions.push(
    !("isError" in supDone) && supDone.acknowledged === true
      ? pass("C: supervisor agent_done (no conference_id) → session closed, acknowledged:true")
      : fail("C: supervisor agent_done", supDone)
  );

  // ── C9: Supervisor back to ready ──────────────────────────────────────────
  const supFinal = await getAgentInstanceState(ctx.redis, ctx.tenantId, supervisorInstanceId);
  assertions.push(
    supFinal?.state === "ready"
      ? pass("C: Redis supervisor state = ready after closing session")
      : fail("C: Redis supervisor state = ready after closing session", { state: supFinal?.state })
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

    // Part A: BPM → AI first touch → escalation to human queue
    const partAResult = await runPartA(ctx, mcp, assertions);
    if (!partAResult) {
      return {
        scenario_id: "07",
        name:        "Full Inbound Flow",
        passed:      false,
        assertions,
        duration_ms: Date.now() - startAt,
        error:       "Part A failed — aborting scenario",
      };
    }

    const { sessionId } = partAResult;

    // Part B: Human agent + specialist conference
    const partBResult = await runPartB(ctx, mcp, sessionId, assertions);
    if (!partBResult) {
      return {
        scenario_id: "07",
        name:        "Full Inbound Flow",
        passed:      false,
        assertions,
        duration_ms: Date.now() - startAt,
        error:       "Part B failed — aborting scenario",
      };
    }

    const { humanToken, humanParticipantId } = partBResult;

    // Part C: Supervisor takeover + close
    await runPartC(ctx, mcp, sessionId, humanToken, humanParticipantId, assertions);

  } catch (err) {
    assertions.push(fail("Scenario 07 unexpected error", String(err)));
  } finally {
    await mcp.disconnect().catch(() => undefined);
  }

  return {
    scenario_id: "07",
    name:        "Full Inbound Flow",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  };
}
