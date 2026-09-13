/**
 * session-bound-resume.test.ts — PID-01 (2026-09-13)
 *
 * `pending_workflow_get` e `workflow_resume` exigem o token LIGADO À SESSÃO, emitido só
 * pelo mcp-server (`/internal/session-token`, a pedido do bridge). Três proposições:
 *
 *  1. sem token, com token inválido, ou com o token do `agent_login` (auto-serviço, sem
 *     sessão) a tool RECUSA nomeando — e não toca o gateway;
 *  2. o tenant vem do token: input divergente é recusa;
 *  3. controle positivo: com o token certo a tool age, no tenant da sessão.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerWorkflowTools } from "../tools/workflow"
import { signSessionBoundToken, signSessionToken, verifySessionBoundToken } from "../infra/jwt"
import { SESSION_BOUND_TOOLS } from "@plughub/schemas"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

function tool(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools?.[name]
  if (!reg) throw new Error(`Tool '${name}' not registered`)
  return reg.handler
}

const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, unknown>
const SESSAO = signSessionBoundToken({ tenant_id: "tenant_s", session_id: "sid-1", instance_id: "i", skill_id: "k" })
const AGENTE = signSessionToken({ tenant_id: "tenant_s", agent_type_id: "a", instance_id: "i", permissions: [] })

const ENTRADAS: Record<string, Record<string, unknown>> = {
  pending_workflow_get: { contact_identifier: "5511999990001" },
  workflow_resume:      { resume_token: "rt", decision: "input" },
}

describe("PID-01 — tools de retomada exigem o token ligado à sessão", () => {
  let server: McpServer
  let chamadas: Array<{ url: string; body: unknown }>

  beforeEach(() => {
    chamadas = []
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      chamadas.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return new Response(JSON.stringify({ found: false }), { status: 200 })
    }))
    vi.spyOn(console, "warn").mockImplementation(() => {})
    server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: "tenant_env", channelGatewayServiceToken: "svc" })
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it("as tools de retomada estão na lista de @plughub/schemas (as de prova entraram na PID-02)", () => {
    for (const t of Object.keys(ENTRADAS)) expect(SESSION_BOUND_TOOLS).toContain(t)
  })

  it("o token de agente (agent_login) não passa por token de sessão — audience diferente", () => {
    expect(() => verifySessionBoundToken(AGENTE)).toThrow()
    expect(verifySessionBoundToken(SESSAO)).toMatchObject({ tenant_id: "tenant_s", session_id: "sid-1" })
  })

  const RECUSAS: Array<[string, Record<string, unknown>, string]> = [
    ["sem token", {}, "missing_session_token"],
    ["token forjado", { session_token: "x.y.z" }, "invalid_session_token"],
    ["token do agent_login", { session_token: AGENTE }, "invalid_session_token"],
    ["tenant divergente", { session_token: SESSAO, tenant_id: "tenant_outro" }, "tenant_mismatch"],
  ]

  for (const nome of Object.keys(ENTRADAS)) {
    it.each(RECUSAS)(`${nome}: %s → recusa nomeada, sem tocar o gateway`, async (_r, extra, erro) => {
      const r = await tool(server, nome)({ ...ENTRADAS[nome], ...extra })
      expect(r.isError).toBe(true)
      expect(corpo(r)).toMatchObject({ error: erro })
      expect(chamadas).toEqual([])
    })

    it(`${nome}: controle POSITIVO — com o token da sessão age no tenant DELA`, async () => {
      const r = await tool(server, nome)({ ...ENTRADAS[nome], session_token: SESSAO, tenant_id: "tenant_s" })
      expect(r.isError).toBeFalsy()
      expect(chamadas.length).toBe(1)
      const c = chamadas[0]!
      if (nome === "workflow_resume") expect((c.body as { tenant_id: string }).tenant_id).toBe("tenant_s")
      else expect(c.url).toContain("tenant_id=tenant_s")
    })
  }
})
