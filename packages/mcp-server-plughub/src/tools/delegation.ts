/**
 * tools/delegation.ts
 * Tools de delegação de skills — agent_delegate e agent_delegate_status.
 * Spec: PlugHub v24.0 seção 4.7 (task step, modes: assist | transfer)
 *
 * Fluxo:
 *   1. task.ts chama agent_delegate → cria job Redis + dispara POST /delegate
 *      no skill-flow-service (fire-and-return)
 *   2. skill-flow-service executa a skill em background, atualiza job Redis
 *   3. task.ts chama agent_delegate_status em loop até completed|failed
 *
 * Isolamento de pipeline (assist mode):
 *   O especialista usa o session_id pai para notificações e menus (canal correto),
 *   mas um pipelineSessionId derivado para o pipeline state e lock — sem conflito
 *   com o agente primário que está em polling.
 */

import { z }       from "zod"
import * as crypto from "crypto"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import type { RedisClient } from "../infra/redis"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface DelegationDeps {
  redis:         RedisClient
  skillFlowUrl:  string   // e.g. http://localhost:3400
  tenantId:      string   // tenant id (from env or config)
}

// ─── Schemas de input ─────────────────────────────────────────────────────────

const AgentDelegateInputSchema = z.object({
  /** session_id da sessão pai — usado para rotear notify/menu ao canal correto. */
  session_id:       z.string().min(1),
  /** skill_id da skill a executar (e.g. "agente_contexto_ia_v1"). */
  target_skill:     z.string().min(1),
  /** Contexto passado ao especialista. */
  payload: z.object({
    customer_id:       z.string().optional(),
    pipeline_step:     z.string().optional(),
    pipeline_context:  z.record(z.unknown()).optional(),
  }),
  /** Modo de delegação. "silent" = assist sem transferência de ownership. */
  delegation_mode:  z.enum(["silent", "transfer"]).default("silent"),
})

const AgentDelegateStatusInputSchema = z.object({
  job_id:     z.string().min(1),
  session_id: z.string().min(1),
})

// ─── Helpers de resposta ──────────────────────────────────────────────────────

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

function handleCaughtError(e: unknown): ToolResult {
  if (e instanceof z.ZodError) {
    return mcpError(
      "validation_error",
      e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
    )
  }
  return mcpError("internal_error", e instanceof Error ? e.message : String(e))
}

// ─── Chave Redis do job ───────────────────────────────────────────────────────

function delegationJobKey(tenantId: string, jobId: string): string {
  return `${tenantId}:delegation:${jobId}`
}

const JOB_TTL_S = 3600  // 1 hora — suficiente para o polling sync (máx 5 min)

// ─── Registro das tools ───────────────────────────────────────────────────────

export function registerDelegationTools(server: McpServer, deps: DelegationDeps): void {
  const { redis, skillFlowUrl, tenantId } = deps

  // ── agent_delegate ────────────────────────────────────────────────────────
  server.tool(
    "agent_delegate",
    "Delega execução de uma skill a um agente especialista. " +
    "Modo assist: especialista executa em paralelo sem transferir ownership da sessão. " +
    "Retorna job_id para polling via agent_delegate_status. Spec 4.7.",
    AgentDelegateInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = AgentDelegateInputSchema.parse(input)
        const { session_id, target_skill, payload, delegation_mode } = parsed

        const jobId    = crypto.randomUUID()
        const jobKey   = delegationJobKey(tenantId, jobId)
        const now      = new Date().toISOString()

        // Criar registro do job no Redis
        await redis.set(jobKey, JSON.stringify({
          job_id:          jobId,
          status:          "queued",
          session_id,
          target_skill,
          delegation_mode,
          created_at:      now,
        }), "EX", JOB_TTL_S)

        // Disparar delegação no skill-flow-service (fire-and-return — não bloqueia)
        const delegateUrl = `${skillFlowUrl}/delegate`
        fetch(delegateUrl, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            job_id:           jobId,
            tenant_id:        tenantId,
            session_id,
            customer_id:      payload.customer_id ?? session_id,
            target_skill,
            pipeline_context: payload.pipeline_context ?? {},
          }),
        }).catch((err) => {
          // Non-fatal — job permanece em "queued" e expira naturalmente.
          // O polling em task.ts irá detectar o timeout via POLL_MAX_ATTEMPTS.
          console.warn(
            `[agent_delegate] POST ${delegateUrl} failed (job=${jobId}):`,
            err instanceof Error ? err.message : String(err)
          )
        })

        return ok({ job_id: jobId, status: "queued" })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── agent_delegate_status ─────────────────────────────────────────────────
  server.tool(
    "agent_delegate_status",
    "Consulta o status de uma delegação iniciada por agent_delegate. " +
    "Status possíveis: queued | running | completed | failed. Spec 4.7.",
    AgentDelegateStatusInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { job_id } = AgentDelegateStatusInputSchema.parse(input)

        const jobKey = delegationJobKey(tenantId, job_id)
        const raw    = await redis.get(jobKey)

        if (!raw) {
          // Job não encontrado — pode ter expirado ou nunca existido.
          return mcpError("job_not_found", `Delegation job '${job_id}' not found or expired`)
        }

        const job = JSON.parse(raw) as {
          status:   string
          outcome?: string
          result?:  unknown
          error?:   string
        }

        return ok({
          status:  job.status,
          outcome: job.outcome,
          result:  job.result,
          error:   job.error,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )
}
