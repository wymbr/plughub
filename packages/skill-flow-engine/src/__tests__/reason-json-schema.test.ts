/**
 * reason-json-schema.test.ts — SFE-08: o contrato de SAÍDA declarado no step `reason` chega
 * ao ai-gateway INTEIRO.
 *
 * O `output_schema` só leva type/enum/minimum/maximum/required; estrutura rica (itens,
 * descrições, enums aninhados) viaja pelo `json_schema` — inline ou `json_schema_ref` —, que
 * o ai-gateway usa como `input_schema` de tool-use. Até a SFE-07 os revisores declaravam essa
 * estrutura no `output_schema`, que a descartava; a SFE-08 a moveu para `json_schema` inline.
 * Este teste prova o elo engine → gateway: sem ele, um engine que parasse de repassar o
 * `json_schema` deixaria os revisores com o contrato cortado outra vez, sem nada vermelho.
 *
 * Pares: inline ↔ ref; com schema (aceita sem a validação flat) ↔ sem schema (a validação
 * flat vale e reprova); ref que não resolve → fallback DITO no log.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import type { ReasonStep } from "@plughub/schemas"
import { executeReason } from "../steps/reason"

const CONTRATO = {
  type: "object",
  required: ["dimension_reviews"],
  properties: {
    dimension_reviews: {
      type: "array",
      description: "Uma entrada por dimensão revisada",
      items: {
        type: "object",
        required: ["dimension_id", "action", "justification"],
        properties: {
          dimension_id:  { type: "string", description: "ID da dimensão" },
          action:        { type: "string", enum: ["approve", "adjust"] },
          justification: { type: "string", description: "mínimo 20 palavras" },
        },
      },
    },
  },
}

function passo(extra: Record<string, unknown>): ReasonStep {
  return {
    id: "revisar", type: "reason", prompt_id: "p", input: {},
    output_schema: { dimension_reviews: { type: "array", required: true } },
    output_as: "review_result", on_success: "ok", on_failure: "falha",
    ...extra,
  } as unknown as ReasonStep
}

function contexto(resposta: unknown, results: Record<string, unknown> = {}) {
  const aiGatewayCall = vi.fn(async (_req: unknown): Promise<unknown> => resposta)
  const ctx = {
    sessionId: "s-sfe08", tenantId: "t", state: { results }, sessionContext: {},
    aiGatewayCall,
  }
  return { ctx: ctx as never, aiGatewayCall }
}

const argsDe = (m: { mock: { calls: unknown[][] } }) =>
  m.mock.calls[0]![0] as Record<string, unknown>

afterEach(() => { vi.restoreAllMocks() })

describe("SFE-08 — o json_schema do reason chega ao ai-gateway", () => {
  it("inline: o schema vai INTEIRO — itens, enums e descrições", async () => {
    const { ctx, aiGatewayCall } = contexto({ dimension_reviews: [] })
    const r = await executeReason(passo({ json_schema: CONTRATO }), ctx)
    expect(argsDe(aiGatewayCall)["json_schema"]).toEqual(CONTRATO)
    expect(r.next_step_id).toBe("ok")
  })

  it("json_schema_ref: resolvido do pipeline_state e repassado", async () => {
    const { ctx, aiGatewayCall } = contexto(
      { dimension_reviews: [] }, { eval_context: { schema: CONTRATO } })
    await executeReason(passo({ json_schema_ref: "$.pipeline_state.eval_context.schema" }), ctx)
    expect(argsDe(aiGatewayCall)["json_schema"]).toEqual(CONTRATO)
  })

  it("com json_schema a validação flat NÃO se aplica — o gateway é quem valida", async () => {
    // o output_schema exige dimension_reviews; a resposta não o traz e mesmo assim passa,
    // porque com tool-use o contrato é o json_schema e o gateway já o validou
    const { ctx } = contexto({ outra_coisa: 1 })
    const r = await executeReason(passo({ json_schema: CONTRATO }), ctx)
    expect(r.next_step_id).toBe("ok")
  })

  it("CONTROLE: sem json_schema nada é repassado, e a validação flat reprova", async () => {
    const { ctx, aiGatewayCall } = contexto({ outra_coisa: 1 })
    const r = await executeReason(passo({ max_format_retries: 0 }), ctx)
    expect(argsDe(aiGatewayCall)).not.toHaveProperty("json_schema")
    expect(r.next_step_id).toBe("falha")
  })

  it("ref que NÃO resolve: cai no flat, e o log diz qual step e qual referência", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const { ctx, aiGatewayCall } = contexto({ dimension_reviews: [] })
    await executeReason(passo({ json_schema_ref: "$.pipeline_state.nao_existe" }), ctx)
    expect(argsDe(aiGatewayCall)).not.toHaveProperty("json_schema")
    const log = warn.mock.calls.map(c => String(c[0])).join("\n")
    expect(log).toContain("json_schema_ref NÃO resolveu")
    expect(log).toContain("step=revisar")
    expect(log).toContain("ref=$.pipeline_state.nao_existe")
  })
})
