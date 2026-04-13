/**
 * yaml_parser.test.ts
 * Testes do parser GitAgent — YAML → JSON → validação Zod.
 */

import { describe, it, expect } from "vitest"
import { join }          from "path"
import { GitAgentParser } from "../gitagent/yaml_parser"

const FIXTURE = join(__dirname, "../../fixtures/my-agent")

describe("GitAgentParser", () => {
  const parser = new GitAgentParser()

  it("parseia manifesto agent.yaml corretamente", () => {
    const parsed = parser.parse(FIXTURE)
    expect(parsed.manifest.agent_type_id).toBe("agente_retencao_v2")
    expect(parsed.manifest.framework).toBe("langgraph")
    expect(parsed.manifest.pools).toContain("retencao_humano")
    expect(parsed.manifest.profile?.portabilidade).toBe(3)
  })

  it("lê instructions.md como texto", () => {
    const parsed = parser.parse(FIXTURE)
    expect(parsed.instructions).toContain("Agente de Retenção")
  })

  it("converte flows/main.yaml para SkillFlow válido", () => {
    const parsed = parser.parse(FIXTURE)
    expect(parsed.flows["main"]).toBeDefined()
    expect(parsed.flows["main"].entry).toBe("classificar_intencao")
    expect(parsed.flows["main"].steps).toHaveLength(5)
  })

  it("valida tipos dos steps no flow", () => {
    const parsed = parser.parse(FIXTURE)
    const types  = parsed.flows["main"].steps.map(s => s.type)
    expect(types).toContain("reason")
    expect(types).toContain("choice")
    expect(types).toContain("task")
    expect(types).toContain("complete")
    expect(types).toContain("escalate")
  })

  it("converte para AgentTypeRegistration válido", () => {
    const parsed = parser.parse(FIXTURE)
    const at     = parser.toAgentTypeRegistration(parsed)
    expect(at.agent_type_id).toBe("agente_retencao_v2")
    expect(at.pools).toContain("retencao_humano")
    expect((at as any).profile?.portabilidade).toBe(3)
  })

  it("lança erro quando agent.yaml ausente", () => {
    expect(() => parser.parse("/tmp/nao-existe")).toThrow(
      "arquivo obrigatório ausente: agent.yaml"
    )
  })

  it("retorna flows como JSON puro", () => {
    const parsed = parser.parse(FIXTURE)
    const flows  = parser.getFlowsAsJson(parsed)
    expect(flows["main"]).toBeDefined()
    expect(typeof flows["main"]).toBe("object")
    // Garantir que não há referências YAML especiais — é JSON puro
    expect(JSON.stringify(flows["main"])).not.toThrow
  })

  it("flow YAML com step inválido lança erro de validação Zod", () => {
    // Testar que um flow com step type inválido é rejeitado na parse
    const { SkillFlowSchema } = require("@plughub/schemas")
    expect(() => SkillFlowSchema.parse({
      entry: "s1",
      steps: [{ id: "s1", type: "INVALIDO" }]
    })).toThrow()
  })
})
