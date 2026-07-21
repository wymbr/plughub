/**
 * tools/outbound.ts
 * Outbound substrate tools — thin wrappers over the mailing-api (schema `outbound`).
 *
 *   mailing_add              — insert/upsert an entry (audience feeding). Producers:
 *                              a skill in a lifecycle hook, or an import adapter.
 *   campaign_drain           — atomically claim a batch of a campaign's entries; the
 *                              outbound skill loops the returned batch and contacts each.
 *   campaign_delivery_result — record the outcome of a delivery after the collect.
 *
 * Like every domain tool, these go through the McpInterceptor (permission check,
 * injection guard, audit) — the caller cannot opt out. Any non-2xx from the
 * mailing-api returns isError so the calling `invoke` step routes to on_failure
 * (degradation is never silent).
 */

import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"

// ─── Injected dependencies ────────────────────────────────────────────────────

export interface OutboundDeps {
  mailingApiUrl: string   // e.g. http://mailing-api:3660
  tenantId:      string   // default tenant (overridden by input when provided)
}

// ─── Schemas ──────────────────────────────────────────────────────────────────

// metadata / contacts are objects; the MCP SDK's ZodRawShapeCompat rejects
// ZodUnion/ZodRecord at the top level in some SDK versions, so they are declared as
// JSON strings and parsed at runtime (same pattern as deploy.ts pool_ids).
const MailingAddInputSchema = z.object({
  mailing_id:  z.string().min(1),
  metadata:    z.string(),            // JSON object (opaque producer↔consumer blob)
  customer_id: z.string().optional(),
  contacts:    z.string().optional(), // JSON object {channel: handle}
  dedup_key:   z.string().optional(),
  source:      z.string().optional(),
  ttl_seconds: z.number().int().positive().optional(),
  tenant_id:   z.string().optional(),
})

const CampaignDrainInputSchema = z.object({
  campaign_id: z.string().min(1),
  limit:       z.number().int().min(1).optional(),
  tenant_id:   z.string().optional(),
})

const DeliveryResultInputSchema = z.object({
  delivery_id:     z.string().min(1),
  result:          z.enum([
    "claimed", "pending", "contacted", "responded",
    "failed", "skipped_ineligible", "suppressed",
  ]),
  session_id:      z.string().optional(),
  root_session_id: z.string().optional(),
  error:           z.string().optional(),
  tenant_id:       z.string().optional(),
})

// Fase 2 — contact governance.
const EligibilityInputSchema = z.object({
  customer_id: z.string().min(1),
  channel:     z.string().min(1),
  campaign_id: z.string().optional(),
  // Default true: reserve the fatigue window at send (writes a contact_log fact when
  // allowed). Pass false for a dry-run check.
  claim:       z.boolean().optional(),
  at:          z.string().optional(),   // ISO instant; default now server-side
  tenant_id:   z.string().optional(),
})

