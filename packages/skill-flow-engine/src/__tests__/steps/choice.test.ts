/**
 * steps/choice.test.ts
 * Testes da lógica de avaliação de condições JSONPath e @ctx.*.
 */

import { describe, it, expect } from "vitest"
import { executeChoice }        from "../../steps/choice"
import type { StepContext }     from "../../executor"
import type { ChoiceStep, PipelineState, ContextEntry } from "@plughub/schemas"

function makeCtx(results: Record<string, unknown>): StepContext {
  return {
    sessionId:      "s1",
    customerId:     "c1",
    sessionContext: {},
    state: {
      results, retry_counters: {}, transitions: [], status: "in_progress",
      flow_id: "test", current_step_id: "s",
      started_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    } as PipelineState,
    tenantId:        "tenant1",
    mcpCall:         async () => ({}),
    aiGatewayCall:   async () => ({}),
    saveState:       async () => {},
    retryStep:       async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    executeFallback: async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    getJobId:        async () => null,
    setJobId:        async () => {},
    redis:                {} as any,
    clearJobId:           async () => {},
    maskedScope:          {},
    transactionOnFailure: null,
  }
}

/** Minimal ContextStore mock para testes de @ctx.* */
function makeCtxWithStore(
  results: Record<string, unknown>,
  storeEntries: Record<string, ContextEntry>,
): StepContext {
  const ctx = makeCtx(results)
  ctx.contextStore = {
    get:        async (_sid: string, tag: string) => storeEntries[tag] ?? null,
    getValue:   async (_sid: string, tag: string) => storeEntries[tag]?.value ?? null,
    getAll:     async () => storeEntries,
    getByPrefix: async () => storeEntries,
    getMissing: async () => ({ missing: [], low_confidence: [], complete: true }),
    set:        async () => {},
    delete:     async () => {},
    clearSession: async () => {},
  } as any
  return ctx
}

const step: ChoiceStep = {
  id:   "rotear",
  type: "choice",
  conditions: [
    { field: "$.pipeline_state.classificacao.intencao", operator: "eq",  value: "portabilidade", next: "step_port" },
    { field: "$.pipeline_state.classificacao.confianca", operator: "lt",  value: 0.60,            next: "step_escalar" },
    { field: "$.pipeline_state.classificacao.confianca", operator: "gte", value: 0.85,            next: "step_autonomo" },
  ],
  default: "step_hibrido",
}

