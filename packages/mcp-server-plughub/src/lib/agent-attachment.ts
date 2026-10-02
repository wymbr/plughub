/**
 * agent-attachment — o ATENDENTE manda um arquivo ao cliente pelo Console (ATT-08, 2026-10-02).
 *
 * O cliente do webchat já sobe arquivo (VOZ-28, WCH-16), e o gateway já ENTREGA ao cliente a
 * mensagem `image`/`document`/`video` do stream, com link assinado (ATT-03). O que faltava era o
 * lado de dentro produzir essa mensagem. Três decisões, cada uma com o porquê:
 *
 *  · **Quem pode** é decidido AQUI, onde o roster mora: `agent_assist.atender` em escrita com
 *    escopo que cubra o pool da sessão (a mesma regra do WebSocket do Console, CAP-19) E a
 *    instância `human-{sub}` ATENDENDO esta sessão (`session:{sid}:human_agents`, a mesma
 *    pergunta do token de mídia, VOZ-15). Grant no pool não é atendimento.
 *  · **A esteira é a do cliente.** Os bytes vão ao gateway (rota interna, só serviço), que faz
 *    reserva + `commit` com classe, tamanho real, assinatura, antivírus e re-codificação. Uma
 *    segunda esteira, mais frouxa para quem está do lado de dentro, seria o defeito.
 *  · **Só canal que DESENHA mídia do stream.** Hoje é o webchat (e a chamada presa a ele): o
 *    WhatsApp, o e-mail e o SMS recebem a fala do agente pelo `conversations.outbound`, que não
 *    leva arquivo. Nesses canais a recusa é NOMEADA (409) — mandar e o cliente não receber é o
 *    "valor plausível" que esta casa não aceita.
 *
 * O nome do arquivo e a legenda passam pela MESMA máscara do texto do atendente (MSK-07): o
 * indicador `[Anexo: nome] legenda` é texto da conversa, com os destinos de sempre.
 */

const ACCESS_RANK: Record<string, number> = { none: 0, read_only: 1, read_write: 2 }

/** Canais cujo cliente recebe mídia do stream canônico (o `StreamSubscriber` do gateway). */
export const MEDIA_CAPABLE_CHANNELS = new Set(["webchat"])

export const MEDIA_KINDS = new Set(["image", "document", "video"])

export interface AgentAttachmentDeps {
  tenantOf(sessionId: string): Promise<{ tenantId: string | null; meta: Record<string, string>; reason: string }>
  attends(sessionId: string, instanceId: string): Promise<boolean>
  roleOf(sessionId: string, instanceId: string): Promise<string>
  uploadToGateway(args: {
    tenantId: string; sessionId: string; fileName: string; mimeType: string
    uploadedBy: string; data: Buffer
  }): Promise<{ status: number; body: Record<string, unknown> }>
  /** Máscara do texto do atendente: devolve o texto seguro (com token) e a exibição ao cliente. */
  mask(tenantId: string, text: string, logCtx: string): Promise<{ safe: string; display: string; extra: Record<string, unknown> }>
  writeStream(entry: Record<string, unknown>): Promise<void>
  publishAgents(sessionId: string, event: Record<string, unknown>): Promise<void>
  publishAnalytics(event: Record<string, unknown>): Promise<void>
  newId(): string
  now(): string
}

export interface AgentAttachmentRequest {
  payload:   Record<string, unknown>   // JWT já verificado
  sessionId: string
  fileName:  string
  mimeType:  string
  caption:   string
  data:      Buffer
}

export type AgentAttachmentResult = { status: number; body: Record<string, unknown> }

function grantCovers(payload: Record<string, unknown>, poolId: string): boolean {
  const mc = (payload["module_config"] ?? {}) as Record<string, Record<string, { access?: string; scope?: unknown }>>
  const g = mc["agent_assist"]?.["atender"]
  if ((ACCESS_RANK[g?.access ?? "none"] ?? 0) < ACCESS_RANK["read_write"]!) return false
  const scope = g?.scope
  if (!Array.isArray(scope) || scope.length === 0) return true
  return !!poolId && scope.map(String).some(s => s === poolId || s === `pool:${poolId}`)
}

