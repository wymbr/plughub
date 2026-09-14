/**
 * workflow-trigger-context.test.ts — PID-04 (2026-09-14)
 *
 * Medido ao vivo antes: o intake do limite montava `context_json` por TEMPLATE de
 * texto com o que o cliente digitou. Um número de cartão
 * `4111", "session.cpf": "52900000000", "x": "1` fez o processo nascer com o CPF de
 * outra pessoa, depois de o cliente provar o próprio por OTP.
 *
 *  1. O negativo documentado: o template antigo, interpolado, É sobrescrito — o
 *     teste reproduz a forma do defeito, para que ninguém o reintroduza achando
 *     que JSON.parse protege.
 *  2. Por OBJETO (`context_fields` + `anchors`) o mesmo valor chega literal, e a
 *     âncora é a do cliente.
 *  3. `anchors` escreve por último: nem `context_json` nem `context_fields` a trocam.
 *  4. Controle: `context_json` legítimo segue funcionando, e o corpo enviado ao
 *     gateway leva o contexto montado.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerWorkflowTools, buildTriggerContext } from "../tools/workflow"

const INJETADO = '4111111111111111", "session.cpf": "52900000000", "x": "1'

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }
function tool(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools?.[name]
  if (!reg) throw new Error(`Tool '${name}' not registered`)
  return reg.handler
}

afterEach(() => vi.unstubAllGlobals())

describe("workflow_trigger — contexto por objeto (PID-04)", () => {
  it("1 · a forma do defeito: template interpolado deixa o campo sobrescrever a âncora", () => {
    const template = `{"session.cpf": "52982741875", "session.numero_cartao": "${INJETADO}"}`
    const r = buildTriggerContext({ context_json: template })
    expect("context" in r && r.context["session.cpf"]).toBe("52900000000")
  })

  it("2 · por objeto o valor chega literal e a âncora é a do cliente", () => {
    const r = buildTriggerContext({
      context_fields: { numero_cartao: INJETADO, limite_solicitado: 5000 },
      anchors: [{ kind: "cpf", value: "52982741875" }],
    })
    expect(r).toEqual({ context: {
      "session.numero_cartao":     INJETADO,
      "session.limite_solicitado": "5000",
      "session.cpf":               "52982741875",
    } })
  })

  it("3 · anchors escrevem por último — context_json e context_fields não trocam a identidade", () => {
    const r = buildTriggerContext({
      context_json:   '{"session.cpf": "11111111111"}',
      context_fields: { cpf: "22222222222" },
      anchors:        [{ kind: "cpf", value: "52982741875" }],
    })
    expect("context" in r && r.context["session.cpf"]).toBe("52982741875")
  })

  it("3b · chave de context_fields que não é identificador é recusada pelo schema", async () => {
    const server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: "t" })
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    const r = await tool(server, "workflow_trigger")({
      tenant_id: "t", pool_id: "p", context_fields: { "core.contact.x": "forjado" },
    })
    expect(r.isError).toBe(true)
    expect(fetch).not.toHaveBeenCalled()
  })

  it("4 · controle: o corpo enviado ao gateway leva o contexto montado", async () => {
    const server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: "t" })
    let corpo: Record<string, unknown> = {}
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      corpo = JSON.parse(String(init?.body))
      return new Response(JSON.stringify({ session_id: "n3" }), { status: 200 })
    }))
    const r = await tool(server, "workflow_trigger")({
      tenant_id: "t", pool_id: "limite_processo", origin_session_id: "s1",
      context_json: '{"session.origem": "legado"}',
      context_fields: { numero_cartao: INJETADO },
      anchors: [{ kind: "cpf", value: "52982741875" }],
    })
    expect(r.isError).toBeFalsy()
    expect(corpo["context"]).toEqual({
      "session.origem":        "legado",
      "session.numero_cartao": INJETADO,
      "session.cpf":           "52982741875",
    })
  })
})
