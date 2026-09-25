/**
 * complete-step.test.ts — SFE-02: o `issue_status` declarado no `complete` SOBREVIVE ao parse.
 *
 * O defeito medido (2026-09-17/25): `CompleteStepSchema` não declarava o campo e o objeto
 * não era `.strict()`, então o Zod o DESCARTAVA em silêncio. O agent-registry valida com
 * este schema ao gravar — e por isso os 111 `complete` dos snapshots vivos tinham
 * `issue_status` em 0, embora 37 dos 96 do repositório o declarassem. Nenhum segmento de
 * IA jamais recebeu o motivo de fechamento que o autor escreveu.
 *
 * Este arquivo foi escrito e rodado ANTES do conserto, e ficou vermelho — é a prova da
 * inércia que a ficha pedia. As duas metades importam: o campo tem de passar, e chave
 * DESCONHECIDA tem de ser recusada alto, senão o próximo campo some do mesmo jeito.
 */

import { describe, it, expect } from "vitest"
import { CompleteStepSchema, FlowStepSchema } from "./skill"

const base = { id: "fim", type: "complete", outcome: "failed" } as const

describe("SFE-02 — CompleteStepSchema", () => {
  it("preserva o issue_status declarado", () => {
    const r = CompleteStepSchema.parse({ ...base, issue_status: "SLA de análise estourou" })
    expect(r.issue_status).toBe("SLA de análise estourou")
  })

  it("preserva também quando o complete chega pela união de steps (o caminho do registry)", () => {
    const r = FlowStepSchema.parse({ ...base, issue_status: "outbound_drain_failed" }) as Record<string, unknown>
    expect(r["issue_status"]).toBe("outbound_drain_failed")
  })

  it("TESTEMUNHA: sem issue_status continua válido, e o campo fica AUSENTE (nunca inventado)", () => {
    const r = CompleteStepSchema.parse(base)
    expect("issue_status" in r).toBe(false)
  })

  it("issue_status vazio é recusado — o invariante do agent_done é 'nunca vazio'", () => {
    expect(CompleteStepSchema.safeParse({ ...base, issue_status: "" }).success).toBe(false)
  })

  it("chave DESCONHECIDA é recusada alto, nunca descartada calada", () => {
    const r = CompleteStepSchema.safeParse({ ...base, isue_status: "grafia errada" })
    expect(r.success).toBe(false)
  })
})
