/**
 * tools/deploy.ts
 * Skill deploy tool — triggered by skill_scheduled_deploy_v1 workflow.
 * Calls agent-registry POST /v1/skills/:id/deploy on behalf of the workflow.
 *
 * Tool: skill_deploy
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
    SkillDeployInputSchema.shape,
    async (rawInput) => {
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

        const res = await fetch(url, {
          method:  "POST",
          headers: {
            "Content-Type": "application/json",
            "x-tenant-id":  tenantId,
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
}
