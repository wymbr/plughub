/**
 * menu-collect-payload.test.ts — VOZ-05 fatia 5a: os parâmetros de coleta por voz/teclado
 * chegam ao CANAL.
 *
 * O `notification_send` valida o `menu` com Zod, e Zod DESCARTA chave não declarada: sem
 * `collect` no schema da tool, o motor mandaria os parâmetros e o gateway receberia um menu sem
 * eles — sem erro em lugar nenhum. A asserção é sobre o que foi PUBLICADO em
 * `conversations.outbound`, que é o que o gateway lê; o controle é o menu sem `collect`.
 */
import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerBpmTools } from "../tools/bpm"
import { createCapturingKafkaProducer } from "../infra/kafka"
import type { CapturingKafkaProducer } from "../infra/kafka"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }
const SID = "sess_voz05_5a"

describe("VOZ-05 5a — collect viaja no menu.payload", () => {
  let kafka: CapturingKafkaProducer
  let call: (input: unknown) => Promise<ToolResponse>

  beforeEach(async () => {
    const redis = new RedisMock()
    kafka = createCapturingKafkaProducer()
    const mcp = new McpServer({ name: "test-voz05-5a", version: "0.0.1" })
    registerBpmTools(mcp, { redis, kafka })
    await redis.set(`session:${SID}:meta`, JSON.stringify({ channel: "webrtc", tenant_id: "tenant_test" }))
    const reg = (mcp as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
      ._registeredTools!["notification_send"]!
    // a tool LANÇA na validação (o servidor MCP converte em erro) — mesma forma dos vizinhos
    call = async (i) => {
      try { return await reg.handler(i) } catch (e) { return { isError: true, content: [{ type: "text", text: String(e) }] } }
    }
  })

  const payloads = () => kafka.events
    .filter(e => e.topic === "conversations.outbound" && e.message["type"] === "menu.payload")
    .map(e => e.message)

  const COLLECT = { input: ["dtmf", "voice"], first_input_timeout_s: 8, max_digits: 1, echo: "plain", barge_in: false }

  it("o gateway recebe os parâmetros de coleta", async () => {
    const r = await call({ session_id: SID, message: "Para fatura tecle 1",
      menu: { interaction: "button", options: [{ id: "fatura", label: "Fatura" }], collect: COLLECT } })
    expect(r.isError).toBeFalsy()
    const [p] = payloads()
    expect(p?.["collect"]).toEqual(COLLECT)
  })

  it("CONTROLE: menu sem collect publica collect nulo", async () => {
    await call({ session_id: SID, message: "Escolha",
      menu: { interaction: "button", options: [{ id: "fatura", label: "Fatura" }] } })
    const [p] = payloads()
    expect(p).toBeDefined()
    expect(p?.["collect"]).toBeNull()
  })

  it("collect inválido é recusado pela tool, e nada sai ao canal", async () => {
    const r = await call({ session_id: SID, message: "Escolha",
      menu: { interaction: "button", options: [], collect: { input: [] } } })
    expect(r.isError).toBe(true)
    expect(payloads()).toEqual([])
  })
})
