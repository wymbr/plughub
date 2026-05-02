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
 *     status?      — filtra por status (login|ready|busy|paused|logout)
 *     pool_id?     — filtra por pool_id
 *     framework?   — filtra por framework (e.g. "human")
 *     page?        — paginação (default: 1)
 *     limit?       — itens por página (default: 50, max: 200)
 *
 * PATCH /v1/instances/:instance_id
 *   Operator actions: pause | resume | force_logout
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma }                  from "../db"
import { publishRegistryChanged }  from "../infra/kafka"

export const instancesRouter = Router()

// ─────────────────────────────────────────────
// GET /v1/instances
// ─────────────────────────────────────────────
instancesRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const status    = req.query["status"]    as string | undefined
    const poolId    = req.query["pool_id"]   as string | undefined
    const framework = req.query["framework"] as string | undefined
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
        ...((poolId || framework) && {
          agent_type: {
            ...(framework && { framework }),
            ...(poolId && {
              pools: {
                some: {
                  pool: {
                    pool_id:   poolId,
                    tenant_id: tenantId,
                  },
                },
              },
            }),
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
        ...((poolId || framework) && {
          agent_type: {
            ...(framework && { framework }),
            ...(poolId && {
              pools: {
                some: {
                  pool: {
                    pool_id:   poolId,
                    tenant_id: tenantId,
                  },
                },
              },
            }),
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
// GET /v1/instances/:instance_id
// Detalhe de uma instância (sem session_token)
// ─────────────────────────────────────────────
instancesRouter.get("/:instance_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId   = _getTenantId(req)
    const instanceId = req.params["instance_id"]!

    const inst = await prisma.agentInstance.findFirst({
      where: { instance_id: instanceId, tenant_id: tenantId },
      include: {
        agent_type: {
          select: {
            agent_type_id:           true,
            framework:               true,
            execution_model:         true,
            max_concurrent_sessions: true,
            traffic_weight:          true,
            status:                  true,
            pools: {
              select: { pool: { select: { pool_id: true } } },
            },
          },
        },
      },
    })
    if (!inst) return res.status(404).json({ error: "Instância não encontrada" })

    return res.json(_formatInstance(inst as unknown as Record<string, unknown>))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PATCH /v1/instances/:instance_id
// Operator action: pause | resume | force_logout
// ─────────────────────────────────────────────
instancesRouter.patch("/:instance_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId   = _getTenantId(req)
    const instanceId = req.params["instance_id"]!
    const action     = (req.body as { action?: string }).action

    const validActions = ["pause", "resume", "force_logout"]
    if (!action || !validActions.includes(action)) {
      return res.status(400).json({
        error: `action inválido — use: ${validActions.join(", ")}`,
      })
    }

    const existing = await prisma.agentInstance.findFirst({
      where: { instance_id: instanceId, tenant_id: tenantId },
    })
    if (!existing) return res.status(404).json({ error: "Instância não encontrada" })

    const newStatus =
      action === "pause"        ? "paused"  :
      action === "resume"       ? "ready"   :
      /* force_logout */          "logout"

    const updated = await prisma.agentInstance.update({
      where: { id: existing.id },
      data:  { status: newStatus as never },
    })

    // Notify routing engine that instance state changed
    await publishRegistryChanged(tenantId, "instance", instanceId, "updated")

    return res.json(_formatInstance(updated as unknown as Record<string, unknown>))
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
