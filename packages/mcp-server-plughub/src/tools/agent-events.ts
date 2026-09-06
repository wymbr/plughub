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
 *   - category regex: 2–AGENT_EVENT_CATEGORY_MAX_SEGMENTS dot-separated snake_case
 *     segments (hoje 8; a constante vive em `@plughub/schemas/agent-events.ts` e e
 *     quem manda — este comentario dizia 5 e estava OBSOLETO)
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
  AGENT_EVENT_CATEGORY_MAX_SEGMENTS,
  AGENT_EVENT_PII_TAG_KEYS,
  decomposeCategoryLevels,
  sanitizeCategoryPath,
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

/**
 * Entrada do `agent_event_record` — a porta de skill-flow para o Arc 12.
 *
 * ⚠️ NAO recebe `category`: ela e COMPOSTA no servidor a partir do pool da sessao.
 * Receber a string inteira obrigaria a CONFERIR o primeiro segmento depois (o que o
 * `agent_event` faz); compondo, o isolamento de namespace vale por construcao.
 */
const AgentEventRecordInputSchema = z.object({
  session_id: z.string().min(1),
  /** l2 — rotulo ESTAVEL do produtor. Nunca um `skill_id`: rename quebraria a serie. */
  emitter:    z.string().min(1),
  /** l3 — o que se mede. */
  metric_key: z.string().min(1),
  /** Cauda pontuada abaixo da metrica (ex.: `sac.info_plano`). */
  path:       z.string().optional(),
  value:      z.number().default(1),
  tags:       z.record(z.string()).optional(),
  segment_id: z.string().optional(),
  tenant_id:  z.string().optional(),
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

// ─── Construtor compartilhado do evento (Arc 12 fatia 3) ──────────────────────

/**
 * Monta a linha de `agent.events`. Existe para haver UM lugar que conhece a forma
 * do evento.
 *
 * O segundo produtor é o `segment_outcome_record` (wrap-up, fatia 3), que NÃO pode
 * chamar a tool `agent_event`: aquela exige `session_token` de `agent_login`, e o
 * wrap-up roda como workflow, sem agente logado. A alternativa seria repetir o
 * literal do evento lá — e "duas implementações da mesma fórmula" é precisamente o
 * defeito que o CLAUDE.md nomeia em `_refresh_pool_snapshots` (a segunda cópia
 * virou a principal e passou a divergir em silêncio).
 *
 * `segment_id`/`instance_id` são `null`, nunca `""`: a coluna é Nullable para que o
 * relatório distinga "não sabemos quem emitiu" de um segmento real.
 */
export function buildAgentBusinessEvent(args: {
  tenant_id:     string
  session_id:    string
  category:      string
  value:         number
  agent_type_id: string
  skill_id?:     string
  pool_id?:      string
  journey_id?:   string | null
  tags?:         Record<string, string>
  segment_id?:   string | null
  instance_id?:  string | null
}): Record<string, unknown> {
  // `decomposeCategoryLevels` devolve OBJETO `{l1,l2,l3,l4}`, não array — conferido
  // na assinatura (`schemas/agent-events.ts:188`). Destructuring por posição
  // compilaria em JS e produziria quatro `undefined`, com o evento saindo com as
  // colunas de categoria vazias: linha gravada, série silenciosamente inútil.
  const { l1: category_l1, l2: category_l2, l3: category_l3, l4: category_l4 } =
    decomposeCategoryLevels(args.category)
  return {
    event_id:      crypto.randomUUID(),
    tenant_id:     args.tenant_id,
    session_id:    args.session_id,
    journey_id:    args.journey_id ?? null,
    agent_type_id: args.agent_type_id,
    skill_id:      args.skill_id || category_l2 || "",
    pool_id:       args.pool_id  || category_l1 || "",
    category:      args.category,
    category_l1, category_l2, category_l3, category_l4,
    value:         args.value,
    tags:          args.tags ?? {},
    emitted_at:    new Date().toISOString(),
    segment_id:    args.segment_id  ?? null,
    instance_id:   args.instance_id ?? null,
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
  // ── agent_event_record ──────────────────────────────────────────────────────
  //
  // A tool `agent_event` exige `session_token` de `agent_login` — e um agente NATIVO
  // de skill-flow nao tem nenhum: o orchestrator-bridge nao emite token (a palavra
  // `session_token` aparece ZERO vezes no `main.py` dele) e o `session_context` que
  // ele monta traz `contact_id`/`channel`/`tenant_id`/`agent_type`/`session_id`, mais
  // nada. Medido em 2026-09-06: e por isso que `agent_event` tinha ZERO chamadores.
  //
  // Esta e a MESMA postura que o `segment_outcome_record` ja usa para o wrap-up
  // ("roda como workflow, sem agente logado"): identifica-se pela SESSAO, nao por
  // token. A formula do evento continua numa casa so — `buildAgentBusinessEvent` —,
  // e o que muda entre as portas e apenas COMO o emissor e identificado.
  //
  // ⚠️ A CATEGORIA E COMPOSTA AQUI, nunca aceita pronta. `agent_event` recebe a
  // string inteira e depois CONFERE que o primeiro segmento e o pool da sessao;
  // compondo, o isolamento de namespace passa a valer **por construcao** — nao ha
  // string que o chamador possa mandar para furar. E o chamador deixa de precisar
  // saber em que pool ele roda, o que importa porque o mesmo skill roda em N pools.
  server.tool(
    "agent_event_record",
    "Emit an Arc 12 business event from a skill-flow agent, identified by SESSION (no " +
    "agent_login token — the native skill-flow path has none). The category is COMPOSED " +
    "server-side as {pool}.{emitter}.{metric_key}[.{path}], with the pool read from the " +
    "session meta: namespace isolation holds by construction, and the caller does not " +
    "need to know which pool it runs in. `emitter` is a STABLE label (e.g. \"navegacao\"), " +
    "never a skill_id — using the skill id would break the series on every rename.",
    AgentEventRecordInputSchema.shape as any,
    async (raw: Record<string, unknown>) => {
      let a: z.infer<typeof AgentEventRecordInputSchema>
      try {
        a = AgentEventRecordInputSchema.parse(raw)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      try {
        // Pool e tenant vem do META da sessao — nunca do chamador. E a diferenca
        // entre isolamento por construcao e isolamento por conferencia.
        const metaRaw = await redis.get(`session:${a.session_id}:meta`)
        if (!metaRaw) {
          return mcpError(
            "session_not_found",
            `sessao ${a.session_id} sem meta no Redis — sem pool nao ha categoria, e ` +
            `inventar um l1 poria o evento no namespace de outro pool`,
          )
        }
        const meta = JSON.parse(metaRaw) as Record<string, unknown>
        const poolId = typeof meta["pool_id"] === "string" ? meta["pool_id"] : ""
        if (!poolId) {
          return mcpError("pool_unknown", `sessao ${a.session_id} sem pool_id no meta — categoria indeterminavel`)
        }
        const tenantId = a.tenant_id || (typeof meta["tenant_id"] === "string" ? meta["tenant_id"] : "")

        const partes = [poolId, a.emitter, a.metric_key]
        if (a.path) partes.push(a.path)
        const category = sanitizeCategoryPath(partes.join("."))

        // Teto do Arc 12: recusar AQUI, nomeando, transforma caminho fundo demais em
        // erro de autoria. Emitir produziria um evento que o schema rejeita depois,
        // longe daqui — buraco na serie em vez de mensagem.
        if (category.split(".").length > AGENT_EVENT_CATEGORY_MAX_SEGMENTS) {
          return mcpError(
            "category_too_deep",
            `categoria ${category} tem ${category.split(".").length} segmentos, teto ${AGENT_EVENT_CATEGORY_MAX_SEGMENTS}`,
          )
        }

        await kafka.publish("agent.events", buildAgentBusinessEvent({
          tenant_id:     tenantId,
          session_id:    a.session_id,
          category,
          value:         a.value ?? 1,
          agent_type_id: typeof meta["agent_type_id"] === "string" ? meta["agent_type_id"] : "",
          skill_id:      a.emitter,
          pool_id:       poolId,
          segment_id:    a.segment_id ?? null,
          tags:          a.tags ?? {},
        }))
        return ok({ emitted: true, category, pool_id: poolId })
      } catch (err) {
        return mcpError("emit_failed", err instanceof Error ? err.message : String(err))
      }
    },
  )
}
