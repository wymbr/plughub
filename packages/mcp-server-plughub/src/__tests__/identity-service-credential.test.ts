/**
 * identity-service-credential.test.ts — IDN-06 (2026-09-13)
 *
 * As rotas `/identity/*` e `/pending/*` do channel-gateway passaram a exigir
 * `X-Service-Token`. Duas proposições sobre as tools que as chamam:
 *
 *  1. TODA chamada de identidade/pendência leva o header — as oito, não "a maioria".
 *     Uma que esqueça degrada muda: o gateway recusa e a tool responde "sem pendência".
 *  2. Credencial recusada (401/403) NÃO vira `found: false`: é `isError` nomeado. Sem
 *     isto, um token faltando no deploy fazia o cliente com pendência ouvir que não
 *     tinha nenhuma, e nada ficava vermelho.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerWorkflowTools } from "../tools/workflow"
import { signSessionBoundToken } from "../infra/jwt"

// PID-01: as tools de retomada exigem o token ligado à sessão.
const SESSAO = signSessionBoundToken({ tenant_id: "t", session_id: "s", instance_id: "i", skill_id: "k" })

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

function tool(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools?.[name]
  if (!reg) throw new Error(`Tool '${name}' not registered`)
  return reg.handler
}

const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, unknown>
const ANCORAS = [{ kind: "phone", value: "+5511999990001" }]
const TOKEN = "svc-token-teste"

type Chamada = { url: string; headers: Record<string, string> }

describe("IDN-06 — tools de identidade apresentam credencial de serviço", () => {
  let server: McpServer
  let chamadas: Chamada[]

  function gateway(status: number, bodyFor: (url: string) => unknown) {
    chamadas = []
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      chamadas.push({ url: String(url), headers: (init?.headers ?? {}) as Record<string, string> })
      return new Response(JSON.stringify(bodyFor(String(url))), { status })
    }))
  }

  const ok = (url: string) => url.includes("/identity/resolve")
    ? { customer_id: "cus_a", status: "identified", matched_by: "existing", verification_class: "possessed" }
    : url.includes("/otp/challenge") ? { sent: true }
    : { found: false, count: 0, pendings: [] }

  beforeEach(() => {
    server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: "t", channelGatewayServiceToken: TOKEN })
  })
  afterEach(() => vi.unstubAllGlobals())

  const CHAMADAS: Array<[string, string, Record<string, unknown>]> = [
    ["pending_workflow_get (âncoras)", "pending_workflow_get", { tenant_id: "t", anchors: ANCORAS, session_token: SESSAO }],
    ["pending_workflow_get (legado)", "pending_workflow_get", { tenant_id: "t", contact_identifier: "5511999990001", session_token: SESSAO }],
    ["customer_resolve", "customer_resolve", { tenant_id: "t", anchors: ANCORAS }],
    ["otp_challenge", "otp_challenge", { tenant_id: "t", customer_id: "cus_a", kind: "phone", value: "+5511999990001" }],
    ["otp_verify", "otp_verify", { tenant_id: "t", customer_id: "cus_a", kind: "phone", value: "+5511999990001", code: "123456" }],
    ["customer_attach_key", "customer_attach_key", { tenant_id: "t", customer_id: "cus_a", kind: "email", value: "a@b.c" }],
    ["customer_update_attributes", "customer_update_attributes", { tenant_id: "t", customer_id: "cus_a", attributes: { nome: "x" } }],
  ]

  it.each(CHAMADAS)("%s: toda chamada ao gateway leva X-Service-Token", async (_rotulo, nome, input) => {
    gateway(200, ok)
    await tool(server, nome)(input)
    // `pending_workflow_get` com âncoras faz DUAS chamadas (resolve + by-customer): as duas.
    expect(chamadas.length).toBeGreaterThan(0)
    for (const c of chamadas) {
      expect(c.headers["X-Service-Token"], c.url).toBe(TOKEN)
      expect(c.headers["X-Service-Name"], c.url).toBe("mcp-server-plughub")
    }
  })

  it.each(CHAMADAS)("%s: credencial recusada é isError nomeado, nunca resposta plausível", async (_rotulo, nome, input) => {
    gateway(401, () => ({ detail: "rota de identidade exige credencial" }))
    const err = vi.spyOn(console, "error").mockImplementation(() => {})
    const r = await tool(server, nome)(input)
    expect(r.isError).toBe(true)
    expect(corpo(r)).toMatchObject({ error: "identity_credential_refused", status: 401 })
    expect(err).toHaveBeenCalled()
    err.mockRestore()
  })

  it("controle: 5xx que não é de credencial mantém o comportamento anterior (found=false)", async () => {
    // Sem este, o caso de cima passaria por uma tool que transforma QUALQUER falha em isError.
    gateway(500, () => ({}))
    const r = await tool(server, "pending_workflow_get")({ tenant_id: "t", anchors: ANCORAS, session_token: SESSAO })
    expect(r.isError).toBeFalsy()
    expect(corpo(r)).toEqual({ found: false })
  })
})
