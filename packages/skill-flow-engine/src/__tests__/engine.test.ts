/**
 * engine.test.ts
 * Testes do SkillFlowEngine — fluxos completos, retomada e idempotência.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine }        from "../engine"
import { PipelineStateManager }   from "../state"
import type { SkillFlow }         from "@plughub/schemas"

// ── Mocks ──────────────────────────────────────

const mockRedis = {
  get:  vi.fn().mockResolvedValue(null),
  set:  vi.fn().mockResolvedValue("OK"),
  del:  vi.fn().mockResolvedValue(1),
  // releaseLock and renewLock use Lua scripts via redis.eval().
  // Default: return 1 (success — lock owned and released / renewed).
  eval: vi.fn().mockResolvedValue(1),
}

const mockMcpCall  = vi.fn()
const mockAiCall   = vi.fn()

function makeEngine() {
  return new SkillFlowEngine({
    redis:         mockRedis as never,
    mcpCall:       mockMcpCall,
    aiGatewayCall: mockAiCall,
  })
}

const TENANT = "tenant-test"

beforeEach(() => {
  vi.clearAllMocks()
  // Redis.get → null por padrão (sem state ativo)
  mockRedis.get.mockResolvedValue(null)
  // Redis.set NX para lock retorna "OK" por padrão (lock adquirido)
  mockRedis.set.mockResolvedValue("OK")
})

// ── Flows de teste ─────────────────────────────

const simpleFlow: SkillFlow = {
  entry: "consultar",
  steps: [
    {
      id:         "consultar",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-crm", tool: "customer_get" },
      input:      { customer_id: "$.session.customer_id" },
      output_as:  "cliente",
      on_success: "concluir",
      on_failure: "escalar",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "escalar",  type: "escalate", target: { pool: "especialista" }, context: "pipeline_state" },
  ],
}

const taskFlow: SkillFlow = {
  entry: "delegar",
  steps: [
    {
      id:             "delegar",
      type:           "task",
      target:         { skill_id: "skill_retencao_v1" },
      execution_mode: "sync",
      on_success:     "concluir",
      on_failure:     "escalar",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "escalar",  type: "escalate", target: { pool: "humano" }, context: "pipeline_state" },
  ],
}

const asyncTaskFlow: SkillFlow = {
  entry: "delegar",
  steps: [
    {
      id:             "delegar",
      type:           "task",
      target:         { skill_id: "skill_retencao_v1" },
      execution_mode: "async",
      on_success:     "concluir",
      on_failure:     "escalar",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "escalar",  type: "escalate", target: { pool: "humano" }, context: "pipeline_state" },
  ],
}

const choiceFlow: SkillFlow = {
  entry: "classificar",
  steps: [
    {
      id:        "classificar",
      type:      "reason",
      prompt_id: "prompt_classificacao_v1",
      input:     { mensagem: "$.session.last_message" },
      output_schema: {
        intencao:  { type: "string", enum: ["portabilidade", "cancelamento", "suporte"] },
        confianca: { type: "number", minimum: 0, maximum: 1 },
      },
      output_as:          "classificacao",
      max_format_retries: 1,
      on_success:         "rotear",
      on_failure:         "escalar",
    },
    {
      id:   "rotear",
      type: "choice",
      conditions: [
        { field: "$.pipeline_state.classificacao.intencao", operator: "eq", value: "portabilidade", next: "concluir" },
        { field: "$.pipeline_state.classificacao.confianca", operator: "lt", value: 0.6, next: "escalar" },
      ],
      default: "concluir",
    },
    { id: "concluir", type: "complete", outcome: "resolved" },
    { id: "escalar",  type: "escalate", target: { pool: "humano" }, context: "pipeline_state" },
  ],
}

// ── invoke → complete ──────────────────────────

describe("SkillFlowEngine — invoke → complete", () => {
  it("executa flow simples e encerra com resolved", async () => {
    mockMcpCall.mockResolvedValue({ customer_id: "uuid", tier: "gold" })

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-001",
      customerId:     "customer-001",
      skillId:        "skill_test_v1",
      flow:           simpleFlow,
      sessionContext: { customer_id: "customer-001" },
    })

    expect(result).not.toHaveProperty("error")
    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")
      expect(result.pipeline_state.status).toBe("completed")
      expect(result.pipeline_state.results["cliente"]).toEqual({ customer_id: "uuid", tier: "gold" })
    }
  })

  it("segue on_failure quando invoke lança erro", async () => {
    mockMcpCall
      .mockRejectedValueOnce(new Error("CRM indisponível"))
      .mockResolvedValue({})  // escalate

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-002",
      customerId:     "customer-001",
      skillId:        "skill_test_v1",
      flow:           simpleFlow,
      sessionContext: {},
    })

    if ("outcome" in result) {
      expect(result.outcome).toBe("escalated_human")
    }
  })
})

// ── Retomada após falha ────────────────────────

describe("SkillFlowEngine — retomada após falha do orquestrador", () => {
  it("retoma do current_step_id sem reiniciar do entry", async () => {
    // Simula crash após ter executado "consultar" — pipeline parado em "concluir"
    const crashState = PipelineStateManager.create("skill_test_v1", "concluir")
    mockRedis.get.mockResolvedValue(JSON.stringify(crashState))

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-003",
      customerId:     "customer-001",
      skillId:        "skill_test_v1",
      flow:           simpleFlow,
      sessionContext: {},
    })

    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")
      // Primeira transição deve partir de "concluir", não de "consultar"
      expect(result.pipeline_state.transitions[0]?.from_step).not.toBe("consultar")
    }
  })

  it("retoma task step após crash — não re-dispara agent_delegate", async () => {
    // Simula crash: pipeline in_progress no step "delegar", job_id já persistido
    const crashState = PipelineStateManager.create("skill_task_v1", "delegar")

    // get(pipeline_state) → estado com crash
    // get(job_key) → job_id existente (delegate já foi chamado)
    mockRedis.get
      .mockResolvedValueOnce("OK")      // acquireLock: SET NX → OK (lock adquirido)
      .mockImplementation((key: string) => {
        if (key.includes(":running")) return Promise.resolve(null) // lock não existe
        if (key.includes(":job:"))    return Promise.resolve("job-456")  // job existente
        return Promise.resolve(JSON.stringify(crashState))  // pipeline state
      })

    // agent_delegate_status retorna completed
    mockMcpCall.mockResolvedValue({ status: "completed", outcome: "resolved", result: { ok: true } })

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-crash",
      customerId:     "customer-001",
      skillId:        "skill_task_v1",
      flow:           taskFlow,
      sessionContext: {},
    })

    // agent_delegate NÃO deve ser chamado (job_id já existia)
    const delegateCalls = mockMcpCall.mock.calls.filter(
      c => c[0] === "agent_delegate" && !String(c[0]).includes("status")
    )
    expect(delegateCalls.length).toBe(0)

    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")
    }
  })
})

// ── Idempotência ───────────────────────────────

describe("SkillFlowEngine — idempotência (PRECONDITION_FAILED)", () => {
  it("retorna PRECONDITION_FAILED quando lock está ativo", async () => {
    // SET NX retorna null → lock já existe (outra instância rodando)
    mockRedis.set.mockResolvedValue(null)

    // pipeline state com job ativo
    const activeState = PipelineStateManager.create("skill_test_v1", "delegar")
    const stateWithJob = PipelineStateManager.setResult(
      activeState, "delegar:__job_id__", "job-123"
    )
    mockRedis.get.mockResolvedValue(JSON.stringify(stateWithJob))

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-idem",
      customerId:     "customer-001",
      skillId:        "skill_test_v1",
      flow:           taskFlow,
      sessionContext: {},
    })

    expect(result).toHaveProperty("error", "PRECONDITION_FAILED")
    if ("error" in result) {
      expect(result.active_job_id).toBe("job-123")
    }
  })

  it("permite nova execução após lock liberado", async () => {
    // Primeiro run: lock disponível
    mockRedis.set.mockResolvedValue("OK")
    mockMcpCall
      .mockResolvedValueOnce({ job_id: "job-789", status: "queued" })  // agent_delegate
      .mockResolvedValue({ status: "completed", outcome: "resolved", result: {} }) // agent_delegate_status

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-ok",
      customerId:     "customer-001",
      skillId:        "skill_task_v1",
      flow:           taskFlow,
      sessionContext: {},
    })

    expect(result).not.toHaveProperty("error")
    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")
    }
  })
})

// ── execution_mode: async ──────────────────────

describe("SkillFlowEngine — task com execution_mode async", () => {
  it("retorna awaiting_task quando job ainda não concluiu", async () => {
    mockMcpCall
      .mockResolvedValueOnce({ job_id: "job-async-1", status: "queued" })  // agent_delegate
      .mockResolvedValue({ status: "running" })  // agent_delegate_status (ainda rodando)

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-async-1",
      customerId:     "customer-001",
      skillId:        "skill_task_v1",
      flow:           asyncTaskFlow,
      sessionContext: {},
    })

    if ("outcome" in result) {
      expect(result.outcome).toBe("awaiting_task")
    }
  })

  it("conclui quando retomado e job está completed", async () => {
    // Retomada: pipeline in_progress, job_id já persistido
    const resumeState = PipelineStateManager.create("skill_task_v1", "delegar")

    mockRedis.set.mockResolvedValue("OK")
    mockRedis.get.mockImplementation((key: string) => {
      if (key.includes(":job:")) return Promise.resolve("job-async-2")
      return Promise.resolve(JSON.stringify(resumeState))
    })

    // agent_delegate_status → completed
    mockMcpCall.mockResolvedValue({ status: "completed", outcome: "resolved", result: { done: true } })

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-async-2",
      customerId:     "customer-001",
      skillId:        "skill_task_v1",
      flow:           asyncTaskFlow,
      sessionContext: {},
    })

    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")
    }
    // agent_delegate nunca chamado na retomada
    expect(mockMcpCall.mock.calls.every(c => c[0] !== "agent_delegate" || c[0] === "agent_delegate_status")).toBe(true)
  })
})

// ── reason → choice ───────────────────────────

describe("SkillFlowEngine — reason → choice", () => {
  it("roteia para portabilidade quando reason retorna intencao correta", async () => {
    mockAiCall.mockResolvedValue({ intencao: "portabilidade", confianca: 0.92 })

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-004",
      customerId:     "customer-001",
      skillId:        "skill_choice_v1",
      flow:           choiceFlow,
      sessionContext: { last_message: "quero portabilidade" },
    })

    if ("outcome" in result) {
      expect(result.outcome).toBe("resolved")
    }
  })

  it("escala quando confiança abaixo do threshold", async () => {
    mockAiCall.mockResolvedValue({ intencao: "suporte", confianca: 0.45 })
    mockMcpCall.mockResolvedValue({})  // escalate

    const engine = makeEngine()
    const result = await engine.run({
      tenantId:       TENANT,
      sessionId:      "session-005",
      customerId:     "customer-001",
      skillId:        "skill_choice_v1",
      flow:           choiceFlow,
      sessionContext: { last_message: "..." },
    })

    if ("outcome" in result) {
      expect(result.outcome).toBe("escalated_human")
    }
  })

  it("choice sem nenhuma condition verdadeira e sem default lança erro descritivo, não loop infinito", async () => {
    /**
     * Quando nenhuma condição do choice é satisfeita e o step não tem 'default',
     * o executor retorna next_step_id=undefined. Na próxima iteração, o engine
     * tenta stepMap.get(undefined) que retorna undefined → lança
     * Error("Step não encontrado: undefined") e falha o pipeline de forma
     * descritiva — sem entrar em loop infinito.
     * (engine.ts linhas ~120-124)
     */
    const choiceFlowNoDefault: SkillFlow = {
      entry: "classificar",
      steps: [
        {
          id:                 "classificar",
          type:               "reason",
          prompt_id:          "prompt_v1",
          input:              {},
          output_schema:      { intencao: { type: "string" } as never },
          output_as:          "classificacao",
          max_format_retries: 0,
          on_success:         "rotear",
          on_failure:         "escalar",
        },
        {
          id:         "rotear",
          type:       "choice",
          conditions: [
            {
              field:    "$.pipeline_state.classificacao.intencao",
              operator: "eq" as const,
              value:    "portabilidade",
              next:     "concluir",
            },
          ],
          // 'default' ausente — nenhuma condição será satisfeita com intencao="cancelamento"
        } as unknown as SkillFlow["steps"][number],
        { id: "concluir", type: "complete", outcome: "resolved" as const },
        { id: "escalar",  type: "escalate", target: { pool: "humano" }, context: "pipeline_state" as const },
      ],
    }

    // reason retorna "cancelamento" — nenhuma condição do choice é satisfeita
    mockAiCall.mockResolvedValue({ intencao: "cancelamento" })

    const engine = makeEngine()

    await expect(
      engine.run({
        tenantId:       TENANT,
        sessionId:      "session-choice-no-default",
        customerId:     "customer-001",
        skillId:        "skill_choice_v1",
        flow:           choiceFlowNoDefault,
        sessionContext: { last_message: "quero cancelar" },
      })
    ).rejects.toThrow(/Step não encontrado/)

    // pipeline_state deve ter sido marcado como failed no Redis
    const failCalls = mockRedis.set.mock.calls.filter(
      (c: unknown[]) => typeof c[0] === "string" && String(c[0]).includes(":state:")
    )
    const lastCall = failCalls.at(-1)
    if (lastCall) {
      const state = JSON.parse(lastCall[1] as string) as { status: string }
      expect(state.status).toBe("failed")
    }
  })
})
