/**
 * tools/agent-events.ts
 * Arc 12 — Agent Business Events MCP tool.
 *
 * Tool:
 *   agent_event — Publish a structured business KPI event from the current session.
 *
 * Input (from agent):
 *   session_token  — JWT from agent_login (resolves tenant_id, agent_type_id, instance_id)
 *   session_id     — current session (required for rate-limit key + Redis meta lookup)
 *   category       — dot-notation hierarchy: pool_id.skill_id.metric_key
 *                    First segment MUST match the session's pool_id (namespace isolation)
 *   value          — numeric KPI (count, duration ms, monetary value, score, …)
 *   tags           — optional map[string,string], max 10 pairs, 64 chars per key/value
 *                    PII keywords are blocked (cpf, email, token, …)
 *
 * Governance enforced here:
 *   - category regex: 2–5 dot-separated snake_case segments
 *   - namespace isolation: category_l1 === session's pool_id (from session meta)
 *   - PII tag keys blocked
 *   - max 10 tags, 64 chars per key/value
 *   - rate limit: 50 events/session (Redis INCR, configurable via AGENT_EVENT_RATE_LIMIT)
 *
 * All calls intercepted by McpInterceptor — audited in mcp.audit (LGPD).
 * Publishes to Kafka topic: agent.events
 */

import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import {
  AgentEventInputSchema,
  AGENT_EVENT_PII_TAG_KEYS,
  decomposeCategoryLevels,
} from "@plughub/schemas"
import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import {
  verifySessionToken,
  InvalidTokenError,
} from "../infra/jwt"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface AgentEventDeps {
  redis: RedisClient
  kafka: KafkaProducer
}

// ─── Input schema (extends AgentEventInputSchema with transport fields) ────────

const AgentEventToolInputSchema = AgentEventInputSchema.extend({
  /** JWT from agent_login — resolves tenant_id, agent_type_id, instance_id. */
  session_token: z.string().min(1),
  /** Current session — used for rate-limit key and pool_id resolution. */
  session_id: z.string().min(1),
})

// ─── Rate-limit config ─────────────────────────────────────────────────────────

const RATE_LIMIT_DEFAULT = 50
const RATE_LIMIT_TTL_S   = 7_200 // 2h — covers long sessions

