/**
 * a2a-channel.test.ts — AAS-01 (2026-10-01): o canal `a2a` (adr-a2a-server-binding D1, D3).
 *
 * PROPOSIÇÃO: `a2a` é canal válido, roda o perfil `agent` (conversa, não workflow), ainda NÃO
 * declara capacidade (o adapter é a AAS-06), e o descritor do pool recusa contrato vazio.
 */
import { describe, expect, it } from "vitest"

import { CHANNEL_CAPABILITIES, channelSatisfies } from "./channel-capabilities"
import { ChannelSchema } from "./common"
import { PoolA2ADescriptorSchema, PoolRegistrationSchema } from "./agent-registry"
import { skillProfileFor } from "./skill-profile"

const descritor = {
  display_name:    "Segunda via",
  description:     "Emite a segunda via de um boleto.",
  input_schema:    { type: "object" },
  output_schema:   { type: "object" },
  skills:          [{ id: "segunda_via", name: "Segunda via", description: "Emite." }],
  principal_kinds: ["partner"],
}

describe("canal a2a", () => {
  it("é valor do ChannelSchema", () => {
    expect(ChannelSchema.safeParse("a2a").success).toBe(true)
  })

  it("roda o perfil `agent` — conversa, não workflow (D1)", () => {
    expect(skillProfileFor(["a2a"])).toBe("agent")
    expect(skillProfileFor(["webhook"])).toBe("workflow")   // controle: a regra não ficou constante
  })

  it("não declara capacidade até o adapter existir (AAS-06)", () => {
    expect(CHANNEL_CAPABILITIES.a2a).toEqual([])
    expect(channelSatisfies("a2a", ["text"])).toBe(false)
    expect(channelSatisfies("webchat", ["text"])).toBe(true)  // controle
  })
})

describe("descritor a2a do pool", () => {
  it("aceita o mínimo e aplica os defaults", () => {
    const r = PoolA2ADescriptorSchema.parse(descritor)
    expect(r.discoverable).toBe(false)
    expect(r.skills[0]!.tags).toEqual([])
  })

  it.each([
    ["schema sem type", { input_schema: { properties: {} } }],
    ["output vazio", { output_schema: {} }],
    ["nome vazio", { display_name: "" }],
    ["skill fora de snake_case", { skills: [{ id: "Segunda Via", name: "x", description: "y" }] }],
    ["principal repetido", { principal_kinds: ["partner", "partner"] }],
  ])("recusa %s", (_n, troca) => {
    expect(PoolA2ADescriptorSchema.safeParse({ ...descritor, ...troca }).success).toBe(false)
  })

  it("o pool aceita o bloco e `null` (limpar no PUT)", () => {
    const base = { pool_id: "segunda_via", channel_types: ["a2a"], sla_target_ms: 1000 }
    expect(PoolRegistrationSchema.safeParse({ ...base, a2a: descritor }).success).toBe(true)
    expect(PoolRegistrationSchema.safeParse({ ...base, a2a: null }).success).toBe(true)
  })
})
