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
import { writeStreamEntry }   from "../lib/write-stream-entry"
import { resolveAgentTypeForSession } from "../lib/routing-ref"
import { channelSatisfies, MASKED_INPUT, maskingChannels } from "@plughub/schemas"

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
   * segment_id do agente que emite a mensagem (opcional).
   * Quando presente, é escrito no stream canônico junto com a mensagem,
   * permitindo filtragem determinística por segmento no frontend (Contatos).
   * Propagado automaticamente pelo Skill Flow Engine via ctx.segmentId.
   */
  segment_id: z.string().optional(),
  /**
   * instance_id do agente que emite a mensagem (opcional).
   * Ex: "agente_nps_v1-001", "agente_wrapup_v1-001".
   * Usado como author.id no stream para que o frontend possa atribuir
   * cada mensagem ao agente correto por participant_id.
   */
  instance_id: z.string().optional(),
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
    /**
     * ALW-10 — `field_id` → id do tipo do catálogo, para os campos mascarados.
     * O canal decide o eco ao cliente por ele. Opcional: ausente = comportamento
     * anterior (todo campo mascarado vira input de senha).
     */
    masked_types: z.record(z.string()).optional(),
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
  /** F7: motivo de escalação normalizado (id do config escalation_reasons). */
  escalation_reason: z.string().optional(),
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

      // Determine active agent type for the session.
      //
      // F5: este site lia `agent_type_id` do sub-documento `snapshot` da chave
      // `session:{id}:routing:{iid}` — sub-documento que a F4 removeu (a chave
      // encolheu para {tenant_id, instance_id, pool_id}). A leitura virou morta,
      // devolvendo sempre null em silêncio. O helper resolve no escopo certo:
      // humano → `human_agent_{pool da sessão}`; IA → o campo do registro, que aí
      // é identidade legítima. Ver ADR adr-human-agent-pool-scoped-identity.
      let agentTypeId: string | null = null
      let agentStatus = "routing"
      const aiAgents  = await deps!.redis.smembers(`session:${parsed.session_id}:ai_agents`)
      const humAgents = await deps!.redis.smembers(`session:${parsed.session_id}:human_agents`)
      if (aiAgents.length > 0 || humAgents.length > 0) {
        agentStatus = "in_progress"
        const firstInstance = aiAgents[0] ?? humAgents[0]
        if (firstInstance) {
          agentTypeId = await resolveAgentTypeForSession(
            deps!.redis, parsed.tenant_id, parsed.session_id, firstInstance, "[process_context_get]",
          )
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
      let metaTenantId: string | null = null      // tenant_id from session meta (for analytics)
      let metaCustomerPid: string | null = null  // customer_participant_id from session meta (fallback)
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
            // Extract customer_participant_id from meta as fallback for the
            // dedicated Redis key (which may have expired by the time post-session
            // hook agents like NPS run).
            if (typeof metaObj["tenant_id"] === "string") {
              metaTenantId = metaObj["tenant_id"] as string
            }
            if (typeof metaObj["customer_participant_id"] === "string") {
              metaCustomerPid = metaObj["customer_participant_id"] as string
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

      // ── NIV-03 (runtime) — um menu MASCARADO nunca sai para canal que não
      //    sabe mascarar. Fecha a MSK-01, que era exatamente isto: o pool
      //    `limite_ia` declara `[webchat, whatsapp]`, o fluxo mascara CVV, e no
      //    WhatsApp o campo virava formulário comum — sem fallback, sem aviso,
      //    sem recusa. O `supports_masked_input: false` que a tabela antiga
      //    atribuía a whatsapp/sms/email era comentário sem leitor.
      //
      //    **Por que AQUI e não no channel-gateway.** Este é o único ponto por
      //    onde um menu sai para o canal do cliente, e é ANTES do Kafka: nada
      //    mascarado chega a ser publicado. Além disso a recusa vira `isError`,
      //    que o `menu` step já converte em `on_failure` (o `try/catch` em volta
      //    do `notification_send`) — o fluxo segue o caminho de falha na hora, em
      //    vez de esperar `timeout_s` inteiro. Recusar no gateway obrigaria a
      //    inventar um sinal novo em `menu:result:{sid}`, chave cuja composição já
      //    produziu um bug silencioso documentado (o agente de fila surdo).
      //
      //    **`agents_only` fica FORA de propósito.** Aquele menu não vai ao
      //    canal do cliente — vai ao Agent Assist, que é superfície de operador.
      //    Guardá-lo recusaria wrap-up e NPS interno sem nenhum ganho.
      //
      //    ⚠️ **Isto é RECUSA, não fallback.** `MaskedFallbackPolicySchema` prevê
      //    `message` e `link`, e nenhum dos dois existe: não há namespace
      //    `masking` no config-api (medido 2026-09-03 — `GET /v1/config/masking`
      //    devolve 404), logo não há política a consultar. Sem política, o único
      //    desfecho honesto é o restritivo. Quando a política ganhar casa, é ESTE
      //    ponto que a lê — e o `decline` já é o comportamento atual.
      const maskedIds = parsed.menu?.masked_fields ?? []
      if (hasMenu && !agentsOnly && maskedIds.length > 0 &&
          !channelSatisfies(channel, [MASKED_INPUT])) {
        // Os IDs entram no log; os VALORES nunca existiram aqui (campo mascarado
        // não trafega no payload de saída — só o nome do campo).
        console.error(
          `[notification_send] RECUSADO: menu mascarado em canal sem '${MASKED_INPUT}' — ` +
          `session=${parsed.session_id} channel=${channel} campos=[${maskedIds.join(",")}] ` +
          `instance=${parsed.instance_id ?? "-"}. Canais que sabem mascarar: ` +
          `[${maskingChannels().join(",")}]. Sem política de fallback configurada ` +
          `(namespace 'masking' do config-api não existe), a recusa é o desfecho. ` +
          `Conserto: tirar '${channel}' do pool, ou não mascarar neste fluxo.`,
        )
        return {
          isError: true,
          content: [{
            type: "text" as const,
            text: `masked_input_unsupported: o canal '${channel}' não coleta valor ` +
                  `mascarado (campos: ${maskedIds.join(", ")}). O menu NÃO foi enviado.`,
          }],
        }
      }

      // Use instance_id as the author identifier when available.
      // instance_id is the agent's name (e.g. "agente_nps_v1-001"),
      // propagated by the Skill Flow Engine via ctx.instanceId.
      // This replaces the hardcoded "orchestrator" so the frontend can
      // distinguish which agent sent which message by matching participant_id.
      // Falls back to segment_id (UUID) then "orchestrator" for backward compat.
      const authorId = parsed.instance_id ?? parsed.segment_id ?? "orchestrator"
      const authorJson = JSON.stringify({ type: "agent_ai", id: authorId })

      if (agentsOnly) {
        // agents_only: write to session stream + publish to agent:events Redis channel.
        // The customer webchat (channel-gateway stream subscriber) filters out agents_only
        // entries. The Agent Assist UI receives the event via agent:events pub/sub.
        // conversations.outbound is NOT used — customer never sees this message.
        if (deps?.redis) {
          try {
            await writeStreamEntry(deps.redis, {
              stream_key:  `session:${parsed.session_id}:stream`,
              type:        "message",
              author_id:   authorId,
              author_role: "specialist",
              visibility:  "agents_only",
              segment_id:  parsed.segment_id,
              payload: {
                message_id: messageId,
                content:    { type: "text", text: parsed.message },
              },
              timestamp,
            })
          } catch { /* non-fatal — stream may not exist yet */ }
          try {
            await deps.redis.publish(`agent:events:${parsed.session_id}`, JSON.stringify({
              type:       "message.text",
              session_id: parsed.session_id,
              message_id: messageId,
              author:     { type: "agent_ai", id: authorId },
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
        // For menus (hasMenu=true), agent:events receives a menu.render event so
        // Agent Assist UI can display interactive menu cards for wrap-up agents.
        // For plain text (hasMenu=false), agent:events receives a message.text
        // event when the visibility targets an agent (not customer-only).
        // Primary use case: NPS agent talks exclusively to the customer without
        // the human agent seeing the messages; wrap-up agent talks exclusively
        // with the human agent after they clicked "Desligar".
        if (deps?.redis) {
          try {
            if (hasMenu) {
              // Interactive menu with array visibility: write interaction_request so
              // webchat StreamSubscriber delivers it as an interaction.request WS event
              // (with buttons / form fields), not just a plain text bubble.
              await writeStreamEntry(deps.redis, {
                stream_key:  `session:${parsed.session_id}:stream`,
                type:        "interaction_request",
                author_id:   authorId,
                author_role: "specialist",
                visibility:  parsed.visibility as string[],
                segment_id:  parsed.segment_id,
                payload: {
                  menu_id:       messageId,
                  interaction:   parsed.menu!.interaction,
                  prompt:        parsed.message,
                  options:       parsed.menu!.options        ?? [],
                  fields:        parsed.menu!.fields         ?? null,
                  masked_fields: parsed.menu!.masked_fields  ?? null,
                  masked_types: parsed.menu!.masked_types   ?? null,
                },
                timestamp,
              })
            } else {
              await writeStreamEntry(deps.redis, {
                stream_key:  `session:${parsed.session_id}:stream`,
                type:        "message",
                author_id:   authorId,
                author_role: "specialist",
                visibility:  parsed.visibility as string[],
                segment_id:  parsed.segment_id,
                payload: {
                  message_id: messageId,
                  content:    { type: "text", text: parsed.message },
                },
                timestamp,
              })
            }
          } catch { /* non-fatal — stream may not exist yet */ }

          // Determine if the visibility array targets the human agent.
          // If it only targets the customer (not any agent), skip agent:events
          // so NPS messages don't leak to Agent Assist UI.
          let targetsAgent = true
          try {
            let custPid = await deps.redis.get(
              `session:${parsed.session_id}:customer_participant_id`
            )
            // Fallback: dedicated key may have expired; use value from session meta
            if (!custPid && metaCustomerPid) custPid = metaCustomerPid
            if (custPid && Array.isArray(parsed.visibility)) {
              const custStr = typeof custPid === "string" ? custPid : String(custPid)
              // If every element in the visibility array is the customer_participant_id,
              // then this message is customer-only — skip agent:events.
              targetsAgent = parsed.visibility.some((v: string) => v !== custStr)
            }
          } catch { /* non-fatal — default to publishing */ }

          if (targetsAgent && !hasMenu) {
            // Publish to agent:events so Agent Assist UI receives the message.
            // This is critical for post-agent_done wrap-up messages that target
            // the human agent's participant_id.
            // When hasMenu is true, we skip this — the menu.render event below
            // will deliver the message to Agent Assist UI instead, avoiding
            // duplicate display (text bubble + menu card for the same message).
            try {
              await deps.redis.publish(`agent:events:${parsed.session_id}`, JSON.stringify({
                type:       "message.text",
                session_id: parsed.session_id,
                message_id: messageId,
                author:     { type: "agent_ai", id: authorId },
                text:       parsed.message,
                timestamp,
                visibility: parsed.visibility,
              }))
            } catch { /* non-fatal */ }
          }
        }

        // Para interações de menu com array visibility (ex: NPS button),
        // publicamos em conversations.outbound para que o channel-gateway
        // entregue a interação ao cliente via o adapter webchat.
        if (hasMenu && deps?.kafka) {
          try {
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
                  masked_types: parsed.menu!.masked_types   ?? null,
              visibility:    parsed.visibility,
              timestamp,
            })
          } catch (kafkaErr) {
            console.warn(
              `[notification_send] conversations.outbound (array-vis menu) publish failed for session ${parsed.session_id}:`,
              kafkaErr instanceof Error ? kafkaErr.message : String(kafkaErr),
            )
          }
        }

        // Also publish menu to agent:events for Agent Assist UI (wrap-up menus),
        // but only if the visibility targets the human agent (not customer-only).
        if (hasMenu && deps?.redis) {
          // Reuse the targetsAgent check from above (same visibility array)
          let menuTargetsAgent = true
          try {
            let custPid2 = await deps.redis.get(
              `session:${parsed.session_id}:customer_participant_id`
            )
            // Fallback: dedicated key may have expired; use value from session meta
            if (!custPid2 && metaCustomerPid) custPid2 = metaCustomerPid
            if (custPid2 && Array.isArray(parsed.visibility)) {
              const custStr2 = typeof custPid2 === "string" ? custPid2 : String(custPid2)
              menuTargetsAgent = parsed.visibility.some((v: string) => v !== custStr2)
            }
          } catch { /* non-fatal */ }

          if (menuTargetsAgent) {
            try {
              await deps.redis.publish(`agent:events:${parsed.session_id}`, JSON.stringify({
                type:         "menu.render",
                session_id:   parsed.session_id,
                menu_id:      messageId,
                // G7 (c): instance de ORIGEM deste menu (= chave do menu:waiting).
                // O Console ecoa em menu_submit (agent_key) p/ roteamento
                // determinístico ao menu:result:{sid}:{instance}, sem depender de
                // resolução de pid — funciona com N humanos/wrap-ups.
                source_instance: authorId,
                interaction:  parsed.menu!.interaction,
                prompt:       parsed.message,
                options:      parsed.menu!.options       ?? [],
                fields:       parsed.menu!.fields        ?? [],
                masked_fields: parsed.menu!.masked_fields ?? undefined,
                masked_types: parsed.menu!.masked_types ?? undefined,
                timestamp,
                visibility:   parsed.visibility,
              }))
            } catch { /* non-fatal */ }
          }
        }
      } else {
        // Default visibility ("all"): write to Redis stream so webchat clients
        // receive the message via StreamSubscriber (XREAD), AND publish to Kafka
        // for conversation history persistence and non-webchat channel delivery.
        if (deps?.redis) {
          try {
            if (hasMenu) {
              // Interactive menu: write interaction_request to stream so webchat
              // StreamSubscriber delivers it as an interaction.request WS event.
              await writeStreamEntry(deps.redis, {
                stream_key:  `session:${parsed.session_id}:stream`,
                type:        "interaction_request",
                author_id:   authorId,
                author_role: "specialist",
                visibility:  "all",
                segment_id:  parsed.segment_id,
                payload: {
                  menu_id:       messageId,
                  interaction:   parsed.menu!.interaction,
                  prompt:        parsed.message,
                  options:       parsed.menu!.options        ?? [],
                  fields:        parsed.menu!.fields         ?? null,
                  masked_fields: parsed.menu!.masked_fields  ?? null,
                  masked_types: parsed.menu!.masked_types   ?? null,
                },
                timestamp,
              })
            } else {
              // Plain text notification: write message to stream.
              await writeStreamEntry(deps.redis, {
                stream_key:  `session:${parsed.session_id}:stream`,
                type:        "message",
                author_id:   authorId,
                author_role: "specialist",
                visibility:  "all",
                segment_id:  parsed.segment_id,
                payload: {
                  message_id: messageId,
                  content:    { type: "text", text: parsed.message },
                },
                timestamp,
              })
            }
          } catch { /* non-fatal — stream may not exist yet */ }

          // Publish to agent:events so Agent Assist UI receives the message too.
          try {
            await deps.redis.publish(`agent:events:${parsed.session_id}`, JSON.stringify({
              type:       hasMenu ? "menu.render" : "message.text",
              session_id: parsed.session_id,
              message_id: messageId,
              author:     { type: "agent_ai", id: authorId },
              text:       parsed.message,
              timestamp,
              visibility: "all",
              ...(hasMenu ? {
                menu_id:       messageId,
                interaction:   parsed.menu!.interaction,
                prompt:        parsed.message,
                options:       parsed.menu!.options  ?? [],
                fields:        parsed.menu!.fields   ?? null,
              } : {}),
            }))
          } catch { /* non-fatal */ }
        }

        // Kafka publish for conversation history persistence (outbound_consumer
        // appends to session messages list) and non-webchat channel delivery.
        // Wrapped in try-catch: Kafka delivery is best-effort. A transient Kafka
        // failure must NOT abort the skill flow — the Redis stream write above
        // already guaranteed the webchat client received the message.
        if (deps?.kafka) {
          try {
            if (hasMenu) {
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
                  masked_types: parsed.menu!.masked_types   ?? null,
                timestamp,
              })
            } else {
              await deps.kafka.publish("conversations.outbound", {
                type:       "message.text",
                contact_id: contactId,
                session_id: parsed.session_id,
                message_id: messageId,
                channel,
                direction:  "outbound",
                author:     { type: "agent_ai", id: authorId },
                content:    { type: "text", text: parsed.message },
                text:       parsed.message,   // kept for channel-gateway backward compat
                timestamp,
              })
            }
          } catch (kafkaErr) {
            // Non-fatal: log and continue. Webchat delivery via Redis stream is
            // unaffected. Analytics will have a gap for this message only.
            console.warn(
              `[notification_send] conversations.outbound publish failed for session ${parsed.session_id}:`,
              kafkaErr instanceof Error ? kafkaErr.message : String(kafkaErr),
            )
          }
        }
      }

      // ── Publish to conversations.events for ClickHouse analytics persistence ──
      // This ensures messages from notification_send (AI agent outbound) are
      // queryable in the analytics-api SessionTranscript ClickHouse fallback.
      if (deps?.kafka && metaTenantId) {
        try {
          await deps.kafka.publish("conversations.events", {
            event_type:   "message_sent",
            session_id:   parsed.session_id,
            tenant_id:    metaTenantId,
            message_id:   messageId,
            author_id:    authorId,
            author_role:  "primary",
            content:      parsed.message,
            content_type: "text",
            visibility:   typeof parsed.visibility === "string" ? parsed.visibility : JSON.stringify(parsed.visibility),
            timestamp,
          })
        } catch { /* non-fatal — analytics persistence is best-effort */ }
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

      // ── TENANT NÃO TEM DEFAULT (2026-08-18) ───────────────────────────────
      //
      // O DEFEITO QUE ISTO FECHA, medido ao vivo. `tenantId` nascia `"default"` e
      // `channel` nascia `"webchat"`, e a leitura do meta era envolvida por um
      // `catch {}` comentado *"ignore — use fallbacks"*. Numa sessão SEM
      // `session:{id}:meta` (o trigger do endpoint webhook externo não cria a
      // chave — só o canal na conexão e o caminho interno de delegate/collect
      // criam) o evento saía com `tenant_id="default"`, e o Routing Engine
      // enfileirava o contato num tenant que não existe: `default:pool:…:queue`,
      // `default:queue_contact:…`, `default:ctx:…`. Nenhuma instância mora lá, então
      // o contato NUNCA é alocado.
      //
      // O que o cliente vive: o agente de fila diz *"vou transferir você agora"*, o
      // segmento fecha com `escalated_human` e o contato morre em silêncio. Nada fica
      // vermelho — é o "valor plausível" da § Postura, com o agravante de o valor
      // inventado ser um IDENTIFICADOR DE ISOLAMENTO. Um tenant fabricado não é
      // degradação: é escrever estado de um contato real num namespace alheio.
      //
      // REGRA: identidade não tem fallback. Canal tem (é preferência de renderização,
      // e um palpite errado degrada a entrega); tenant NÃO (é a fronteira de
      // isolamento, e um palpite errado corrompe a fronteira). Sem tenant conhecido a
      // escalação RECUSA, alto, e o `on_failure` do flow decide o que fazer.
      let contactId  = parsed.session_id
      let tenantId   = ""
      let customerId = parsed.session_id
      // "chat" não é canal válido do ConversationInboundEvent e faria o Routing
      // Engine descartar o evento em silêncio — normalizado abaixo.
      let channel    = "webchat"
      let metaFound  = false

      if (deps?.redis) {
        try {
          const meta = await deps.redis.get(`session:${parsed.session_id}:meta`)
          if (meta) {
            metaFound = true
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
        } catch (err) {
          // Degradação nunca silenciosa: o `catch` mudo aqui foi metade do defeito —
          // ele transformava "o Redis falhou" e "a sessão não tem meta" na MESMA
          // ausência, e as duas caíam no tenant inventado.
          console.error(
            `[conversation_escalate] falha ao ler session:${parsed.session_id}:meta —`,
            err,
          )
        }
      }

      if (!tenantId) {
        console.error(
          `[conversation_escalate] RECUSADA: session=${parsed.session_id} ` +
          `target_pool=${parsed.target_pool} — tenant_id desconhecido ` +
          `(session:{id}:meta ${metaFound ? "existe mas não traz tenant_id" : "AUSENTE"}). ` +
          `A escalação NÃO foi publicada: publicá-la com um tenant inventado enfileira ` +
          `o contato num namespace que não tem instância nenhuma, e ele morre em silêncio ` +
          `depois de o cliente ser avisado da transferência. Origem provável: sessão criada ` +
          `por um caminho que não escreve o meta (trigger de webhook externo).`,
        )
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({
              escalated:  false,
              error:      "tenant_unknown",
              session_id: parsed.session_id,
              target_pool: parsed.target_pool,
              detail:     "session meta ausente ou sem tenant_id — escalação recusada",
            }),
          }],
          isError: true,
        }
      }

      // Write participant_left to the session stream so the webchat client sees the
      // AI agent leaving before the human agent joins.
      // participant_id is not available in this context (no session JWT) — use "ai-agent"
      // as a stable label. role "ai" lets the webchat render a transfer notification
      // instead of a generic leave message.
      if (deps?.redis) {
        try {
          await writeStreamEntry(deps.redis, {
            stream_key:  `session:${parsed.session_id}:stream`,
            type:        "participant_left",
            author_id:   "ai-agent",
            author_role: "specialist",
            visibility:  "all",
            payload:     { participant_id: "ai-agent", reason: "escalated" },
          })
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
          escalation_reason: parsed.escalation_reason,
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

  // ── mention_command_dispatch — REMOVIDA em 2026-09-02 (ALW-07) ──────────────
  //
  // Era a SEGUNDA casa do `set_context` de `mention_commands`, e **nao tinha
  // chamador**. A propria descricao dela dizia *"Chamado pelo orchestrator bridge
  // quando detecta mention_routing:true"* — e o bridge nunca chamou: ele implementa
  // a acao por dentro (`main.py` `process_mention_routing` -> `dispatch_mention_command`,
  // ligado ao consumer Kafka). Medido: zero referencias em bridge, skill YAML, UI e
  // e2e; o unico texto que restava era o desta descricao, prometendo um chamador.
  //
  // Por que REMOVER e nao unificar: as duas casas divergiam desde a ALW-02 (esta
  // roteava `journey.*` para o hash do processo; a Python grava no da sessao e AVISA),
  // e duas respostas para o mesmo fato significam que a permissiva vale. Com uma delas
  // morta, escolher e deletar — nao ha o que mesclar. A casa que fica e a que RODA.
  //
  // ⚠️ O que se perde, dito por inteiro: o roteamento correto de `journey.*` neste
  // caminho. Nao ha regressao hoje — o censo de 2026-09-02 achou UMA declaracao viva
  // de `set_context` no repositorio (`agente_copilot_v1` / `pausa` ->
  // `session.copilot.mode`, escopo de sessao) e ZERO `journey.*`. A limitacao que
  // sobrevive e a divida ALW-03 do funil Python, ja registrada la, e ela degrada
  // BARULHENTA: `write_context_tags(on_foreign_scope="warn")` grava e loga que gravou
  // no lugar errado.
  //
  // Bonus medido: a tool estava na tabela do `probe_mcp_tool_guard_census.sh` como
  // `guard|divida` — superficie MCP sem credencial (CAP-09). Some uma.

}
