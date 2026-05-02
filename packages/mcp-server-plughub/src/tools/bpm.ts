/**
 * tools/bpm.ts
 * Tools de BPM — consumidores externos (sistemas de negócio, orquestradores).
 * Spec: PlugHub v24.0 seção 9.4
 *
 * Estas tools são o contrato entre a plataforma e sistemas externos.
 * Nunca implementam lógica de negócio — roteiam para os componentes internos.
 */

import { z }        from "zod"
import * as crypto   from "crypto"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import type { KafkaProducer } from "../infra/kafka"
import type { RedisClient }   from "../infra/redis"
import { withGuard }          from "../infra/tool-guard"
import type { Skill }         from "@plughub/schemas"

/**
 * Generates a session ID that satisfies SessionIdSchema:
 * sess_{YYYYMMDD}T{HHMMSS}_{[A-Z0-9]{22}}
 */
function genSessionId(): string {
  const now  = new Date()
  const pad  = (n: number, len = 2) => String(n).padStart(len, "0")
  const date = `${pad(now.getUTCFullYear(), 4)}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}`
  const time = `${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
  const rand  = Array.from(crypto.randomBytes(22), b => chars[b % 36]).join("")
  return `sess_${date}T${time}_${rand}`
}

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface BpmDeps {
  kafka: KafkaProducer
  redis: RedisClient
}

// ─────────────────────────────────────────────
// Schemas de input
// ─────────────────────────────────────────────

const ConversationStartInputSchema = z.object({
  channel:      z.enum(["whatsapp", "webchat", "voice", "email", "sms", "instagram", "telegram", "webrtc"]),
  customer_id:  z.string().uuid(),
  tenant_id:    z.string(),
  /** Contexto inicial — intent detectado pelo Channel Layer */
  intent:       z.string().optional(),
  /** Payload de processo BPM — quando acionado por um workflow */
  process_context: z.object({
    process_id:       z.string().optional(),
    process_instance: z.string().optional(),
    status:           z.string().optional(),
    payload:          z.record(z.unknown()).optional(),
  }).optional(),
})

const ConversationStatusInputSchema = z.object({
  session_id: z.string().uuid(),
  tenant_id:  z.string(),
})

const ConversationEndInputSchema = z.object({
  session_id: z.string().uuid(),
  tenant_id:  z.string(),
  reason:     z.enum(["timeout", "cancelled", "system_error", "bpm_terminated"]),
})

const RuleDryRunInputSchema = z.object({
  tenant_id:    z.string(),
  /** Definição da regra a ser simulada */
  rule: z.object({
    name:       z.string(),
    expression: z.record(z.unknown()),
    target_pool: z.string(),
  }),
  /** Janela histórica em dias para simulação */
  history_window_days: z.number().int().min(1).max(90).default(30),
})

const OutboundContactRequestInputSchema = z.object({
  tenant_id:    z.string(),
  customer_id:  z.string().uuid(),
  channel:      z.enum(["whatsapp", "webchat", "voice", "email", "sms", "instagram", "telegram", "webrtc"]),
  /** Tipo de agente que deve atender quando o cliente aceitar o contato */
  agent_type_id: z.string().optional(),
  /** Pool de destino (inferido pelo Routing Engine se omitido) */
  pool_id:       z.string().optional(),
  /** Metadados livres — passados ao agente via SessionContext */
  metadata:      z.record(z.unknown()).optional(),
})

const NotificationSendInputSchema = z.object({
  /** session_id da conversa ativa */
  session_id: z.string(),
  /** Texto da mensagem a ser entregue ao cliente */
  message:    z.string().min(1),
  /** Canal de entrega — "session" → webchat da sessão atual */
  channel:    z.enum(["session", "whatsapp", "sms", "email"]).default("session"),
  /**
   * Visibilidade da mensagem.
   *   "all"         → entregue ao cliente via conversations.outbound (padrão)
   *   "agents_only" → escrita no stream da sessão e publicada em agent:events:*
   *                   para entrega em tempo real ao Agent Assist UI.
   *                   O cliente NÃO recebe. Usado por co-pilots e especialistas.
   *   ["part_id1"]  → escrita no stream da sessão com visibility array.
   *                   Entregue APENAS aos participant_ids listados.
   *                   O stream_subscriber do webchat entrega ao cliente se o
   *                   customer_participant_id estiver na lista. O agent:events
   *                   NÃO é notificado (agentes não veem). Usado pelo agente NPS
   *                   para conversar exclusivamente com o cliente.
   */
  visibility: z.union([
    z.enum(["all", "agents_only"]),
    z.array(z.string().min(1)).min(1),
  ]).default("all"),
  /**
   * Menu interativo (opcional) — quando presente e interaction != "text",
   * publica menu.payload em conversations.outbound em vez de message.text.
   * Usado pelo step menu do Skill Flow. Spec 4.7.
   */
  menu: z.object({
    interaction: z.enum(["text", "button", "list", "checklist", "form"]),
    options: z.array(z.object({
      id:    z.string(),
      label: z.string(),
    })).optional(),
    fields: z.array(z.record(z.unknown())).optional(),
    /**
     * IDs dos campos mascarados neste step (subset de fields[].id).
     * O Channel Gateway usa essa lista para:
     *   - webchat: renderizar os campos como <input type="password">
     *   - outros canais: aplicar masked_fallback (link ou texto de instrução)
     */
    masked_fields: z.array(z.string()).optional(),
  }).optional(),
})

const ConversationEscalateInputSchema = z.object({
  /** session_id da conversa a ser escalada */
  session_id:     z.string(),
  /** Pool de destino (human pool) */
  target_pool:    z.string(),
  /** Estado completo do pipeline — transferido como contexto ao agente humano */
  pipeline_state: z.record(z.unknown()).optional(),
  /** Razão da escalada (para auditoria) */
  error_reason:   z.string().optional(),
})

const MentionCommandDispatchInputSchema = z.object({
  /** ID do tenant */
  tenant_id:    z.string(),
  /** ID da sessão onde o comando foi enviado */
  session_id:   z.string(),
  /**
   * ID da instância do agente especialista alvo.
   * Necessário para terminate_self (identifica a sessão do agente).
   * Opcional para set_context e trigger_step.
   */
  instance_id:  z.string().optional(),
  /**
   * Skill ID do agente especialista — usado para carregar o mapa mention_commands
   * do Redis (key: {tenant_id}:skill:{skill_id}).
   */
  skill_id:     z.string(),
  /** Nome do comando extraído de args_raw (ex: "ativa", "para") */
  command_name: z.string(),
})

// ─────────────────────────────────────────────
// Registro das tools de BPM
// ─────────────────────────────────────────────

export function registerBpmTools(server: McpServer, deps?: BpmDeps): void {

  // ── conversation_start ──────────────────────
  server.tool(
    "conversation_start",
    "Inicia um atendimento na plataforma. Retorna session_id para rastreamento.",
    ConversationStartInputSchema.shape as any,
    withGuard("conversation_start", async (input: Record<string, unknown>) => {
      const parsed    = ConversationStartInputSchema.parse(input)
      const session_id = genSessionId()
      const contact_id = crypto.randomUUID()
      const started_at = new Date().toISOString()
      const ttl        = 14_400  // 4h — aligned with session TTL across services

      // 1. Persist session meta to Redis (mirrors channel-gateway on WebSocket connect)
      const meta = {
        contact_id,
        session_id,
        tenant_id:   parsed.tenant_id,
        customer_id: parsed.customer_id,
        channel:     parsed.channel,
        started_at,
        ...(parsed.process_context ? { process_context: parsed.process_context } : {}),
      }
      await deps!.redis.setex(`session:${session_id}:contact_id`, ttl, contact_id)
      await deps!.redis.setex(`session:${session_id}:meta`,       ttl, JSON.stringify(meta))

      // 2. Publish contact_open lifecycle event
      await deps!.kafka.publish("conversations.events", {
        event_type:  "contact_open",
        contact_id,
        session_id,
        channel:     parsed.channel,
        started_at,
      })

      // 3. Publish routing event to conversations.inbound so Routing Engine allocates agent
      await deps!.kafka.publish("conversations.inbound", {
        session_id,
        tenant_id:   parsed.tenant_id,
        customer_id: parsed.customer_id,
        channel:     parsed.channel,
        started_at,
        elapsed_ms:  0,
        ...(parsed.intent ? { intent: parsed.intent } : {}),
      })

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id,
            contact_id,
            customer_id: parsed.customer_id,
            channel:     parsed.channel,
            status:      "routing",
            started_at,
          }),
        }],
      }
    }),
  )

  // ── conversation_status ─────────────────────
  server.tool(
    "conversation_status",
    "Retorna o estado atual de uma conversa em andamento.",
    ConversationStatusInputSchema.shape as any,
    withGuard("conversation_status", async (input: Record<string, unknown>) => {
      const parsed = ConversationStatusInputSchema.parse(input)

      // Read session meta written by channel-gateway or conversation_start
      const metaRaw = await deps!.redis.get(`session:${parsed.session_id}:meta`)
      if (!metaRaw) {
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({
              error:      "session_not_found",
              session_id: parsed.session_id,
            }),
          }],
        }
      }
      const meta = JSON.parse(metaRaw) as Record<string, string>

      // Compute elapsed SLA from started_at
      const startedAt  = meta["started_at"] ? new Date(meta["started_at"]).getTime() : Date.now()
      const elapsedMs  = Date.now() - startedAt
      const targetMs   = 480_000  // 8 min default SLA target
      const urgency    = Math.min(elapsedMs / targetMs, 1)

      // Determine active agent type from routing snapshot in Redis
      // orchestrator-bridge writes session:{id}:routing:{instance_id} after allocation
      let agentTypeId: string | null = null
      let agentStatus = "routing"
      const aiAgents  = await deps!.redis.smembers(`session:${parsed.session_id}:ai_agents`)
      const humAgents = await deps!.redis.smembers(`session:${parsed.session_id}:human_agents`)
      if (aiAgents.length > 0 || humAgents.length > 0) {
        agentStatus = "in_progress"
        // Read first routing snapshot to get agent_type_id
        const firstInstance = aiAgents[0] ?? humAgents[0]
        const snapRaw = await deps!.redis.get(
          `session:${parsed.session_id}:routing:${firstInstance}`
        )
        if (snapRaw) {
          const snap = JSON.parse(snapRaw) as Record<string, unknown>
          agentTypeId = (snap["snapshot"] as Record<string, unknown>)?.["agent_type_id"] as string ?? null
        }
      }

      // Read context (insights + sentiment) from routing engine
      const ctxKey = `${parsed.tenant_id}:session:${parsed.session_id}:context`
      const ctxRaw = await deps!.redis.get(ctxKey)
      const ctx    = ctxRaw ? JSON.parse(ctxRaw) as Record<string, unknown> : null

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id:    parsed.session_id,
            contact_id:    meta["contact_id"] ?? null,
            channel:       meta["channel"]    ?? null,
            status:        agentStatus,
            agent_type_id: agentTypeId,
            sentiment:     null,  // populated by AI Gateway per turn — read from context if needed
            context_loaded: ctx !== null,
            sla: {
              elapsed_ms:      elapsedMs,
              target_ms:       targetMs,
              urgency:         Math.round(urgency * 100) / 100,
              breach_imminent: urgency > 0.85,
            },
            snapshot_at: new Date().toISOString(),
          }),
        }],
      }
    }),
  )

  // ── conversation_end ────────────────────────
  server.tool(
    "conversation_end",
    "Encerra forçado uma conversa (timeout, cancelamento, erro de sistema).",
    ConversationEndInputSchema.shape as any,
    withGuard("conversation_end", async (input: Record<string, unknown>) => {
      const parsed  = ConversationEndInputSchema.parse(input)
      const ended_at = new Date().toISOString()

      // 1. Look up contact_id and channel from session meta
      let contactId = parsed.session_id  // fallback
      let channel   = "chat"
      let startedAt = ended_at
      try {
        const metaRaw = await deps!.redis.get(`session:${parsed.session_id}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          if (meta["contact_id"]) contactId = meta["contact_id"]
          if (meta["channel"])    channel   = meta["channel"]
          if (meta["started_at"]) startedAt = meta["started_at"]
        }
      } catch { /* use fallback */ }

      // 2. Notify active agent via Redis pub/sub so it can do graceful shutdown
      await deps!.redis.publish(`agent:events:${parsed.session_id}`, JSON.stringify({
        type:    "session.closed",
        reason:  parsed.reason,
        ended_at,
      }))

      // 3. Publish contact_closed lifecycle event (conversation-writer persists transcript)
      await deps!.kafka.publish("conversations.events", {
        event_type: "contact_closed",
        contact_id: contactId,
        session_id: parsed.session_id,
        channel,
        reason:     "agent_done",   // closest standard reason for forced end
        started_at: startedAt,
        ended_at,
        forced_by:  parsed.reason,  // audit: original force reason
      })

      // 4. Notify channel-gateway to close WebSocket
      await deps!.kafka.publish("conversations.outbound", {
        type:       "session.closed",
        contact_id: contactId,
        session_id: parsed.session_id,
        channel,
        reason:     parsed.reason,
      })

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id: parsed.session_id,
            contact_id: contactId,
            terminated: true,
            reason:     parsed.reason,
            ended_at,
          }),
        }],
      }
    }),
  )

  // ── rule_dry_run ────────────────────────────
  server.tool(
    "rule_dry_run",
    "Simula uma regra do Rules Engine contra histórico de conversas. Spec 3.2b.",
    RuleDryRunInputSchema.shape as any,
    withGuard("rule_dry_run", async (input: Record<string, unknown>) => {
      const parsed = RuleDryRunInputSchema.parse(input)

      // Delegate to Rules Engine REST API — it has the ClickHouse connection
      const rulesEngineUrl = process.env["RULES_ENGINE_URL"] ?? "http://localhost:3500"
      let simulation: Record<string, unknown>
      try {
        const res = await fetch(`${rulesEngineUrl}/v1/rules/dry-run`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            tenant_id:           parsed.tenant_id,
            rule:                parsed.rule,
            history_window_days: parsed.history_window_days,
          }),
        })
        if (!res.ok) throw new Error(`rules-engine responded ${res.status}`)
        simulation = await res.json() as Record<string, unknown>
      } catch (err) {
        simulation = {
          error:   "rules_engine_unavailable",
          message: err instanceof Error ? err.message : "unknown error",
        }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            rule_name:           parsed.rule.name,
            target_pool:         parsed.rule.target_pool,
            history_window_days: parsed.history_window_days,
            simulation,
            simulated_at: new Date().toISOString(),
          }),
        }],
      }
    }),
  )

  // ── notification_send ───────────────────────
  server.tool(
    "notification_send",
    "Envia mensagem de texto ao cliente via canal da sessão. Usado pelo step notify do Skill Flow. Spec 4.7.",
    NotificationSendInputSchema.shape as any,
    withGuard("notification_send", async (input: Record<string, unknown>) => {
      const parsed = NotificationSendInputSchema.parse(input)

      // Look up contact_id and channel via Redis keys written by channel-gateway on connect.
      // Keys: session:{session_id}:contact_id → contact_id string
      //       session:{session_id}:meta        → JSON with channel field
      let contactId = parsed.session_id  // fallback: use session_id as contact_id
      let channel   = "webchat"          // fallback channel (outbound_consumer requires "webchat")
      if (deps?.redis) {
        try {
          const stored = await deps.redis.get(`session:${parsed.session_id}:contact_id`)
          if (stored) contactId = stored
        } catch {
          // ignore — use fallback
        }
        try {
          const meta = await deps.redis.get(`session:${parsed.session_id}:meta`)
          if (meta) {
            const metaObj = JSON.parse(meta) as Record<string, unknown>
            if (typeof metaObj["channel"] === "string") {
              // Normalize legacy "chat" → "webchat": the outbound_consumer
              // filters channel != "webchat" and silently drops the message otherwise.
              const rawCh = metaObj["channel"] as string
              channel = rawCh === "chat" ? "webchat" : rawCh
            }
          }
        } catch {
          // ignore — use fallback
        }
      }

      const messageId = crypto.randomUUID()
      const timestamp  = new Date().toISOString()
      // Cria interaction.request para todas as interações não-text.
      // Para text: cria interaction.request apenas quando há masked_fields (ex: PIN input)
      // para que o webchat renderize <input type="password"> em vez de campo normal.
      // Text sem masked_fields continua como message.text (preserva compatibilidade).
      const hasMaskedText = parsed.menu?.interaction === "text" &&
                            (parsed.menu?.masked_fields?.length ?? 0) > 0
      const hasMenu    = parsed.menu && (parsed.menu.interaction !== "text" || hasMaskedText)
      const agentsOnly    = parsed.visibility === "agents_only"
      const isArrayVis    = Array.isArray(parsed.visibility)

      if (agentsOnly) {
        // agents_only: write to session stream + publish to agent:events Redis channel.
        // The customer webchat (channel-gateway stream subscriber) filters out agents_only
        // entries. The Agent Assist UI receives the event via agent:events pub/sub.
        // conversations.outbound is NOT used — customer never sees this message.
        if (deps?.redis) {
          try {
            await deps.redis.xadd(
              `session:${parsed.session_id}:stream`,
              "*",
              "type",       "message",
              "timestamp",  timestamp,
              "author",     JSON.stringify({ type: "agent_ai", id: "orchestrator" }),
              "visibility", JSON.stringify("agents_only"),
              "payload",    JSON.stringify({
                message_id: messageId,
                content:    { type: "text", text: parsed.message },
              }),
            )
          } catch { /* non-fatal — stream may not exist yet */ }
          try {
            await deps.redis.publish(`agent:events:${parsed.session_id}`, JSON.stringify({
              type:       "message.text",
              session_id: parsed.session_id,
              message_id: messageId,
              author:     { type: "agent_ai", id: "orchestrator" },
              text:       parsed.message,
              timestamp,
              visibility: "agents_only",
            }))
          } catch { /* non-fatal */ }
        }
      } else if (isArrayVis) {
        // Array visibility: write to session stream with the participant_id list.
        // The stream_subscriber delivers to the webchat client ONLY if
        // customer_participant_id is in the list.
        // agent:events is NOT notified — agents listed in the array receive
        // the message via their own stream subscription (session_context_get).
        // conversations.outbound is NOT used.
        // Primary use case: NPS agent talks exclusively to the customer without
        // the human agent seeing the messages.
        if (deps?.redis) {
          try {
            await deps.redis.xadd(
              `session:${parsed.session_id}:stream`,
              "*",
              "type",       "message",
              "timestamp",  timestamp,
              "author",     JSON.stringify({ type: "agent_ai", id: "orchestrator" }),
              "visibility", JSON.stringify(parsed.visibility),
              "payload",    JSON.stringify({
                message_id: messageId,
                content:    { type: "text", text: parsed.message },
              }),
            )
          } catch { /* non-fatal — stream may not exist yet */ }
          // NÃO publicamos em agent:events — visibilidade restrita impede que
          // o Agent Assist UI do agente humano veja estas mensagens.
          // Os participantes listados no array já lêem o stream diretamente
          // (customer via StreamSubscriber, agentes via session_context_get).
        }

        // Para interações de menu com array visibility (ex: NPS button),
        // publicamos em conversations.outbound para que o channel-gateway
        // entregue a interação ao cliente via o adapter webchat.
        if (hasMenu && deps?.kafka) {
          await deps.kafka.publish("conversations.outbound", {
            type:          "menu.payload",
            contact_id:    contactId,
            session_id:    parsed.session_id,
            menu_id:       messageId,
            channel,
            interaction:   parsed.menu!.interaction,
            prompt:        parsed.message,
            options:       parsed.menu!.options        ?? [],
            fields:        parsed.menu!.fields         ?? null,
            masked_fields: parsed.menu!.masked_fields  ?? null,
            visibility:    parsed.visibility,
            timestamp,
          })
        }
      } else if (deps?.kafka) {
        if (hasMenu) {
          // Interactive menu step: publish menu.payload so channel-gateway
          // renders native buttons/list instead of a plain text bubble.
          // The outbound_consumer maps this → WsMenuRender → WebSocket.
          await deps.kafka.publish("conversations.outbound", {
            type:        "menu.payload",
            contact_id:  contactId,
            session_id:  parsed.session_id,
            menu_id:     messageId,
            channel,
            interaction:   parsed.menu!.interaction,
            prompt:        parsed.message,
            options:       parsed.menu!.options        ?? [],
            fields:        parsed.menu!.fields         ?? null,
            masked_fields: parsed.menu!.masked_fields  ?? null,
            timestamp,
          })
        } else {
          // Plain text notification (notify step, or menu with interaction="text").
          await deps.kafka.publish("conversations.outbound", {
            type:       "message.text",
            contact_id: contactId,
            session_id: parsed.session_id,
            message_id: messageId,
            channel,
            direction:  "outbound",
            author:     { type: "agent_ai", id: "orchestrator" },
            content:    { type: "text", text: parsed.message },
            text:       parsed.message,   // kept for channel-gateway backward compat
            timestamp,
          })
        }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            delivered:  true,
            session_id: parsed.session_id,
            contact_id: contactId,
            message_id: messageId,
            sent_at:    timestamp,
          }),
        }],
      }
    }),
  )

  // ── outbound_contact_request ────────────────
  server.tool(
    "outbound_contact_request",
    "Solicita ao Channel Gateway que contacte um cliente (fluxo outbound). " +
    "Publica em conversations.outbound com type outbound.contact_request. " +
    "O Channel Gateway persiste o contato no Redis e publica conversations.inbound quando aceito. Spec 9.4.",
    OutboundContactRequestInputSchema.shape as any,
    withGuard("outbound_contact_request", async (input: Record<string, unknown>) => {
      const parsed       = OutboundContactRequestInputSchema.parse(input)
      const contact_id   = crypto.randomUUID()
      const requested_at = new Date().toISOString()
      const ttl          = 14_400  // 4h — same as session TTL

      // 1. Persist outbound request meta so Channel Gateway can enrich the session on accept
      if (deps?.redis) {
        await deps.redis.setex(
          `outbound:${contact_id}:meta`,
          ttl,
          JSON.stringify({
            contact_id,
            tenant_id:     parsed.tenant_id,
            customer_id:   parsed.customer_id,
            channel:       parsed.channel,
            agent_type_id: parsed.agent_type_id ?? null,
            pool_id:       parsed.pool_id       ?? null,
            metadata:      parsed.metadata      ?? {},
            requested_at,
            status:        "pending",
          })
        )
      }

      // 2. Publish outbound contact request to conversations.outbound
      //    Channel Gateway subscribes and initiates the outbound call/message
      if (deps?.kafka) {
        await deps.kafka.publish("conversations.outbound", {
          type:          "outbound.contact_request",
          contact_id,
          tenant_id:     parsed.tenant_id,
          customer_id:   parsed.customer_id,
          channel:       parsed.channel,
          agent_type_id: parsed.agent_type_id ?? undefined,
          pool_id:       parsed.pool_id       ?? undefined,
          metadata:      parsed.metadata      ?? {},
          requested_at,
        })
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            contact_id,
            status:      "pending",
            channel:     parsed.channel,
            customer_id: parsed.customer_id,
            tenant_id:   parsed.tenant_id,
            requested_at,
          }),
        }],
      }
    }),
  )

  // ── conversation_escalate ───────────────────
  server.tool(
    "conversation_escalate",
    "Escala a conversa para um pool humano via Routing Engine. Usado pelo step escalate do Skill Flow. Spec 4.7 + 9.5i.",
    ConversationEscalateInputSchema.shape as any,
    withGuard("conversation_escalate", async (input: Record<string, unknown>) => {
      const parsed = ConversationEscalateInputSchema.parse(input)

      // Retrieve stored session metadata from Redis (written by channel-gateway + orchestrator bridge)
      let contactId  = parsed.session_id
      let tenantId   = "default"
      let customerId = parsed.session_id
      // Default to "webchat" — "chat" is not a valid ConversationInboundEvent channel
      // and would cause the Routing Engine to silently drop the escalation event.
      let channel    = "webchat"

      if (deps?.redis) {
        try {
          const meta = await deps.redis.get(`session:${parsed.session_id}:meta`)
          if (meta) {
            const parsed_meta = JSON.parse(meta) as Record<string, string>
            if (parsed_meta["contact_id"])  contactId  = parsed_meta["contact_id"]
            if (parsed_meta["tenant_id"])   tenantId   = parsed_meta["tenant_id"]
            if (parsed_meta["customer_id"]) customerId = parsed_meta["customer_id"]
            if (parsed_meta["channel"]) {
              // Normalize legacy "chat" → "webchat" so the Routing Engine's Literal
              // validation passes (spec channels: whatsapp, webchat, voice, email, …)
              const rawChannel = parsed_meta["channel"]
              channel = rawChannel === "chat" ? "webchat" : rawChannel
            }
          }
        } catch {
          // ignore — use fallbacks
        }
      }

      // Write participant_left to the session stream so the webchat client sees the
      // AI agent leaving before the human agent joins.
      // participant_id is not available in this context (no session JWT) — use "ai-agent"
      // as a stable label. role "ai" lets the webchat render a transfer notification
      // instead of a generic leave message.
      if (deps?.redis) {
        try {
          await (deps.redis as any).xadd(
            `session:${parsed.session_id}:stream`,
            "*",
            "event_id",   crypto.randomUUID(),
            "type",       "participant_left",
            "timestamp",  new Date().toISOString(),
            "author",     JSON.stringify({ participant_id: "ai-agent", instance_id: "ai-agent", role: "ai" }),
            "visibility", JSON.stringify("all"),
            "payload",    JSON.stringify({ participant_id: "ai-agent", reason: "escalated" }),
          )
        } catch { /* non-fatal */ }
      }

      // Publish ConversationInboundEvent to conversations.inbound so the Routing Engine
      // re-routes the session to the target_pool (human pool).
      // pool_id is set directly — the Routing Engine restricts its search to that pool only,
      // preventing re-allocation to an AI agent and ensuring the escalation reaches humans.
      const routingEvent = {
        session_id:   parsed.session_id,
        tenant_id:    tenantId,
        customer_id:  customerId,
        channel,
        pool_id:      parsed.target_pool,  // explicit target — no pool inference
        confidence:   0.0,   // confidence=0 → Routing Engine picks supervised mode
        started_at:   new Date().toISOString(),
        elapsed_ms:   0,
        process_context: {
          escalated_from: "skill_flow",
          error_reason:   parsed.error_reason,
          pipeline_state: parsed.pipeline_state,
        },
      }

      if (deps?.kafka) {
        await deps.kafka.publish("conversations.inbound", routingEvent)
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            escalated:   true,
            session_id:  parsed.session_id,
            target_pool: parsed.target_pool,
            tenant_id:   tenantId,
            escalated_at: new Date().toISOString(),
          }),
        }],
      }
    }),
  )

  // ── mention_command_dispatch ─────────────────
  server.tool(
    "mention_command_dispatch",
    "Processa um @mention command endereçado a um agente especialista. " +
    "Chamado pelo orchestrator bridge quando detecta mention_routing:true em NormalizedInboundEvent. " +
    "Carrega mention_commands do skill do agente, executa a ação declarada (set_context, " +
    "trigger_step ou terminate_self) e opcionalmente envia acknowledgment agents_only. " +
    "Comandos desconhecidos são ignorados silenciosamente. Spec: docs/guias/mention-protocol.md.",
    MentionCommandDispatchInputSchema.shape as any,
    withGuard("mention_command_dispatch", async (input: Record<string, unknown>) => {
      const parsed    = MentionCommandDispatchInputSchema.parse(input)
      const redis     = deps?.redis
      const now       = new Date().toISOString()
      const resultKey = `menu:result:${parsed.session_id}`

      // ── 1. Load skill definition from Redis ──────────────────────────────
      let mentionCommands: NonNullable<Skill["mention_commands"]> = {}

      if (redis) {
        try {
          const raw = await redis.get(`${parsed.tenant_id}:skill:${parsed.skill_id}`)
          if (raw) {
            const skill = JSON.parse(raw) as Skill
            mentionCommands = skill.mention_commands ?? {}
          }
        } catch {
          // Non-fatal — if skill can't be loaded, command is unrecognised (handled below)
        }
      }

      // ── 2. Look up command ────────────────────────────────────────────────
      const cmd = mentionCommands[parsed.command_name]
      if (!cmd) {
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({ handled: false, reason: "unknown_command" }),
          }],
        }
      }

      const action = cmd.action

      // ── 3. Execute action ─────────────────────────────────────────────────

      if ("set_context" in action && redis) {
        const ctxKey = `${parsed.tenant_id}:ctx:${parsed.session_id}`
        for (const [tag, value] of Object.entries(action.set_context)) {
          try {
            const entry = JSON.stringify({
              value,
              confidence: 1.0,
              source:     `mention_command:${parsed.command_name}`,
              visibility: "agents_only",
              updated_at: now,
            })
            await redis.hset(ctxKey, tag, entry)
          } catch { /* non-fatal */ }
        }
      }

      if ("trigger_step" in action && redis) {
        // Interrupt any blocked menu:result BLPOP — the menu step detects this special payload
        // and jumps to the specified step instead of returning a normal on_success result.
        try {
          await redis.lpush(resultKey, JSON.stringify({
            _mention_trigger_step: action.trigger_step,
          }))
        } catch { /* non-fatal */ }
      }

      if ("terminate_self" in action && action.terminate_self && redis) {
        // Wake up any blocked menu BLPOP with a terminate signal.
        // The menu step (or the engine's completion logic) handles agent_done.
        try {
          await redis.lpush(resultKey, JSON.stringify({ _mention_terminate: true }))
        } catch { /* non-fatal */ }
      }

      // ── 4. Acknowledgment — agents_only message ───────────────────────────
      if (cmd.acknowledge && redis) {
        try {
          await (redis as any).xadd(
            `session:${parsed.session_id}:stream`,
            "*",
            "event_id",   crypto.randomUUID(),
            "type",       "message",
            "timestamp",  now,
            "author",     JSON.stringify({ type: "agent_ai", id: parsed.instance_id ?? "mention_dispatcher" }),
            "visibility", JSON.stringify("agents_only"),
            "payload",    JSON.stringify({
              content: { type: "text", text: `[mention_command] /${parsed.command_name} acknowledged` },
            }),
          )
        } catch { /* non-fatal */ }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            handled:        true,
            command_name:   parsed.command_name,
            action_type:    Object.keys(action)[0] ?? "unknown",
            acknowledged:   cmd.acknowledge,
          }),
        }],
      }
    }),
  )
}
