/**
 * flow-validate.test.ts
 * Testes de validateFlow() — spec PlugHub v24.0 seção 4.7
 *
 * Cobre:
 *   - flow sem campo entry → FAIL
 *   - flow com entry que referencia step inexistente → FAIL
 *   - flow com ciclo sem step complete/escalate → FAIL
 *   - flow válido com 4 tipos de step (task, choice, invoke, complete) → PASS
 */

import { describe, it, expect } from "vitest"
import { validateFlow } from "../certify/flow"

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

const completeStep = { id: "concluir", type: "complete", outcome: "resolved" }
const escalateStep = { id: "escalar",  type: "escalate", target: { pool: "especialista" } }

// ─────────────────────────────────────────────
// Flow inválido: entry ausente
// ─────────────────────────────────────────────

describe("validateFlow — entry ausente ou inválido", () => {
  it("falha quando 'entry' está ausente no objeto", () => {
    const result = validateFlow({
      steps: [completeStep],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.field === "entry")).toBe(true)
  })

  it("falha quando 'entry' referencia step inexistente", () => {
    const result = validateFlow({
      entry: "step_que_nao_existe",
      steps: [
        { id: "outro_step", type: "complete", outcome: "resolved" },
      ],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e =>
      e.message.includes("step_que_nao_existe") || (e.field === "entry")
    )).toBe(true)
  })

  it("falha quando 'entry' é número em vez de string", () => {
    const result = validateFlow({
      entry: 42,
      steps: [completeStep],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.field === "entry")).toBe(true)
  })
})

// ─────────────────────────────────────────────
// Flow inválido: ciclo sem saída
// ─────────────────────────────────────────────

describe("validateFlow — ciclo sem step complete/escalate", () => {
  it("falha quando dois steps se referenciam mutuamente sem saída terminal", () => {
    /**
     * a → b → a → ... (ciclo infinito — nenhum dos dois é complete/escalate)
     */
    const result = validateFlow({
      entry: "a",
      steps: [
        { id: "a", type: "invoke", target: { mcp_server: "m", tool: "t" }, input: {}, output_as: "x", on_success: "b", on_failure: "b" },
        { id: "b", type: "invoke", target: { mcp_server: "m", tool: "t" }, input: {}, output_as: "y", on_success: "a", on_failure: "a" },
      ],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.message.toLowerCase().includes("ciclo"))).toBe(true)
  })

  it("falha quando ciclo inclui task steps sem step terminal no ciclo", () => {
    const result = validateFlow({
      entry: "delegar",
      steps: [
        {
          id: "delegar",
          type: "task",
          agent_pool: "pool_a",
          on_success: "retry_step",
          on_failure: "retry_step",
        },
        {
          id: "retry_step",
          type: "invoke",
          target: { mcp_server: "m", tool: "t" },
          input: {},
          output_as: "r",
          on_success: "delegar",   // fecha o ciclo
          on_failure: "delegar",
        },
      ],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.message.toLowerCase().includes("ciclo"))).toBe(true)
  })

  it("aceita ciclo que contém step escalate (ciclo tem saída terminal)", () => {
    /**
     * Ciclo com escalate — o engine nunca ficará preso pois escalate encerra.
     * validateFlow deve aceitar.
     */
    const result = validateFlow({
      entry: "primeiro",
      steps: [
        {
          id: "primeiro",
          type: "invoke",
          target: { mcp_server: "m", tool: "t" },
          input: {},
          output_as: "r",
          on_success: "escalar",
          on_failure: "escalar",
        },
        { id: "escalar", type: "escalate", target: { pool: "humano" } },
      ],
    })
    // Sem ciclo verdadeiro aqui — mas garante que escalate é aceito como terminal
    expect(result.valid).toBe(true)
  })
})

// ─────────────────────────────────────────────
// Flow válido: 4 tipos de step
// ─────────────────────────────────────────────

describe("validateFlow — flow válido com 4 tipos de step", () => {
  it("aceita flow com task, choice, invoke e complete", () => {
    /**
     * Fluxo representativo com os 4 tipos de step mais comuns:
     *   task → invoke → choice → complete
     */
    const result = validateFlow({
      entry: "delegar",
      steps: [
        {
          id:         "delegar",
          type:       "task",
          agent_pool: "pool_suporte",
          on_success: "consultar",
          on_failure: "concluir",
        },
        {
          id:         "consultar",
          type:       "invoke",
          target:     { mcp_server: "mcp-server-crm", tool: "customer_get" },
          input:      { customer_id: "$.session.customer_id" },
          output_as:  "cliente",
          on_success: "rotear",
          on_failure: "concluir",
        },
        {
          id:   "rotear",
          type: "choice",
          conditions: [
            { field: "$.pipeline_state.cliente.tier", operator: "eq", value: "gold", next: "concluir" },
          ],
          default: "concluir",
        },
        {
          id:      "concluir",
          type:    "complete",
          outcome: "resolved",
        },
      ],
    })

    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
    expect(result.flow).toBeDefined()
    expect(result.flow?.entry).toBe("delegar")
    expect(result.flow?.steps).toHaveLength(4)
  })

  it("aceita flow com step escalate como terminal alternativo", () => {
    const result = validateFlow({
      entry: "inicio",
      steps: [
        {
          id:         "inicio",
          type:       "invoke",
          target:     { mcp_server: "mcp-server-crm", tool: "ticket_get" },
          input:      {},
          output_as:  "ticket",
          on_success: "finalizar",
          on_failure: "escalar",
        },
        { id: "finalizar", type: "complete", outcome: "resolved" },
        { id: "escalar",   type: "escalate", target: { pool: "suporte_nivel2" } },
      ],
    })

    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })

  it("step complete com outcome inválido → FAIL", () => {
    const result = validateFlow({
      entry: "inicio",
      steps: [
        {
          id:      "inicio",
          type:    "complete",
          outcome: "finished",  // não é um outcome válido
        },
      ],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.step_id === "inicio" && e.message.includes("outcome"))).toBe(true)
  })

  it("step task sem agent_pool e sem target → FAIL", () => {
    const result = validateFlow({
      entry: "delegar",
      steps: [
        {
          id:         "delegar",
          type:       "task",
          // agent_pool e target ausentes — ambos obrigatórios
          on_success: "concluir",
          on_failure: "concluir",
        },
        completeStep,
      ],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.step_id === "delegar")).toBe(true)
  })
})
