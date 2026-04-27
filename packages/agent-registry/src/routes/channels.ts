/**
 * routes/channels.ts
 * CRUD de GatewayConfig — configurações de canal por tenant
 *
 * Credenciais são mascaradas nas respostas GET (exceto na criação/update,
 * onde o payload de entrada é devolvido para confirmação antes de persistir).
 *
 * Channels suportados: whatsapp | webchat | voice | email | sms | instagram | telegram | webrtc
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma }                                   from "../db"
import { publishRegistryChanged }                   from "../infra/kafka"
import type { GatewayConfigDelegate }               from "../types/gateway-config"

// Typed shim until `prisma generate` is re-run with the updated schema
const gatewayConfig = (prisma as unknown as { gatewayConfig: GatewayConfigDelegate }).gatewayConfig

export const channelsRouter = Router()

const VALID_CHANNELS = new Set([
  "whatsapp", "webchat", "voice", "email",
  "sms", "instagram", "telegram", "webrtc",
])

// ─────────────────────────────────────────────
// GET /v1/channels
// Lista todas as configurações de canal do tenant
// ─────────────────────────────────────────────
channelsRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const channel  = req.query["channel"] as string | undefined

    const where: Record<string, unknown> = { tenant_id: tenantId }
    if (channel) where["channel"] = channel

    const configs = await gatewayConfig.findMany({
      where,
      orderBy: [{ channel: "asc" }, { created_at: "asc" }],
    })

    return res.json({ channels: configs.map(_maskCredentials) })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// POST /v1/channels
// Cria nova configuração de canal
// ─────────────────────────────────────────────
channelsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const createdBy = _getUserId(req)
    const body      = req.body as {
      channel:       string
      display_name:  string
      active?:       boolean
      credentials?:  Record<string, unknown>
      settings?:     Record<string, unknown>
    }

    if (!body.channel || !VALID_CHANNELS.has(body.channel)) {
      return res.status(400).json({
        error: `channel inválido — deve ser um de: ${[...VALID_CHANNELS].join(", ")}`,
      })
    }
    if (!body.display_name?.trim()) {
      return res.status(400).json({ error: "display_name é obrigatório" })
    }

    const config = await gatewayConfig.create({
      data: {
        tenant_id:    tenantId,
        channel:      body.channel,
        display_name: body.display_name.trim(),
        active:       body.active ?? true,
        credentials:  body.credentials ?? {},
        settings:     body.settings    ?? {},
        created_by:   createdBy,
      },
    })

    await publishRegistryChanged(tenantId, "gateway_config", config.id, "created")

    return res.status(201).json(_maskCredentials(config))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/channels/:id
// Detalhe de uma configuração (credenciais mascaradas)
// ─────────────────────────────────────────────
channelsRouter.get("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const config = await gatewayConfig.findFirst({
      where: { id, tenant_id: tenantId },
    })
    if (!config) return res.status(404).json({ error: "Configuração não encontrada" })

    return res.json(_maskCredentials(config))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PUT /v1/channels/:id
// Atualiza configuração (merge parcial)
// ─────────────────────────────────────────────
channelsRouter.put("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!
    const body     = req.body as {
      display_name?:  string
      active?:        boolean
      credentials?:   Record<string, unknown>
      settings?:      Record<string, unknown>
    }

    const existing = await gatewayConfig.findFirst({
      where: { id, tenant_id: tenantId },
    })
    if (!existing) return res.status(404).json({ error: "Configuração não encontrada" })

    const updates: Record<string, unknown> = {}
    if (body.display_name !== undefined) updates["display_name"] = body.display_name.trim()
    if (body.active       !== undefined) updates["active"]       = body.active
    if (body.credentials  !== undefined) updates["credentials"]  = body.credentials
    if (body.settings     !== undefined) updates["settings"]     = body.settings

    const updated = await gatewayConfig.update({
      where: { id },
      data:  updates,
    })

    await publishRegistryChanged(tenantId, "gateway_config", id, "updated")

    return res.json(_maskCredentials(updated))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// DELETE /v1/channels/:id
// Remove configuração de canal
// ─────────────────────────────────────────────
channelsRouter.delete("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const existing = await gatewayConfig.findFirst({
      where: { id, tenant_id: tenantId },
    })
    if (!existing) return res.status(404).json({ error: "Configuração não encontrada" })

    await gatewayConfig.delete({ where: { id } })
    await publishRegistryChanged(tenantId, "gateway_config", id, "deleted")

    return res.status(204).send()
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
function _getUserId(req: Request): string {
  return (req.headers["x-user-id"] as string) ?? "system"
}

/** Mask sensitive credential values — replaces every value with "••••••" */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function _maskCredentials(config: any): Record<string, unknown> {
  const credentials = config["credentials"] as Record<string, unknown> | null | undefined
  const masked: Record<string, string> = {}
  if (credentials && typeof credentials === "object") {
    for (const key of Object.keys(credentials)) {
      masked[key] = "••••••"
    }
  }
  return { ...config, credentials: masked }
}
