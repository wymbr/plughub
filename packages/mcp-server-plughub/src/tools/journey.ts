/**
 * tools/journey.ts
 * Journey J3 — merge de journeys por raiz.
 *
 * Tool:
 *   journey_merge — grava uma aresta de alias da raiz mais NOVA (source) para a
 *   mais ANTIGA (canonical / sobrevivente). Publica em journey.merges; a
 *   analytics-api persiste em journey_aliases e resolve por union-find na leitura.
 *
 * Invariantes:
 *   - NUNCA reescreve root_session_id das sessões — só grava a aresta.
 *   - Ordem novo→antigo (sobrevivente = mais antiga por default) ⇒ floresta sem ciclo.
 *   - O fluxo pode nomear a sobrevivente; a tool só re-ordena para oldest quando
 *     consegue resolver os timestamps das duas raízes (senão confia no chamador).
 *   - Quem pode comandar (role primary/human/N3) é gateado pela lista de permissões
 *     do JWT (McpInterceptor), como qualquer side-effect. Toda chamada é auditada.
 *
 * (Reintroduz o nome journey_merge removido no Arc 19 Fase F, agora SEM entidade
 *  Journey — é apenas uma aresta de proveniência/alias.)
 */
import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { JourneyMergedEventSchema } from "@plughub/schemas"
import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import { verifySessionToken, InvalidTokenError } from "../infra/jwt"

export interface JourneyDeps {
  redis: RedisClient
  kafka: KafkaProducer
}

const JourneyMergeInputSchema = z.object({
  /** JWT from agent_login — resolves tenant_id + actor. */
  session_token:  z.string().min(1),
  /** Raiz da journey a ser absorvida (por default, a mais nova). */
  source_root:    z.string().min(1),
  /** Raiz sobrevivente (por default, a mais antiga). */
  canonical_root: z.string().min(1),
})

type ToolResult = { isError?: true; content: Array<{ type: "text"; text: string }> }
function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}
function mcpError(code: string, message: string): ToolResult {
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }] }
}

/** Best-effort: idade (ms epoch) de uma raiz a partir do session meta no Redis. */
async function readStartedAt(redis: RedisClient, root: string): Promise<number | null> {
  try {
    const raw = await redis.get(`session:${root}:meta`)
    if (!raw) return null
    const meta = JSON.parse(raw) as Record<string, unknown>
    for (const k of ["started_at", "opened_at", "created_at"]) {
      const v = meta[k]
      if (typeof v === "string") {
        const t = Date.parse(v)
        if (!Number.isNaN(t)) return t
      }
    }
    return null
  } catch {
    return null
  }
}

export function registerJourneyTools(server: McpServer, deps: JourneyDeps): void {
  const { redis, kafka } = deps

  server.tool(
    "journey_merge",
    "Journey J3 — merge two journeys by root. Records an alias edge from the NEWER root " +
    "(source) to the OLDER root (canonical survivor); publishes journey.merges. Idempotent; " +
    "never rewrites root_session_id. Caller may name the survivor — the tool defaults survivor " +
    "= oldest when both roots' timestamps are resolvable.",
    JourneyMergeInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, source_root, canonical_root } = JourneyMergeInputSchema.parse(input)

        let tenant_id: string
        let actor:     string
        try {
          const payload = verifySessionToken(session_token)
          tenant_id = payload.tenant_id
          actor     = payload.instance_id || payload.agent_type_id || "agent"
        } catch (e) {
          if (e instanceof InvalidTokenError) {
            return mcpError("invalid_token", "session_token is invalid or expired")
          }
          throw e
        }

        if (source_root === canonical_root) {
          return mcpError("self_merge", "source_root and canonical_root are the same — nothing to merge")
        }

        // ── Survivor = oldest (default). Swap se o canonical do chamador for o mais novo. ──
        let src   = source_root
        let canon = canonical_root
        const [tsSource, tsCanon] = await Promise.all([
          readStartedAt(redis, source_root),
          readStartedAt(redis, canonical_root),
        ])
        if (tsSource !== null && tsCanon !== null) {
          // canonical deve ser a mais ANTIGA (ts menor); desempate lexical por id.
          const sourceIsOlder =
            tsSource < tsCanon || (tsSource === tsCanon && source_root < canonical_root)
          if (sourceIsOlder) { src = canonical_root; canon = source_root }
        }
        // else: mantém a designação do chamador (sobrevivente nomeada pelo fluxo).

        const event = {
          event_id:       crypto.randomUUID(),
          tenant_id,
          source_root:    src,
          canonical_root: canon,
          merged_at:      new Date().toISOString(),
          actor,
        }
        const parsed = JourneyMergedEventSchema.safeParse(event)
        if (!parsed.success) {
          return mcpError("validation_error", parsed.error.errors.map(e => e.message).join("; "))
        }

        try {
          await kafka.publish("journey.merges", parsed.data)
        } catch (kafkaErr) {
          return mcpError("publish_failed", `Failed to publish journey.merges: ${String(kafkaErr)}`)
        }

        return ok({
          merged:         true,
          source_root:    src,
          canonical_root: canon,
          merged_at:      event.merged_at,
        })
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(er => er.message).join("; "))
        }
        if (e instanceof InvalidTokenError) {
          return mcpError("invalid_token", "session_token is invalid or expired")
        }
        return mcpError("internal_error", String(e))
      }
    },
  )
}
