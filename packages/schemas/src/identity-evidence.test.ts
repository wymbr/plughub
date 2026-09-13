import { describe, it, expect } from "vitest"
import {
  isReservedIdentityTag, identityEvidenceTag, RESERVED_IDENTITY_PREFIXES, IDENTITY_EVIDENCE_FIELDS,
} from "./identity-evidence"
import { resolveContextStore, DEFAULT_CONTEXT_MAP, buildContextTagIndex, resolveContextTag } from "./context-map"
import { SESSION_BOUND_TOOLS } from "./session-bound-tools"

describe("PID-02 — evidência de identidade reservada", () => {
  it("reserva os dois ramos, e só eles", () => {
    expect([...RESERVED_IDENTITY_PREFIXES].sort()).toEqual(["core.identity.", "core.journey.identity."])
    expect(isReservedIdentityTag("core.journey.identity.otp.status")).toBe(true)
    expect(isReservedIdentityTag("core.identity.x")).toBe(true)
  })

  it("controle: o core.* que fluxos já escrevem continua livre (skill_limite_processo_v1, skill_revisao_treplica_v1)", () => {
    for (const t of ["core.workflow.dialog_form_id", "core.workflow.max_rounds", "core.journey.pedido", "journey.identity.x", "core.identityx"]) {
      expect(isReservedIdentityTag(t)).toBe(false)
    }
  })

  it("as tags de evidência vivem na JOURNEY (D5)", () => {
    for (const f of IDENTITY_EVIDENCE_FIELDS) {
      expect(resolveContextStore(identityEvidenceTag("otp", f))).toBe("journey")
    }
  })

  it("o mapa as reconhece como família dinâmica (5 níveis não cabem em escopo.domínio.campo)", () => {
    const idx = buildContextTagIndex(DEFAULT_CONTEXT_MAP)
    expect(resolveContextTag(identityEvidenceTag("otp", "status"), idx).origin).toBe("dynamic")
  })

  it("otp_challenge e otp_verify são tools ligadas à sessão — a prova precisa saber em qual sessão ocorreu", () => {
    expect(SESSION_BOUND_TOOLS).toContain("otp_challenge")
    expect(SESSION_BOUND_TOOLS).toContain("otp_verify")
  })
})
