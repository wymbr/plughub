import { describe, expect, it } from "vitest"
import { AuditAccessEventSchema } from "./audit-access"

// Forma copiada de `channel-gateway/recording_router.py::_audit` (datetime.isoformat() do Python).
const escuta = {
  event_id: "4f1b1b2e-8f55-4c61-9a37-7d3c2a6f0b11", tenant_id: "tenant_demo",
  actor_sub: "u-1", actor_kind: "user", endpoint: "channel-gateway:recording.listen",
  target_kind: "recording", target_id: "s-1/9d2c…", result: "ok", row_count: 1,
  accessed_at: "2026-09-18T12:00:00.123456+00:00",
}

describe("AuditAccessEventSchema (VOZ-36)", () => {
  it("aceita a escuta concedida e a recusa sem credencial", () => {
    expect(AuditAccessEventSchema.safeParse(escuta).success).toBe(true)
    const anon = { ...escuta, actor_sub: "", actor_kind: "anonymous", result: "denied", row_count: 0 }
    expect(AuditAccessEventSchema.safeParse(anon).success).toBe(true)
  })

  it("recusa campo a mais — a trilha diz quem acessou o quê, nunca o conteúdo", () => {
    expect(AuditAccessEventSchema.safeParse({ ...escuta, transcript: "..." }).success).toBe(false)
  })

  it("recusa result fora do domínio e alvo vazio", () => {
    expect(AuditAccessEventSchema.safeParse({ ...escuta, result: "maybe" }).success).toBe(false)
    expect(AuditAccessEventSchema.safeParse({ ...escuta, target_id: "" }).success).toBe(false)
  })
})
