/**
 * SFE-07 — campo desconhecido no `output_schema` do reason e no step `menu` é RECUSADO,
 * nunca descartado. Os testes vêm em par: o campo que o contrato entende passa (senão o
 * `.strict()` teria quebrado o autor comum), o que ele não entende reprova NOMEANDO.
 */
import { describe, it, expect } from "vitest"
import { SkillFlowSchema, MenuStepSchema } from "./skill"

const reason = (field: Record<string, unknown>) => ({
  entry: "r",
  steps: [
    { id: "r", type: "reason", prompt_id: "p", input: {}, output_schema: { x: field },
      output_as: "o", on_success: "fim", on_failure: "fim" },
    { id: "fim", type: "complete", outcome: "resolved" },
  ],
})

const menu = (extra: Record<string, unknown> = {}) => ({
  id: "m", type: "menu", interaction: "text", prompt: "Diga",
  output_as: "resp", on_success: "a", on_failure: "b", ...extra,
})

describe("SFE-07 — reason.output_schema", () => {
  it("TESTEMUNHA: os cinco atributos do contrato passam", () => {
    const r = SkillFlowSchema.safeParse(reason({ type: "number", minimum: 0, maximum: 10, required: true }))
    expect(r.success).toBe(true)
    const e = SkillFlowSchema.safeParse(reason({ type: "string", enum: ["a", "b"] }))
    expect(e.success).toBe(true)
  })

  it.each(["description", "items", "properties", "minItems", "nullable"])(
    "`%s` é RECUSADO — o ai-gateway nunca o levaria ao modelo",
    (k) => {
      const r = SkillFlowSchema.safeParse(reason({ type: "array", [k]: k === "minItems" ? 1 : k === "nullable" ? true : { type: "string" } }))
      expect(r.success).toBe(false)
      if (!r.success) expect(JSON.stringify(r.error.issues)).toContain(k)
    },
  )
})

describe("SFE-07 — step menu", () => {
  it("TESTEMUNHA: um menu comum passa", () => {
    expect(MenuStepSchema.safeParse(menu()).success).toBe(true)
  })

  it("`context_tags` num menu é RECUSADO — o menu não escreve tag", () => {
    const r = MenuStepSchema.safeParse(menu({ context_tags: { outputs: { email: { tag: "caller.email" } } } }))
    expect(r.success).toBe(false)
    if (!r.success) expect(JSON.stringify(r.error.issues)).toContain("context_tags")
  })

  it("o default de `timeout_s` continua sendo aplicado (o .strict() não mexe em default)", () => {
    const r = MenuStepSchema.safeParse(menu())
    expect(r.success && r.data.timeout_s).toBe(300)
  })
})