function getEventRateLimit(): number {
  const raw = process.env["AGENT_EVENT_RATE_LIMIT"]
  if (!raw) return RATE_LIMIT_DEFAULT
  const n = parseInt(raw, 10)
  return Number.isFinite(n) && n > 0 ? n : RATE_LIMIT_DEFAULT
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerAgentEventTools(
  server: McpServer,
  deps:   AgentEventDeps,
): void {
  const { redis, kafka } = deps

  // ── agent_event ─────────────────────────────────────────────────────────────
  server.tool(
    "agent_event",
    "Publish a structured business KPI event for the current session. " +
    "category uses dot-notation: pool_id.skill_id.metric_key. " +
    "First segment must match the session pool. " +
    "Max 50 events per session. PII tag keys (cpf, email, token, …) are blocked. Arc 12.",
    AgentEventToolInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = AgentEventToolInputSchema.parse(input)
        const { session_token, session_id, category, value, tags, segment_id } = parsed

        // ── Decode JWT ──────────────────────────────────────────────────────
        let tenant_id:     string
        let agent_type_id: string
        let instance_id:   string
        try {
          const payload = verifySessionToken(session_token)
          tenant_id     = payload.tenant_id
          agent_type_id = payload.agent_type_id
          instance_id   = payload.instance_id
        } catch (e) {
          if (e instanceof InvalidTokenError) {
            return mcpError("invalid_token", "session_token is invalid or expired")
          }
          throw e
        }

        // ── PII tag key check ───────────────────────────────────────────────
        for (const key of Object.keys(tags)) {
          if (AGENT_EVENT_PII_TAG_KEYS.has(key.toLowerCase())) {
            return mcpError(
              "pii_tag_blocked",
              `Tag key '${key}' is blocked — PII fields are not allowed in agent events`,
            )
          }
        }

        // ── Decompose category ──────────────────────────────────────────────
        const { l1: category_l1, l2: category_l2, l3: category_l3, l4: category_l4 } =
          decomposeCategoryLevels(category)

        // ── Resolve session pool_id for namespace isolation check ───────────
        let pool_id  = ""
        let skill_id = ""
        let journey_id: string | null = null
        try {
          const metaRaw = await redis.get(`session:${session_id}:meta`)
          if (metaRaw) {
            const meta = JSON.parse(metaRaw) as Record<string, unknown>
            if (typeof meta["pool_id"]  === "string") pool_id  = meta["pool_id"]
            if (typeof meta["skill_id"] === "string") skill_id = meta["skill_id"]
            if (typeof meta["journey_id"] === "string") journey_id = meta["journey_id"]
          }
        } catch {
          // non-fatal — best-effort context enrichment
        }

        // ── Namespace isolation: category_l1 must equal session pool_id ─────
        // Only enforced when pool_id is resolvable (non-empty).
        if (pool_id && category_l1 !== pool_id) {
          return mcpError(
            "namespace_mismatch",
            `category first segment '${category_l1}' must match session pool_id '${pool_id}'`,
          )
        }

        // ── Rate limit: INCR counter per session ────────────────────────────
        const rateLimitKey = `${tenant_id}:agent_event_count:${session_id}`
        let count: number
        try {
          count = await redis.incr(rateLimitKey)
          if (count === 1) {
            // Set TTL on first event only (idempotent on subsequent calls)
            await redis.expire(rateLimitKey, RATE_LIMIT_TTL_S)
          }
        } catch {
          // Redis unavailable — fail open (don't block event; log via catch below)
          count = 0
        }
        const rateLimit = getEventRateLimit()
        if (count > rateLimit) {
          return mcpError(
            "rate_limit_exceeded",
            `agent_event rate limit of ${rateLimit} events per session exceeded`,
          )
        }

        // ── Build and publish event ─────────────────────────────────────────
        const event_id   = crypto.randomUUID()
        const emitted_at = new Date().toISOString()

        const event = {
          event_id,
          tenant_id,
          session_id,
          journey_id:    journey_id ?? null,
          agent_type_id,
          skill_id:      skill_id || category_l2 || "",  // fallback to category segment
          pool_id:       pool_id  || category_l1 || "",  // fallback to category segment
          category,
          category_l1,
          category_l2,
          category_l3,
          category_l4,
          value,
          tags,
          emitted_at,
          // Arc 12 fatia 2 (2026-08-03) — atribuição por PARTICIPANTE.
          //
          // `segment_id` (caminho A): vem do skill via `$.segment_id`, built-in que o
          // engine já tem em memória. `null` — não `""` — quando ausente: ausência é
          // um fato diferente de "segmento vazio", e a coluna é Nullable justamente
          // para que o relatório possa distinguir "não sabemos quem emitiu" de um
          // segmento real.
          //
          // `instance_id` (caminho B): já era decodificado do JWT logo acima e
          // DESCARTADO. Publicá-lo custa zero e dá ao consumer a chave para resolver o
          // segmento via `SegmentEnricher` quando A não veio — cobre humanos e replay
          // de DLQ sem I/O extra no caminho quente.
          segment_id:  segment_id ?? null,
          instance_id: instance_id || null,
        }

        try {
          await kafka.publish("agent.events", event)
        } catch (kafkaErr) {
          // Kafka publish failure is non-fatal in terms of acknowledgement,
          // but we do want to surface it so the agent knows the event was lost.
          return mcpError(
            "publish_failed",
            `Failed to publish event to agent.events: ${String(kafkaErr)}`,
          )
        }

        return ok({
          event_id,
          category,
          value,
          emitted_at,
          session_event_count: count,
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
