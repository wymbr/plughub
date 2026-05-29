/**
 * tools/workflow.ts
 * Arc 19 — Webhook Workflow tools for skill-flow agents.
 *
 * Exposes workflow_trigger so that intake agents (or any skill-flow step)
 * can start an async webhook workflow session and propagate:
 *   - origin_session_id  (traceability link to the initiating session)
 *   - context            (seed ContextStore entries for the new session)
 *
 * The tool POSTs to channel-gateway POST /v1/channels/webhook/{skill_id}.
 * Channel-gateway writes the context to ContextStore BEFORE publishing to
 * Kafka, so the first skill-flow step already has @ctx.* available.
 *
 * Design notes:
 *   - tenant_id and origin_session_id are passed explicitly as inputs.
 *     In skill-flow YAML invoke steps, use $.tenant_id and $.session_id
 *     which are built-in references added to the JSONPath evalContext.
 *   - context_* prefixed inputs are collected into the ContextStore seed dict.
 *     Key mapping: "context_session_foo_bar" → "session.foo.bar" (underscores
 *     after the first two segments become dots). For explicit control, pass
 *     context as flat "ctx_tag" fields where the tag uses underscores as
 *     dot separators: ctx_session_numero_atual → "session.numero.atual"
 *     ... or simply pass context_json (JSON string).
 *   - Errors returned as MCP error response, never thrown unhandled.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { withGuard }      from "../infra/tool-guard"

// ─── Dependências injetadas ──────────────────────────────────────────────────

export interface WorkflowDeps {
  channelGatewayUrl: string   // e.g. http://channel-gateway:8010
}

// ─── Schemas ─────────────────────────────────────────────────────────────────

const WorkflowTriggerInputSchema = z.object({
  tenant_id: z.string().min(1).describe(
    "Tenant ID. In skill-flow YAML use $.tenant_id (built-in reference)."
  ),

  skill_id: z.string().min(1).describe(
    "Skill ID of the webhook workflow to trigger " +
    "(e.g. 'skill_portabilidade_demo_v1'). Must be registered in a webhook pool."
  ),

  origin_session_id: z.string().optional().describe(
    "Current session ID — stored as session.origin_session_id in the new workflow " +
    "session ContextStore for traceability. In skill-flow YAML use $.session_id."
  ),

  context_json: z.string().optional().describe(
    "JSON-encoded ContextStore seed entries {tag: value} for the new session. " +
    "Written before routing so workflow step 1 can read them via @ctx.*. " +
    "Example: '{\"session.numero_atual\": \"11999999999\"}'"
  ),

  customer_id: z.string().optional().describe(
    "Customer identifier for the new webhook session."
  ),
})

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerWorkflowTools(
  server: McpServer,
  deps:   WorkflowDeps,
) {
  server.tool(
    "workflow_trigger",
    "Trigger an async webhook workflow session (Arc 19 unified session model). " +
    "Creates a new session with channel_type=webhook, seeds its ContextStore with " +
    "the provided context entries, and links it to the current session via " +
    "origin_session_id. The workflow runs independently — the current session " +
    "continues normally after this call. Returns { workflow_session_id, status }.",
    WorkflowTriggerInputSchema.shape,
    withGuard("workflow_trigger", async (input: Record<string, unknown>) => {
      const parsed = WorkflowTriggerInputSchema.safeParse(input)
      if (!parsed.success) {
        return {
          content: [{ type: "text" as const, text: `Invalid input: ${parsed.error.message}` }],
          isError: true,
        }
      }
      const { tenant_id, skill_id, origin_session_id, context_json, customer_id } = parsed.data

      // ── Parse context_json ────────────────────────────────────────────────
      let context: Record<string, string> = {}
      if (context_json) {
        try {
          context = JSON.parse(context_json) as Record<string, string>
        } catch {
          return {
            content: [{ type: "text" as const, text: `Invalid context_json: must be a valid JSON object string` }],
            isError: true,
          }
        }
      }

      // ── POST to channel-gateway trigger endpoint ───────────────────────────
      const url  = `${deps.channelGatewayUrl}/v1/channels/webhook/${encodeURIComponent(skill_id)}`
      const body = {
        tenant_id,
        trigger_type:      "task",
        origin_session_id: origin_session_id ?? null,
        customer_id:       customer_id ?? null,
        context,
      }

      let res: Response
      try {
        res = await fetch(url, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(body),
        })
      } catch (err) {
        return {
          content: [{ type: "text" as const, text: `Channel gateway unreachable: ${String(err)}` }],
          isError: true,
        }
      }

      if (!res.ok) {
        const text = await res.text().catch(() => "")
        return {
          content: [{ type: "text" as const, text: `Trigger failed (HTTP ${res.status}): ${text}` }],
          isError: true,
        }
      }

      const data = (await res.json()) as { session_id: string }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            workflow_session_id: data.session_id,
            origin_session_id:   origin_session_id ?? null,
            skill_id,
            status:              "triggered",
          }),
        }],
      }
    }),
  )
}
