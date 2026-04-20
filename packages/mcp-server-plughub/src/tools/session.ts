/**
 * tools/session.ts
 * Tools de sessão — contrato central entre agentes e o Core.
 * Spec: plughub_spec_v1.docx seções 5, 6
 *
 * Grupo: Session (5 tools)
 *   session_context_get   — lê SessionContext completo (chamada única no início)
 *   message_send          — envia mensagem ao stream canônico
 *   session_invite        — convida especialista (TaskStep mode: "assist")
 *   session_escalate      — transferência completa (TaskStep mode: "transfer")
 *   session_channel_change — propõe mudança de canal ao cliente
 *
 * Invariantes:
 *   - Nenhuma lógica de negócio — apenas exposição de tools
 *   - Toda tool valida input com Zod antes de qualquer operação
 *   - Erros retornados como MCP error response (isError: true)
 *   - Nenhuma tool persiste estado em memória — Redis ou Kafka apenas
 *   - Chaves do stream canônico: session:{id}:stream (Redis Streams)
 */

import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import {
  SessionContextSchema,
  MessageContentSchema,
  MessageVisibilitySchema,
  ChannelSchema,
  StreamEventTypeSchema,
} from "@plughub/schemas"
import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import {
  verifySessionToken,
  InvalidTokenError,
} from "../infra/jwt"
import { MaskingService } from "../lib/masking"
import { TokenVault }     from "../lib/token-vault"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface SessionDeps {
  redis: RedisClient
  kafka: KafkaProducer
}

// ─── Helpers de resposta ──────────────────────────────────────────────────────

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string): ToolResult {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }],
  }
}

function handleCaughtError(e: unknown): ToolResult {
  if (e instanceof z.ZodError) {
    return mcpError(
      "validation_error",
      e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
    )
  }
  if (e instanceof InvalidTokenError) {
    return mcpError("invalid_token", e.message)
  }
  return mcpError("internal_error", e instanceof Error ? e.message : String(e))
}

// ─── Schemas de input ─────────────────────────────────────────────────────────

const SessionContextGetInputSchema = z.object({
  session_token:  z.string().min(1),
  session_id:     z.string().min(1),
  /**
   * participant_id do agente solicitante — determina a visibilidade das mensagens.
   * Mensagens com visibility=["part_A","part_B"] só aparecem se participant_id
   * estiver na lista. Mensagens visibility="all" e "agents_only" são sempre incluídas
   * (exceto "agents_only" que é filtrada para o cliente, mas agentes veem tudo).
   */
  participant_id: z.string().uuid(),
})

const MessageSendInputSchema = z.object({
  session_token:  z.string().min(1),
  session_id:     z.string().min(1),
  participant_id: z.string().uuid(),
  content:        MessageContentSchema,
  /**
   * Visibilidade da mensagem:
   *   "all"         → todos os participantes incluindo o cliente
   *   "agents_only" → agentes apenas, sem o cliente
   *   string[]      → lista explícita de participant_ids
   */
  visibility:     MessageVisibilitySchema.default("all"),
})

const SessionInviteInputSchema = z.object({
  session_token:  z.string().min(1),
  session_id:     z.string().min(1),
  participant_id: z.string().uuid(),
  /**
   * Especialista a convidar — identificado por skill_id ou por agent_type_id + pool_id.
   * O Routing Engine resolve o skill_id para um agent_type disponível.
   */
  skill_id:       z.string().optional(),
  agent_type_id:  z.string().optional(),
  pool_id:        z.string().optional(),
  reason:         z.string().optional(),
}).refine(
  (d) => d.skill_id !== undefined || (d.agent_type_id !== undefined && d.pool_id !== undefined),
  { message: "Informe skill_id ou (agent_type_id + pool_id)" }
)

const SessionEscalateInputSchema = z.object({
  session_token:   z.string().min(1),
  session_id:      z.string().min(1),
  participant_id:  z.string().uuid(),
  /** Pool de destino — o Routing Engine seleciona o agente disponível */
  target_pool:     z.string().min(1),
  handoff_reason:  z.string().min(1),
  /** Estado do pipeline para contexto do agente receptor (opcional) */
  pipeline_state:  z.record(z.unknown()).optional(),
})

