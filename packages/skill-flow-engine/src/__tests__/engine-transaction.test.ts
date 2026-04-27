/**
 * engine-transaction.test.ts
 * Engine integration tests for begin_transaction / end_transaction masked flows.
 * Spec: docs/guias/masked-input.md
 *
 * Covered:
 *   1. Happy path: menu(masked) → invoke(reads @masked.*) → end_transaction → complete
 *      - masked values are available to invoke via @masked.*
 *      - end_transaction status is persisted (not values)
 *      - masked values are NOT in the final pipeline_state
 *   2. Failure path: invoke fails inside block → engine rewinds to on_failure
 *      - maskedScope is cleared on rewind
 *   3. Failure at menu: menu timeout inside block → rewind
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine }      from "../engine"
import type { SkillFlow }       from "@plughub/schemas"

// ─────────────────────────────────────────────
// Shared mocks
// ─────────────────────────────────────────────

const mockRedis = {
  get:   vi.fn().mockResolvedValue(null),
  set:   vi.fn().mockResolvedValue("OK"),
  del:   vi.fn().mockResolvedValue(1),
  eval:  vi.fn().mockResolvedValue(1),
  expire: vi.fn().mockResolvedValue(1),
  blpop: vi.fn(),
}

const mockMcpCall = vi.fn()
const mockAiCall  = vi.fn()

function makeEngine() {
  return new SkillFlowEngine({
    redis:         mockRedis as never,
    mcpCall:       mockMcpCall,
    aiGatewayCall: mockAiCall,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRedis.get.mockResolvedValue(null)
  mockRedis.set.mockResolvedValue("OK")
  mockRedis.eval.mockResolvedValue(1)
  mockRedis.expire.mockResolvedValue(1)
  mockRedis.del.mockResolvedValue(1)
})

// ─────────────────────────────────────────────
// Test flows
// ─────────────────────────────────────────────

/**
 * Flow:
 *   begin_transaction (on_failure: recolher)
 *   → coletar_senha (menu, masked:true)
 *   → validar (invoke — reads @masked.coletar_senha)
 *   → tx_fim (end_transaction, result_as: "tx_result")
 *   → concluir (complete)
 *
 * recolher: notify + complete
 */
const transactionFlow: SkillFlow = {
  entry: "tx_start",
  steps: [
    {
      id:         "tx_start",
      type:       "begin_transaction",
      on_failure: "recolher",
    },
    {
      id:          "coletar_senha",
      type:        "menu",
      interaction: "text",
      prompt:      "Informe sua senha:",
      timeout_s:   300,
      masked:      true,
      output_as:   "coletar_senha",
      on_success:  "validar",
      on_failure:  "tx_start",   // on_failure goes back to begin (retry)
    },
    {
      id:         "validar",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-auth", tool: "validate_pin" },
      input:      { senha: "@masked.coletar_senha" },
      output_as:  "validacao",
      on_success: "tx_fim",
      on_failure: "tx_start",   // on_failure inside block → will rewind to begin's on_failure
    },
    {
      id:         "tx_fim",
      type:       "end_transaction",
      result_as:  "tx_result",
      on_success: "concluir",
    },
    {
      id:      "concluir",
      type:    "complete",
      outcome: "resolved",
    },
    {
      id:      "recolher",
      type:    "complete",
      outcome: "escalated_human",
    },
  ],
}

// ─────────────────────────────────────────────
// 1. Happy path
// ─────────────────────────────────────────────

