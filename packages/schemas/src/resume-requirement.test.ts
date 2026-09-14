import { describe, it, expect } from "vitest"
import {
  judgeResumeEvidence, judgeResumeRequirementSteps, RESUME_EVIDENCE_MAX_AGE_S, ResumeRequiresFieldSchema,
  evidenceCustomers,
} from "./resume-requirement"
import { CollectStepSchema, FlowStepSchema } from "./skill"

const NOW = Date.parse("2026-09-13T12:00:00Z")
const entry = (value: string) => JSON.stringify({ value, confidence: 1, updated_at: "2026-09-13T11:59:00Z" })
type Campos = Partial<Record<"status" | "proven_in_session" | "verified_at" | "customer_id", string>>
function hash(fields: Campos, mecanismo = "otp"): Record<string, string> {
  const h: Record<string, string> = {}
  for (const [k, v] of Object.entries(fields)) h[`core.journey.identity.${mecanismo}.${k}`] = entry(v!)
  return h
}
const PROVA = { status: "verified", proven_in_session: "S1", verified_at: "2026-09-13T11:58:00Z", customer_id: "cus_A" }

describe("PID-06 — judgeResumeEvidence: a prova é DESTA sessão, recente e verificada", () => {
  it("controle: prova verificada, desta sessão, dentro da idade → satisfaz", () => {
    expect(judgeResumeEvidence(["otp"], hash(PROVA), { sessionId: "S1", nowMs: NOW, customerId: "cus_A" })).toEqual({ satisfied: true, missing: [] })
  })

  it("a MESMA prova pedida por outra sessão não satisfaz — é o vetor (4)", () => {
    const r = judgeResumeEvidence(["otp"], hash(PROVA), { sessionId: "S2", nowMs: NOW, customerId: "cus_A" })
    expect(r.satisfied).toBe(false)
    expect(r.missing).toEqual([{ mechanism: "otp", reason: "other_session" }])
  })

  it("sem evidência, com status não verificado, vencida ou sem data: recusa nomeando o motivo", () => {
    expect(judgeResumeEvidence(["otp"], {}, { sessionId: "S1", nowMs: NOW, customerId: "cus_A" }).missing[0]!.reason).toBe("not_verified")
    expect(judgeResumeEvidence(["otp"], hash({ ...PROVA, status: "failed" }), { sessionId: "S1", nowMs: NOW, customerId: "cus_A" }).missing[0]!.reason).toBe("not_verified")
    const velha = NOW + (RESUME_EVIDENCE_MAX_AGE_S + 120) * 1000
    expect(judgeResumeEvidence(["otp"], hash(PROVA), { sessionId: "S1", nowMs: velha, customerId: "cus_A" }).missing[0]!.reason).toBe("stale")
    const { verified_at: _, ...semData } = PROVA
    expect(judgeResumeEvidence(["otp"], hash(semData), { sessionId: "S1", nowMs: NOW, customerId: "cus_A" }).missing[0]!.reason).toBe("no_verified_at")
  })

  it("exigência vazia não exige nada; mecanismo desconhecido nunca passa", () => {
    expect(judgeResumeEvidence([], {}, { sessionId: "S1", nowMs: NOW, customerId: "cus_A" }).satisfied).toBe(true)
    expect(judgeResumeEvidence(["biometria"], hash(PROVA), { sessionId: "S1", nowMs: NOW, customerId: "cus_A" }).missing[0]!.reason).toBe("unknown_mechanism")
  })
})

describe("PID-09 — a prova é DESTE cliente, e a chegada pelo WhatsApp satisfaz a posse", () => {
  const OPTS = { sessionId: "S1", nowMs: NOW, customerId: "cus_A" }

  it("prova verificada de OUTRO cliente não satisfaz — é quem chega pelo próprio número pedindo o pedido de outra pessoa", () => {
    expect(judgeResumeEvidence(["otp"], hash(PROVA), { ...OPTS, customerId: "cus_B" }).missing)
      .toEqual([{ mechanism: "otp", reason: "other_customer" }])
  })

  it("sem saber o cliente das pendências, nada satisfaz; e registro sem cliente também não", () => {
    expect(judgeResumeEvidence(["otp"], hash(PROVA), { ...OPTS, customerId: undefined }).missing[0]!.reason).toBe("no_customer")
    const { customer_id: _, ...semCliente } = PROVA
    expect(judgeResumeEvidence(["otp"], hash(semCliente), OPTS).missing[0]!.reason).toBe("no_customer")
  })

  it("a chegada pelo WhatsApp do mesmo cliente, nesta sessão, satisfaz a exigência de OTP", () => {
    expect(judgeResumeEvidence(["otp"], hash(PROVA, "whatsapp"), OPTS)).toEqual({ satisfied: true, missing: [] })
  })

  it("o WhatsApp obedece às mesmas regras: outra sessão, vencido e outro cliente não satisfazem", () => {
    expect(judgeResumeEvidence(["otp"], hash(PROVA, "whatsapp"), { ...OPTS, sessionId: "S2" }).missing[0]!.reason).toBe("other_session")
    const velha = NOW + (RESUME_EVIDENCE_MAX_AGE_S + 120) * 1000
    expect(judgeResumeEvidence(["otp"], hash(PROVA, "whatsapp"), { ...OPTS, nowMs: velha }).missing[0]!.reason).toBe("stale")
    expect(judgeResumeEvidence(["otp"], hash(PROVA, "whatsapp"), { ...OPTS, customerId: "cus_B" }).missing[0]!.reason).toBe("other_customer")
  })

  it("um WhatsApp que falhou não impede o OTP de satisfazer, e o motivo relatado é o do registro mais perto", () => {
    const h = { ...hash({ ...PROVA, status: "failed" }, "whatsapp"), ...hash(PROVA) }
    expect(judgeResumeEvidence(["otp"], h, OPTS).satisfied).toBe(true)
    const h2 = { ...hash({ ...PROVA, status: "failed" }, "whatsapp"), ...hash({ ...PROVA, customer_id: "cus_B" }) }
    expect(judgeResumeEvidence(["otp"], h2, OPTS).missing).toEqual([{ mechanism: "otp", reason: "other_customer" }])
  })

  it("`whatsapp` não é EXIGÍVEL por nome: a lista aceita só `otp`", () => {
    expect(ResumeRequiresFieldSchema.safeParse(["otp"]).success).toBe(true)
    expect(ResumeRequiresFieldSchema.safeParse(["whatsapp"]).success).toBe(false)
    expect(judgeResumeEvidence(["whatsapp"], hash(PROVA, "whatsapp"), OPTS).missing[0]!.reason).toBe("unknown_mechanism")
  })

  it("evidenceCustomers lista os clientes com prova no hash, de qualquer mecanismo", () => {
    const h = { ...hash(PROVA), ...hash({ ...PROVA, customer_id: "cus_W" }, "whatsapp") }
    expect(evidenceCustomers(h).sort()).toEqual(["cus_A", "cus_W"])
    expect(evidenceCustomers({})).toEqual([])
  })
})

