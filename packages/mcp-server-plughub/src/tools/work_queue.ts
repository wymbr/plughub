/**
 * tools/work_queue.ts
 * Frente 1 F2a-2 — dispatch pull: tools MCP (clientes MCP/IA).
 *
 * Wrappers finos sobre `lib/work-queue.ts` (lógica compartilhada com as rotas HTTP
 * /api/work_queue/* da inbox do Console). LEITURA (work_queue_list) é Redis-direta;
 * ESCRITA (claim/release) vai por HTTP ao Routing Engine — o único árbitro.
 */

import { McpServer }       from "@modelcontextprotocol/sdk/server/mcp.js"
import { z }               from "zod"
import type { RedisClient } from "../infra/redis"
import { listQueue, claimTask, releaseTask } from "../lib/work-queue"

export interface WorkQueueDeps {
  redis:       RedisClient
  routingUrl:  string                  // ex.: http://routing-engine:3550
  adminToken?: string | undefined      // X-Admin-Token opcional (exactOptionalPropertyTypes)
}

function mcpOk(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}
function mcpError(code: string, message: string) {
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }] }
}

export function registerWorkQueueTools(server: McpServer, deps: WorkQueueDeps): void {
  const { redis, routingUrl, adminToken } = deps

  server.tool(
    "work_queue_list",
    "Lista os contatos CLAIMÁVEIS nas filas pull dos pools informados (dispatch pull). " +
    "Leitura — não retira o contato. Passe 'pools' = pools pull acessíveis ao agente.",
    {
      tenant_id: z.string().describe("Tenant ID"),
      pools:     z.array(z.string()).describe("Pools pull a listar (accessible_pools do agente)"),
      top_n:     z.number().optional().describe("Máximo de contatos por pool (default 20)"),
    } as any,
    async ({ tenant_id, pools, top_n }: { tenant_id: string; pools: string[]; top_n?: number }) => {
      const contacts = await listQueue(redis, tenant_id, pools, top_n ?? 20)
      return mcpOk({ contacts, total: contacts.length })
    },
  )

  server.tool(
    "work_task_claim",
    "Retira (claim) um contato de uma fila pull para um agente logado. O Routing Engine " +
    "concede atomicamente (um único vencedor) e o contato vira atendimento normal.",
    {
      tenant_id:        z.string(),
      pool_id:          z.string(),
      session_id:       z.string(),
      instance_id:      z.string().describe("Instância do agente que está puxando"),
      conference_id:    z.string().optional(),
      claimant_user_id: z.string().optional().describe("Camada B — user_id do claimant p/ casar com assigned_to (ramal); ausente = derivado de instance_id"),
    } as any,
    async (args: { tenant_id: string; pool_id: string; session_id: string; instance_id: string; conference_id?: string; claimant_user_id?: string }) => {
      try {
        return mcpOk(await claimTask(routingUrl, adminToken, args))
      } catch (err) {
        return mcpError("routing_unreachable", `claim falhou: ${String(err)}`)
      }
    },
  )

  server.tool(
    "work_task_release",
    "Devolve um contato claimado à fila pull (o agente desistiu da task) — re-enfileira e libera a vaga.",
    {
      tenant_id:   z.string(),
      pool_id:     z.string(),
      session_id:  z.string(),
      instance_id: z.string(),
    } as any,
    async (args: { tenant_id: string; pool_id: string; session_id: string; instance_id: string }) => {
      try {
        return mcpOk(await releaseTask(routingUrl, adminToken, args))
      } catch (err) {
        return mcpError("routing_unreachable", `release falhou: ${String(err)}`)
      }
    },
  )
}
