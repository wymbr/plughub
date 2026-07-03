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
  tenantId:          string
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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    WorkflowTriggerInputSchema.shape as any,
    withGuard("workflow_trigger", async (input: Record<string, unknown>) => {
      console.log("[workflow_trigger] invoked input=%j", input)
      const parsed = WorkflowTriggerInputSchema.safeParse(input)
      if (!parsed.success) {
        console.error("[workflow_trigger] validation failed: %s", parsed.error.message)
        return {
          content: [{ type: "text" as const, text: `Invalid input: ${parsed.error.message}` }],
          isError: true,
        }
      }
      const { tenant_id, skill_id, origin_session_id, context_json, customer_id } = parsed.data
      console.log("[workflow_trigger] parsed ok tenant=%s skill=%s origin=%s", tenant_id, skill_id, origin_session_id)

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
      console.log("[workflow_trigger] POST %s body=%j", url, { tenant_id, trigger_type: "task", origin_session_id })
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

  // ── workflow_resume ─────────────────────────────────────────────────────────
  //
  // Called by an I/O agent at the end of its skill to resume the parent
  // workflow that delegated work to it via the delegate() step.
  //
  // The agent reads the resume_token from its own ContextStore:
  //   @ctx.session.workflow_resume_token   (written by the delegate step engine)
  //
  // This tool posts to channel-gateway POST /v1/channels/webhook/resume/{token}.
  //
  // decision values:
  //   "input"    — agent collected data (e.g. customer confirmed, form filled)
  //   "approved" — agent received an approval signal
  //   "rejected" — agent received a rejection or cancellation
  //   "timeout"  — agent timed out waiting for customer response
  //
  // payload: any data collected by the agent, merged into the delegate step's
  //   output_value in the workflow pipeline_state.

  server.tool(
    "workflow_resume",
    "Resume a parent workflow that delegated I/O to this agent via the delegate() step. " +
    "Call this as the last step of your skill after completing the assigned I/O task. " +
    "Pass resume_token from @ctx.session.workflow_resume_token (interpolated by skill-flow-engine). " +
    "decision: 'input' (data collected), 'approved', 'rejected', or 'timeout'.",
    {
      resume_token: z.string().min(1).describe(
        "The resume token for the parent workflow. In skill-flow YAML use " +
        "@ctx.session.workflow_resume_token — the engine resolves it from ContextStore."
      ),
      decision: z.enum(["input", "approved", "rejected", "timeout"]).describe(
        "Outcome of the I/O task. 'input' = data collected from customer. " +
        "'approved'/'rejected' = explicit customer choice. 'timeout' = no response received."
      ),
      payload: z.record(z.unknown()).optional().describe(
        "Data collected by the agent. Merged into the delegate step output in workflow pipeline_state."
      ),
      resume_origin: z.string().optional().describe(
        "Identity Resolver (nível b) — how the customer returned: same_channel|token|identity. " +
        "'identity' when resuming a pending discovered via cross-channel identity lookup. In " +
        "skill-flow YAML pass @ctx.session.resume_origin; absent/unresolved/invalid → 'token'."
      ),
    } as any,
    withGuard("workflow_resume", async (input: Record<string, unknown>) => {
      const parsed = z.object({
        resume_token:  z.string().min(1),
        decision:      z.enum(["input", "approved", "rejected", "timeout"]),
        payload:       z.record(z.unknown()).optional(),
        // Loose on purpose: a skill may pass @ctx.session.resume_origin that
        // resolves to undefined/empty on non-reconnect paths — tolerate it and
        // fall back to "token" rather than failing the resume with a validation
        // error (which would break the normal confirmation flow).
        resume_origin: z.string().optional(),
      }).safeParse(input)

      if (!parsed.success) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "validation_error",
            message: parsed.error.message,
          }) }],
        }
      }

      const { resume_token, decision, payload, resume_origin } = parsed.data
      // Only forward a recognised origin; anything else (undefined/""/garbage
      // from an unresolved @ctx ref) is dropped → endpoint defaults to "token".
      const validOrigin =
        resume_origin && ["same_channel", "token", "identity"].includes(resume_origin)
          ? resume_origin
          : undefined

      // POST to channel-gateway webhook resume endpoint
      const url = `${deps.channelGatewayUrl}/v1/channels/webhook/resume/${encodeURIComponent(resume_token)}`
      let res: Response
      try {
        res = await fetch(url, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            tenant_id: deps.tenantId,
            // Fase E.3: source default "agent" (um agente retomou o workflow via
            // delegate). Um source explícito no payload do chamador prevalece
            // (ex.: intake cancelar → "customer_reconnect").
            payload:   { decision, source: "agent", ...(payload ?? {}) },
            // Identity Resolver (nível b) — top-level axis distinct from payload.source.
            // Omitted when the caller didn't set it → endpoint defaults to "token".
            ...(validOrigin ? { resume_origin: validOrigin } : {}),
          }),
        })
      } catch (err) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "channel_gateway_unreachable",
            message: String(err),
          }) }],
        }
      }

      if (!res.ok) {
        const text = await res.text().catch(() => "")
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: `resume_failed_http_${res.status}`,
            message: text,
          }) }],
        }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ resumed: true, decision }),
        }],
      }
    }),
  )

  // ── pending_workflow_get ─────────────────────────────────────────────────────
  //
  // Checks whether a customer has an active pending workflow awaiting their
  // confirmation (created via the delegate() step pattern).
  //
  // Called by intake agents after collecting the customer's contact_identifier.
  // If a pending workflow is found, the agent presents a menu so the customer
  // can continue their existing process instead of starting a new one.
  //
  // The lookup is O(1): channel-gateway writes a {tenant}:pending_workflow:{id}
  // key when delegate() creates Session C, validated against resume_tokens.
  //
  // Returns (output_as in YAML → $.pipeline_state.<output_as>):
  //   { found: false }
  //   { found: true, resume_token, context: { numero_atual, operadora_destino, ... } }

  server.tool(
    "pending_workflow_get",
    "Check whether the customer has an active pending workflow awaiting their confirmation. " +
    "Preferred: pass anchors[] (phone/email/cpf/princ) collected during intake — the Identity " +
    "Resolver maps them to a native customer_id and finds pendings across channels. " +
    "Legacy: pass a single contact_identifier. " +
    "If found=true, present a menu so the customer can continue; use resume_token with workflow_resume.",
    {
      anchors: z.array(z.object({
        kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
        value: z.string().min(1),
      })).optional().describe(
        "Identity anchors collected during intake (preferred). Cross-channel resolution."
      ),
      contact_identifier: z.string().optional().describe(
        "LEGACY single lookup key (phone/email). Treated as one inferred phone anchor."
      ),
      tenant_id: z.string().min(1).describe(
        "Tenant ID. In skill-flow YAML use $.tenant_id."
      ),
    } as any,
    withGuard("pending_workflow_get", async (input: Record<string, unknown>) => {
      const parsed = z.object({
        anchors: z.array(z.object({
          kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
          value: z.string().min(1),
        })).optional(),
        contact_identifier: z.string().optional(),
        tenant_id:          z.string().min(1),
      }).safeParse(input)

      if (!parsed.success) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "invalid_input",
            message: parsed.error.message,
          }) }],
        }
      }

      const { anchors, contact_identifier, tenant_id } = parsed.data

      // Preferred path: anchors → Identity Resolver (Lookup 1 → Lookup 2, cross-channel).
      if (anchors && anchors.length > 0) {
        try {
          const rRes = await fetch(`${deps.channelGatewayUrl}/v1/channels/webhook/identity/resolve`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ tenant_id, anchors, provision: false }),
          })
          if (!rRes.ok) {
            return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
          }
          const ref = await rRes.json() as { customer_id: string; status: string }
          if (!ref.customer_id) {
            return { content: [{ type: "text" as const, text: JSON.stringify({ found: false, count: 0 }) }] }
          }
          const pRes = await fetch(
            `${deps.channelGatewayUrl}/v1/channels/webhook/pending/by-customer/${encodeURIComponent(ref.customer_id)}?tenant_id=${encodeURIComponent(tenant_id)}`,
          )
          const pdata = pRes.ok
            ? await pRes.json() as { found: boolean; count: number; pendings: unknown[] }
            : { found: false, count: 0, pendings: [] }
          return {
            content: [{ type: "text" as const, text: JSON.stringify({ customer_id: ref.customer_id, ...pdata }) }],
          }
        } catch {
          return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
        }
      }

      // Legacy path: single contact_identifier → old by-handle endpoint.
      if (!contact_identifier) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "invalid_input", message: "provide anchors[] or contact_identifier",
          }) }],
        }
      }
      const url = `${deps.channelGatewayUrl}/v1/channels/webhook/pending/${encodeURIComponent(contact_identifier)}?tenant_id=${encodeURIComponent(tenant_id)}`
      let res: Response
      try {
        res = await fetch(url)
      } catch (err) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
      }
      if (!res.ok) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
      }
      const data = await res.json() as { found: boolean; resume_token?: string; context?: Record<string, string> }
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
    }),
  )

  // ── customer_resolve ─────────────────────────────────────────────────────────
  //
  // Identity-only resolution (no pendings). Resolves/provisions a native
  // customer_id from anchors — used to key history, memory, or to stamp identity
  // before delegating. PII travels only on the loopback body; hashing is
  // server-side in the channel-gateway.
  //
  // Returns: { customer_id, status, matched_by, confidence }

  server.tool(
    "customer_resolve",
    "Resolve (or provision) the native customer_id from identity anchors (phone/email/cpf/princ). " +
    "Use to identify the customer before loading history or delegating. " +
    "provision=true creates an ephemeral prospect when no match exists.",
    {
      anchors: z.array(z.object({
        kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
        value: z.string().min(1),
      })).min(1).describe("Identity anchors collected during intake."),
      tenant_id: z.string().min(1).describe("Tenant ID. In skill-flow YAML use $.tenant_id."),
      provision: z.boolean().optional().describe("Create an ephemeral prospect if no match (default true)."),
    } as any,
    withGuard("customer_resolve", async (input: Record<string, unknown>) => {
      const parsed = z.object({
        anchors: z.array(z.object({
          kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
          value: z.string().min(1),
        })).min(1),
        tenant_id: z.string().min(1),
        provision: z.boolean().optional(),
      }).safeParse(input)

      if (!parsed.success) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "invalid_input", message: parsed.error.message,
          }) }],
        }
      }

      const { anchors, tenant_id, provision } = parsed.data
      try {
        const res = await fetch(`${deps.channelGatewayUrl}/v1/channels/webhook/identity/resolve`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ tenant_id, anchors, provision: provision ?? true }),
        })
        if (!res.ok) {
          return {
            isError: true,
            content: [{ type: "text" as const, text: JSON.stringify({ error: `resolve_failed_http_${res.status}` }) }],
          }
        }
        const data = await res.json()
        return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
      } catch (err) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({ error: "resolve_unreachable" }) }],
        }
      }
    }),
  )
}
