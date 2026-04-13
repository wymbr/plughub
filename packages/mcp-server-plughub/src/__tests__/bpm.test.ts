/**
 * bpm.test.ts
 * Testes das tools de BPM — validação de input e contrato de saída.
 * Spec: PlugHub v24.0 seção 9.4
 */

import { describe, it, expect } from "vitest"
import { createServer }         from "../server"

describe("Tools BPM — validação de input", () => {
  const server = createServer()

  it("conversation_start rejeita customer_id com formato inválido", async () => {
    await expect(
      (server as unknown as { callTool: (name: string, input: unknown) => Promise<unknown> })
        .callTool("conversation_start", {
          channel:     "chat",
          customer_id: "nao-uuid",
          tenant_id:   "tenant_test",
        })
    ).rejects.toThrow()
  })

  it("conversation_start aceita payload mínimo válido", async () => {
    const result = await (server as unknown as { callTool: (name: string, input: unknown) => Promise<{ content: Array<{ text: string }> }> })
      .callTool("conversation_start", {
        channel:     "chat",
        customer_id: "550e8400-e29b-41d4-a716-446655440000",
        tenant_id:   "tenant_test",
      })
    const body = JSON.parse(result.content[0]!.text)
    expect(body).toHaveProperty("session_id")
    expect(body).toHaveProperty("status", "routing")
    expect(body).toHaveProperty("started_at")
  })

  it("conversation_start aceita process_context opcional", async () => {
    const result = await (server as unknown as { callTool: (name: string, input: unknown) => Promise<{ content: Array<{ text: string }> }> })
      .callTool("conversation_start", {
        channel:     "whatsapp",
        customer_id: "550e8400-e29b-41d4-a716-446655440000",
        tenant_id:   "tenant_test",
        process_context: {
          process_id:       "proc_001",
          process_instance: "inst_001",
          status:           "running",
        },
      })
    const body = JSON.parse(result.content[0]!.text)
    expect(body).toHaveProperty("session_id")
  })

  it("conversation_end rejeita reason inválido", async () => {
    await expect(
      (server as unknown as { callTool: (name: string, input: unknown) => Promise<unknown> })
        .callTool("conversation_end", {
          session_id: "550e8400-e29b-41d4-a716-446655440000",
          tenant_id:  "tenant_test",
          reason:     "invalid_reason",
        })
    ).rejects.toThrow()
  })

  it("rule_dry_run retorna estrutura de simulação", async () => {
    const result = await (server as unknown as { callTool: (name: string, input: unknown) => Promise<{ content: Array<{ text: string }> }> })
      .callTool("rule_dry_run", {
        tenant_id: "tenant_test",
        rule: {
          name:        "churn_escalation",
          expression:  { sentiment_below: -0.5, churn_risk_above: 0.7 },
          target_pool: "retencao_especialista",
        },
        history_window_days: 7,
      })
    const body = JSON.parse(result.content[0]!.text)
    expect(body).toHaveProperty("rule_name", "churn_escalation")
    expect(body).toHaveProperty("simulation")
    expect(body.simulation).toHaveProperty("total_conversations")
  })
})