const UnsubscribeInputSchema = z.object({
  customer_id: z.string().min(1),
  // "mailing" (default) → entry.status='unsubscribed'; "global" → cadastro do_not_contact.
  scope:       z.enum(["mailing", "global"]).optional(),
  mailing_id:  z.string().optional(),   // mailing scope: omit = all of the customer's mailings
  channel:     z.string().optional(),   // global scope: omit/'all' = full; a channel = per-channel
  tenant_id:   z.string().optional(),
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

function parseZod<T>(schema: z.ZodType<T>, raw: unknown): T {
  return schema.parse(raw)
}

/** Parse a JSON-object string argument; throws a friendly error for non-objects. */
function parseJsonObject(raw: string, field: string): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    throw new Error(`${field} must be a JSON object string`)
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${field} must be a JSON object`)
  }
  return parsed as Record<string, unknown>
}

async function postJson(
  url: string, tenantId: string, body: unknown,
): Promise<{ ok: boolean; status: number; data: unknown; text: string }> {
  const res = await fetch(url, {
    method:  "POST",
    headers: { "Content-Type": "application/json", "x-tenant-id": tenantId },
    body:    JSON.stringify(body),
  })
  const text = await res.text().catch(() => "")
  let data: unknown = undefined
  try { data = text ? JSON.parse(text) : undefined } catch { /* keep text */ }
  return { ok: res.ok, status: res.status, data, text }
}

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerOutboundTools(server: McpServer, deps: OutboundDeps): void {
  const { mailingApiUrl, tenantId: defaultTenantId } = deps

  // ── mailing_add ─────────────────────────────────────────────────────────────
  server.tool(
    "mailing_add",
    "Insert or upsert an entry (person + channels + opaque context metadata) into a " +
    "mailing (audience). Upsert is by dedup_key (derived from the mailing's dedup_policy " +
    "unless given). Used by lifecycle-hook skills and import adapters to feed an audience.",
    MailingAddInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof MailingAddInputSchema>
      try {
        input = parseZod(MailingAddInputSchema, rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      let metadata: Record<string, unknown>
      let contacts: Record<string, unknown> | undefined
      try {
        metadata = parseJsonObject(input.metadata, "metadata")
        contacts = input.contacts ? parseJsonObject(input.contacts, "contacts") : undefined
      } catch (e) {
        return mcpError("validation_error", e instanceof Error ? e.message : String(e))
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const body: Record<string, unknown> = { metadata }
      if (input.customer_id !== undefined) body["customer_id"] = input.customer_id
      if (contacts !== undefined)          body["contacts"]    = contacts
      if (input.dedup_key !== undefined)   body["dedup_key"]   = input.dedup_key
      if (input.source !== undefined)      body["source"]      = input.source
      if (input.ttl_seconds !== undefined) body["ttl_seconds"] = input.ttl_seconds

      try {
        const url = `${mailingApiUrl}/v1/mailings/${encodeURIComponent(input.mailing_id)}/entries`
        const r = await postJson(url, tenantId, body)
        if (!r.ok) {
          return mcpError("mailing_add_failed",
            `mailing-api responded ${r.status}: ${r.text.slice(0, 200)}`)
        }
        return ok(r.data)
      } catch (e) {
        return mcpError("network_error", e instanceof Error ? e.message : String(e))
      }
    }
  )

  // ── campaign_drain ──────────────────────────────────────────────────────────
  server.tool(
    "campaign_drain",
    "Atomically claim a batch of a campaign's mailing entries (FOR UPDATE SKIP LOCKED). " +
    "Returns the drained entries { delivery_id, entry_id, customer_id, contacts, metadata } " +
    "for the outbound skill to loop over and contact. Avoids double-drain across ticks.",
    CampaignDrainInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof CampaignDrainInputSchema>
      try {
        input = parseZod(CampaignDrainInputSchema, rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const body: Record<string, unknown> = {}
      if (input.limit !== undefined) body["limit"] = input.limit

      try {
        const url = `${mailingApiUrl}/v1/campaigns/${encodeURIComponent(input.campaign_id)}/drain`
        const r = await postJson(url, tenantId, body)
        if (!r.ok) {
          return mcpError("campaign_drain_failed",
            `mailing-api responded ${r.status}: ${r.text.slice(0, 200)}`)
        }
        return ok(r.data)
      } catch (e) {
        return mcpError("network_error", e instanceof Error ? e.message : String(e))
      }
    }
  )

  // ── campaign_delivery_result ─────────────────────────────────────────────────
  server.tool(
    "campaign_delivery_result",
    "Record the outcome of a campaign delivery after the collect (contacted/responded/" +
    "failed/…). 'failed' bumps attempts (drives per-campaign retry); session_id links the " +
    "outbound session for drill-through. Never silent — the error reason is stored.",
    DeliveryResultInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof DeliveryResultInputSchema>
      try {
        input = parseZod(DeliveryResultInputSchema, rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const body: Record<string, unknown> = { result: input.result }
      if (input.session_id !== undefined)      body["session_id"]      = input.session_id
      if (input.root_session_id !== undefined) body["root_session_id"] = input.root_session_id
      if (input.error !== undefined)           body["error"]           = input.error

      try {
        const url = `${mailingApiUrl}/v1/deliveries/${encodeURIComponent(input.delivery_id)}/result`
        const r = await postJson(url, tenantId, body)
        if (!r.ok) {
          return mcpError("delivery_result_failed",
            `mailing-api responded ${r.status}: ${r.text.slice(0, 200)}`)
        }
        return ok(r.data)
      } catch (e) {
        return mcpError("network_error", e instanceof Error ? e.message : String(e))
      }
    }
  )

  // ── contact_eligibility_check ────────────────────────────────────────────────
  server.tool(
    "contact_eligibility_check",
    "Decide whether an outbound contact to a customer on a channel is allowed by the " +
    "effective contact policy (fatigue engine: frequency caps / quarantine / channel caps " +
    "over contact_log). claim=true (default) reserves the window by logging the contact " +
    "when allowed. Generic — survey is a caller. Returns { allowed, reason, retry_after, claimed }.",
    EligibilityInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof EligibilityInputSchema>
      try {
        input = parseZod(EligibilityInputSchema, rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const body: Record<string, unknown> = {
        customer_id: input.customer_id,
        channel:     input.channel,
      }
      if (input.campaign_id !== undefined) body["campaign_id"] = input.campaign_id
      if (input.claim !== undefined)       body["claim"]       = input.claim
      if (input.at !== undefined)          body["at"]          = input.at

      try {
        const url = `${mailingApiUrl}/v1/contact/eligibility`
        const r = await postJson(url, tenantId, body)
        if (!r.ok) {
          return mcpError("eligibility_check_failed",
            `mailing-api responded ${r.status}: ${r.text.slice(0, 200)}`)
        }
        return ok(r.data)
      } catch (e) {
        return mcpError("network_error", e instanceof Error ? e.message : String(e))
      }
    }
  )

  // ── mailing_unsubscribe ──────────────────────────────────────────────────────
  server.tool(
    "mailing_unsubscribe",
    "Suppression. scope 'mailing' (default): flip a customer's entries to 'unsubscribed' " +
    "(the drain excludes non-active; mailing_id omitted = all mailings). scope 'global': " +
    "write do_not_contact in the customer cadastro (channel omitted/'all' = full opt-out; " +
    "a channel = per-channel) — a veto enforced by the opt_out gate at eligibility.",
    UnsubscribeInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof UnsubscribeInputSchema>
      try {
        input = parseZod(UnsubscribeInputSchema, rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const body: Record<string, unknown> = { customer_id: input.customer_id }
      if (input.scope !== undefined)      body["scope"]      = input.scope
      if (input.mailing_id !== undefined) body["mailing_id"] = input.mailing_id
      if (input.channel !== undefined)    body["channel"]    = input.channel

      try {
        const url = `${mailingApiUrl}/v1/unsubscribe`
        const r = await postJson(url, tenantId, body)
        if (!r.ok) {
          return mcpError("unsubscribe_failed",
            `mailing-api responded ${r.status}: ${r.text.slice(0, 200)}`)
        }
        return ok(r.data)
      } catch (e) {
        return mcpError("network_error", e instanceof Error ? e.message : String(e))
      }
    }
  )
}
