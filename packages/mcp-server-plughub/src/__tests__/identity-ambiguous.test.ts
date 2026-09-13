/**
 * identity-ambiguous.test.ts — IDN-12 (2026-09-13)
 *
 * Quando as âncoras identificam mais de um cliente no mesmo score, o resolve do
 * channel-gateway devolve `matched_by: "ambiguous"` SEM `customer_id`. As duas tools
 * que o consomem não podem transformar isso em cliente:
 *
 *  - `pending_workflow_get` devolvia as pendências (e o `resume_token`) do cliente que
 *    o resolve escolhia ao acaso, porque só testava `customer_id` e `verification_class`;
 *  - `customer_resolve` alimenta skills que gravam `caller.customer_id` direto do
 *    resultado e desviam para `on_failure` ("segue sem carimbar") quando ela falha.
 *
 * O `fetch` é substituído: a proposição é o que a TOOL faz com a resposta, não o
 * resolve (esse é provado contra Postgres e Redis em `probe_identity_index_owner.sh`).
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
const ANCORAS = [{ kind: "phone", value: "+5511999990001" }, { kind: "phone", value: "+5511999990002" }]

describe("IDN-12 — resolve ambíguo não vira cliente", () => {
  let server: McpServer
  let chamadas: string[]

  function resolveResponde(body: Record<string, unknown>) {
    chamadas = []
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      chamadas.push(String(url))
      if (String(url).includes("/identity/resolve")) {
        return new Response(JSON.stringify(body), { status: 200 })
      }
      // Qualquer outra chamada é a busca de pendências — o ramo que NÃO pode acontecer.
      return new Response(JSON.stringify({ found: true, count: 1, pendings: [{ resume_token: "tok_alheio" }] }),
        { status: 200 })
    }))
  }

  beforeEach(() => {
    server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: "t" })
  })
  afterEach(() => vi.unstubAllGlobals())

  it("pending_workflow_get: ambíguo devolve found=false com ambiguous=true e NÃO busca pendências", async () => {
    resolveResponde({ customer_id: "", status: "none", matched_by: "ambiguous", confidence: 0 })
    const r = await tool(server, "pending_workflow_get")({ tenant_id: "t", anchors: ANCORAS, session_token: SESSAO })
    expect(corpo(r)).toEqual({ found: false, count: 0, ambiguous: true })
    expect(chamadas.some((u) => u.includes("/pending/by-customer/"))).toBe(false)
    expect(r.content[0]!.text).not.toContain("tok_alheio")
  })

  it("pending_workflow_get: não resolvido comum segue sem o sinal de ambiguidade", async () => {
    resolveResponde({ customer_id: "", status: "none", matched_by: "none", confidence: 0 })
    const r = await tool(server, "pending_workflow_get")({ tenant_id: "t", anchors: ANCORAS, session_token: SESSAO })
    expect(corpo(r)).toEqual({ found: false, count: 0 })
  })

  it("pending_workflow_get: controle POSITIVO — cliente possessed resolvido busca as pendências", async () => {
    // Sem este, os dois de cima passariam por uma tool que nunca busca nada.
    resolveResponde({ customer_id: "cus_a", status: "identified", matched_by: "existing", verification_class: "possessed" })
    const r = await tool(server, "pending_workflow_get")({ tenant_id: "t", anchors: ANCORAS, session_token: SESSAO })
    expect(chamadas.some((u) => u.includes("/pending/by-customer/cus_a"))).toBe(true)
    expect(corpo(r)).toMatchObject({ customer_id: "cus_a", found: true })
  })

  it("customer_resolve: ambíguo é isError com error=ambiguous (o skill cai no on_failure)", async () => {
    resolveResponde({ customer_id: "", status: "none", matched_by: "ambiguous", confidence: 0 })
    const r = await tool(server, "customer_resolve")({ tenant_id: "t", anchors: ANCORAS, session_token: SESSAO })
    expect(r.isError).toBe(true)
    expect(corpo(r)).toMatchObject({ error: "ambiguous" })
  })

  it("customer_resolve: controle POSITIVO — resolvido volta como sucesso, sem mudança", async () => {
    resolveResponde({ customer_id: "cus_a", status: "identified", matched_by: "existing", confidence: 0.7 })
    const r = await tool(server, "customer_resolve")({ tenant_id: "t", anchors: ANCORAS, session_token: SESSAO })
    expect(r.isError).toBeFalsy()
    expect(corpo(r)).toMatchObject({ customer_id: "cus_a", matched_by: "existing" })
  })
})
