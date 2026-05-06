/**
 * tools/calendar.ts
 * Calendar engine MCP tools — wraps Calendar API engine endpoints so that
 * Skill Flow agents can query business hours via `invoke` steps.
 *
 * Tools:
 *   calendar_is_open             — GET  /v1/engine/is-open
 *   calendar_next_open_slot      — GET  /v1/engine/next-open-slot
 *   calendar_add_business_duration — POST /v1/engine/add-business-duration
 *   calendar_business_duration   — POST /v1/engine/business-duration
 */

import { z }         from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface CalendarDeps {
  calendarApiUrl: string   // e.g. http://localhost:3700
  tenantId:       string   // default tenant (overridden by input when provided)
}

// ─── Schemas ──────────────────────────────────────────────────────────────────

const CalendarIsOpenInputSchema = z.object({
  entity_type: z.string().min(1).describe("Type of entity (e.g. 'pool', 'agent', 'queue')"),
  entity_id:   z.string().min(1).describe("ID of the entity to check"),
  at:          z.string().optional().describe("ISO-8601 datetime to evaluate; defaults to now"),
  tenant_id:   z.string().optional().describe("Tenant ID; defaults to server-configured tenant"),
})

const CalendarNextOpenSlotInputSchema = z.object({
  entity_type: z.string().min(1).describe("Type of entity"),
  entity_id:   z.string().min(1).describe("ID of the entity"),
  after:       z.string().optional().describe("ISO-8601 datetime after which to look; defaults to now"),
  tenant_id:   z.string().optional().describe("Tenant ID; defaults to server-configured tenant"),
})

const CalendarAddBusinessDurationInputSchema = z.object({
  entity_type: z.string().min(1).describe("Type of entity"),
  entity_id:   z.string().min(1).describe("ID of the entity"),
  from_dt:     z.string().min(1).describe("ISO-8601 start datetime"),
  hours:       z.number().describe("Number of business hours to add"),
  tenant_id:   z.string().optional().describe("Tenant ID; defaults to server-configured tenant"),
})

const CalendarBusinessDurationInputSchema = z.object({
  entity_type: z.string().min(1).describe("Type of entity"),
  entity_id:   z.string().min(1).describe("ID of the entity"),
  from_dt:     z.string().min(1).describe("ISO-8601 start datetime"),
  to_dt:       z.string().min(1).describe("ISO-8601 end datetime"),
  tenant_id:   z.string().optional().describe("Tenant ID; defaults to server-configured tenant"),
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

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerCalendarTools(server: McpServer, deps: CalendarDeps): void {
  const { calendarApiUrl, tenantId: defaultTenantId } = deps

  // ── calendar_is_open ──────────────────────────────────────────────────────

  server.tool(
    "calendar_is_open",
    "Check whether an entity (pool, queue, agent) is currently open according to its configured " +
    "calendar. Returns status ('open' | 'closed' | 'holiday'), a boolean 'open' field (deprecated), " +
    "and the evaluated_at timestamp.",
    CalendarIsOpenInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof CalendarIsOpenInputSchema>
      try {
        input = CalendarIsOpenInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const params = new URLSearchParams({
        tenant_id:   tenantId,
        entity_type: input.entity_type,
        entity_id:   input.entity_id,
      })
      if (input.at) params.set("at", input.at)

      try {
        const resp = await fetch(`${calendarApiUrl}/v1/engine/is-open?${params}`, {
          headers: { "x-tenant-id": tenantId },
        })
        if (!resp.ok) {
          const body = await resp.text().catch(() => "")
          return mcpError("calendar_api_error", `Calendar API returned ${resp.status}: ${body}`)
        }
        return ok(await resp.json())
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )

  // ── calendar_next_open_slot ───────────────────────────────────────────────

  server.tool(
    "calendar_next_open_slot",
    "Find the next datetime when an entity will be open, starting from 'after' (defaults to now). " +
    "Returns { next_open, entity_type, entity_id } where next_open is an ISO-8601 datetime or null " +
    "if no open slot can be determined within the calendar horizon.",
    CalendarNextOpenSlotInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof CalendarNextOpenSlotInputSchema>
      try {
        input = CalendarNextOpenSlotInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const params = new URLSearchParams({
        tenant_id:   tenantId,
        entity_type: input.entity_type,
        entity_id:   input.entity_id,
      })
      if (input.after) params.set("after", input.after)

      try {
        const resp = await fetch(`${calendarApiUrl}/v1/engine/next-open-slot?${params}`, {
          headers: { "x-tenant-id": tenantId },
        })
        if (!resp.ok) {
          const body = await resp.text().catch(() => "")
          return mcpError("calendar_api_error", `Calendar API returned ${resp.status}: ${body}`)
        }
        return ok(await resp.json())
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )

  // ── calendar_add_business_duration ────────────────────────────────────────

  server.tool(
    "calendar_add_business_duration",
    "Calculate the deadline datetime that is N business hours after from_dt, respecting the entity's " +
    "configured calendar (working hours, holidays, exceptions). Useful for SLA deadline calculation.",
    CalendarAddBusinessDurationInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof CalendarAddBusinessDurationInputSchema>
      try {
        input = CalendarAddBusinessDurationInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId

      try {
        const resp = await fetch(`${calendarApiUrl}/v1/engine/add-business-duration`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-tenant-id": tenantId },
          body: JSON.stringify({
            tenant_id:   tenantId,
            entity_type: input.entity_type,
            entity_id:   input.entity_id,
            from_dt:     input.from_dt,
            hours:       input.hours,
          }),
        })
        if (!resp.ok) {
          const body = await resp.text().catch(() => "")
          return mcpError("calendar_api_error", `Calendar API returned ${resp.status}: ${body}`)
        }
        return ok(await resp.json())
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )

  // ── calendar_business_duration ────────────────────────────────────────────

  server.tool(
    "calendar_business_duration",
    "Calculate the number of business hours between from_dt and to_dt, respecting the entity's " +
    "configured calendar. Returns { business_hours, business_minutes, from_dt, to_dt, entity_type, entity_id }.",
    CalendarBusinessDurationInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof CalendarBusinessDurationInputSchema>
      try {
        input = CalendarBusinessDurationInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId

      try {
        const resp = await fetch(`${calendarApiUrl}/v1/engine/business-duration`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-tenant-id": tenantId },
          body: JSON.stringify({
            tenant_id:   tenantId,
            entity_type: input.entity_type,
            entity_id:   input.entity_id,
            from_dt:     input.from_dt,
            to_dt:       input.to_dt,
          }),
        })
        if (!resp.ok) {
          const body = await resp.text().catch(() => "")
          return mcpError("calendar_api_error", `Calendar API returned ${resp.status}: ${body}`)
        }
        return ok(await resp.json())
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )
}
