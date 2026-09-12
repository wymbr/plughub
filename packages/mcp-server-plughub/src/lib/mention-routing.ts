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
 * Invariante preservado: **quem CONDUZ menciona** — `role === "primary"`, humano ou
 * IA indiferentemente. O gate é do CHAMADOR — este módulo não sabe quem chamou e não
 * deve adivinhar —, mas a DECISÃO é uma só, em `lib/participant-role.ts`
 * (`mayRouteMentions`), consumida pelos dois chamadores. *(Reescrito em 2026-09-12,
 * MEN-01: dizia "`role: primary` ou `role: human`", e `human` nunca existiu no domínio
 * de papel — era o que fazia a regra parecer falar de ESPÉCIE em vez de posição.)*
 */

import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import { parseMentions }      from "./mention-parser"
import { writeStreamEntry }   from "./write-stream-entry"

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
  /**
   * Papel do emissor NESTA sessão, já resolvido e provado pelo chamador.
   *
   * Não tem default de propósito: os dois chamadores acabaram de resolvê-lo para
   * decidir se podiam chamar esta função (`mayRouteMentions`), e um default aqui
   * inventaria um autor para o evento durável que ninguém conferiu.
   */
  fromRole: string
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
/**
 * Aviso ao EMISSOR de que o comando dele foi recebido, e com que desfecho.
 *
 * Endereçado: `recipient_participant_id` nomeia quem deve ver. O canal
 * `agent:events:{sid}` é de sessão e chega a todos os agentes — a filtragem é do
 * consumidor, como em toda entrega por este canal —, mas o campo existe para que
 * "só o emissor vê" seja EXPRESSÁVEL em vez de presumido.
 *
 * ⚠️ Isto NÃO é a casa da autoria. Pub/sub é efêmero e best-effort: quem responde
 * *"quem convidou este especialista?"* é o evento `mention_command` no stream, que é
 * durável. Confundir os dois foi o risco que a MEN-06 nomeou — feedback e registro
 * têm tempos de vida diferentes e por isso não moram juntos.
 */
async function avisaEmissor(
  redis:  RedisClient,
  sid:    string,
  dados:  Record<string, unknown>,
  log:    string,
): Promise<void> {
  try {
    await redis.publish(`agent:events:${sid}`, JSON.stringify({
      type: "mention.ack",
      session_id: sid,
      ...dados,
    }))
  } catch (err) {
    // Não-fatal: o comando já rodou. Mas nunca silencioso — sem este log, o
    // emissor não recebe nada e ninguém sabe que era o aviso que faltou.
    console.warn(`${log} @mention ack não publicado (session=${sid}) —`, err)
  }
}

export async function routeMentions(p: RouteMentionsParams): Promise<number> {
  const {
    text, tenantId, sessionId, senderPoolId, fromParticipantId, fromRole,
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
        // O emissor precisa saber, e antes de 2026-09-12 ele não sabia: o texto dele
        // aparecia na tela e nada acontecia, indistinguível de comando aceito. Agora
        // o alias desconhecido tem desfecho NOMEADO.
        //
        // Não gera evento no stream de propósito: `mention_command` registra o que
        // ACONTECEU na sessão, e alias que não resolve não é fato da sessão — é
        // retorno ao emissor.
        await avisaEmissor(redis, sessionId, {
          recipient_participant_id: fromParticipantId,
          from_participant_id:      fromParticipantId,
          alias:                    mention.alias,
          outcome:                  "unknown_alias",
          at:                       timestamp,
        }, log)
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

      // ── o comando, como EVENTO ────────────────────────────────────────
      // Durável, no stream canônico, e é aqui que vive a AUTORIA do convite: o
      // `participant_joined` do convidado registra quem ENTROU, nunca quem PEDIU.
      // Até 2026-09-12 esse elo só existia no texto `agents_only` da mensagem — que
      // a MEN-05 removeu justamente por misturar comando com conteúdo. Tirar o texto
      // sem criar esta casa teria apagado a autoria sem nada ficar vermelho.
      //
      // `agents_only`: o cliente nunca vê comando de plataforma. E nenhum leitor
      // entrega tipo desconhecido ao cliente de qualquer forma — o
      // `stream_subscriber` casa tipo por `if` encadeado (medido).
      try {
        await writeStreamEntry(redis, {
          stream_key:  `session:${sessionId}:stream`,
          type:        "mention_command",
          author_id:   fromParticipantId,
          author_role: fromRole,
          visibility:  "agents_only",
          timestamp,
          payload: {
            alias:          mention.alias,
            target_pool_id: targetPoolId,
            from_pool_id:   senderPoolId,
            args:           mentionArgs,
            outcome:        "routed",
          },
        })
      } catch (err) {
        // Não-fatal para o roteamento — o convite já foi publicado —, mas barulhento:
        // sem este entry a sessão fica com um especialista que ninguém convidou.
        console.warn(
          `${log} @mention: comando roteado mas NÃO registrado no stream ` +
          `(session=${sessionId}, alias="${mention.alias}") — a autoria do convite ` +
          `se perdeu neste caso:`, err
        )
      }

      await avisaEmissor(redis, sessionId, {
        recipient_participant_id: fromParticipantId,
        from_participant_id:      fromParticipantId,
        alias:                    mention.alias,
        outcome:                  "routed",
        target_pool_id:           targetPoolId,
        at:                       timestamp,
      }, log)

      routed++
    }

    return routed
  } catch (err) {
    console.error(`${log} @mention routing error (não-fatal): session=${sessionId} —`, err)
    return 0
  }
}
