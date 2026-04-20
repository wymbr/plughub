/**
 * routes/instances.ts
 * Leitura de instâncias de agentes em execução.
 * Spec: PlugHub v24.0 seção 4.5 — ciclo de vida de instância
 *
 * Instâncias são criadas pelo mcp-server-plughub (agent_login).
 * O Agent Registry expõe apenas leitura.
 *
 * GET /v1/instances
 *   Query params:
 *     status?  — filtra por status (login|ready|busy|paused|logout)
 *     pool_id? — filtra por pool_id
 *     page?    — paginação (default: 1)
 *     limit?   — itens por página (default: 50, max: 200)
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma } from "../db"

export const instancesRouter = Router()

// ─────────────────────────────────────────────
// GET /v1/instances
// ─────────────────────────────────────────────
instancesRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const status   = req.query["status"]  as string | undefined
    const poolId   = req.query["pool_id"] as string | undefined
    const page     = Math.max(1, parseInt((req.query["page"]  as string) ?? "1",  10))
    const limit    = Math.min(200, Math.max(1, parseInt((req.query["limit"] as string) ?? "50", 10)))

    // Validar status se fornecido
    const validStatuses = ["login", "ready", "busy", "paused", "logout"]
    if (status && !validStatuses.includes(status)) {
      return res.status(422).json({
        error:  "validation_error",
        detail: `status inválido — use: ${validStatuses.join(", ")}`,
      })
    }

    const instances = await prisma.agentInstance.findMany({
      where: {
        tenant_id: tenantId,
        ...(status && { status: status as never }),
        ...(poolId && {
          agent_type: {
            pools: {
              some: {
                pool: {
                  pool_id:   poolId,
                  tenant_id: tenantId,
                },
              },
            },
          },
        }),
      },
      include: {
        agent_type: {
          select: {
            agent_type_id:           true,
            framework:               true,
            execution_model:         true,
            max_concurrent_sessions: true,
            traffic_weight:          true,
            status:                  true,
          },
        },
      },
      orderBy: { updated_at: "desc" },
      skip:  (page - 1) * limit,
      take:  limit,
    })

    const total = await prisma.agentInstance.count({
      where: {
        tenant_id: tenantId,
        ...(status && { status: status as never }),
        ...(poolId && {
          agent_type: {
            pools: {
              some: {
                pool: {
                  pool_id:   poolId,
                  tenant_id: tenantId,
                },
              },
            },
          },
        }),
      },
    })

    return res.json({
      instances: instances.map(_formatInstance),
      total,
      page,
      limit,
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

function _formatInstance(inst: Record<string, unknown>): Record<string, unknown> {
  const { id: _id, session_token: _token, ...rest } = inst
  return rest
}