describe("PID-06 — judgeResumeRequirementSteps: a config do slot contém o piso do skill (D7)", () => {
  const step = (extra: Record<string, unknown>) => ({ id: "aprovar", type: "delegate", customer_resumable: true, ...extra })

  it("controle: ref com a config contendo o piso → sem violação; [] é válido sem piso", () => {
    expect(judgeResumeRequirementSteps([step({ resume_requires: "$.config.resume_requires", resume_requires_floor: ["otp"] })],
      { resume_requires: ["otp"] })).toEqual([])
    expect(judgeResumeRequirementSteps([step({ resume_requires: "$.config.resume_requires" })], { resume_requires: [] })).toEqual([])
  })

  it("config abaixo do piso é recusada, nunca ajustada", () => {
    const v = judgeResumeRequirementSteps([step({ resume_requires: "$.config.resume_requires", resume_requires_floor: ["otp"] })],
      { resume_requires: [] })
    expect(v.map(x => x.error)).toEqual(["resume_requires_abaixo_do_piso"])
    expect(v[0]!.message).toContain("falta otp")
  })

  it("config AUSENTE é erro, mesmo sem piso", () => {
    expect(judgeResumeRequirementSteps([step({ resume_requires: "$.config.resume_requires" })], {}).map(x => x.error))
      .toEqual(["resume_requires_ausente_na_config"])
  })

  it("valor inválido, piso sem exigência e exigência fora de pendência de cliente são recusados", () => {
    expect(judgeResumeRequirementSteps([step({ resume_requires: "$.config.r" })], { r: ["senha"] })[0]!.error).toBe("resume_requires_invalido")
    expect(judgeResumeRequirementSteps([step({ resume_requires_floor: ["otp"] })], {})[0]!.error).toBe("piso_sem_resume_requires")
    expect(judgeResumeRequirementSteps([step({ customer_resumable: false, resume_requires: ["otp"] })], {})[0]!.error)
      .toBe("resume_requires_sem_pendencia")
  })

  it("literal no step também é julgado contra o piso", () => {
    expect(judgeResumeRequirementSteps([step({ resume_requires: [], resume_requires_floor: ["otp"] })], {})[0]!.error)
      .toBe("resume_requires_abaixo_do_piso")
  })

  it("steps sem os campos não são população: fluxo antigo passa intacto", () => {
    expect(judgeResumeRequirementSteps([{ id: "x", type: "notify" }, step({})], {})).toEqual([])
  })
})

describe("PID-06 — o campo cabe nos steps que criam pendência de cliente", () => {
  it("delegate e collect aceitam lista e ref $.config; recusam ref de runtime", () => {
    const delegate = { id: "d", type: "delegate", pool: "p", customer_resumable: true,
      resume_requires: "$.config.resume_requires", resume_requires_floor: ["otp"],
      on_resume: { next: "a" }, on_timeout: { next: "b" } }
    const r = FlowStepSchema.safeParse(delegate)
    expect(r.success, JSON.stringify(r.success ? "" : r.error.issues)).toBe(true)
    // o schema NÃO pode descartar o campo em silêncio (objeto zod não-estrito remove chave desconhecida)
    expect(r.success && (r.data as Record<string, unknown>)["resume_requires"]).toBe("$.config.resume_requires")
    expect(r.success && (r.data as Record<string, unknown>)["resume_requires_floor"]).toEqual(["otp"])
    expect(ResumeRequiresFieldSchema.safeParse(["otp"]).success).toBe(true)
    expect(ResumeRequiresFieldSchema.safeParse("@ctx.session.exige").success).toBe(false)
    expect(ResumeRequiresFieldSchema.safeParse("$.pipeline_state.x").success).toBe(false)
    expect(CollectStepSchema.shape.resume_requires).toBeDefined()
  })
})
