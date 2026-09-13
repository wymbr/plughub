/**
 * identity-floor.test.ts — PID-06 (D7). O portão do deploy: a exigência de retomada
 * efetiva do slot contém o piso que o skill declara. A regra tem suíte própria em
 * `@plughub/schemas`; aqui se prova a FORMA do veredicto que as rotas devolvem, com o
 * par positivo de cada recusa.
 */
import { describe, it, expect } from "vitest"
import { judgeIdentityFloor } from "../lib/identity-floor"

const CTX = { poolId: "limite_processo", skillId: "skill_limite_processo_v1" }
const aprovar = (extra: Record<string, unknown> = {}) => ({
  steps: [
    { id: "inicio", type: "invoke" },
    { id: "aprovar", type: "delegate", customer_resumable: true,
      resume_requires: "$.config.resume_requires", resume_requires_floor: ["otp"], ...extra },
  ],
})

describe("judgeIdentityFloor", () => {
  it("controle: config contém o piso → ok", () => {
    expect(judgeIdentityFloor(aprovar(), { resume_requires: ["otp"] }, CTX)).toEqual({ kind: "ok" })
  })

  it("config abaixo do piso → block nomeando step, pool, skill e o que falta", () => {
    const v = judgeIdentityFloor(aprovar(), { resume_requires: [] }, CTX)
    expect(v.kind).toBe("block")
    if (v.kind !== "block") return
    expect(v.error).toBe("resume_requires_abaixo_do_piso")
    expect(v.message).toContain("aprovar")
    expect(v.message).toContain("limite_processo")
    expect(v.message).toContain("falta otp")
  })

  it("config sem a chave → block (ausente é erro, nunca 'sem exigência')", () => {
    const v = judgeIdentityFloor(aprovar(), { outra: 1 }, CTX)
    expect(v.kind === "block" && v.error).toBe("resume_requires_ausente_na_config")
  })

  it("fluxo sem os campos, ou snapshot ausente → ok (não é população deste portão)", () => {
    expect(judgeIdentityFloor({ steps: [{ id: "x", type: "delegate", customer_resumable: true }] }, {}, CTX)).toEqual({ kind: "ok" })
    expect(judgeIdentityFloor(null, {}, CTX)).toEqual({ kind: "ok" })
  })
})
