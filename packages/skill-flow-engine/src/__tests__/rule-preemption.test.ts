/**
 * RUL-02 — escalação por regra: a IA que conduz pára na fronteira do passo e escala a si mesma.
 *
 * O caso que decide não é "escalou": uma implementação que escalasse SEMPRE também passaria nele.
 * Os controles negativos são os que pesam — marca de OUTRA instância, marca já tomada, sem
 * instância — porque é neles que uma regra arrancaria o contato de quem não devia (o especialista,
 * a IA do pool de destino, uma segunda réplica).
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine } from "../engine"
import { claimRulePreemption, parseMark, ruleEscalationKey, ruleEscalationTakenKey } from "../rule-preemption"
import { parseSignal } from "../steps/signals"
import type { SkillFlow } from "@plughub/schemas"

const SID  = "sess-rul02"
const INST = "agente_ia-001"

let marca: string | null = null
let tomada = false

const mockRedis = {
  get:    vi.fn(async (k: string) => (k === ruleEscalationKey(SID) ? marca : null)),
  set:    vi.fn(async (k: string, ..._a: unknown[]) => {
    if (k === ruleEscalationTakenKey(SID)) {
      if (tomada) return null
      tomada = true
    }
    return "OK"
  }),
  del:    vi.fn().mockResolvedValue(1),
  eval:   vi.fn().mockResolvedValue(1),
  expire: vi.fn().mockResolvedValue(1),
}
const mockMcpCall = vi.fn()

const flow: SkillFlow = {
  entry: "consultar",
  steps: [
    {
      id: "consultar", type: "invoke",
      target: { mcp_server: "mcp-server-crm", tool: "customer_get" },
      input: { customer_id: "$.session.customer_id" }, output_as: "cliente",
      on_success: "concluir", on_failure: "concluir",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
  ],
}

// `null` = sem instância. Não usar `undefined`: parâmetro com default o troca por INST, e o
// controle "sem instância" passaria a rodar COM instância (medido: foi o que aconteceu).
function run(instanceId: string | null = INST) {
  return new SkillFlowEngine({ redis: mockRedis as never, mcpCall: mockMcpCall, aiGatewayCall: vi.fn() })
    .run({ tenantId: "t", sessionId: SID, customerId: "c", skillId: "skill_x", flow,
           sessionContext: { customer_id: "c" }, ...(instanceId ? { instanceId } : {}) })
}

const tools = () => mockMcpCall.mock.calls.map(c => c[0])

beforeEach(() => {
  vi.clearAllMocks()
  marca = null
  tomada = false
  mockMcpCall.mockResolvedValue({ ok: true })
})

describe("RUL-02 — a IA pára na fronteira do passo", () => {
  it("marca para ESTA instância: avisa o cliente, escala, e o passo seguinte NÃO roda", async () => {
    marca = JSON.stringify({ instance_id: INST, target_pool: "humano_ret", rule_id: "r1",
                             customer_notice: "Vou te passar para um especialista." })
    const r = await run()
    expect("outcome" in r && r.outcome).toBe("escalated_human")
    expect(tools()).toEqual(["notification_send", "conversation_escalate"])
    const esc = mockMcpCall.mock.calls.find(c => c[0] === "conversation_escalate")![1]
    expect(esc.target_pool).toBe("humano_ret")
    expect(esc.escalation_reason).toBe("rule_escalation")
    const aviso = mockMcpCall.mock.calls[0][1]
    expect(aviso.message).toBe("Vou te passar para um especialista.")
    expect(aviso.visibility).toBe("all")
    if ("pipeline_state" in r) {
      expect(r.pipeline_state.results["escalation_reason"]).toBe("rule_escalation")
      expect(r.pipeline_state.transitions.at(-1)?.to_step).toBe("__rule_escalation__")
    }
  })

  it("sem aviso na regra, não inventa texto — só escala", async () => {
    marca = JSON.stringify({ instance_id: INST, target_pool: "humano_ret", rule_id: "r1" })
    await run()
    expect(tools()).toEqual(["conversation_escalate"])
  })

  it("CONTROLE: marca de OUTRA instância não pára esta run", async () => {
    marca = JSON.stringify({ instance_id: "especialista-009", target_pool: "humano_ret", rule_id: "r1" })
    const r = await run()
    expect("outcome" in r && r.outcome).toBe("resolved")
    expect(tools()).toEqual(["customer_get"])
  })

  it("CONTROLE: marca já tomada (outra réplica) não escala de novo", async () => {
    marca = JSON.stringify({ instance_id: INST, target_pool: "humano_ret", rule_id: "r1" })
    tomada = true
    const r = await run()
    expect("outcome" in r && r.outcome).toBe("resolved")
    expect(tools()).not.toContain("conversation_escalate")
  })

  it("CONTROLE: run sem instância não obedece a marca nenhuma", async () => {
    marca = JSON.stringify({ instance_id: INST, target_pool: "humano_ret", rule_id: "r1" })
    const r = await run(null)
    expect("outcome" in r && r.outcome).toBe("resolved")
  })

  it("escalar falhou: o fluxo segue do passo em que estava, e não tenta de novo", async () => {
    marca = JSON.stringify({ instance_id: INST, target_pool: "humano_ret", rule_id: "r1" })
    mockMcpCall.mockImplementation(async (tool: string) => {
      if (tool === "conversation_escalate") throw new Error("mcp fora")
      return { ok: true }
    })
    const r = await run()
    expect("outcome" in r && r.outcome).toBe("resolved")
    expect(tools().filter(t => t === "conversation_escalate")).toHaveLength(1)
    expect(tools()).toContain("customer_get")
  })
})

describe("RUL-02 — peças", () => {
  it("tomar a marca limpa a fila de sinal da instância (senão desviaria o próximo menu)", async () => {
    marca = JSON.stringify({ instance_id: INST, target_pool: "p", rule_id: "r" })
    const m = await claimRulePreemption(mockRedis as never, SID, INST)
    expect(m?.target_pool).toBe("p")
    expect(mockRedis.del).toHaveBeenCalledWith(`menu:signal:${SID}:${INST}`)
  })

  it("marca ilegível ou sem pool é ignorada, dita", () => {
    expect(parseMark("{nao json")).toBeNull()
    expect(parseMark(JSON.stringify({ instance_id: INST }))).toBeNull()
    expect(parseMark(JSON.stringify({ instance_id: INST, target_pool: "p", customer_notice: "  " }))
      ).toEqual({ instance_id: INST, target_pool: "p", rule_id: "" })
  })

  it("o sinal do bridge é reconhecido como preempção, não como sinal ilegível", () => {
    expect(parseSignal(JSON.stringify({ _rule_preempt: true }))).toEqual({ kind: "preempt" })
  })
})
