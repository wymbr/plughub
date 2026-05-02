/**
 * 20_masked_form.ts
 * Scenario 20: MASKED FORM — FIELD-LEVEL MASKING POLICY
 *
 * Validates that the field-level masking policy (field.masked overrides step.masked)
 * works correctly for `interaction: form` steps with mixed field definitions.
 *
 * Part A — Mixed-field form (some masked, some plain):
 *   Form with three fields:
 *     email      → no masked flag  → should survive to pipeline_state
 *     senha      → masked: true   → must be routed to maskedScope only
 *     codigo_2fa → masked: true   → must be routed to maskedScope only
 *   @masked.senha is used in the invoke step (validate_pin).
 *   Asserts:
 *     A1: outcome=resolved
 *     A2: dados_coletados.email present and correct in pipeline_state
 *     A3: "senha" key absent from dados_coletados (masked, not in output)
 *     A4: masked string values ("123456", "654321") absent from full pipeline_state JSON
 *     A5: validacao_pin.valid=true (masked.senha was correctly passed to invoke)
 *     A6: status (end_transaction result) present
 *
 * Part B — step.masked=true with field.masked=false override:
 *   step.masked=true makes all fields masked by default.
 *   One field explicitly sets masked:false (override wins).
 *     cpf → masked: false  → survives to pipeline_state despite step.masked=true
 *     pin → (no override) → inherits step.masked=true → masked
 *   Asserts:
 *     B1: outcome=resolved
 *     B2: dados_identificacao.cpf present and correct (override wins over step.masked)
 *     B3: "pin" key absent from dados_identificacao
 *     B4: masked PIN value absent from full pipeline_state JSON
 *     B5: CPF value IS present in pipeline_state JSON (field-level override preserved it)
 *
 * Prerequisites: same demo stack as scenario 19 (mcp-server-auth on port 3150)
 *
 * Run:
 *   ts-node runner.ts --masked         — adds scenarios 20 + 21
 *   ts-node runner.ts --only 20        — run only scenario 20
 *
 * Assertions: 11
 * Timeout:    60s
 */

import { randomUUID }     from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { SkillFlowClient } from "../lib/http-client";
import { seedSessionMeta } from "../lib/redis-client";
import { pass, fail }      from "../lib/report";

