/**
 * otp-challenge-refusal.test.ts — PID-10 (2026-09-13)
 *
 * O channel-gateway passou a RECUSAR o desafio de OTP (âncora não-entregável, sem
 * entrega real, âncora não autoritativa para o cliente) com `{sent: false, reason}`
 * e HTTP 200. A tool devolvia esse corpo como sucesso, e os dois skills que a chamam
 * seguem pelo `on_success` para pedir ao cliente um código que ninguém mandou.
 *
 * O `fetch` é substituído: a proposição é o que a TOOL faz com a resposta; a recusa
 * em si é provada contra Postgres e Redis em `probe_otp_gate.sh`.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerWorkflowTools } from "../tools/workflow"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

function tool(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools?.[name]
  if (!reg) throw new Error(`Tool '${name}' not registered`)
  return reg.handler
}

const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, unknown>
const ENTRADA = { tenant_id: "t", customer_id: "cus_a", kind: "phone", value: "+5511999990001" }

describe("PID-10 — otp_challenge: recusa do gateway é erro, não sucesso", () => {
  let server: McpServer
  let enviado: unknown

  function gatewayResponde(body: Record<string, unknown>, status = 200) {
    enviado = undefined
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      enviado = init?.body ? JSON.parse(String(init.body)) : undefined
      return new Response(JSON.stringify(body), { status })
    }))
  }

  beforeEach(() => {
    server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: "t" })
  })
  afterEach(() => vi.unstubAllGlobals())

  it.each(["anchor_not_authoritative", "undeliverable_kind", "delivery_unavailable", "rate_limited"])(
    "sent=false (%s) vira isError com o motivo", async (reason) => {
      gatewayResponde({ sent: false, reason })
      const r = await tool(server, "otp_challenge")(ENTRADA)
      expect(r.isError).toBe(true)
      expect(corpo(r)).toEqual({ error: "otp_not_sent", reason })
    })

  it("controle POSITIVO — sent=true segue como sucesso, com o corpo intacto", async () => {
    // Sem este, os de cima passariam por uma tool que sempre falha.
    gatewayResponde({ sent: true, delivery: "dev_log", challenge_ttl_s: 300 })
    const r = await tool(server, "otp_challenge")(ENTRADA)
    expect(r.isError).toBeFalsy()
    expect(corpo(r)).toMatchObject({ sent: true, delivery: "dev_log" })
  })

  it("repassa o customer_id ao gateway — é contra ELE que a procedência é lida", async () => {
    gatewayResponde({ sent: true, delivery: "dev_log" })
    await tool(server, "otp_challenge")(ENTRADA)
    expect(enviado).toMatchObject({ customer_id: "cus_a", kind: "phone" })
  })

  it("sem customer_id é invalid_input, e o gateway nem é chamado", async () => {
    gatewayResponde({ sent: true })
    const { customer_id: _omit, ...semCliente } = ENTRADA
    const r = await tool(server, "otp_challenge")(semCliente)
    expect(r.isError).toBe(true)
    expect(corpo(r)).toMatchObject({ error: "invalid_input" })
    expect(enviado).toBeUndefined()
  })

  it("HTTP de erro do gateway continua sendo o erro de transporte de antes", async () => {
    gatewayResponde({ detail: "boom" }, 500)
    const r = await tool(server, "otp_challenge")(ENTRADA)
    expect(r.isError).toBe(true)
    expect(corpo(r)).toEqual({ error: "otp_challenge_failed_http_500" })
  })
})
