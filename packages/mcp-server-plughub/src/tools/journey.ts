/**
 * tools/journey.ts
 * Journey lifecycle MCP tools — Arc 10 + Arc 16.
 *
 * Tools:
 *   journey_start          — Create a journey and trigger its governing skill-flow
 *   journey_link_session   — Associate an additional session with an existing journey
 *   journey_merge          — Merge a secondary journey into a primary (Phase D)
 *   journey_split          — Extract collect sessions into a new journey (Phase F)
 *   journey_context_get    — Read tags from the Journey ContextStore namespace (Arc 16)
 *   journey_context_set    — Write a tag to the Journey ContextStore namespace (Arc 16)
 *   journey_list_suspended — List suspended journeys for a pool (Arc 16 Tier 1 poller)
 *   journey_resume         — Resume a suspended journey with optional context (Arc 16)
 *   journey_check_pending  — Check if a customer has journeys with pending collect steps (Arc 16 Phase E)
 *
 * All calls are intercepted by McpInterceptor for permission validation,
 * injection guard, and audit (LGPD). Never bypass.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import type { Redis }     from "ioredis"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface JourneyDeps {
  workflowApiUrl: string   // e.g. http://localhost:3800
  tenantId:       string   // resolved from agent JWT context
  redis:          Redis    // Arc 16: journey ContextStore read/write
}

// ─── Schemas ──────────────────────────────────────────────────────────────────

const JourneyStartInputSchema = z.object({
  skill_id:   z.string().min(1).describe("Skill-flow that governs this service process"),
  session_id: z.string().min(1).describe("Current session — becomes origin_session_id"),
  customer_id: z.string().optional().describe("Customer identifier (caller.*)"),
  metadata:   z.record(z.unknown()).optional().describe("Additional context passed to the workflow"),
})

const JourneyLinkSessionInputSchema = z.object({
  journey_id: z.string().uuid().describe("Journey to associate the session with"),
  session_id: z.string().min(1).describe("Session to link to the journey"),
})

const JourneyMergeInputSchema = z.object({
  journey_id_primary:   z.string().uuid().describe("Primary journey — absorbs the secondary (remains active)"),
  journey_id_secondary: z.string().uuid().describe("Secondary journey — becomes merged (read-only)"),
})

// Arc 16 — Journey ContextStore tools
const JourneyContextGetInputSchema = z.object({
  journey_id: z.string().uuid().describe("Journey whose context to read"),
  tags:       z.array(z.string()).optional().describe(
    "Tags to read. If omitted, all tags in the journey namespace are returned. " +
    "Tag names must use the journey.* prefix (e.g. 'journey.status', 'journey.cpf').",
  ),
})

const JourneyContextSetInputSchema = z.object({
  journey_id:  z.string().uuid().describe("Journey whose context to write"),
  tag:         z.string().describe(
    "Tag name — must start with 'journey.' (e.g. 'journey.approval_decision'). " +
    "Enforced to prevent accidental writes to session namespace.",
  ),
  value:       z.unknown().describe("Value to store (any JSON-serialisable type)"),
  confidence:  z.number().min(0).max(1).default(1.0).describe("Confidence score (0-1)"),
  source:      z.string().default("agent_explicit").describe(
    "Source identifier for audit — e.g. 'agente_portabilidade_v1:step_aprovacao'",
  ),
})

const JourneySplitInputSchema = z.object({
  journey_id:  z.string().uuid().describe("Source journey to extract sessions from"),
  session_ids: z.array(z.string()).min(1).describe(
    "Collect session IDs to move to the new journey. " +
    "Must not include the source journey's origin_session_id. " +
    "Order by started_at so the first element becomes the new journey's origin_session_id.",
  ),
  skill_id: z.string().optional().describe(
    "If provided, triggers a new workflow for the new journey immediately after split. " +
    "If omitted, the new journey is created with status active and no workflow.",
  ),
  metadata: z.record(z.unknown()).optional().describe("Additional context for the new journey"),
})

// Arc 16 Phase E — Inbound journey resume tool

const JourneyCheckPendingInputSchema = z.object({
  customer_id: z.string().min(1).describe(
    "Customer identifier to check — e.g. a phone number, email address, or CRM ID. " +
    "Must match the customer_id recorded when the journey was started.",
  ),
  channel: z.string().optional().describe(
    "Current channel of the inbound contact — e.g. 'whatsapp', 'sms', 'email', 'webchat'. " +
    "When provided, only journeys whose pending collect step requires[] are compatible " +
    "with this channel's capabilities are returned. " +
    "When omitted, all active journeys with a pending collect are returned regardless of channel.",
  ),
  limit: z.number().int().min(1).max(20).default(5).describe(
    "Maximum number of pending journeys to return (default 5).",
  ),
})

// ── Channel capability map (mirrors channel_capability_registry.py) ────────────
// Keep in sync with CHANNEL_CAPABILITIES in channel-gateway.
const _CHANNEL_CAPABILITIES: Record<string, ReadonlySet<string>> = {
  whatsapp: new Set(["text", "file_upload", "rich_menu"]),
  sms:      new Set(["text"]),
  email:    new Set(["text", "file_upload"]),
  voice:    new Set(["audio"]),
  webchat:  new Set(["text", "file_upload", "rich_menu", "masked_input"]),
  webrtc:   new Set(["text", "audio", "video", "file_upload"]),
}

function _channelSatisfies(channel: string, requires: string[]): boolean {
  if (requires.length === 0) return true
  const caps = _CHANNEL_CAPABILITIES[channel]
  if (!caps) return false
  return requires.every(r => caps.has(r))
}

// Arc 16 Phase C — Tier 1 poller tools

const JourneyListSuspendedInputSchema = z.object({
  pool_id: z.string().min(1).describe(
    "Pool whose suspended journeys to list. " +
    "Only journeys created with pool_id matching this value are returned.",
  ),
  skill_id: z.string().optional().describe(
    "Optional skill_id filter — useful when a Tier 1 workflow manages only one skill.",
  ),
  limit: z.number().int().min(1).max(200).default(50).describe(
    "Maximum number of suspended journeys to return (default 50).",
  ),
})

const JourneyResumeInputSchema = z.object({
  journey_id: z.string().uuid().describe("Journey to resume — must be in status 'suspended'"),
  context:    z.record(z.unknown()).optional().describe(
    "Optional key/value pairs forwarded as payload in the workflow.resumed Kafka event. " +
    "For durable storage, call journey_context_set before calling journey_resume.",
  ),
  decision: z.enum(["input", "approval", "webhook"]).default("input").describe(
    "Resume decision type — passed through to the workflow engine's resumeContext.decision.",
  ),
})

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

async function callWorkflowApi(
  url: string,
  method: "POST" | "PATCH" | "GET",
  tenantId: string,
  body?: unknown,
): Promise<{ ok: boolean; status: number; data: unknown }> {
  let resp: Response
  try {
    resp = await fetch(url, {
      method,
      headers: {
        "Content-Type":  "application/json",
        "x-tenant-id":   tenantId,
        "x-internal":    "1",
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    })
  } catch (networkErr) {
    console.error(
      `[journey] Network error calling ${method} ${url}:`,
      networkErr instanceof Error ? networkErr.message : String(networkErr),
    )
    throw networkErr
  }
  const data = await resp.json().catch(() => ({ detail: resp.statusText }))
  if (!resp.ok) {
    console.error(
      `[journey] ${method} ${url} → HTTP ${resp.status}:`,
      JSON.stringify(data),
    )
  }
  return { ok: resp.ok, status: resp.status, data }
}

// ─── Registration ─────────────────────────────────────────────────────────────

// Journey ContextStore TTL: 30 days (in seconds)
const JOURNEY_CTX_TTL_S = 30 * 24 * 60 * 60

export function registerJourneyTools(server: McpServer, deps: JourneyDeps): void {
  const { workflowApiUrl, tenantId, redis } = deps

  // ── journey_start ─────────────────────────────────────────────────────────

  server.tool(
    "journey_start",
    "Create a Journey and trigger its governing skill-flow workflow. " +
    "A Journey groups all sessions involved in resolving a single service process " +
    "and enables end-to-end observability and KPIs. " +
    "Returns journey_id and workflow_instance_id.",
    JourneyStartInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyStartInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        console.error("[journey_start] INVALID_INPUT — rawInput:", JSON.stringify(rawInput), "error:", parsed.error.message)
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { skill_id, session_id, customer_id, metadata } = parsed.data
      console.log(`[journey_start] skill_id=${skill_id} session_id=${session_id} url=${workflowApiUrl}/v1/journeys`)

      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys`,
        "POST",
        tenantId,
        {
          skill_id,
          origin_session_id: session_id,
          customer_id:       customer_id ?? null,
          metadata:          metadata ?? null,
        },
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_start failed: ${detail}`,
        )
      }

      const body = result.data as Record<string, unknown>
      return ok({
        journey_id:           body.journey_id,
        workflow_instance_id: body.workflow_instance_id,
        status:               body.status,
        skill_id:             body.skill_id,
        origin_session_id:    body.origin_session_id,
      })
    },
  )

  // ── journey_link_session ──────────────────────────────────────────────────

  server.tool(
    "journey_link_session",
    "Associate an additional session with an existing Journey. " +
    "Use when a customer contacts again as part of the same service process " +
    "(e.g. a follow-up call or a collect step response). " +
    "Merged journeys cannot receive new sessions.",
    JourneyLinkSessionInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyLinkSessionInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { journey_id, session_id } = parsed.data

      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys/${journey_id}/link-session`,
        "POST",
        tenantId,
        { session_id },
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_link_session failed: ${detail}`,
        )
      }

      return ok(result.data)
    },
  )

  // ── journey_merge ─────────────────────────────────────────────────────────

  server.tool(
    "journey_merge",
    "Merge a secondary Journey into a primary Journey. " +
    "The secondary journey becomes read-only (status: merged) and its sessions " +
    "are associated with the primary journey for end-to-end tracking. " +
    "This operation is irreversible. " +
    "Use when duplicate journeys are detected for the same service process, " +
    "or when a follow-up contact spawned a new journey that belongs to an existing one.",
    JourneyMergeInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyMergeInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { journey_id_primary, journey_id_secondary } = parsed.data

      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys/${journey_id_primary}/merge`,
        "POST",
        tenantId,
        { journey_id_secondary },
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_merge failed: ${detail}`,
        )
      }

      return ok(result.data)
    },
  )

  // ── journey_split ─────────────────────────────────────────────────────────

  server.tool(
    "journey_split",
    "Extract collect sessions from an existing Journey into a new independent Journey. " +
    "Use when sessions within a journey belong to a different service process than originally intended. " +
    "The source journey retains its workflow_instance_id. " +
    "The new journey starts with status active and no workflow unless skill_id is provided. " +
    "Constraints: session_ids must be collect sessions of the source journey; " +
    "the source journey's origin_session_id cannot be moved; " +
    "merged journeys are read-only. This operation is irreversible.",
    JourneySplitInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneySplitInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { journey_id, session_ids, skill_id, metadata } = parsed.data

      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys/${journey_id}/split`,
        "POST",
        tenantId,
        { session_ids, skill_id: skill_id ?? null, metadata: metadata ?? null },
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_split failed: ${detail}`,
        )
      }

      const body = result.data as Record<string, unknown>
      return ok({
        new_journey_id:           body.new_journey_id,
        new_workflow_instance_id: body.new_workflow_instance_id,
        source_journey_id:        body.source_journey_id,
        moved_count:              body.moved_count,
      })
    },
  )

  // ── journey_context_get (Arc 16) ──────────────────────────────────────────

  server.tool(
    "journey_context_get",
    "Read context tags from the Journey ContextStore namespace — Arc 16. " +
    "The journey namespace ({tenant}:ctx:journey:{journey_id}) is shared across all " +
    "sessions linked to the same journey, enabling Tier 1 Business Workflow agents " +
    "to access data collected in previous collect sessions. " +
    "Returns a map of tag → {value, confidence, source, updated_at}. " +
    "If 'tags' is omitted, all tags in the journey namespace are returned.",
    JourneyContextGetInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyContextGetInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { journey_id, tags } = parsed.data

      const key = `${tenantId}:ctx:journey:${journey_id}`
      try {
        let raw: Record<string, string>
        if (tags && tags.length > 0) {
          // Read only requested tags
          const values = await redis.hmget(key, ...tags)
          raw = Object.fromEntries(
            tags.map((t, i) => [t, values[i] ?? ""])
          ) as Record<string, string>
        } else {
          raw = await redis.hgetall(key) as Record<string, string>
        }

        if (!raw || Object.keys(raw).length === 0) {
          return ok({ journey_id, context: {}, tag_count: 0 })
        }

        const context: Record<string, unknown> = {}
        for (const [tag, rawValue] of Object.entries(raw)) {
          if (!rawValue) continue
          try {
            context[tag] = JSON.parse(rawValue)
          } catch {
            context[tag] = { value: rawValue }
          }
        }

        return ok({ journey_id, context, tag_count: Object.keys(context).length })
      } catch (err) {
        return mcpError("REDIS_ERROR", `Failed to read journey context: ${err}`)
      }
    },
  )

  // ── journey_context_set (Arc 16) ──────────────────────────────────────────

  server.tool(
    "journey_context_set",
    "Write a context tag to the Journey ContextStore namespace — Arc 16. " +
    "Stores the value in {tenant}:ctx:journey:{journey_id} with a 30-day TTL. " +
    "Tag name MUST start with 'journey.' to prevent accidental session namespace writes. " +
    "Use to record business process state that needs to be visible across all sessions " +
    "of the same journey (e.g. 'journey.approval_decision', 'journey.collected_docs').",
    JourneyContextSetInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyContextSetInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { journey_id, tag, value, confidence, source } = parsed.data

      // Enforce journey.* prefix
      if (!tag.startsWith("journey.")) {
        return mcpError(
          "INVALID_TAG",
          `Tag must start with 'journey.' — got: '${tag}'. ` +
          "This restriction prevents accidental writes to the session namespace.",
        )
      }

      const key = `${tenantId}:ctx:journey:${journey_id}`
      const entry = JSON.stringify({
        value,
        confidence,
        source,
        visibility:  "agents_only",
        updated_at:  new Date().toISOString(),
      })

      try {
        await redis.hset(key, tag, entry)
        // Renew TTL on every write — keeps the hash alive as long as it's active
        await redis.expire(key, JOURNEY_CTX_TTL_S)
        return ok({ journey_id, tag, written: true })
      } catch (err) {
        return mcpError("REDIS_ERROR", `Failed to write journey context: ${err}`)
      }
    },
  )

  // ── journey_list_suspended (Arc 16 Phase C) ───────────────────────────────

  server.tool(
    "journey_list_suspended",
    "List suspended Journeys for a given pool. " +
    "Used by Tier 1 Business Workflow pollers to discover journeys awaiting external decisions. " +
    "Returns journey_id, skill_id, workflow_instance_id, customer_id, and suspend_reason from " +
    "the linked workflow instance. " +
    "Filter by skill_id to handle multiple journeys from a single poller workflow.",
    JourneyListSuspendedInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyListSuspendedInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { pool_id, skill_id, limit } = parsed.data

      const params = new URLSearchParams({
        status: "suspended",
        pool_id,
        limit:  String(limit),
      })
      if (skill_id) params.set("skill_id", skill_id)

      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys?${params.toString()}`,
        "GET",
        tenantId,
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_list_suspended failed: ${detail}`,
        )
      }

      return ok(result.data)
    },
  )

  // ── journey_resume (Arc 16 Phase C) ──────────────────────────────────────

  server.tool(
    "journey_resume",
    "Resume a suspended Journey, optionally providing context that will be forwarded to the " +
    "resumed workflow instance via resumeContext.payload. " +
    "The resume_token is resolved internally — callers never need to handle it. " +
    "For durable journey-namespace storage, call journey_context_set first, then journey_resume. " +
    "Used by Tier 1 Business Workflow pollers after obtaining an external decision. " +
    "Emits workflow.resumed so the skill-flow-worker picks up execution immediately.",
    JourneyResumeInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyResumeInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { journey_id, context, decision } = parsed.data

      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys/${journey_id}/resume`,
        "POST",
        tenantId,
        { context: context ?? null, decision },
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_resume failed: ${detail}`,
        )
      }

      const body = result.data as Record<string, unknown>
      return ok({
        journey_id:           body.journey_id,
        status:               body.status,
        workflow_instance_id: body.workflow_instance_id,
        decision:             body.decision,
        wait_duration_ms:     body.wait_duration_ms,
      })
    },
  )

  // ── journey_check_pending (Arc 16 Phase E) ────────────────────────────────

  server.tool(
    "journey_check_pending",
    "Check whether a customer has active journeys with a pending collect step awaiting their response. " +
    "Called by the pool's AI agent at the start of an inbound session to detect whether the customer " +
    "is in the middle of a multi-session service process and offer to continue. " +
    "Returns a list of pending journeys with their skill_id, pool_id, required capabilities, " +
    "and the channel + contact_id the collect prompt was sent to. " +
    "If the customer accepts resumption, call journey_link_session then journey_resume. " +
    "Requires ABAC permission journey.read. " +
    "Only available in pools with inbound_journey_resume: true.",
    JourneyCheckPendingInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      const parsed = JourneyCheckPendingInputSchema.safeParse(rawInput)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { customer_id, channel, limit } = parsed.data

      // ── Step 1: fetch active journeys for this customer ──────────────────
      const params = new URLSearchParams({
        customer_id,
        status: "active",
        limit:  String(limit * 3),  // over-fetch to allow for filtering
        offset: "0",
      })
      const result = await callWorkflowApi(
        `${workflowApiUrl}/v1/journeys?${params.toString()}`,
        "GET",
        tenantId,
      )

      if (!result.ok) {
        const detail = (result.data as Record<string, unknown>)?.detail ?? "unknown error"
        return mcpError(
          `WORKFLOW_API_${result.status}`,
          `journey_check_pending failed: ${detail}`,
        )
      }

      const body   = result.data as { items?: unknown[] }
      const items  = Array.isArray(body.items) ? body.items : []

      // ── Step 2: enrich each journey with pending_collect_info from Redis ─
      const pending: Array<Record<string, unknown>> = []

      for (const item of items) {
        const j = item as Record<string, unknown>
        const journey_id = j.journey_id as string | undefined
        if (!journey_id) continue

        const journeyKey = `${tenantId}:ctx:journey:${journey_id}`
        let pendingInfo: Record<string, unknown> | null = null

        try {
          const raw = await redis.hget(journeyKey, "journey.pending_collect_info")
          if (raw) {
            const entry = JSON.parse(raw) as { value: Record<string, unknown> }
            pendingInfo = entry.value ?? null
          }
        } catch {
          // Redis unavailable — skip pending check for this journey
        }

        if (!pendingInfo) continue  // no pending collect, skip

        const requires  = (pendingInfo.requires as string[]) ?? []
        const pChannel  = (pendingInfo.channel  as string)   ?? null
        const contactId = (pendingInfo.contact_id as string) ?? null
        const dispatched = (pendingInfo.dispatched_at as string) ?? null

        // ── Step 3: filter by channel capability if requested ───────────
        if (channel && !_channelSatisfies(channel, requires)) {
          continue  // this channel cannot satisfy the collect step requirements
        }

        pending.push({
          journey_id:           journey_id,
          skill_id:             j.skill_id,
          pool_id:              j.pool_id,
          workflow_instance_id: j.workflow_instance_id,
          customer_id:          j.customer_id,
          pending_channel:      pChannel,
          pending_contact_id:   contactId,
          requires,
          dispatched_at:        dispatched,
          origin_session_id:    j.origin_session_id,
        })

        if (pending.length >= limit) break
      }

      return ok({
        customer_id,
        channel:        channel ?? null,
        pending_count:  pending.length,
        pending_journeys: pending,
      })
    },
  )
}
