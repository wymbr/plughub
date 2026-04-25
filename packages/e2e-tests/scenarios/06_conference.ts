/**
 * 06_conference.ts
 * Scenario 6: CONFERENCE FLOW + RECONNECT RESILIENCE
 *
 * Part A — Conference happy path:
 *   Primary agent joins session → supervisor invites specialist via agent_join_conference
 *   → specialist calls agent_busy (same session, own participant_id) → specialist calls
 *   agent_done with conference_id (session stays open) → primary calls agent_done (closes)
 *
 * Part B — Reconnect resilience:
 *   Primary agent joins session → MCP transport is torn down (simulates mcp-server restart)
 *   → new transport reconnects → agent re-logs in with SAME instance_id → Redis state
 *   (agent instance, active session) persists → agent can cleanly conclude via agent_done_v2
 *
 * Spec: PlugHub CLAUDE.md — "End-to-end conference test: pm2 restart + 3-terminal validation"
 *
 * Assertions:
 * - agent_join_conference returns conference_id + participant_id
 * - Redis: conference:{id}:participants contains specialist participant_id
 * - Redis: session:{id}:conference_id set after join
 * - agent_done with conference_id does NOT close the primary session
 * - Primary agent state = busy while specialist is active
 * - Primary agent state = ready after own agent_done
 * - After reconnect: Redis agent instance still present
 * - After reconnect: active session_id persists in agent conversations set
 * - Re-login with same instance_id succeeds and returns new session_token
 * - agent_done with new session_token acknowledges successfully
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { McpTestClient } from "../lib/mcp-client";
import {
  seedSessionMeta,
  getAgentInstanceState,
  getConferenceParticipants,
  getSessionConferenceId,
  getAgentActiveSessions,
  genSessionId,
} from "../lib/redis-client";
import { pass, fail } from "../lib/report";

// ─── Part A: Conference happy path ────────────────────────────────────────────

async function runPartA(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const primaryInstanceId  = `e2e-conference-primary-${randomUUID()}`;
  const specialistInstanceId = `e2e-conference-specialist-${randomUUID()}`;
  const sessionId          = genSessionId();
  const customerId         = randomUUID();
  const primaryParticipantId = randomUUID();

  const mcp = new McpTestClient(ctx.mcpServerUrl);

  try {
    await mcp.connect();

    // ── A1: Seed session meta (required by agent_join_conference) ─────────────
    await seedSessionMeta(
      ctx.redis,
      sessionId,
      ctx.tenantId,
      customerId,
      "webchat",
      "retencao_humano"
    );
    assertions.push(pass("A: session meta seeded in Redis"));

    // ── A2: Primary agent login → ready → busy (v2) ──────────────────────────
    let primaryToken: string;
    try {
      const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", primaryInstanceId);
      primaryToken = login.session_token;
      assertions.push(pass("A: primary agent_login OK", { instance_id: login.instance_id }));
    } catch (err) {
      assertions.push(fail("A: primary agent_login OK", String(err)));
      return;
    }

    try {
      await mcp.agentReady(primaryToken);
      assertions.push(pass("A: primary agent_ready OK"));
    } catch (err) {
      assertions.push(fail("A: primary agent_ready OK", String(err)));
      return;
    }

    try {
      const busy = await mcp.agentBusyV2(primaryToken, sessionId, primaryParticipantId);
      assertions.push(
        busy.status === "busy"
          ? pass("A: primary agent_busy v2 → status:busy", { current_sessions: busy.current_sessions })
          : fail("A: primary agent_busy v2 → status:busy", { status: busy.status })
      );
    } catch (err) {
      assertions.push(fail("A: primary agent_busy v2 → status:busy", String(err)));
      return;
    }

    // ── A3: Verify Redis state — primary is busy ──────────────────────────────
    const primaryState = await getAgentInstanceState(ctx.redis, ctx.tenantId, primaryInstanceId);
    assertions.push(
      primaryState?.state === "busy"
        ? pass("A: Redis primary state = busy")
        : fail("A: Redis primary state = busy", { state: primaryState?.state })
    );

    // ── A4: Supervisor invites specialist via agent_join_conference ───────────
    let conferenceId: string;
    let specialistParticipantId: string;

    const joinResult = await mcp.agentJoinConference(
      sessionId,
      "agente_retencao_v1",   // specialist agent type (same for test simplicity)
      "retencao_humano",
      "conference",
      { text: "Assistente IA" }
    );

    if ("isError" in joinResult) {
      assertions.push(fail("A: agent_join_conference returns conference_id", joinResult.error));
      return;
    }

    conferenceId           = joinResult.conference_id;
    specialistParticipantId = joinResult.participant_id;

    assertions.push(
      conferenceId && specialistParticipantId
        ? pass("A: agent_join_conference returns conference_id + participant_id", {
            conference_id:   conferenceId,
            participant_id:  specialistParticipantId,
          })
        : fail("A: agent_join_conference returns conference_id + participant_id", joinResult)
    );

    // ── A5: Redis conference:* keys written ───────────────────────────────────
    const conferenceParticipants = await getConferenceParticipants(ctx.redis, conferenceId);
    assertions.push(
      conferenceParticipants.includes(specialistParticipantId)
        ? pass("A: Redis conference:participants includes specialist participant_id")
        : fail("A: Redis conference:participants includes specialist participant_id", {
            conferenceParticipants,
            expected: specialistParticipantId,
          })
    );

    const storedConferenceId = await getSessionConferenceId(ctx.redis, sessionId);
    assertions.push(
      storedConferenceId === conferenceId
        ? pass("A: Redis session:conference_id matches returned conference_id")
        : fail("A: Redis session:conference_id matches returned conference_id", {
            stored: storedConferenceId,
            expected: conferenceId,
          })
    );

    // ── A6: Specialist logs in, becomes ready, takes the session (v2 API) ────
    let specialistToken: string;
    try {
      const specLogin = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", specialistInstanceId);
      specialistToken = specLogin.session_token;
      await mcp.agentReady(specialistToken);
      const specBusy = await mcp.agentBusyV2(specialistToken, sessionId, specialistParticipantId);
      assertions.push(
        specBusy.status === "busy"
          ? pass("A: specialist agent_busy v2 OK")
          : fail("A: specialist agent_busy v2 OK", { status: specBusy.status })
      );
    } catch (err) {
      assertions.push(fail("A: specialist login+ready+busy", String(err)));
      return;
    }

    // ── A7: Specialist calls agent_done WITH conference_id ────────────────────
    const specDone = await mcp.agentDoneV2({
      session_token: specialistToken,
      session_id:    sessionId,
      participant_id: specialistParticipantId,
      outcome:        "resolved",
      issue_status:   "Specialist assistance completed",
      conference_id:  conferenceId,
    });

    if ("isError" in specDone) {
      assertions.push(fail("A: specialist agent_done with conference_id OK", specDone.error));
    } else {
      assertions.push(
        specDone.acknowledged === true
          ? pass("A: specialist agent_done with conference_id → acknowledged:true")
          : fail("A: specialist agent_done with conference_id → acknowledged:true", specDone)
      );
    }

    // ── A8: Primary is still busy (session not closed by specialist done) ─────
    const primaryStillBusy = await getAgentInstanceState(ctx.redis, ctx.tenantId, primaryInstanceId);
    assertions.push(
      primaryStillBusy?.state === "busy"
        ? pass("A: primary remains busy after specialist agent_done (session not closed)")
        : fail("A: primary remains busy after specialist agent_done (session not closed)", {
            state: primaryStillBusy?.state,
          })
    );

    // ── A9: Primary calls agent_done (no conference_id) → session closes ─────
    const primaryDone = await mcp.agentDoneV2({
      session_token:  primaryToken,
      session_id:     sessionId,
      participant_id: primaryParticipantId,
      outcome:        "resolved",
      issue_status:   "Atendimento concluído com sucesso",
    });

    if ("isError" in primaryDone) {
      assertions.push(fail("A: primary agent_done → acknowledged:true", primaryDone.error));
    } else {
      assertions.push(
        primaryDone.acknowledged === true
          ? pass("A: primary agent_done → acknowledged:true")
          : fail("A: primary agent_done → acknowledged:true", primaryDone)
      );
    }

    // ── A10: Primary back to ready after agent_done ───────────────────────────
    const primaryFinal = await getAgentInstanceState(ctx.redis, ctx.tenantId, primaryInstanceId);
    assertions.push(
      primaryFinal?.state === "ready"
        ? pass("A: Redis primary state = ready after agent_done")
        : fail("A: Redis primary state = ready after agent_done", { state: primaryFinal?.state })
    );
  } finally {
    await mcp.disconnect().catch(() => undefined);
  }
}

// ─── Part B: Reconnect resilience ─────────────────────────────────────────────

async function runPartB(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const instanceId   = `e2e-reconnect-${randomUUID()}`;
  const sessionId    = genSessionId();
  const customerId   = randomUUID();
  const participantId = randomUUID();

  const mcp = new McpTestClient(ctx.mcpServerUrl);

  try {
    await mcp.connect();

    // ── B1: Agent login → ready → busy ───────────────────────────────────────
    let firstToken: string;
    try {
      const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", instanceId);
      firstToken = login.session_token;
      assertions.push(pass("B: initial agent_login OK", { instance_id: login.instance_id }));
    } catch (err) {
      assertions.push(fail("B: initial agent_login OK", String(err)));
      return;
    }

    await mcp.agentReady(firstToken);

    await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId);

    try {
      await mcp.agentBusyV2(firstToken, sessionId, participantId);
      assertions.push(pass("B: agent is busy with session"));
    } catch (err) {
      assertions.push(fail("B: agent is busy with session", String(err)));
      return;
    }

    // Verify in Redis that sessions counter = 1
    const preCrash = await getAgentInstanceState(ctx.redis, ctx.tenantId, instanceId);
    assertions.push(
      preCrash?.state === "busy" && preCrash?.current_sessions === "1"
        ? pass("B: Redis state = busy, current_sessions=1 before crash")
        : fail("B: Redis state = busy, current_sessions=1 before crash", {
            state:            preCrash?.state,
            current_sessions: preCrash?.current_sessions,
          })
    );

    // Verify session tracked in conversations set
    const preActiveSessions = await getAgentActiveSessions(ctx.redis, ctx.tenantId, instanceId);
    assertions.push(
      preActiveSessions.includes(sessionId)
        ? pass("B: session_id tracked in agent conversations set before crash")
        : fail("B: session_id tracked in agent conversations set before crash", {
            sessions:  preActiveSessions,
            sessionId,
          })
    );

    // ── B2: Simulate mcp-server restart — tear down and reconnect transport ──
    await mcp.reconnect();
    assertions.push(pass("B: MCP transport reconnected (simulates server restart)"));

    // ── B3: Re-login with same instance_id → Redis state persists ────────────
    let newToken: string;
    try {
      const reLogin = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", instanceId);
      newToken = reLogin.session_token;
      assertions.push(
        reLogin.instance_id === instanceId
          ? pass("B: re-login with same instance_id succeeds", { instance_id: reLogin.instance_id })
          : fail("B: re-login with same instance_id succeeds", {
              expected: instanceId,
              got:      reLogin.instance_id,
            })
      );
    } catch (err) {
      assertions.push(fail("B: re-login with same instance_id succeeds", String(err)));
      return;
    }

    // ── B4: Agent instance still exists in Redis ──────────────────────────────
    const postRelogin = await getAgentInstanceState(ctx.redis, ctx.tenantId, instanceId);
    assertions.push(
      postRelogin !== null
        ? pass("B: Redis agent instance persists after reconnect", { state: postRelogin.state })
        : fail("B: Redis agent instance persists after reconnect")
    );

    // ── B5: Session_id still in conversations set (Redis persisted through restart) ──
    const postActiveSessions = await getAgentActiveSessions(ctx.redis, ctx.tenantId, instanceId);
    assertions.push(
      postActiveSessions.includes(sessionId)
        ? pass("B: session_id persists in conversations set after reconnect")
        : fail("B: session_id persists in conversations set after reconnect", {
            sessions:  postActiveSessions,
            sessionId,
          })
    );

    // ── B6: Agent becomes ready again ────────────────────────────────────────
    try {
      await mcp.agentReady(newToken);
      const readyState = await getAgentInstanceState(ctx.redis, ctx.tenantId, instanceId);
      assertions.push(
        readyState?.state === "ready"
          ? pass("B: agent is ready after reconnect + agent_ready")
          : fail("B: agent is ready after reconnect + agent_ready", { state: readyState?.state })
      );
    } catch (err) {
      assertions.push(fail("B: agent is ready after reconnect + agent_ready", String(err)));
    }

    // ── B7: Agent concludes the in-progress session using new token ───────────
    // The agent still knows its session_id + participant_id (context from Routing Engine)
    const cleanup = await mcp.agentDoneV2({
      session_token:  newToken,
      session_id:     sessionId,
      participant_id: participantId,
      outcome:        "resolved",
      issue_status:   "Sessão concluída após reconexão",
    });

    if ("isError" in cleanup) {
      assertions.push(
        fail("B: agent_done with new token closes in-progress session", cleanup.error)
      );
    } else {
      assertions.push(
        cleanup.acknowledged === true
          ? pass("B: agent_done with new token closes in-progress session → acknowledged:true")
          : fail("B: agent_done with new token closes in-progress session", cleanup)
      );
    }

    // ── B8: Final state = ready ───────────────────────────────────────────────
    const finalState = await getAgentInstanceState(ctx.redis, ctx.tenantId, instanceId);
    assertions.push(
      finalState?.state === "ready"
        ? pass("B: Redis state = ready after final agent_done")
        : fail("B: Redis state = ready after final agent_done", { state: finalState?.state })
    );
  } finally {
    await mcp.disconnect().catch(() => undefined);
  }
}

// ─── Entrypoint ───────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  try {
    // Part A: Conference happy path
    await runPartA(ctx, assertions);

    // Part B: Reconnect resilience (clean slate — runner flushes Redis between scenarios)
    await runPartB(ctx, assertions);
  } catch (err) {
    assertions.push(fail("Scenario 06 unexpected error", String(err)));
  }

  return {
    scenario_id: "06",
    name:        "Conference Flow + Reconnect Resilience",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  };
}
