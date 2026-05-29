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
 * Invariants:
 *   - No business logic — routes to channel-gateway only
 *   - session_token carries tenant_id + origin session_id (used as origin_session_id)
 *   - Errors returned as MCP error response, never thrown unhandled
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { verifySessionToken, InvalidTokenError } from "../infra/jwt"
import { withGuard }      from "../infra/tool-guard"

// ─── Dependências injetadas ──────────────────────────────────────────────────

export interface WorkflowDeps {
  channelGatewayUrl: string   // e.g. http://channel-gateway:8010
}

// ─── Schemas ─────────────────────────────────────────────────────────────────

const WorkflowTriggerInputSchema = z.object({
  session_token: z.string().min(1).describe(
    "Agent session token (carries tenant_id and origin session_id)"
  ),

  skill_id: z.string().min(1).describe(
    "Skill ID of the webhook workflow to trigger " +
    "(e.g. 'skill_portabilidade_demo_v1'). Must be registered in a webhook pool."
  ),

  context: z.record(z.string()).optional().describe(
    "ContextStore seed entries for the new session. " +
    "Dict of {tag: value} where both are strings. " +
    "Example: {\"session.numero_atual\": \"11999999999\"}. " +
    "Written to ContextStore before routing so the workflow can read them from step 1."
  ),

  customer_id: z.string().optional().describe(
    "Customer identifier for the new webhook session. " +
    "Defaults to the customer_id from the origin session when omitted."
  ),
})

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerWorkflowTools(
  server: McpServer,
  deps:   WorkflowDeps,
) {
  // ── workflow_trigger ────────────────────────────────────────────────────────

  server.tool(
    "workflow_trigger",
    "Trigger an async webhook workflow session (Arc 19 unified session model). " +
    "Creates a new session with channel_type=webhook, seeds its ContextStore with " +
    "the provided context entries, and links it to the current session via " +
    "origin_session_id. The workflow runs independently — the current session " +
    "continues normally after this call. " +
    "Returns the new workflow session_id.",
    WorkflowTriggerInputSchema.shape,
    withGuard(async (input) => {
      // ── Decode session token ────────────────────────────────────────────────
      let tenantId:    string
      let sessionId:   string
      let customerId:  string | undefined

      try {
        const decoded = verifySessionToken(input.session_token)
        tenantId   = decoded.tenant_id
        sessionId  = decoded.session_id
        customerId = input.customer_id ?? (decoded as any).customer_id
      } catch (err) {
        if (err instanceof InvalidTokenError) {
          return {
            content: [{ type: "text" as const, text: `Invalid session token: ${err.message}` }],
            isError: true,
          }
        }
        throw err
      }

      // ── POST to channel-gateway trigger endpoint ────────────────────────────
      const url  = `${deps.channelGatewayUrl}/v1/channels/webhook/${encodeURIComponent(input.skill_id)}`
      const body = {
        tenant_id:         tenantId,
        trigger_type:      "task",          // initiated by an agent task
        origin_session_id: sessionId,       // traceability link
        customer_id:       customerId,
        context:           input.context ?? {},
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
            origin_session_id:   sessionId,
            skill_id:            input.skill_id,
            status:              "triggered",
          }),
        }],
      }
    }),
  )
}
