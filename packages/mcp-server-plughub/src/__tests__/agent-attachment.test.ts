/**
 * ATT-08 — o atendente manda arquivo ao cliente pelo Console.
 *
 * Proposições, cada recusa com o controle positivo ao lado:
 *   - só quem ATENDE a sessão manda (grant no pool não basta), com o tenant do token;
 *   - só em canal que DESENHA mídia do stream (webchat) — nos outros, recusa nomeada, nunca
 *     "enviado" que o cliente não recebe;
 *   - a recusa da esteira (gateway) volta com o motivo e NADA vai ao stream;
 *   - aceito: a mensagem vai ao stream com `content.type` de mídia (o que o gateway entrega ao
 *     cliente), o indicador mascarado como texto, e o `attachment` sem o nome.
 */
import { describe, it, expect } from "vitest"
import { sendAgentAttachment, type AgentAttachmentDeps } from "../lib/agent-attachment"

const ATENDER = { agent_assist: { atender: { access: "read_write", scope: [] } } }
const payload = (mc: unknown = ATENDER, tenant = "t1") => ({ sub: "u1", tenant_id: tenant, module_config: mc })

function deps(over: Partial<AgentAttachmentDeps> & { meta?: Record<string, string>; atende?: boolean;
                                                   gw?: { status: number; body: Record<string, unknown> } } = {}) {
  const stream: Record<string, unknown>[] = []
  const agents: Record<string, unknown>[] = []
  const analytics: Record<string, unknown>[] = []
  const uploads: unknown[] = []
  const d: AgentAttachmentDeps = {
    tenantOf: async () => ({ tenantId: "t1", reason: "", meta: over.meta ?? { pool_id: "sac", channel: "webchat", contact_id: "c1" } }),
    attends:  async () => over.atende ?? true,
    roleOf:   async () => "primary",
    uploadToGateway: async a => { uploads.push(a); return over.gw ?? { status: 201, body: {
      file_id: "f1", mime_type: "application/pdf", size_bytes: 10, content_type: "document" } } },
    mask: async (_t, text) => ({ safe: text.replace("123.456.789-00", "[cpf:tk_ab12:***00]"),
                                 display: text.replace("123.456.789-00", "***00"), extra: {} }),
    writeStream: async e => { stream.push(e) },
    publishAgents: async (_s, e) => { agents.push(e) },
    publishAnalytics: async e => { analytics.push(e) },
    newId: () => "m-1",
    now:   () => "2026-10-02T10:00:00Z",
    ...over,
  }
  return { d, stream, agents, analytics, uploads }
}

const req = (p = payload(), extra: Partial<{ fileName: string; caption: string; data: Buffer }> = {}) => ({
  payload: p, sessionId: "s1", fileName: extra.fileName ?? "contrato.pdf", mimeType: "application/pdf",
  caption: extra.caption ?? "", data: extra.data ?? Buffer.from("%PDF-1.4"),
})

describe("ATT-08 — quem pode mandar arquivo", () => {
  it("controle positivo: quem atende, no webchat, manda", async () => {
    const { d, stream, uploads } = deps()
    const r = await sendAgentAttachment(d, req())
    expect(r.status).toBe(201)
    expect(uploads).toHaveLength(1)
    expect(stream).toHaveLength(1)
  })

  it("grant no pool sem ATENDER a sessão é 403, e nada sobe", async () => {
    const { d, uploads, stream } = deps({ atende: false })
    const r = await sendAgentAttachment(d, req())
    expect(r).toMatchObject({ status: 403, body: { error: "not_attending" } })
    expect(uploads).toHaveLength(0)
    expect(stream).toHaveLength(0)
  })

  it("sem agent_assist.atender em escrita é 403", async () => {
    const { d, uploads } = deps()
    const r = await sendAgentAttachment(d, req(payload({ agent_assist: { atender: { access: "read_only" } } })))
    expect(r.status).toBe(403)
    expect(uploads).toHaveLength(0)
  })

  it("grant escopado a OUTRO pool é 403", async () => {
    const { d } = deps()
    const r = await sendAgentAttachment(d, req(payload({ agent_assist: { atender: { access: "read_write", scope: ["outro"] } } })))
    expect(r.status).toBe(403)
  })

  it("token de outro tenant é 403", async () => {
    const { d } = deps()
    expect((await sendAgentAttachment(d, req(payload(ATENDER, "t2")))).status).toBe(403)
  })
})

describe("ATT-08 — canal e esteira", () => {
  it("canal que não desenha mídia do stream recusa NOMEANDO, sem subir nada", async () => {
    const { d, uploads, stream } = deps({ meta: { pool_id: "sac", channel: "whatsapp" } })
    const r = await sendAgentAttachment(d, req())
    expect(r).toMatchObject({ status: 409, body: { error: "channel_without_media", channel: "whatsapp" } })
    expect(uploads).toHaveLength(0)
    expect(stream).toHaveLength(0)
  })

  it("a recusa do gateway volta com o motivo e NADA vai ao stream", async () => {
    const { d, stream, agents } = deps({ gw: { status: 422, body: { detail: "attachment_infected" } } })
    const r = await sendAgentAttachment(d, req())
    expect(r).toMatchObject({ status: 422, body: { detail: "attachment_infected" } })
    expect(stream).toHaveLength(0)
    expect(agents).toHaveLength(0)
  })
})

describe("ATT-08 — o que vai ao stream", () => {
  it("content.type de mídia, indicador mascarado, attachment sem o nome", async () => {
    const { d, stream, agents, analytics } = deps()
    await sendAgentAttachment(d, req(payload(), { fileName: "cpf 123.456.789-00.pdf", caption: "segue" }))
    const e = stream[0] as Record<string, any>
    expect(e.visibility).toBe("all")
    expect(e.author_id).toBe("human-u1")
    const c = e.payload.content
    expect(c.type).toBe("document")
    expect(c.text).toBe("[Anexo: cpf [cpf:tk_ab12:***00].pdf] segue")
    expect(c.original_name).toBe("cpf ***00.pdf")
    expect(c.attachment).toEqual({ media_type: "document", file_id: "f1", mime_type: "application/pdf", size_bytes: 10 })
    expect(JSON.stringify(c.attachment)).not.toContain("cpf")
    expect((agents[0] as any).attachment.file_id).toBe("f1")
    expect((analytics[0] as any).content).toBe(c.text)
  })
})