export async function sendAgentAttachment(
  deps: AgentAttachmentDeps, req: AgentAttachmentRequest,
): Promise<AgentAttachmentResult> {
  const sub = typeof req.payload["sub"] === "string" ? req.payload["sub"] : ""
  if (!sub) return { status: 401, body: { error: "unauthorized" } }
  const instance = `human-${sub}`
  if (!req.data.length) return { status: 400, body: { error: "empty_body" } }
  if (!req.fileName || !req.mimeType) return { status: 400, body: { error: "file_name_and_mime_type_required" } }

  const { tenantId, meta, reason } = await deps.tenantOf(req.sessionId)
  if (!tenantId) {
    console.error(`[agent_attachment] RECUSADO session=${req.sessionId}: tenant desconhecido (${reason})`)
    return { status: 409, body: { error: "tenant_unknown", reason } }
  }
  const tokenTenant = typeof req.payload["tenant_id"] === "string" ? req.payload["tenant_id"] : ""
  if (tokenTenant && tokenTenant !== tenantId) {
    return { status: 403, body: { error: "tenant_mismatch" } }
  }
  const poolId = meta["pool_id"] ?? ""
  if (!grantCovers(req.payload, poolId)) {
    console.warn(`[agent_attachment] RECUSADO ${instance} session=${req.sessionId}: sem agent_assist.atender para o pool ${poolId || "?"}`)
    return { status: 403, body: { error: "forbidden", reason: "agent_assist.atender" } }
  }
  if (!(await deps.attends(req.sessionId, instance))) {
    console.warn(`[agent_attachment] RECUSADO ${instance} session=${req.sessionId}: nao atende esta sessao`)
    return { status: 403, body: { error: "not_attending" } }
  }
  const channel = (meta["channel"] === "chat" ? "webchat" : meta["channel"]) || ""
  if (!MEDIA_CAPABLE_CHANNELS.has(channel)) {
    console.warn(`[agent_attachment] RECUSADO session=${req.sessionId}: o canal "${channel || "?"}" nao entrega arquivo ao cliente`)
    return { status: 409, body: { error: "channel_without_media", channel } }
  }

  const up = await deps.uploadToGateway({
    tenantId, sessionId: req.sessionId, fileName: req.fileName, mimeType: req.mimeType,
    uploadedBy: instance, data: req.data,
  })
  if (up.status < 200 || up.status >= 300) {
    console.warn(`[agent_attachment] o gateway RECUSOU o arquivo de ${instance} session=${req.sessionId}: ${up.status} ${JSON.stringify(up.body)}`)
    return { status: up.status, body: { error: "upload_refused", detail: up.body["detail"] ?? up.body } }
  }
  const fileId   = String(up.body["file_id"] ?? "")
  const kind     = MEDIA_KINDS.has(String(up.body["content_type"])) ? String(up.body["content_type"]) : "document"
  const mimeType = String(up.body["mime_type"] ?? req.mimeType)
  const size     = Number(up.body["size_bytes"] ?? req.data.length)

  const logCtx = `session=${req.sessionId} role=agent via=agent-attachment`
  const indicador = await deps.mask(tenantId, `[Anexo: ${req.fileName}]` + (req.caption ? ` ${req.caption}` : ""), logCtx)
  const nome      = await deps.mask(tenantId, req.fileName, logCtx)
  const legenda   = req.caption ? await deps.mask(tenantId, req.caption, logCtx) : null
  const attachment = { media_type: kind, file_id: fileId, mime_type: mimeType, size_bytes: size }
  const messageId = deps.newId()
  const ts        = deps.now()
  const role      = await deps.roleOf(req.sessionId, instance)

  await deps.writeStream({
    stream_key:  `session:${req.sessionId}:stream`,
    type:        "message",
    author_id:   instance,
    author_role: role || "primary",
    visibility:  "all",
    event_id:    messageId,
    timestamp:   ts,
    payload: {
      message_id: messageId,
      // `type` é o que o gateway lê para entregar `msg.document`/`msg.image` ao cliente;
      // `text` é o indicador que todo leitor antigo (Console, transcrição, avaliador) já mostra.
      content: {
        type: kind, text: indicador.safe, file_id: fileId, mime_type: mimeType,
        size_bytes: size, original_name: nome.display, caption: legenda?.display ?? null,
        attachment,
      },
      text: indicador.safe,
      ...indicador.extra,
    },
  })
  await deps.publishAgents(req.sessionId, {
    type: "message.text", message_id: messageId,
    author: { type: "agent_human", id: instance, instance_id: instance },
    text: indicador.safe, timestamp: ts, session_id: req.sessionId,
    contact_id: meta["contact_id"] ?? "", visibility: "all", attachment,
  })
  await deps.publishAnalytics({
    event_type: "message_sent", message_id: messageId, session_id: req.sessionId,
    tenant_id: tenantId, author_id: instance, author_role: role || "primary",
    content_type: "text", content: indicador.safe, visibility: "all", timestamp: ts,
  })
  console.log(`[agent_attachment] ${instance} enviou ${kind} file=${fileId} session=${req.sessionId} msg=${messageId}`)
  return { status: 201, body: { message_id: messageId, text: indicador.safe, timestamp: ts, attachment } }
}
