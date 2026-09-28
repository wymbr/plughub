/**
 * DUR-01 F3 — o `menu_submit` do Console e a resposta do atendente avisam o bridge.
 *
 * Sem o aviso, a resposta dada no Console fica na lista e a conversa ESTACIONADA só acorda
 * no prazo do menu. O que se confere aqui é o contrato no LEITOR: tópico `menu.wake`, chave
 * de partição = sessão, e o corpo aceito pelo `MenuWakeEventSchema` — o mesmo que o bridge
 * espera. Um aviso que o schema recusa não é publicado, e isso é dito (`false`).
 */
import { describe, it, expect } from "vitest"
import { MenuWakeEventSchema } from "@plughub/schemas"
import { publishMenuWake } from "../lib/menu-wake"
import type { KafkaProducer } from "../infra/kafka"

function produtor() {
  const enviados: Array<{ topic: string; message: Record<string, unknown>; key?: string }> = []
  const kafka: KafkaProducer = {
    async publish(topic, message, key) { enviados.push({ topic, message, ...(key ? { key } : {}) }) },
    async disconnect() {},
  }
  return { kafka, enviados }
}

describe("publishMenuWake", () => {
  it("publica em menu.wake, com a sessão como chave, um corpo que o schema do leitor aceita", async () => {
    const { kafka, enviados } = produtor()
    const ok = await publishMenuWake(kafka, {
      tenant_id: "tenant_demo", session_id: "s1", field: "sac_ia-001", reason: "menu_submit",
    })
    expect(ok).toBe(true)
    expect(enviados).toHaveLength(1)
    expect(enviados[0]!.topic).toBe("menu.wake")
    expect(enviados[0]!.key).toBe("s1")
    expect(MenuWakeEventSchema.safeParse(enviados[0]!.message).success).toBe(true)
  })

  it("aviso inválido (campo vazio) NÃO é publicado, e a falha é devolvida", async () => {
    const { kafka, enviados } = produtor()
    const ok = await publishMenuWake(kafka, {
      tenant_id: "tenant_demo", session_id: "s1", field: "", reason: "menu_submit",
    })
    expect(ok).toBe(false)
    expect(enviados).toHaveLength(0)
  })

  it("Kafka fora não derruba quem chamou: a resposta já está na lista", async () => {
    const kafka: KafkaProducer = {
      async publish() { throw new Error("broker indisponível") },
      async disconnect() {},
    }
    await expect(publishMenuWake(kafka, {
      tenant_id: "t", session_id: "s1", field: "_default_", reason: "agent_reply",
    })).resolves.toBe(false)
  })
})