const SessionChannelChangeInputSchema = z.object({
  session_token:  z.string().min(1),
  session_id:     z.string().min(1),
  participant_id: z.string().uuid(),
  from_channel:   ChannelSchema,
  to_channel:     ChannelSchema,
  reason:         z.string().optional(),
})

// ─── Registro das tools ───────────────────────────────────────────────────────

export function registerSessionTools(server: McpServer, deps: SessionDeps): void {
  const { redis, kafka } = deps

  // ── session_context_get ───────────────────────────────────────────────────
  server.tool(
    "session_context_get",
    "Lê o SessionContext completo para o participante solicitante. " +
    "Deve ser chamada uma única vez no início do atendimento. " +
    "Retorna sessão, participantes, mensagens filtradas por visibilidade, " +
    "sentimento e identidade do cliente. Spec seção 5.",
    SessionContextGetInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id } =
          SessionContextGetInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        // ── Ler metadados da sessão ────────────────────────────────────────
        // Chave escrita pelo Core / Channel Gateway no session_opened.
        const metaRaw = await redis.get(`session:${session_id}:meta`)
        if (!metaRaw) {
          return mcpError(
            "session_not_found",
            `Sessão '${session_id}' não encontrada ou expirada`
          )
        }
        const meta = JSON.parse(metaRaw) as Record<string, unknown>

        // ── Ler participantes ─────────────────────────────────────────────
        let participants: unknown[] = []
        try {
          const rawParticipants = await redis.get(`session:${session_id}:participants`)
          if (rawParticipants) {
            participants = JSON.parse(rawParticipants) as unknown[]
          }
        } catch { /* participantes vazios — sessão muito nova */ }

        // ── Ler mensagens do stream canônico ──────────────────────────────
        // session:{id}:stream é um Redis Stream (XADD/XRANGE).
        // Lemos apenas eventos do tipo "message" e filtramos pela visibilidade
        // do participant_id solicitante.
        let messages: unknown[] = []
        try {
          // XRANGE retorna [[id, [field, val, ...]], ...]
          const streamEntries = await (redis as any).xrange(
            `session:${session_id}:stream`, "-", "+"
          ) as Array<[string, string[]]>

          for (const [, fields] of streamEntries) {
            // Campos são pares alternados: [field, value, field, value, ...]
            const obj: Record<string, unknown> = {}
            for (let i = 0; i < fields.length; i += 2) {
              const key = fields[i] as string
              const val = fields[i + 1] as string
              try { obj[key] = JSON.parse(val) } catch { obj[key] = val }
            }

            if (obj["type"] !== "message") continue

            // Filtro de visibilidade
            const visibility = obj["visibility"]
            const shouldInclude =
              visibility === "all" ||
              visibility === "agents_only" ||
              (Array.isArray(visibility) && (visibility as string[]).includes(participant_id))

            if (!shouldInclude) continue

            // Monta objeto de mensagem completo: combina campos do stream com payload.
            // Necessário para satisfazer MessageSchema (message_id, session_id, etc.)
            // e para preservar original_content para roles autorizados.
            const payload = (obj["payload"] as Record<string, unknown>) ?? {}
            const msg: Record<string, unknown> = {
              message_id:       (obj["event_id"] as string) ?? crypto.randomUUID(),
              session_id,
              timestamp:        typeof obj["timestamp"] === "string"
                                  ? obj["timestamp"]
                                  : new Date().toISOString(),
              author:           obj["author"] ?? { participant_id: "unknown", role: "primary" },
              visibility:       obj["visibility"] ?? "all",
              ...payload,
            }
            messages.push(msg)
          }
        } catch {
          // Stream não existe ainda ou operação XRANGE não suportada no mock
          // Fallback: ler lista legada (session:{id}:messages)
          try {
            const rawMsgs = await redis.lrange(`session:${session_id}:messages`, 0, -1)
            messages = rawMsgs.map(s => {
              try { return JSON.parse(s) } catch { return s }
            })
          } catch { /* sem mensagens */ }
        }

        // ── Ler sentimento ────────────────────────────────────────────────
        // session:{id}:sentiment → JSON array de { score, timestamp }
        let sentiment: unknown[] = []
        try {
          const rawSentiment = await redis.get(`session:${session_id}:sentiment`)
          if (rawSentiment) {
            sentiment = JSON.parse(rawSentiment) as unknown[]
          }
        } catch { /* sem sentimento */ }

        // ── Ler identidade do cliente ─────────────────────────────────────
        let customer: unknown = undefined
        try {
          const rawCustomer = await redis.get(`session:${session_id}:customer`)
          if (rawCustomer) {
            customer = JSON.parse(rawCustomer) as unknown
          }
        } catch { /* sem identidade */ }

        // ── Filtro de original_content por role ───────────────────────────
        // Carrega a MaskingAccessPolicy do tenant e filtra original_content
        // das mensagens para roles não autorizados.
        // primary e specialist nunca recebem original_content — operam via tokens.
        const accessPolicy = await MaskingService.loadAccessPolicy(redis, tenant_id)

        // Determina o role do participante solicitante
        let requesterRole = "primary"
        try {
          const roleRaw = await redis.hget(
            `${tenant_id}:agent:instance:${participant_id}`,
            "role"
          )
          if (roleRaw) requesterRole = roleRaw
        } catch { /* usa 'primary' como fallback seguro */ }

        const canReadOriginal = MaskingService.canReadOriginalContent(
          requesterRole as any,
          accessPolicy
        )

        // Filtra original_content das mensagens para roles não autorizados
        if (!canReadOriginal && messages.length > 0) {
          messages = (messages as Record<string, unknown>[]).map((msg) => {
            if (!msg["masked"]) return msg
            // eslint-disable-next-line @typescript-eslint/no-unused-vars
            const { original_content: _oc, ...rest } = msg as Record<string, unknown>
            return rest
          })
        }

        // ── Montar SessionContext ─────────────────────────────────────────
        const context = {
          session_id,
          tenant_id:    tenant_id ?? meta["tenant_id"] ?? "",
          status:       meta["status"]      ?? "active",
          channel:      meta["channel"]     ?? "webchat",
          medium:       meta["medium"]      ?? "message",
          origin:       meta["origin"]      ?? meta["contact_id"] ?? "",
          destination:  meta["destination"] ?? "",
          gateway_id:   meta["gateway_id"]  ?? "",
          metadata:     meta["metadata"]    ?? {},
          customer,
          participants,
          messages,
          sentiment,
          opened_at:    meta["started_at"]  ?? meta["opened_at"] ?? new Date().toISOString(),
          skill_id:     meta["skill_id"]    ?? undefined,
          tags:         meta["tags"]        ?? [],
        }

        // Valida com o schema canônico (lança ZodError se malformado)
        const validated = SessionContextSchema.parse(context)

        return ok(validated)
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── message_send ─────────────────────────────────────────────────────────
  server.tool(
    "message_send",
    "Envia mensagem ao stream canônico da sessão. " +
    "Escreve no Redis Stream (session:{id}:stream) e publica em Kafka. " +
    "O mascaramento LGPD é aplicado pelo Core antes da entrega ao cliente. " +
    "Spec seção 5.",
    MessageSendInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id, content, visibility } =
          MessageSendInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        // Lê papel do participante no Redis para montar o author
        let role = "primary"
        try {
          const instRaw = await redis.hget(
            `${tenant_id}:instance:${participant_id}`,
            "role"
          )
          if (instRaw) role = instRaw
        } catch { /* usa 'primary' como fallback */ }

        const event_id   = crypto.randomUUID()
        const timestamp  = new Date().toISOString()
        const message_id = crypto.randomUUID()

        // ── Mascaramento LGPD com tokenização ────────────────────────────────
        // Aplica MaskingService ao conteúdo antes de gravar no stream.
        // Mensagens de agentes (role !== "customer") não são mascaradas —
        // dados sensíveis vêm do cliente, não do agente.
        const SESSION_TTL = 14400  // 4h — TTL padrão de sessão
        const vault = new TokenVault({ redis })
        const maskingConfig = await MaskingService.loadConfig(redis, tenant_id)

        let finalContent   = content
        let originalContent: typeof content | undefined
        let masked          = false
        let maskedCategories: string[] = []

        // Aplica mascaramento apenas em mensagens do cliente (role === "customer")
        // ou quando não é possível determinar o role (fallback seguro: aplica)
        if (role === "customer" || role === "primary") {
          try {
            const maskResult = await MaskingService.applyMasking(
              content,
              maskingConfig,
              vault,
              tenant_id,
              SESSION_TTL
            )
            if (maskResult.masked) {
              finalContent    = maskResult.tokenized_content
              originalContent = maskResult.original_content
              masked          = true
              maskedCategories = maskResult.categories_detected
            }
          } catch { /* mascaramento não-fatal — entrega conteúdo original */ }
        }

        const payload = {
          content:          finalContent,
          ...(originalContent ? { original_content: originalContent } : {}),
          masked,
          masked_categories: maskedCategories,
        }

        const event = {
          event_id,
          session_id,
          type:      "message" satisfies z.infer<typeof StreamEventTypeSchema>,
          timestamp,
          author: {
            participant_id,
            role,
          },
          visibility,
          payload,
        }

        // ── Escrita no stream canônico (Redis Streams) ────────────────────
        // XADD session:{id}:stream * event_id <uuid> type message ...
        try {
          await (redis as any).xadd(
            `session:${session_id}:stream`,
            "*",
            "event_id",   event_id,
            "type",       "message",
            "timestamp",  timestamp,
            "author",     JSON.stringify(event.author),
            "visibility", JSON.stringify(visibility),
            "payload",    JSON.stringify(payload),
          )
        } catch {
          // Redis Streams não suportado no ambiente (ex: mock) — fallback para RPUSH
          await redis.rpush(
            `session:${session_id}:messages`,
            JSON.stringify({
              message_id,
              session_id,
              timestamp,
              author: event.author,
              content,
              visibility,
              masked:            false,
              masked_categories: [],
            })
          )
        }

        // ── Publicar no Kafka ─────────────────────────────────────────────
        await kafka.publish("conversations.message_sent", {
          event_id,
          session_id,
          tenant_id,
          message_id,
          participant_id,
          content,
          visibility: Array.isArray(visibility) ? visibility : visibility,
          timestamp,
        })

        return ok({ message_id, event_id, session_id, timestamp })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── session_invite ────────────────────────────────────────────────────────
  server.tool(
    "session_invite",
    "Convida especialista para a sessão (mode: assist). " +
    "O especialista entra como participante paralelo (role: specialist). " +
    "A sessão continua com múltiplos agentes — não é uma transferência. " +
    "Equivale ao TaskStep mode: 'assist' do Skill Flow. Spec seção 5.",
    SessionInviteInputSchema._def.schema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id, skill_id, agent_type_id, pool_id, reason } =
          SessionInviteInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        const event_id   = crypto.randomUUID()
        const timestamp  = new Date().toISOString()

        // Escreve evento no stream canônico
        try {
          await (redis as any).xadd(
            `session:${session_id}:stream`,
            "*",
            "event_id",      event_id,
            "type",          "interaction_request",
            "timestamp",     timestamp,
            "author",        JSON.stringify({ participant_id, role: "primary" }),
            "visibility",    JSON.stringify("agents_only"),
            "payload",       JSON.stringify({
              interaction_id:   event_id,
              interaction_type: "session_invite",
              prompt:           reason ?? "session_invite",
              timeout_s:        0,
            }),
          )
        } catch { /* stream não disponível — non-fatal */ }

        // Publica em conversations.inbound para o Routing Engine alocar o especialista
        await kafka.publish("conversations.inbound", {
          session_id,
          tenant_id,
          mode:          "assist",    // sinaliza ao Routing Engine: paralelo, não transferência
          participant_id,             // agente que está convidando
          skill_id:      skill_id    ?? undefined,
          agent_type_id: agent_type_id ?? undefined,
          pool_id:       pool_id    ?? undefined,
          reason:        reason     ?? undefined,
          timestamp,
        })

        return ok({
          invited:    true,
          session_id,
          event_id,
          skill_id:      skill_id ?? null,
          agent_type_id: agent_type_id ?? null,
          pool_id:       pool_id ?? null,
          timestamp,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── session_escalate ──────────────────────────────────────────────────────
  server.tool(
    "session_escalate",
    "Transferência completa da sessão para outro pool (mode: transfer). " +
    "O agente atual é removido após confirmação pelo Routing Engine. " +
    "Equivale ao TaskStep mode: 'transfer' e ao step escalate do Skill Flow. " +
    "Requer handoff_reason. Spec seção 5.",
    SessionEscalateInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id, target_pool, handoff_reason, pipeline_state } =
          SessionEscalateInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        // Lê metadados da sessão para enriquecer o evento de roteamento
        let channel  = "webchat"
        let customerId = ""
        try {
          const metaRaw = await redis.get(`session:${session_id}:meta`)
          if (metaRaw) {
            const meta = JSON.parse(metaRaw) as Record<string, string>
            if (meta["channel"])     channel    = meta["channel"]
            if (meta["customer_id"]) customerId = meta["customer_id"]
          }
        } catch { /* usa defaults */ }

        const event_id  = crypto.randomUUID()
        const timestamp = new Date().toISOString()

        // Escreve evento de saída do agente atual no stream
        try {
          await (redis as any).xadd(
            `session:${session_id}:stream`,
            "*",
            "event_id",   event_id,
            "type",       "participant_left",
            "timestamp",  timestamp,
            "author",     JSON.stringify({ participant_id, role: "primary" }),
            "visibility", JSON.stringify("agents_only"),
            "payload",    JSON.stringify({
              participant_id,
              reason: handoff_reason,
            }),
          )
        } catch { /* non-fatal */ }

        // Publica em conversations.inbound para re-roteamento (transferência)
        await kafka.publish("conversations.inbound", {
          session_id,
          tenant_id,
          mode:           "transfer",   // Routing Engine trata como transferência
          from_participant: participant_id,
          pool_id:        target_pool,
          customer_id:    customerId || undefined,
          channel,
          handoff_reason,
          pipeline_state: pipeline_state ?? undefined,
          timestamp,
        })

        // Evento de ciclo de vida para o Rules Engine
        await kafka.publish("agent.done", {
          session_id,
          tenant_id,
          participant_id,
          outcome:        "transferred",
          handoff_reason,
          completed_at:   timestamp,
        })

        return ok({
          escalated:      true,
          session_id,
          event_id,
          target_pool,
          handoff_reason,
          timestamp,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── session_channel_change ────────────────────────────────────────────────
  server.tool(
    "session_channel_change",
    "Propõe mudança de canal ao cliente mantendo o session_id. " +
    "Registra o evento channel_transitioned no stream canônico. " +
    "O cliente deve aceitar a proposta — a mudança efetiva ocorre no Channel Gateway. " +
    "Distinto de medium_transitioned: opera em canais distintos, não em mídias. Spec seção 5.",
    SessionChannelChangeInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id, from_channel, to_channel, reason } =
          SessionChannelChangeInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        const event_id  = crypto.randomUUID()
        const timestamp = new Date().toISOString()

        const payload = {
          from_channel,
          to_channel,
          requested_by: participant_id,
          reason:       reason ?? undefined,
        }

        // Escreve evento channel_transitioned no stream canônico
        try {
          await (redis as any).xadd(
            `session:${session_id}:stream`,
            "*",
            "event_id",   event_id,
            "type",       "channel_transitioned",
            "timestamp",  timestamp,
            "author",     JSON.stringify({ participant_id, role: "primary" }),
            "visibility", JSON.stringify("all"),
            "payload",    JSON.stringify(payload),
          )
        } catch { /* non-fatal */ }

        // Notifica o Channel Gateway via Kafka
        await kafka.publish("conversations.channel_change", {
          event_id,
          session_id,
          tenant_id,
          from_channel,
          to_channel,
          requested_by: participant_id,
          reason:       reason ?? undefined,
          timestamp,
        })

        return ok({
          proposed:     true,
          event_id,
          session_id,
          from_channel,
          to_channel,
          reason:       reason ?? null,
          timestamp,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )
}
