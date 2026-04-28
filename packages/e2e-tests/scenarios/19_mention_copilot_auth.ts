/**
 * 19_mention_copilot_auth.ts
 * Scenario 19: @MENTION CO-PILOT + MASKED PIN AUTH
 *
 * Validates two new demo flows introduced in Sprint @mention / masked-input:
 *
 * Part A — Masked PIN authentication (agente_auth_ia_v1):
 *   skill-flow-service /execute → begin_transaction → coletar_pin (menu, masked)
 *   → inject PIN "123456" via Redis LPUSH → validar_pin (mcp-server-auth)
 *   → end_transaction → confirmacao → complete
 *   Asserts: outcome=resolved, auth_status.status="ok", PIN never in pipeline_state
 *
 * Part B — Masked PIN failure path:
 *   Same flow, inject PIN "999999" (always fails in mcp-server-auth)
 *   → validar_pin returns error → engine rewinds to begin_transaction.on_failure
 *   → falha_autenticacao notify → escalated_human
 *   Asserts: outcome=escalated_human, PIN never in pipeline_state
 *
 * Part C — @mention co-pilot: trigger + AI suggestion + terminate:
 *   skill-flow-service /execute with agente_copilot_v1 flow
 *   → apresentar (notify) → aguardar (menu, timeout=-1, BLPOP)
 *   → inject {"_mention_trigger_step": "analisar_sessao"} via Redis LPUSH
 *   → reason step calls AI Gateway (real LLM, api key from .env.demo)
 *   → enviar_sugestao (notify, agents_only) → loops back to aguardar
 *   → poll pipeline_state until analise.sugestao is populated
 *   → inject {"_mention_terminate": true} via Redis LPUSH
 *   → engine takes on_failure → encerrar → complete (outcome=resolved)
 *   Asserts: sugestao is a non-empty string, outcome=resolved, notify sentinel present
 *
 * Prerequisites (demo stack only):
 *   docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
 *   mcp-server-auth must be running (port 3150, wired in skill-flow-service)
 *   ANTHROPIC_API_KEY must be set (required by co-pilot reason step)
 *
 * Run:
 *   ts-node runner.ts --mention          — adds scenario 19 to run set
 *   ts-node runner.ts --only 19          — run only scenario 19
 *
 * Assertions: 14
 * Timeout:    90s (LLM reason step can take 3–10s with real API)
 */

import { randomUUID }     from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { SkillFlowClient }  from "../lib/http-client";
import { seedSessionMeta }  from "../lib/redis-client";
import { pass, fail }       from "../lib/report";

