/**
 * lib/mention-routing.ts
 * Roteamento de @mention — implementação ÚNICA, compartilhada pelas duas
 * superfícies que podem originar uma menção.
 *
 * Antes da F5 havia DUAS implementações independentes:
 *   - `server.ts` (WebSocket do Console) — completa, e correta por construção:
 *     o pool vem do query-param da conexão, e há uma conexão WS por pool.
 *   - `tools/session.ts` (tool MCP `message_send`) — incompleta (publicava só o
 *     evento de dispatch, nunca o de alocação) e quebrada em duas frentes:
 *     lia o pool com `HGET` de uma chave que é String JSON (WRONGTYPE engolido
 *     por `catch {}` ⇒ menções caíam em silêncio) e, ainda que o tipo estivesse
 *     certo, leria o `pool_id` GLOBAL da instância em vez do pool da sessão.
 *
 * Consertar só o tipo trocaria um no-op silencioso por um convite ao pool errado.
 * A correção estrutural é esta: o pool do remetente é **parâmetro**, resolvido
 * por quem o conhece no escopo certo (o WS conhece pela conexão; a tool MCP
 * resolve pelo registro por-(sessão, instância) — ver `lib/routing-ref.ts`).
 *
 * ADR `docs/adr/adr-human-agent-pool-scoped-identity.md` § B6.
 * Protocolo: `docs/guias/mention-protocol.md`.
 *
 * Invariante preservado: apenas `role: primary` ou `role: human` emite @mention.
 * O gate é do CHAMADOR — este módulo não sabe quem chamou e não deve adivinhar.
 */

import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import { parseMentions }      from "./mention-parser"

export interface RouteMentionsParams {
  /** Texto integral da mensagem do agente (com os tokens @alias). */
  text:       string
  tenantId:   string
  sessionId:  string
  /**
   * Pool do REMETENTE nesta sessão — nunca o `pool_id` do registro global da
   * instância. Define o domínio de `mentionable_pools`, ou seja, quais aliases
   * este agente pode invocar daqui.
   */
  senderPoolId: string
  /** Identidade do emissor para os consumidores (participant_id ou instance_id). */
  fromParticipantId: string
  redis:      RedisClient
  kafka:      KafkaProducer
  timestamp:  string
  /** Prefixo de log da superfície chamadora, p.ex. "[agent-ws]". */
  logPrefix?: string
}

/**
 * Resolve os @alias de `text` contra o `mentionable_pools` do pool do remetente e
 * convida cada pool alvo.
 *
 * Dois eventos por alias resolvido:
 *   (a) `mention_routing: true` — despacha o comando para um especialista JÁ ATIVO
 *       na sessão (consumido pelo `process_mention_routing` do orchestrator-bridge).
 *   (b) `ConversationInboundEvent` completo com `conference_id` — faz o Routing
 *       Engine ALOCAR o especialista quando ele ainda não está rodando na sessão.
 *       O guard de dedup do `process_routed` evita ativação dupla.
 *
 * Best-effort quanto a NÃO bloquear a entrega da mensagem — mas nunca silencioso:
 * todo caminho que não roteia diz por quê.
 *
 * @returns quantidade de aliases efetivamente roteados.
 */
export async function routeMentions(p: RouteMentionsParams): Promise<number> {
  const {
    text, tenantId, sessionId, senderPoolId, fromParticipantId,
    redis, kafka, timestamp,
  } = p
  const log = p.logPrefix ?? "[mention]"

  const parsed = parseMentions(text)
  if (!parsed.has_mentions) return 0

  if (!senderPoolId) {
    // Sem pool do remetente não há domínio de aliases. Antes da F5 este caso era
    // um `return` mudo — e era o estado PERMANENTE da tool MCP.
    console.warn(
      `${log} @mention ignorada: pool do remetente não resolvido ` +
      `(session=${sessionId} from=${fromParticipantId}) — sem domínio de mentionable_pools`
    )
    return 0
  }

  try {
    // Metadados da sessão — necessários apenas para o evento (b).
    let customerId = ""
    let channel    = "webchat"
    try {
      const metaRaw = await redis.get(`session:${sessionId}:meta`)
      if (metaRaw) {
        const meta = JSON.parse(metaRaw) as Record<string, string>
        if (meta["customer_id"]) customerId = meta["customer_id"]
        if (meta["channel"])     channel    = meta["channel"]
      }
      if (!customerId) {
        const cidRaw = await redis.get(`session:${sessionId}:contact_id`)
        if (cidRaw) customerId = cidRaw
      }
    } catch { /* defaults acima */ }

    const poolConfigRaw = await redis.get(`${tenantId}:pool_config:${senderPoolId}`)
    if (!poolConfigRaw) {
      console.warn(
        `${log} @mention ignorada: sem pool_config para pool="${senderPoolId}" ` +
        `(tenant=${tenantId}, session=${sessionId})`
      )
      return 0
    }

    const poolConfig = JSON.parse(poolConfigRaw) as Record<string, unknown>
    const mentionablePools =
      poolConfig["mentionable_pools"] && typeof poolConfig["mentionable_pools"] === "object"
        ? (poolConfig["mentionable_pools"] as Record<string, string>)
        : {}

    let routed = 0
    for (const mention of parsed.mentions) {
      const targetPoolId = mentionablePools[mention.alias]
      if (!targetPoolId) {
        console.log(
          `${log} @mention alias "${mention.alias}" não está em mentionable_pools ` +
          `do pool "${senderPoolId}" — ignorado`
        )
        continue
      }

      // Resolve referências @ctx.* dos argumentos contra o ContextStore da sessão.
      const mentionArgs: Record<string, string> = {}
      for (const ref of mention.ctx_refs) {
        try {
          const entryRaw = await redis.hget(`${tenantId}:ctx:${sessionId}`, ref.field)
          if (entryRaw) {
            const entry = JSON.parse(entryRaw) as { value?: unknown }
            mentionArgs[ref.field] = String(entry.value ?? ref.fallback)
          } else {
            mentionArgs[ref.field] = ref.fallback
          }
        } catch {
          mentionArgs[ref.field] = ref.fallback
        }
      }

      console.log(
        `${log} @mention routing: alias="${mention.alias}" → pool="${targetPoolId}" ` +
        `session="${sessionId}" from_pool="${senderPoolId}"`
      )

      // (a) dispatch para especialista já ativo
      await kafka.publish("conversations.inbound", {
        mention_routing:     true,
        session_id:          sessionId,
        tenant_id:           tenantId,
        pool_id:             targetPoolId,
        alias:               mention.alias,
        mention_text:        mention.args_raw || "",
        mention_args:        mentionArgs,
        from_participant_id: fromParticipantId,
        from_pool_id:        senderPoolId,
        timestamp,
      })

      // (b) alocação via Routing Engine quando ainda não está ativo
      await kafka.publish("conversations.inbound", {
        session_id:    sessionId,
        tenant_id:     tenantId,
        customer_id:   customerId || sessionId,
        channel,
        pool_id:       targetPoolId,
        conference_id: sessionId,   // sinaliza modo conferência/assist
        started_at:    new Date().toISOString(),
        elapsed_ms:    0,
      })

      routed++
    }

    return routed
  } catch (err) {
    console.error(`${log} @mention routing error (não-fatal): session=${sessionId} —`, err)
    return 0
  }
}
