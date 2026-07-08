import { describe, it, expect } from "vitest"
import { SkillConfigParamSchema, SkillSchema } from "./skill"

describe("SkillConfigParamSchema", () => {
  it("applies defaults (type=string, required=false)", () => {
    const p = SkillConfigParamSchema.parse({ key: "form_id" })
    expect(p.type).toBe("string")
    expect(p.required).toBe(false)
    expect(p.source).toBeUndefined()
  })

  it("accepts a system-source hint (open string, not validated against a closed set)", () => {
    const p = SkillConfigParamSchema.parse({ key: "form_id", source: "dialogforms", required: true })
    expect(p.source).toBe("dialogforms")
    expect(p.required).toBe(true)
    // an unknown/typo'd source is still accepted at the schema layer (UI degrades to text)
    expect(SkillConfigParamSchema.parse({ key: "form_id", source: "dialogfroms" }).source).toBe("dialogfroms")
  })

  it("accepts static options and numeric bounds", () => {
    const p = SkillConfigParamSchema.parse({
      key: "max_attempts", type: "number", default: 2, min: 1, max: 5,
      options: [{ value: "1" }, { value: "2", label: "two" }],
    })
    expect(p.type).toBe("number")
    expect(p.min).toBe(1)
    expect(p.options).toHaveLength(2)
  })

  it("rejects a non-identifier key", () => {
    expect(SkillConfigParamSchema.safeParse({ key: "Form-Id" }).success).toBe(false)
    expect(SkillConfigParamSchema.safeParse({ key: "1form" }).success).toBe(false)
  })

  it("is optional on SkillSchema and coexists with interface", () => {
    const base = {
      skill_id: "skill_survey_multi_v1",
      name: "Survey multi",
      version: "1",
      description: "d",
      classification: { type: "orchestrator" as const },
      // orchestrator skills require a flow (SkillSchema .refine)
      flow: { entry: "done", steps: [{ id: "done", type: "complete", outcome: "resolved" }] },
    }
    // absent → valid
    expect(SkillSchema.safeParse(base).success).toBe(true)
    // present → carried through
    const parsed = SkillSchema.parse({
      ...base,
      config_params: [{ key: "form_id", source: "dialogforms", required: true }],
    })
    expect(parsed.config_params?.[0]?.key).toBe("form_id")
  })
})
