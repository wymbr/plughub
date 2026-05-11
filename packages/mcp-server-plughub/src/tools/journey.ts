/**
 * tools/journey.ts
 * Journey lifecycle MCP tools — Arc 10.
 *
 * Tools:
 *   journey_start        — Create a journey and trigger its governing skill-flow
 *   journey_link_session — Associate an additional session with an existing journey
 *   journey_merge        — Merge a secondary journey into a primary (Phase D)
 *
 * All calls are intercepted by McpInterceptor for permission validation,
 * injection guard, and audit (LGPD). Never bypass.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface JourneyDeps {
  workflowApiUrl: string   // e.g. http://localhost:3800
  tenantId:       string   // resolved from agent JWT context
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
  const resp = await fetch(url, {
    method,
    headers: {
      "Content-Type":  "application/json",
      "x-tenant-id":   tenantId,
      "x-internal":    "1",
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await resp.json().catch(() => ({ detail: resp.statusText }))
  return { ok: resp.ok, status: resp.status, data }
}

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerJourneyTools(server: McpServer, deps: JourneyDeps): void {
  const { workflowApiUrl, tenantId } = deps

  // ── journey_start ─────────────────────────────────────────────────────────

  server.tool(
    "journey_start",
    "Create a Journey and trigger its governing skill-flow workflow. " +
    "A Journey groups all sessions involved in resolving a single service process " +
    "and enables end-to-end observability and KPIs. " +
    "Returns journey_id and workflow_instance_id.",
    JourneyStartInputSchema.shape,
    async (input) => {
      const parsed = JourneyStartInputSchema.safeParse(input)
      if (!parsed.success) {
        return mcpError("INVALID_INPUT", parsed.error.message)
      }
      const { skill_id, session_id, customer_id, metadata } = parsed.data

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
    JourneyLinkSessionInputSchema.shape,
    async (input) => {
      const parsed = JourneyLinkSessionInputSchema.safeParse(input)
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
    JourneyMergeInputSchema.shape,
    async (input) => {
      const parsed = JourneyMergeInputSchema.safeParse(input)
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
}