// ─────────────────────────────────────────────────────────────────────────────
// Auth flow (agente_auth_ia_v1) — embedded inline to avoid YAML parse dependency
// ─────────────────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const AUTH_FLOW: Record<string, any> = {
  entry: "solicitar_inicio",
  steps: [
    {
      id:         "solicitar_inicio",
      type:       "notify",
      message:    "Para prosseguir, precisamos verificar sua identidade. Você receberá uma solicitação para informar seu PIN de segurança.",
      on_success: "tx_inicio",
    },
    {
      id:         "tx_inicio",
      type:       "begin_transaction",
      on_failure: "falha_autenticacao",
    },
    {
      id:          "coletar_pin",
      type:        "menu",
      interaction: "text",
      prompt:      "Informe seu PIN de segurança (6 dígitos):",
      timeout_s:   120,
      masked:      true,
      output_as:   "pin_input",
      on_success:  "validar_pin",
      on_failure:  "tx_inicio",
      on_timeout:  "tx_inicio",
    },
    {
      id:         "validar_pin",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-auth", tool: "validate_pin" },
      input:      { customer_id: "e2e-test-customer", pin: "@masked.pin_input" },
      output_as:  "validacao_pin",
      on_success: "tx_fim",
      on_failure: "tx_inicio",
    },
    {
      id:         "tx_fim",
      type:       "end_transaction",
      result_as:  "auth_status",
      on_success: "confirmacao",
    },
    {
      id:         "confirmacao",
      type:       "notify",
      message:    "Identidade verificada com sucesso. ✅ Podemos prosseguir.",
      on_success: "concluir",
    },
    {
      id:      "concluir",
      type:    "complete",
      outcome: "resolved",
    },
    {
      id:         "falha_autenticacao",
      type:       "notify",
      message:    "Não foi possível verificar sua identidade no momento. Um de nossos especialistas irá atendê-lo em seguida.",
      on_success: "encerrar_escalado",
    },
    {
      id:      "encerrar_escalado",
      type:    "complete",
      outcome: "escalated_human",
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Copilot flow (agente_copilot_v1) — embedded inline
// ─────────────────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const COPILOT_FLOW: Record<string, any> = {
  entry: "apresentar",
  steps: [
    {
      id:         "apresentar",
      type:       "notify",
      message:    "🤖 Co-pilot SAC conectado. Comandos disponíveis: @copilot ativa (analisa contexto), @copilot pausa, @copilot para.",
      on_success: "aguardar",
      on_failure: "aguardar",   // resilient — don't fail if session not fully seeded
    },
    {
      id:          "aguardar",
      type:        "menu",
      interaction: "text",
      prompt:      "(co-pilot em standby — aguardando @copilot ativa)",
      timeout_s:   -1,
      on_success:  "analisar_sessao",
      on_failure:  "encerrar",
      on_timeout:  "encerrar",
    },
    {
      id:      "analisar_sessao",
      type:    "reason",
      message: [
        "Você é um co-pilot de atendimento SAC. Analise o contexto abaixo e gere",
        "UMA sugestão prática e concisa (máximo 2 frases) para o agente humano.",
        "Seja direto e útil — sem perguntas, apenas a sugestão.",
        "",
        "Dados da sessão:",
        "- Cliente: @ctx.caller.nome",
        "- Motivo do contato: @ctx.caller.motivo_contato",
        "- Sentimento atual: @ctx.session.sentimento.categoria",
        "- Histórico: @ctx.session.historico_resumo",
      ].join("\n"),
      output_as:     "analise",
      output_schema: {
        type:       "object",
        properties: {
          sugestao: {
            type:        "string",
            description: "Sugestão direta para o agente humano (máximo 2 frases)",
          },
        },
        required: ["sugestao"],
      },
      on_success: "enviar_sugestao",
      on_failure: "aguardar",
    },
    {
      id:         "enviar_sugestao",
      type:       "notify",
      message:    "💡 Co-pilot: {{$.results.analise.sugestao}}",
      on_success: "aguardar",
      on_failure: "aguardar",   // resilient — loop back even if notify fails
    },
    {
      id:      "encerrar",
      type:    "complete",
      outcome: "resolved",
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Poll pipeline state until predicate returns true or timeout elapses. */
async function pollPipeline(
  client:    SkillFlowClient,
  tenantId:  string,
  sessionId: string,
  predicate: (state: unknown) => boolean,
  timeoutMs: number = 25000,
  intervalMs: number = 800
): Promise<unknown | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await client.getPipeline(tenantId, sessionId);
    if (state && predicate(state)) return state;
    await sleep(intervalMs);
  }
  return null;
}

/** Injects a menu response to unblock a BLPOP in the skill-flow-engine. */
async function injectMenuResponse(
  redis:     import("ioredis").Redis,
  sessionId: string,
  value:     unknown
): Promise<void> {
  const payload = typeof value === "string" ? value : JSON.stringify(value);
  await redis.lpush(`menu:result:${sessionId}`, payload);
}

// ─────────────────────────────────────────────────────────────────────────────
// Part A: Masked PIN happy path
// ─────────────────────────────────────────────────────────────────────────────

async function runPartA(
  ctx:        ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const sessionId  = `sess_${Date.now()}T000000_AUTHHAPPY${randomUUID().replace(/-/g,"").slice(0,10).toUpperCase()}`;
  const customerId = randomUUID();
  const client     = new SkillFlowClient(ctx.skillFlowUrl);

  // Seed session meta so notification_send can resolve channel/contact
  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId, "webchat", "auth_ia");

  // Start the flow execution concurrently — it will block at coletar_pin (BLPOP)
  const executePromise = client.execute({
    tenant_id:       ctx.tenantId,
    session_id:      sessionId,
    customer_id:     customerId,
    skill_id:        "skill_auth_ia_v1",
    flow:            AUTH_FLOW,
    session_context: {},
  });

  // Wait for the flow to reach the menu step (after solicitar_inicio notify + tx_inicio)
  await sleep(1200);

  // Inject a valid PIN: starts with "1", 6 digits → mcp-server-auth accepts it
  await injectMenuResponse(ctx.redis, sessionId, "123456");

  let result: Awaited<typeof executePromise>;
  try {
    result = await executePromise;
  } catch (err) {
    assertions.push(fail("A: auth flow /execute completes without HTTP error", String(err)));
    return;
  }

  // A1: outcome must be resolved
  assertions.push(
    "outcome" in result && result.outcome === "resolved"
      ? pass("A: PIN happy path — outcome=resolved", { outcome: (result as any).outcome })
      : fail("A: PIN happy path — outcome=resolved", result)
  );

  if (!("pipeline_state" in result)) return;
  const ps = result.pipeline_state as Record<string, unknown>;
  const psResults = (ps["results"] ?? {}) as Record<string, unknown>;

  // A2: auth_status.status === "ok" (end_transaction result)
  const authStatus = psResults["auth_status"] as Record<string, unknown> | undefined;
  assertions.push(
    authStatus?.["status"] === "ok"
      ? pass("A: auth_status.status=ok in pipeline_state", { auth_status: authStatus })
      : fail("A: auth_status.status=ok in pipeline_state", { auth_status: authStatus })
  );

  // A3: validacao_pin present (invoke result from mcp-server-auth)
  const validacao = psResults["validacao_pin"] as Record<string, unknown> | undefined;
  assertions.push(
    validacao !== undefined && validacao["valid"] === true
      ? pass("A: validacao_pin.valid=true in pipeline_state", { validacao_pin: validacao })
      : fail("A: validacao_pin.valid=true in pipeline_state", { validacao_pin: validacao })
  );

  // A4: masked value "123456" NEVER appears anywhere in pipeline_state
  const psStr = JSON.stringify(ps);
  assertions.push(
    !psStr.includes("123456")
      ? pass("A: masked PIN value '123456' absent from pipeline_state")
      : fail("A: masked PIN value '123456' absent from pipeline_state (leaked!)", {
          found_in_keys: Object.keys(psResults),
        })
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Part B: Masked PIN failure path
// ─────────────────────────────────────────────────────────────────────────────

async function runPartB(
  ctx:        ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const sessionId  = `sess_${Date.now()}T000000_AUTHFAIL${randomUUID().replace(/-/g,"").slice(0,10).toUpperCase()}`;
  const customerId = randomUUID();
  const client     = new SkillFlowClient(ctx.skillFlowUrl);

  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId, "webchat", "auth_ia");

  const executePromise = client.execute({
    tenant_id:       ctx.tenantId,
    session_id:      sessionId,
    customer_id:     customerId,
    skill_id:        "skill_auth_ia_v1",
    flow:            AUTH_FLOW,
    session_context: {},
  });

  // Wait for flow to reach coletar_pin BLPOP
  await sleep(1200);

  // Inject a PIN that always fails in mcp-server-auth
  await injectMenuResponse(ctx.redis, sessionId, "999999");

  let result: Awaited<typeof executePromise>;
  try {
    result = await executePromise;
  } catch (err) {
    assertions.push(fail("B: auth failure path /execute completes without HTTP error", String(err)));
    return;
  }

  // B1: engine rewinds to begin_transaction.on_failure → escalated_human
  assertions.push(
    "outcome" in result && result.outcome === "escalated_human"
      ? pass("B: PIN failure path — outcome=escalated_human")
      : fail("B: PIN failure path — outcome=escalated_human", result)
  );

  if (!("pipeline_state" in result)) return;
  const ps    = result.pipeline_state as Record<string, unknown>;
  const psStr = JSON.stringify(ps);

  // B2: masked PIN "999999" never in pipeline_state
  assertions.push(
    !psStr.includes("999999")
      ? pass("B: masked PIN value '999999' absent from pipeline_state (failure path)")
      : fail("B: masked PIN value '999999' absent from pipeline_state (leaked!)")
  );

  // B3: no auth_status in results (end_transaction was never reached)
  const psResults = (ps["results"] ?? {}) as Record<string, unknown>;
  assertions.push(
    psResults["auth_status"] === undefined
      ? pass("B: auth_status absent from pipeline_state (end_transaction not reached)")
      : fail("B: auth_status absent from pipeline_state (end_transaction not reached)", {
          auth_status: psResults["auth_status"],
        })
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Part C: @mention co-pilot — trigger + AI suggestion + terminate
// ─────────────────────────────────────────────────────────────────────────────

async function runPartC(
  ctx:        ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const sessionId  = `sess_${Date.now()}T000000_COPILOT${randomUUID().replace(/-/g,"").slice(0,10).toUpperCase()}`;
  const customerId = randomUUID();
  const client     = new SkillFlowClient(ctx.skillFlowUrl);

  // Seed session meta + some caller context for the reason step to read
  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId, "webchat", "copilot_sac");

  // Seed minimal ContextStore fields so the reason step has something to work with
  const ctxEntry = (value: string) =>
    JSON.stringify({ value, confidence: 0.9, source: "e2e_test", visibility: "agents_only", updated_at: new Date().toISOString() });
  await ctx.redis.hset(`${ctx.tenantId}:ctx:${sessionId}`,
    "caller.nome",            ctxEntry("João E2E"),
    "caller.motivo_contato",  ctxEntry("Cancelamento de plano"),
    "session.sentimento.categoria", ctxEntry("neutral"),
    "session.historico_resumo",     ctxEntry("Primeiro contato"),
  );

  // ── Start flow execution (will block at aguardar BLPOP) ──────────────────
  const executePromise = client.execute({
    tenant_id:       ctx.tenantId,
    session_id:      sessionId,
    customer_id:     customerId,
    skill_id:        "skill_copilot_sac_v1",
    flow:            COPILOT_FLOW,
    session_context: {},
  });

  // ── C1: Verify mcp-server-auth is reachable (health check) ───────────────
  const authUrl = process.env["MCP_AUTH_URL"] ?? "http://localhost:3150";
  try {
    const res = await fetch(`${authUrl}/health`);
    const body = (await res.json()) as Record<string, unknown>;
    assertions.push(
      res.ok && body["status"] === "ok"
        ? pass("C: mcp-server-auth /health → ok", { service: body["service"] })
        : fail("C: mcp-server-auth /health → ok", { status: res.status, body })
    );
  } catch (err) {
    assertions.push(fail("C: mcp-server-auth /health → ok", String(err)));
    // Cancel by injecting terminate so executePromise doesn't hang
    await injectMenuResponse(ctx.redis, sessionId, { _mention_terminate: true });
    await executePromise.catch(() => undefined);
    return;
  }

  // Wait for apresentar (notify) + engine to reach aguardar BLPOP
  // apresentar calls notification_send → mcp-server-plughub → Kafka; ~500ms total
  await sleep(1500);

  // ── C2: Inject @mention trigger — simulates bridge dispatching "ativa" command ──
  await injectMenuResponse(ctx.redis, sessionId, { _mention_trigger_step: "analisar_sessao" });
  assertions.push(pass("C: @mention trigger injected (_mention_trigger_step: analisar_sessao)"));

  // ── C3: Poll pipeline_state until analise.sugestao is populated ──────────
  // The reason step calls AI Gateway with the real Anthropic API.
  // This typically takes 2–8 seconds. We poll for up to 25s.
  const stateWithAnalise = await pollPipeline(
    client, ctx.tenantId, sessionId,
    (s) => {
      const results = ((s as any)?.results ?? {}) as Record<string, unknown>;
      const analise = results["analise"] as Record<string, unknown> | undefined;
      return typeof analise?.["sugestao"] === "string" && analise["sugestao"].length > 0;
    },
    25000,
  );

  if (!stateWithAnalise) {
    assertions.push(fail("C: analise.sugestao populated in pipeline_state after trigger (≤25s)",
      "Timeout — LLM reason step did not complete in time"));
    // Force terminate
    await injectMenuResponse(ctx.redis, sessionId, { _mention_terminate: true });
    await executePromise.catch(() => undefined);
    return;
  }

  const psResults = ((stateWithAnalise as any)?.results ?? {}) as Record<string, unknown>;
  const analise   = psResults["analise"] as Record<string, unknown>;
  const sugestao  = analise?.["sugestao"] as string | undefined;

  // C3: sugestao is a non-empty string
  assertions.push(
    typeof sugestao === "string" && sugestao.length > 3
      ? pass("C: analise.sugestao is a non-empty string from LLM", { sugestao: sugestao.slice(0, 80) })
      : fail("C: analise.sugestao is a non-empty string from LLM", { sugestao })
  );

  // After reason step completes, engine runs enviar_sugestao (notify) then loops
  // back to aguardar. Give it a moment to complete the notify and re-enter BLPOP.
  await sleep(1500);

  // ── C4: enviar_sugestao sentinel confirms notify was dispatched ──────────
  const stateAfterNotify = await client.getPipeline(ctx.tenantId, sessionId);
  const sentinelKey  = "enviar_sugestao:__notified__";
  const sentinelVal  = ((stateAfterNotify as any)?.results ?? {})[sentinelKey];
  assertions.push(
    sentinelVal === "completed" || sentinelVal === "dispatched"
      ? pass("C: enviar_sugestao notify sentinel present in pipeline_state", { sentinel: sentinelVal })
      : fail("C: enviar_sugestao notify sentinel present in pipeline_state", { sentinel: sentinelVal })
  );

  // ── C5: Inject @mention terminate — simulates "@copilot para" command ────
  await injectMenuResponse(ctx.redis, sessionId, { _mention_terminate: true });

  // ── Await flow completion ─────────────────────────────────────────────────
  let result: Awaited<typeof executePromise>;
  try {
    result = await executePromise;
  } catch (err) {
    assertions.push(fail("C: co-pilot flow /execute completes without HTTP error", String(err)));
    return;
  }

  // C5 continued: outcome must be resolved (on_failure from aguardar → encerrar → resolved)
  assertions.push(
    "outcome" in result && result.outcome === "resolved"
      ? pass("C: @mention terminate → outcome=resolved")
      : fail("C: @mention terminate → outcome=resolved", result)
  );

  // C6: pipeline_state has analise in final results
  if ("pipeline_state" in result) {
    const finalResults = ((result.pipeline_state as any)?.results ?? {}) as Record<string, unknown>;
    const finalAnalise = finalResults["analise"] as Record<string, unknown> | undefined;
    assertions.push(
      typeof finalAnalise?.["sugestao"] === "string"
        ? pass("C: analise present in final pipeline_state")
        : fail("C: analise present in final pipeline_state", { analise: finalAnalise })
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Entrypoint
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  try {
    // Part A — Masked PIN happy path (4 assertions)
    await runPartA(ctx, assertions);

    // Part B — Masked PIN failure path (3 assertions)
    await runPartB(ctx, assertions);

    // Part C — @mention co-pilot: trigger + AI suggestion + terminate (7 assertions)
    await runPartC(ctx, assertions);
  } catch (err) {
    assertions.push(fail("Scenario 19 unexpected error", String(err)));
  }

  return {
    scenario_id: "19",
    name:        "@mention Co-pilot + Masked PIN Auth",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  };
}
