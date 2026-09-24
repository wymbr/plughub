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
 *   3. Menu outcomes inside the block (NIV-19): a DECLARED on_timeout/on_invalid/on_disconnect is
 *      followed and ends the transaction; with no declared branch, or on channel `aborted`, the
 *      engine still rewinds to begin_transaction.on_failure
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine }      from "../engine"
import type { SkillFlow }       from "@plughub/schemas"
import { redisKeys }            from "../redis-keys"

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
 *   → coletar_senha (menu, masked:"credential")
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
      masked:      "credential",
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

// NIV-19 — dentro do bloco, o ramo que o AUTOR declarou para o desfecho vale; sem ramo declarado,
// o rewind para o on_failure do bloco continua. Medido em chamada real (VOZ-37): o on_timeout era
// engolido pelo rewind e o cliente ouvia a mensagem de falha. O que faria estes testes ficarem
// vermelhos: o engine voltar a ignorar `declared_branch` (os de ramo) ou passar a seguir o
// fallback do menu no lugar do rewind (os sem ramo — são eles que provam que o rewind não morreu).
describe("SkillFlowEngine — masked transaction menu outcomes (NIV-19)", () => {

  // `clearAllMocks` não desfaz implementação: o `mockRejectedValue` dos testes de cima derrubaria o
  // `notification_send` do menu, que sairia por on_failure ANTES de esperar — e o rewind resultante
  // pareceria o caso "sem ramo". Cada teste daqui parte de mocks limpos.
  beforeEach(() => {
    mockMcpCall.mockReset().mockResolvedValue({})
    mockRedis.blpop.mockReset()
  })

  // Bloco com os três destinos DISTINTOS, para o teste saber qual rodou pelo outcome.
  function flowCom(ramos: Record<string, string>): SkillFlow {
    return {
      entry: "tx_start",
      steps: [
        { id: "tx_start", type: "begin_transaction", on_failure: "recolher" },
        {
          id: "coletar_senha", type: "menu", interaction: "text",
          prompt: "Senha:", masked: "credential", timeout_s: 30,
          on_success: "tx_fim", on_failure: "falha_do_menu", ...ramos,
        },
        { id: "tx_fim", type: "end_transaction", on_success: "concluir" },
        { id: "concluir", type: "complete", outcome: "resolved" },
        { id: "recolher", type: "complete", outcome: "escalated_human" },
        { id: "sem_resposta", type: "complete", outcome: "timeout" },
        { id: "invalido", type: "complete", outcome: "rejected" },
        { id: "saiu", type: "complete", outcome: "abandoned" },
        { id: "falha_do_menu", type: "complete", outcome: "failed" },
      ],
    } as SkillFlow
  }

  // As chaves vêm do MESMO `redisKeys` que o menu usa — string montada à mão errou o
  // `instanceId` na primeira versão, e o sinal virou "resposta do cliente".
  const INST   = "inst-niv19"
  const sinal  = (sid: string, outcome: string) =>
    [redisKeys.menuSignal(sid, INST), JSON.stringify({ _collect_outcome: outcome })]

  async function rodar(flow: SkillFlow, sid: string) {
    const result = await makeEngine().run({
      tenantId: "tenant-test", sessionId: sid, customerId: "c5", instanceId: INST,
      skillId: "skill_auth_v1", flow, sessionContext: {},
    })
    expect("outcome" in result).toBe(true)
    return result as { outcome: string; pipeline_state: Record<string, unknown> }
  }

  it("follows the declared on_timeout when the BLPOP expires inside the block", async () => {
    mockRedis.blpop.mockResolvedValue(null)
    const r = await rodar(flowCom({ on_timeout: "sem_resposta" }), "sess-tx-5")
    expect(r.outcome).toBe("timeout")
  })

  it("follows the declared on_timeout when the CHANNEL reports the collect timeout", async () => {
    mockRedis.blpop.mockResolvedValue(sinal("sess-tx-6", "timeout"))
    const r = await rodar(flowCom({ on_timeout: "sem_resposta" }), "sess-tx-6")
    expect(r.outcome).toBe("timeout")
  })

  it("follows the declared on_invalid when the channel exhausts max_invalid", async () => {
    mockRedis.blpop.mockResolvedValue(sinal("sess-tx-7", "invalid"))
    const r = await rodar(flowCom({ on_invalid: "invalido" }), "sess-tx-7")
    expect(r.outcome).toBe("rejected")
  })

  it("follows the declared on_disconnect when the customer leaves", async () => {
    mockRedis.blpop.mockResolvedValue([redisKeys.sessionClosed("sess-tx-8"), "1"])
    const r = await rodar(flowCom({ on_disconnect: "saiu" }), "sess-tx-8")
    expect(r.outcome).toBe("abandoned")
  })

  it("still rewinds to the block's on_failure when NO branch is declared", async () => {
    mockRedis.blpop.mockResolvedValue(null)
    const r = await rodar(flowCom({}), "sess-tx-9")
    // nem o fallback do menu (falha_do_menu → failed): o rewind do bloco vence
    expect(r.outcome).toBe("escalated_human")
  })

  it("still rewinds when the channel reports invalid and NO on_invalid is declared", async () => {
    // o fallback do menu (on_failure) nunca é "ramo declarado" — senão falha qualquer do menu furaria o bloco
    mockRedis.blpop.mockResolvedValue(sinal("sess-tx-12", "invalid"))
    const r = await rodar(flowCom({ on_timeout: "sem_resposta" }), "sess-tx-12")
    expect(r.outcome).toBe("escalated_human")
  })

  it("discards the masked scope on a declared branch: a later step cannot read the old value", async () => {
    // a senha entra no escopo; a 2ª coleta do MESMO bloco expira e sai pelo on_timeout declarado;
    // o step depois da saída pede @masked.coletar_senha — tem de NÃO receber o segredo
    mockRedis.blpop
      .mockResolvedValue(null)
      .mockResolvedValueOnce([redisKeys.menuResult("sess-tx-13", INST), "segredo_9012"])
    const flow = {
      entry: "tx_start",
      steps: [
        { id: "tx_start", type: "begin_transaction", on_failure: "recolher" },
        { id: "coletar_senha", type: "menu", interaction: "text", prompt: "Senha:", masked: "credential",
          timeout_s: 30, on_success: "coletar_codigo", on_failure: "recolher" },
        { id: "coletar_codigo", type: "menu", interaction: "text", prompt: "Codigo:", masked: "credential",
          timeout_s: 30, on_success: "tx_fim", on_failure: "recolher", on_timeout: "registrar" },
        { id: "tx_fim", type: "end_transaction", on_success: "fim" },
        { id: "fim", type: "complete", outcome: "resolved" },
        { id: "recolher", type: "complete", outcome: "escalated_human" },
        { id: "registrar", type: "invoke", target: { mcp_server: "mcp-server-auth", tool: "registrar_tentativa" },
          input: { senha: "@masked.coletar_senha" }, output_as: "reg", on_success: "sem_resposta", on_failure: "sem_resposta" },
        { id: "sem_resposta", type: "complete", outcome: "timeout" },
      ],
    } as SkillFlow
    const r = await rodar(flow, "sess-tx-13")
    expect(r.outcome).toBe("timeout")
    // testemunha: o step depois da saída RODOU — senão "não recebeu o segredo" seria por ausência
    expect(mockMcpCall.mock.calls.some(c => c[0] === "registrar_tentativa")).toBe(true)
    expect(JSON.stringify(mockMcpCall.mock.calls)).not.toContain("segredo_9012")
  })

  it("still rewinds when the channel ABORTS the protected collect, even with branches declared", async () => {
    // NIV-07: `aborted` não é desfecho do cliente, é o canal dizendo que não protegeu — nunca é ramo
    mockRedis.blpop.mockResolvedValue(sinal("sess-tx-10", "aborted"))
    const r = await rodar(flowCom({ on_timeout: "sem_resposta", on_invalid: "invalido" }), "sess-tx-10")
    expect(r.outcome).toBe("escalated_human")
  })

  it("ends the transaction on a declared branch: the masked value never reaches pipeline_state", async () => {
    // 1ª espera devolve o segredo; o invoke falha e o bloco rewinda para `de_novo`; o cliente aceita e
    // a transação REABRE; a 3ª espera (a coleta de novo, dentro do bloco) expira e o on_timeout
    // declarado é seguido — o segredo da 1ª tentativa não pode ter sobrado em lugar nenhum
    mockRedis.blpop
      .mockResolvedValue(null)                       // depois das duas abaixo: timeout (nunca laço)
      .mockResolvedValueOnce([redisKeys.menuResult("sess-tx-11", INST), "segredo_4471"])
      .mockResolvedValueOnce([redisKeys.menuResult("sess-tx-11", INST), "sim"])
      .mockResolvedValueOnce(null)
    // só a validação falha — o `notification_send` do menu precisa passar, senão o menu cai em
    // on_failure antes de esperar e o teste mediria outra coisa
    mockMcpCall.mockImplementation((tool: string) =>
      tool === "validate_pin" ? Promise.reject(new Error("PIN inválido")) : Promise.resolve({}))
    const flow = {
      entry: "tx_start",
      steps: [
        // retry de verdade: a falha do bloco pergunta e reabre a transação — o ciclo passa por um
        // menu (bloqueia em I/O), senão o validador de fluxo o recusa como ciclo sem guarda
        { id: "tx_start", type: "begin_transaction", on_failure: "de_novo" },
        { id: "coletar_senha", type: "menu", interaction: "text", prompt: "Senha:", masked: "credential",
          timeout_s: 30, on_success: "validar", on_failure: "tx_start", on_timeout: "sem_resposta" },
        { id: "validar", type: "invoke", target: { mcp_server: "mcp-server-auth", tool: "validate_pin" },
          input: { senha: "@masked.coletar_senha" }, output_as: "v", on_success: "tx_fim", on_failure: "tx_start" },
        { id: "tx_fim", type: "end_transaction", on_success: "fim" },
        { id: "fim", type: "complete", outcome: "resolved" },
        { id: "sem_resposta", type: "complete", outcome: "timeout" },
        // FORA da ordem do bloco: o begin_transaction avança para o PRÓXIMO step do array
        { id: "de_novo", type: "menu", interaction: "text", prompt: "Tentar de novo?", timeout_s: 30,
          on_success: "tx_start", on_failure: "sem_resposta" },
      ],
    } as SkillFlow
    const r = await rodar(flow, "sess-tx-11")
    expect(r.outcome).toBe("timeout")
    // testemunha do caminho: coleta, pergunta, coleta de novo — senão o timeout poderia ter vindo
    // da 1ª espera, fora do cenário que o teste afirma medir
    expect(mockRedis.blpop).toHaveBeenCalledTimes(3)
    expect(JSON.stringify(r.pipeline_state)).not.toContain("segredo_4471")
  })
})