describe("executeChoice", () => {
  it("retorna o next da primeira condição satisfeita", async () => {
    const ctx = makeCtx({ classificacao: { intencao: "portabilidade", confianca: 0.90 } })
    const result = await executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_port")
  })

  it("avalia segunda condição quando primeira falha", async () => {
    const ctx = makeCtx({ classificacao: { intencao: "cancelamento", confianca: 0.45 } })
    const result = await executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_escalar")
  })

  it("retorna default quando nenhuma condição satisfeita", async () => {
    const ctx = makeCtx({ classificacao: { intencao: "suporte", confianca: 0.72 } })
    const result = await executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_hibrido")
  })

  it("operador contains funciona com string", async () => {
    const stepContains: ChoiceStep = {
      id: "c", type: "choice",
      conditions: [{ field: "$.pipeline_state.msg", operator: "contains", value: "portabilidade", next: "match" }],
      default: "nomatch",
    }
    const ctx = makeCtx({ msg: "quero fazer portabilidade" })
    expect((await executeChoice(stepContains, ctx)).next_step_id).toBe("match")
  })

  it("retorna default quando campo JSONPath não existe", async () => {
    const ctx = makeCtx({})
    const result = await executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_hibrido")
  })

  it("operador exists: true quando tag @ctx presente", async () => {
    const entry: ContextEntry = { value: "123.456.789-00", confidence: 0.95, source: "customer_input", visibility: "agents_only", updated_at: new Date().toISOString() }
    const ctx = makeCtxWithStore({}, { "caller.cpf": entry })
    const stepExists: ChoiceStep = {
      id: "e", type: "choice",
      conditions: [{ field: "@ctx.caller.cpf", operator: "exists", next: "tem_cpf" }],
      default: "sem_cpf",
    }
    const result = await executeChoice(stepExists, ctx)
    expect(result.next_step_id).toBe("tem_cpf")
  })

  it("operador exists: default quando tag @ctx ausente", async () => {
    const ctx = makeCtxWithStore({}, {})
    const stepExists: ChoiceStep = {
      id: "e", type: "choice",
      conditions: [{ field: "@ctx.caller.cpf", operator: "exists", next: "tem_cpf" }],
      default: "sem_cpf",
    }
    const result = await executeChoice(stepExists, ctx)
    expect(result.next_step_id).toBe("sem_cpf")
  })

  it("operador confidence_gte: retorna match quando confiança suficiente", async () => {
    const entry: ContextEntry = { value: "João", confidence: 0.9, source: "mcp_call:crm", visibility: "agents_only", updated_at: new Date().toISOString() }
    const ctx = makeCtxWithStore({}, { "caller.nome": entry })
    const stepConf: ChoiceStep = {
      id: "cf", type: "choice",
      conditions: [{ field: "@ctx.caller.nome", operator: "confidence_gte", value: 0.8, next: "usar_nome" }],
      default: "coletar_nome",
    }
    const result = await executeChoice(stepConf, ctx)
    expect(result.next_step_id).toBe("usar_nome")
  })

  it("operador confidence_gte: default quando confiança insuficiente", async () => {
    const entry: ContextEntry = { value: "João?", confidence: 0.5, source: "ai_inferred:step1", visibility: "agents_only", updated_at: new Date().toISOString() }
    const ctx = makeCtxWithStore({}, { "caller.nome": entry })
    const stepConf: ChoiceStep = {
      id: "cf", type: "choice",
      conditions: [{ field: "@ctx.caller.nome", operator: "confidence_gte", value: 0.8, next: "usar_nome" }],
      default: "coletar_nome",
    }
    const result = await executeChoice(stepConf, ctx)
    expect(result.next_step_id).toBe("coletar_nome")
  })

  // ── `exists` sobre `$.` — o defeito medido em contato real (2026-09-07) ────
  //
  // Ate aqui `exists` existia SO no ramo `@ctx.`; no ramo `$.` caia no `default`
  // do switch e devolvia `false`. Uma condicao que nunca casa nao vira erro num
  // `choice`: vira o `default` do step, que quase sempre e caminho legitimo.
  // Foi assim que o `continuar` do orquestrador terminou o contato com o
  // ponteiro de continuacao PRESENTE no pipeline_state.
  describe("operador exists sobre $. (RET-05/2026-09-07)", () => {
    const passo: ChoiceStep = {
      id: "c", type: "choice",
      conditions: [{ field: "$.pipeline_state.nivel.on_return", operator: "exists", next: "continuar" }],
      default: "finalizar",
    }

    it("o CASO MEDIDO: ponteiro presente leva a continuacao, nao ao default", async () => {
      const ctx = makeCtx({ nivel: { on_return: "pos_atendimento" } })
      expect((await executeChoice(passo, ctx)).next_step_id).toBe("continuar")
    })

    it("ausente cai no default — o controle negativo", async () => {
      const ctx = makeCtx({ nivel: { category_path: "sac.info_plano" } })
      expect((await executeChoice(passo, ctx)).next_step_id).toBe("finalizar")
    })

    it("caminho intermediario ausente tambem cai no default", async () => {
      expect((await executeChoice(passo, makeCtx({}))).next_step_id).toBe("finalizar")
    })

    // ── O CASO QUE CARREGA PESO ────────────────────────────────────────────
    //
    // O criterio e `!== undefined`, NUNCA truthiness. Estes quatro sao valores
    // PRESENTES, e no ramo `@ctx.` `exists` ja os aceitava ("entry presente com
    // qualquer valor"). Uma implementacao com `if (fieldValue)` passaria nos
    // tres testes acima e faria um contador zerado, uma flag `false` ou uma
    // string vazia DESAPARECEREM — e os dois ramos voltariam a discordar, que e
    // exatamente o defeito que este conserto fecha.
    it.each([
      ["zero",          0],
      ["false",         false],
      ["string vazia",  ""],
      ["null explicito", null],
    ])("valor %s é PRESENTE — exists nao e truthiness", async (_rotulo, valor) => {
      const ctx = makeCtx({ nivel: { on_return: valor } })
      expect((await executeChoice(passo, ctx)).next_step_id).toBe("continuar")
    })

    it("paridade com o ramo @ctx: os dois decidem igual para o mesmo fato", async () => {
      const entry: ContextEntry = {
        value: 0, confidence: 1, source: "teste",
        visibility: "agents_only", updated_at: new Date().toISOString(),
      }
      const viaCtx = await executeChoice({
        id: "c", type: "choice",
        conditions: [{ field: "@ctx.x.y", operator: "exists", next: "continuar" }],
        default: "finalizar",
      }, makeCtxWithStore({}, { "x.y": entry }))
      const viaPipeline = await executeChoice({
        id: "c", type: "choice",
        conditions: [{ field: "$.pipeline_state.x.y", operator: "exists", next: "continuar" }],
        default: "finalizar",
      }, makeCtx({ x: { y: 0 } }))
      expect(viaPipeline.next_step_id).toBe(viaCtx.next_step_id)
    })
  })
})
