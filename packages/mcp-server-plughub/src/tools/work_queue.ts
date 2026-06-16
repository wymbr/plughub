/**
 * tools/work_queue.ts
 * Frente 1 F2a-2 — dispatch pull: operações de fila para o operador (inbox).
 *
 * LEITURA (work_queue_list) é Redis-direta (mesmo padrão de operational.ts).
 * ESCRITA (claim/release) vai por HTTP ao Routing Engine — o único árbitro:
 *   POST {routingUrl}/v1/work_queue/{claim,release}
 * (ZREM/claim_instance/mark_busy/lease/routed acontecem DENTRO do engine.)
 */

import { McpServer }       from "@modelcontextprotocol/sdk/server/mcp.js"
import { z }               from "zod"
import type { RedisClient } from "../infra/redis"
import { keys }            from "../infra/redis"

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

  async function callRouting(path: string, body: unknown): Promise<unknown> {
    const res = await fetch(`${routingUrl}${path}`, {
      method:  "POST",
      headers: {
        "content-type": "application/json",
        ...(adminToken ? { "X-Admin-Token": adminToken } : {}),
      },
      body: JSON.stringify(body),
    })
    return res.json()
  }

  // ── work_queue_list ────────────────────────────────────────────────────────
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
      const limit = Math.max(1, Math.min(top_n ?? 20, 100))
      const nowMs = Date.now()
      const contacts: unknown[] = []
      for (const pool_id of pools) {
        let sessions: string[] = []
        try {
          sessions = await redis.zrevrange(keys.poolQueue(tenant_id, pool_id), 0, limit - 1)
        } catch { sessions = [] }
        for (const session_id of sessions) {
          let contact: Record<string, unknown> | null = null
          try {
            const raw = await redis.get(keys.queueContact(tenant_id, session_id))
            if (raw) contact = JSON.parse(raw)
          } catch { /* ignore */ }
          const queuedAtMs = Number(contact?.["queued_at_ms"]) || 0
          contacts.push({
            session_id,
            pool_id,
            state:        "claimable",
            channel:      contact?.["channel"]  ?? null,
            summary:      contact?.["summary"]  ?? contact?.["title"] ?? null,
            queued_at_ms: queuedAtMs || null,
            age_ms:       queuedAtMs ? Math.max(nowMs - queuedAtMs, 0) : null,
          })
        }
      }
      return mcpOk({ contacts, total: contacts.length })
    },
  )

  // ── work_task_claim ──────────────────────────────────────────────────────
  server.tool(
    "work_task_claim",
    "Retira (claim) um contato de uma fila pull para um agente logado. O Routing Engine " +
    "concede atomicamente (um único vencedor) e o contato vira atendimento normal.",
    {
      tenant_id:     z.string(),
      pool_id:       z.string(),
      session_id:    z.string(),
      instance_id:   z.string().describe("Instância do agente que está puxando"),
      conference_id: z.string().optional(),
    } as any,
    async (args: { tenant_id: string; pool_id: string; session_id: string; instance_id: string; conference_id?: string }) => {
      try {
        const result = await callRouting("/v1/work_queue/claim", {
          tenant_id:     args.tenant_id,
          pool_id:       args.pool_id,
          session_id:    args.session_id,
          instance_id:   args.instance_id,
          conference_id: args.conference_id ?? "",
        })
        return mcpOk(result)
      } catch (err) {
        return mcpError("routing_unreachable", `claim falhou: ${String(err)}`)
      }
    },
  )

  // ── work_task_release ────────────────────────────────────────────────────
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
        const result = await callRouting("/v1/work_queue/release", {
          tenant_id:   args.tenant_id,
          pool_id:     args.pool_id,
          session_id:  args.session_id,
          instance_id: args.instance_id,
        })
        return mcpOk(result)
      } catch (err) {
        return mcpError("routing_unreachable", `release falhou: ${String(err)}`)
      }
    },
  )
}