// ─────────────────────────────────────────────────────────────────────────────
// Flow A — form with 1 plain field + 2 masked fields
// ─────────────────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FORM_FLOW_A: Record<string, any> = {
  entry: "tx_inicio",
  steps: [
    {
      id:         "tx_inicio",
      type:       "begin_transaction",
      on_failure: "falha",
    },
    {
      id:          "coletar_dados",
      type:        "menu",
      interaction: "form",
      prompt:      "Preencha os campos abaixo:",
      timeout_s:   120,
      output_as:   "dados_coletados",
      fields: [
        { id: "email",      label: "Email" },                         // plain — survives to pipeline_state
        { id: "senha",      label: "Senha",       masked: true },     // masked  → maskedScope only
        { id: "codigo_2fa", label: "Código 2FA",  masked: true },     // masked  → maskedScope only
      ],
      on_success: "validar",
      on_failure: "tx_inicio",
    },
    {
      id:         "validar",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-auth", tool: "validate_pin" },
      input:      { customer_id: "e2e-form-a", pin: "@masked.senha" },  // reads from maskedScope
      output_as:  "validacao_pin",
      on_success: "tx_fim",
      on_failure: "tx_inicio",
    },
    {
      id:         "tx_fim",
      type:       "end_transaction",
      result_as:  "status",
      on_success: "concluir",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "falha",    type: "complete", outcome: "escalated_human" },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Flow B — step.masked=true with one field.masked=false override
// ─────────────────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FORM_FLOW_B: Record<string, any> = {
  entry: "tx_inicio",
  steps: [
    {
      id:         "tx_inicio",
      type:       "begin_transaction",
      on_failure: "falha",
    },
    {
      id:          "coletar_identificacao",
      type:        "menu",
      interaction: "form",
      prompt:      "Informe seus dados de identificação:",
      timeout_s:   120,
      masked:      true,           // step-level: all fields masked by default
      output_as:   "dados_identificacao",
      fields: [
        { id: "cpf", label: "CPF", masked: false }, // override: NOT masked despite step.masked=true
        { id: "pin", label: "PIN" },                 // inherits step.masked=true → masked
      ],
      on_success: "validar",
      on_failure: "tx_inicio",
    },
    {
      id:         "validar",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-auth", tool: "validate_pin" },
      input:      { customer_id: "e2e-form-b", pin: "@masked.pin" },
      output_as:  "validacao_pin",
      on_success: "tx_fim",
      on_failure: "tx_inicio",
    },
    {
      id:         "tx_fim",
      type:       "end_transaction",
      result_as:  "status",
      on_success: "concluir",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "falha",    type: "complete", outcome: "escalated_human" },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function injectFormResponse(
  redis:     import("ioredis").Redis,
  sessionId: string,
  fields:    Record<string, string>
): Promise<void> {
  // The menu step does JSON.parse(value) for form interactions.
  await redis.lpush(`menu:result:${sessionId}`, JSON.stringify(fields));
}

// ─────────────────────────────────────────────────────────────────────────────
// Part A: Mixed-field form (email plain, senha masked, codigo_2fa masked)
// ─────────────────────────────────────────────────────────────────────────────

async function runPartA(
  ctx:        ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const sessionId  = `sess_${Date.now()}T000000_FORMAM${randomUUID().replace(/-/g,"").slice(0,8).toUpperCase()}`;
  const customerId = randomUUID();
  const client     = new SkillFlowClient(ctx.skillFlowUrl);

  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId, "webchat", "auth_ia");

  const executePromise = client.execute({
    tenant_id:       ctx.tenantId,
    session_id:      sessionId,
    customer_id:     customerId,
    skill_id:        "skill_masked_form_a_v1",
    flow:            FORM_FLOW_A,
    session_context: {},
  });

  // Wait for the flow to reach coletar_dados BLPOP (begin_transaction is instant)
  await sleep(1000);

  // Inject form response: email plain, senha starts with "1" (mcp-server-auth accepts)
  await injectFormResponse(ctx.redis, sessionId, {
    email:      "test@e2e.com",
    senha:      "123456",    // masked — goes to maskedScope, used by @masked.senha in invoke
    codigo_2fa: "654321",    // masked — goes to maskedScope, not used further
  });

  let result: Awaited<typeof executePromise>;
  try {
    result = await executePromise;
  } catch (err) {
    assertions.push(fail("A: form flow /execute completes without HTTP error", String(err)));
    return;
  }

  // A1: outcome=resolved (validar succeeded, end_transaction ran)
  assertions.push(
    "outcome" in result && result.outcome === "resolved"
      ? pass("A: mixed-field form — outcome=resolved")
      : fail("A: mixed-field form — outcome=resolved", { outcome: (result as any).outcome })
  );

  if (!("pipeline_state" in result)) return;
  const ps        = result.pipeline_state as Record<string, unknown>;
  const psResults = (ps["results"] ?? {}) as Record<string, unknown>;
  const psJson    = JSON.stringify(ps);

  // A2: dados_coletados.email present (non-masked field survives)
  const dadosColetados = psResults["dados_coletados"] as Record<string, unknown> | undefined;
  assertions.push(
    dadosColetados?.["email"] === "test@e2e.com"
      ? pass("A: dados_coletados.email=test@e2e.com in pipeline_state (non-masked field preserved)")
      : fail("A: dados_coletados.email in pipeline_state", { dados_coletados: dadosColetados })
  );

  // A3: "senha" key absent from dados_coletados (masked field not in form output object)
  assertions.push(
    dadosColetados !== undefined && !("senha" in dadosColetados) && !("codigo_2fa" in dadosColetados)
      ? pass("A: 'senha' and 'codigo_2fa' keys absent from dados_coletados (masked, not in output)")
      : fail("A: masked keys absent from dados_coletados", { dados_coletados: dadosColetados })
  );

  // A4: masked values never appear anywhere in pipeline_state JSON
  const maskedValuesLeaked = psJson.includes("123456") || psJson.includes("654321");
  assertions.push(
    !maskedValuesLeaked
      ? pass("A: masked form values ('123456', '654321') absent from full pipeline_state JSON")
      : fail("A: masked form values leaked into pipeline_state!", {
          has123456: psJson.includes("123456"),
          has654321: psJson.includes("654321"),
        })
  );

  // A5: validacao_pin.valid=true (masked.senha was correctly passed to invoke)
  const validacao = psResults["validacao_pin"] as Record<string, unknown> | undefined;
  assertions.push(
    validacao !== undefined && validacao["valid"] === true
      ? pass("A: validacao_pin.valid=true — @masked.senha was correctly forwarded to invoke")
      : fail("A: validacao_pin.valid=true", { validacao_pin: validacao })
  );

  // A6: status (end_transaction result) present
  const status = psResults["status"] as Record<string, unknown> | undefined;
  assertions.push(
    status?.["status"] === "ok"
      ? pass("A: end_transaction result status=ok in pipeline_state")
      : fail("A: end_transaction result status=ok", { status })
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Part B: step.masked=true + field.masked=false override (CPF plain, PIN masked)
// ─────────────────────────────────────────────────────────────────────────────

async function runPartB(
  ctx:        ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const sessionId  = `sess_${Date.now()}T000000_FORMBM${randomUUID().replace(/-/g,"").slice(0,8).toUpperCase()}`;
  const customerId = randomUUID();
  const client     = new SkillFlowClient(ctx.skillFlowUrl);

  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, customerId, "webchat", "auth_ia");

  const executePromise = client.execute({
    tenant_id:       ctx.tenantId,
    session_id:      sessionId,
    customer_id:     customerId,
    skill_id:        "skill_masked_form_b_v1",
    flow:            FORM_FLOW_B,
    session_context: {},
  });

  await sleep(1000);

  // cpf gets masked:false override → survives to pipeline_state
  // pin inherits step.masked=true → goes to maskedScope, used by @masked.pin
  // NOTE: cpf "98765432109" was chosen to NOT contain PIN "111111" as substring,
  //       avoiding a false positive in assertion B4 (psJson substring check).
  await injectFormResponse(ctx.redis, sessionId, {
    cpf: "98765432109",
    pin: "111111",  // starts with "1" → valid per mcp-server-auth rule
  });

  let result: Awaited<typeof executePromise>;
  try {
    result = await executePromise;
  } catch (err) {
    assertions.push(fail("B: form flow /execute completes without HTTP error", String(err)));
    return;
  }

  // B1: outcome=resolved
  assertions.push(
    "outcome" in result && result.outcome === "resolved"
      ? pass("B: field.masked=false override — outcome=resolved")
      : fail("B: field.masked=false override — outcome=resolved", { outcome: (result as any).outcome })
  );

  if (!("pipeline_state" in result)) return;
  const ps        = result.pipeline_state as Record<string, unknown>;
  const psResults = (ps["results"] ?? {}) as Record<string, unknown>;
  const psJson    = JSON.stringify(ps);

  // B2: dados_identificacao.cpf present (masked:false override preserved it despite step.masked=true)
  const dadosId = psResults["dados_identificacao"] as Record<string, unknown> | undefined;
  assertions.push(
    dadosId?.["cpf"] === "98765432109"
      ? pass("B: dados_identificacao.cpf present — field.masked=false overrides step.masked=true")
      : fail("B: dados_identificacao.cpf present (field-level override)", { dados_identificacao: dadosId })
  );

  // B3: "pin" key absent from dados_identificacao (inherits step.masked=true)
  assertions.push(
    dadosId !== undefined && !("pin" in dadosId)
      ? pass("B: 'pin' key absent from dados_identificacao (inherits step.masked=true)")
      : fail("B: 'pin' key absent from dados_identificacao", { dados_identificacao: dadosId })
  );

  // B4: masked PIN value absent from full pipeline_state JSON
  // PIN is "111111" — chosen so it doesn't appear as a substring of CPF "98765432109"
  assertions.push(
    !psJson.includes("111111")
      ? pass("B: masked PIN value '111111' absent from full pipeline_state JSON")
      : fail("B: masked PIN value '111111' leaked into pipeline_state!", {
          found_in: Object.keys(psResults).filter(k => JSON.stringify(psResults[k]).includes("111111")),
        })
  );

  // B5: CPF value IS in pipeline_state (field-level override: not masked)
  assertions.push(
    psJson.includes("98765432109")
      ? pass("B: CPF value '98765432109' present in pipeline_state (field.masked=false preserved it)")
      : fail("B: CPF value '98765432109' absent from pipeline_state — override did not work")
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Entrypoint
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  try {
    // Part A — mixed-field form: email plain, senha masked, codigo_2fa masked (6 assertions)
    await runPartA(ctx, assertions);

    // Part B — step.masked=true with field.masked=false override: cpf plain, pin masked (5 assertions)
    await runPartB(ctx, assertions);
  } catch (err) {
    assertions.push(fail("Scenario 20 unexpected error", String(err)));
  }

  return {
    scenario_id: "20",
    name:        "Masked Form — field-level masking policy",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  };
}
