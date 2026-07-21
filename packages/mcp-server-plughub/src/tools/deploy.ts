/**
 * tools/deploy.ts
 * Skill deploy tools — triggered by deploy workflows.
 *
 *   skill_deploy  — skill-centric legacy path (POST /v1/skills/:id/deploy),
 *                   used by skill_scheduled_deploy_v1.
 *   pool_promote  — POOL-centric promote (POST /v1/pools/:id/promote), the SINGLE
 *                   promote path (next→current→previous + SkillDeployment). Used by
 *                   skill_deploy_promote_v1, the body of a scheduled-promote agenda
 *                   (Scheduler Fase 2). `invoke` reaches the agent-registry via THIS
 *                   MCP tool — never an arbitrary HTTP call — so the promotion is
 *                   permission-checked, injection-guarded and audited by the
 *                   McpInterceptor like any other domain tool call.
 */

import { z }         from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface DeployDeps {
  agentRegistryUrl: string   // e.g. http://localhost:3300
  tenantId:         string   // default tenant (overridden by input when provided)
}

// ─── Schemas ──────────────────────────────────────────────────────────────────

// pool_ids is declared as string because the MCP SDK's ZodRawShapeCompat
// constraint does not accept ZodUnion<[ZodArray, ZodString]> — union of
// non-scalar types causes refine() return-type incompatibility at the type
// level. The handler normalises the string to string[] at runtime.
const SkillDeployInputSchema = z.object({
  skill_id:    z.string().min(1),
  pool_ids:    z.string(),   // JSON array or comma-separated list of pool_ids
  deployed_by: z.string().optional(),
  notes:       z.string().optional(),
  tenant_id:   z.string().optional(),
})

// pool_promote — promote the target pool's staged `next` slot to `current`.
// The pool is the addressable unit (invariant S4): the caller names the POOL to
// promote, never a skill/version. "Promote" = "turn the `next` in force at T into
// current" — the pool owner staged what goes live; the agenda just pulls the trigger.
const PoolPromoteInputSchema = z.object({
  target_pool_id: z.string().min(1),
  promoted_by:    z.string().optional(),
  tenant_id:      z.string().optional(),
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

export function registerDeployTools(server: McpServer, deps: DeployDeps): void {
  const { agentRegistryUrl, tenantId: defaultTenantId } = deps

  server.tool(
    "skill_deploy",
    "Deploy a skill to one or more pools via agent-registry. " +
    "Called by the skill_scheduled_deploy_v1 workflow after its timer fires.",
    SkillDeployInputSchema.shape as any,   // cast required — ZodOptional not assignable to ZodRawShapeCompat in this SDK version
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof SkillDeployInputSchema>
      try {
        input = SkillDeployInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError(
            "validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
          )
        }
        throw e
      }

      // Normalise pool_ids — may be a JSON array string or comma-separated list
      let poolIds: string[]
      try {
        const parsed = JSON.parse(input.pool_ids)
        poolIds = Array.isArray(parsed) ? parsed : [input.pool_ids]
      } catch {
        // Comma-separated fallback: "sac,retencao" → ["sac", "retencao"]
        poolIds = input.pool_ids.split(",").map(s => s.trim()).filter(Boolean)
      }

      if (poolIds.length === 0) {
        return mcpError("validation_error", "pool_ids must not be empty")
      }

      const tenantId = input.tenant_id ?? defaultTenantId

      try {
        const url = `${agentRegistryUrl}/v1/skills/${encodeURIComponent(input.skill_id)}/deploy`
        const body = {
          pool_ids:    poolIds,
          deployed_by: input.deployed_by ?? "workflow:skill_scheduled_deploy_v1",
          notes:       input.notes ?? "Scheduled deploy via workflow",
          tenant_id:   tenantId,
        }

        // G-PROBE platform-wide: o agent-registry gateia POST /v1/skills/:id/deploy;
        // este caller (workflow) usa a credencial de serviço (env; omitida se vazia).
        const svcToken = process.env["AGENT_REGISTRY_SERVICE_TOKEN"] ?? ""
        const res = await fetch(url, {
          method:  "POST",
          headers: {
            "Content-Type": "application/json",
            "x-tenant-id":  tenantId,
            ...(svcToken ? { "x-service-token": svcToken } : {}),
          },
          body: JSON.stringify(body),
        })

        if (!res.ok) {
          let detail = ""
          try { detail = await res.text() } catch { /* ignore */ }
          return mcpError(
            "deploy_failed",
            `agent-registry responded ${res.status}: ${detail}`
          )
        }

        const data = await res.json() as unknown
        return ok({
          success:   true,
          skill_id:  input.skill_id,
          pool_ids:  poolIds,
          tenant_id: tenantId,
          deployment: data,
        })
      } catch (e) {
        return mcpError(
          "network_error",
          e instanceof Error ? e.message : String(e)
        )
      }
    }
  )

  // ── pool_promote ──────────────────────────────────────────────────────────
  // Wraps the SINGLE promote path (POST /v1/pools/:id/promote). Returns isError
  // on any non-2xx (409 empty-`next` / 422 capacity / 404 unknown pool) so the
  // calling `invoke` step routes to on_failure — a failed promote NEVER surfaces
  // as a silent success (degradation is never silent; no auto-retry in v1).
  server.tool(
    "pool_promote",
    "Promote a pool's staged `next` slot to `current` via agent-registry " +
    "(next→current→previous, records a SkillDeployment, revalidates capacity). " +
    "The pool is the addressable unit — no skill/version pin. Returns an error on " +
    "empty `next` (409) or capacity violation (422) so the caller can handle it.",
    PoolPromoteInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof PoolPromoteInputSchema>
      try {
        input = PoolPromoteInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError(
            "validation_error",
            e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
          )
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId

      try {
        const url = `${agentRegistryUrl}/v1/pools/${encodeURIComponent(input.target_pool_id)}/promote`
        // The pool-slots router is not service-token-gated today, but we send the
        // service credential when present (future-proof, matches skill_deploy).
        const svcToken = process.env["AGENT_REGISTRY_SERVICE_TOKEN"] ?? ""
        const res = await fetch(url, {
          method:  "POST",
          headers: {
            "Content-Type": "application/json",
            "x-tenant-id":  tenantId,
            "x-user-id":    input.promoted_by ?? "scheduler:deploy_promote",
            ...(svcToken ? { "x-service-token": svcToken } : {}),
          },
          body: JSON.stringify({}),
        })

        if (!res.ok) {
          let detail = ""
          try { detail = await res.text() } catch { /* ignore */ }
          return mcpError(
            "promote_failed",
            `agent-registry responded ${res.status} promoting pool ` +
            `'${input.target_pool_id}': ${detail}`
          )
        }

        const data = await res.json() as unknown
        return ok({
          success:        true,
          target_pool_id: input.target_pool_id,
          tenant_id:      tenantId,
          result:         data,
        })
      } catch (e) {
        return mcpError(
          "network_error",
          e instanceof Error ? e.message : String(e)
        )
      }
    }
  )
}