describe("SkillFlowEngine — masked transaction happy path", () => {

  it("completes with resolved outcome and persists tx_result (not values)", async () => {
    // menu step blpop returns the masked password
    mockRedis.blpop.mockResolvedValue([`menu:result:sess-tx-1`, "super_secret_123"])
    // invoke (validate_pin) succeeds
    mockMcpCall.mockResolvedValue({ valid: true, session_token: "tok_abc" })

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       "tenant-test",
      sessionId:      "sess-tx-1",
      customerId:     "c1",
      skillId:        "skill_auth_v1",
      flow:           transactionFlow,
      sessionContext: {},
    })

    expect("outcome" in result).toBe(true)
    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")

      const state = result.pipeline_state
      expect(state.status).toBe("completed")

      // tx_result must be present with status "ok" and fields_collected
      const txResult = state.results["tx_result"] as Record<string, unknown>
      expect(txResult).toBeDefined()
      expect(txResult["status"]).toBe("ok")
      expect(txResult["fields_collected"]).toContain("coletar_senha")

      // The masked value must NEVER appear in pipeline_state
      const stateStr = JSON.stringify(state)
      expect(stateStr).not.toContain("super_secret_123")

      // validacao result (non-sensitive) should be present
      expect(state.results["validacao"]).toMatchObject({ valid: true })
    }
  })

  it("passes @masked.* value to invoke input", async () => {
    mockRedis.blpop.mockResolvedValue([`menu:result:sess-tx-2`, "my_pin_9999"])
    mockMcpCall.mockResolvedValue({ valid: true })

    const engine = makeEngine()
    await engine.run({
      tenantId:       "tenant-test",
      sessionId:      "sess-tx-2",
      customerId:     "c2",
      skillId:        "skill_auth_v1",
      flow:           transactionFlow,
      sessionContext: {},
    })

    // The invoke call should have received the masked value as the `senha` argument
    const invokeCall = mockMcpCall.mock.calls.find(
      (call) => call[0] === "validate_pin"
    )
    expect(invokeCall).toBeDefined()
    expect(invokeCall![1]).toMatchObject({ senha: "my_pin_9999" })
  })
})

// ─────────────────────────────────────────────
// 2. Failure inside block — rewind to on_failure
// ─────────────────────────────────────────────

describe("SkillFlowEngine — masked transaction failure rewind", () => {

  it("rewinds to begin_transaction.on_failure when invoke fails inside block", async () => {
    // menu succeeds (password entered)
    mockRedis.blpop.mockResolvedValue([`menu:result:sess-tx-3`, "wrong_password"])
    // invoke fails (validation rejected)
    mockMcpCall.mockRejectedValue(new Error("PIN inválido"))

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       "tenant-test",
      sessionId:      "sess-tx-3",
      customerId:     "c3",
      skillId:        "skill_auth_v1",
      flow:           transactionFlow,
      sessionContext: {},
    })

    // Should end at the "recolher" complete step (on_failure of begin_transaction)
    expect("outcome" in result).toBe(true)
    if ("outcome" in result) {
      expect(result.outcome).toBe("escalated_human")
    }
  })

  it("does NOT persist masked value in pipeline_state when invoke fails", async () => {
    mockRedis.blpop.mockResolvedValue([`menu:result:sess-tx-4`, "stolen_value_xyz"])
    mockMcpCall.mockRejectedValue(new Error("rejected"))

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       "tenant-test",
      sessionId:      "sess-tx-4",
      customerId:     "c4",
      skillId:        "skill_auth_v1",
      flow:           transactionFlow,
      sessionContext: {},
    })

    if ("outcome" in result) {
      // The sensitive value must NOT be in the final pipeline_state
      expect(JSON.stringify(result.pipeline_state)).not.toContain("stolen_value_xyz")
    }
  })
})

// ─────────────────────────────────────────────
// 3. Menu timeout inside block
// ─────────────────────────────────────────────

describe("SkillFlowEngine — masked transaction menu timeout", () => {

  it("rewinds when menu times out inside the block", async () => {
    // blpop returns null = timeout
    mockRedis.blpop.mockResolvedValue(null)

    const flowWithTimeout: SkillFlow = {
      entry: "tx_start",
      steps: [
        { id: "tx_start", type: "begin_transaction", on_failure: "recolher" },
        {
          id: "coletar_senha", type: "menu", interaction: "text",
          prompt: "Senha:", masked: true, timeout_s: 30,
          on_success: "tx_fim", on_failure: "tx_start", on_timeout: "tx_start",
        },
        { id: "tx_fim", type: "end_transaction" },
        { id: "recolher", type: "complete", outcome: "escalated_human" },
      ],
    }

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       "tenant-test",
      sessionId:      "sess-tx-5",
      customerId:     "c5",
      skillId:        "skill_auth_v1",
      flow:           flowWithTimeout,
      sessionContext: {},
    })

    // menu timeout → on_timeout = "tx_start" (which is begin_transaction.on_failure when inside block)
    // Actually: on_timeout leads to on_failure step ("tx_start"), but since we're inside a transaction
    // block with transactionOnFailure="recolher", the engine catches it and rewinds to "recolher"
    expect("outcome" in result).toBe(true)
    if ("outcome" in result) {
      expect(result.outcome).toBe("escalated_human")
    }
  })
})
