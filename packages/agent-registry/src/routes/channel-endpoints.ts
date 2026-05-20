/**
 * routes/channel-endpoints.ts
 * CRUD for ChannelEndpoint — maps external channel identifiers to pools.
 *
 * Each entry point (WhatsApp number, webchat slug, voice DID, etc.)
 * is a separate record that maps to exactly one pool.
 *
 * Publishes registry.changed on every write so channel-gateway can
 * invalidate its lookup cache.
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma }                                   from "../db"
import { publishRegistryChanged }                   from "../infra/kafka"
import type { ChannelEndpointDelegate }             from "../types/channel-endpoint"

// Typed shim until `prisma generate` is re-run with the updated schema
const channelEndpoint = (prisma as unknown as { channelEndpoint: ChannelEndpointDelegate }).channelEndpoint

export const channelEndpointsRouter = Router()

const VALID_CHANNELS = new Set(["webchat", "whatsapp", "voice", "sms", "email", "webhook"])

// ─────────────────────────────────────────────
// GET /v1/channel-endpoints
// List endpoints for tenant, optionally filtered by channel / pool_id / active
// ─────────────────────────────────────────────
channelEndpointsRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId       = _getTenantId(req)
    const channel         = req.query["channel"]           as string | undefined
    const identifier      = req.query["identifier"]        as string | undefined
    const poolId          = req.query["pool_id"]           as string | undefined
    const activeQ         = req.query["active"]            as string | undefined
    const gatewayConfigId = req.query["gateway_config_id"] as string | undefined

    const where: Record<string, unknown> = { tenant_id: tenantId }
    if (channel)              where["channel"]          = channel
    if (identifier)           where["identifier"]       = identifier
    if (poolId)               where["pool_id"]          = poolId
    if (activeQ !== undefined) where["active"]          = activeQ === "true"
    if (gatewayConfigId)      where["gateway_config_id"] = gatewayConfigId

    const endpoints = await channelEndpoint.findMany({
      where,
      orderBy: [{ channel: "asc" }, { identifier: "asc" }],
    })

    return res.json({ endpoints })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/channel-endpoints/:id
// ─────────────────────────────────────────────
channelEndpointsRouter.get("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const ep = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!ep) return res.status(404).json({ error: "Channel endpoint not found" })

    return res.json(ep)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// POST /v1/channel-endpoints
// ─────────────────────────────────────────────
channelEndpointsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const body     = req.body as {
      channel:            string
      identifier:         string
      pool_id:            string
      display_name:       string
      settings?:          Record<string, unknown>
      active?:            boolean
      gateway_config_id?: string | null
    }

    if (!body.channel || !VALID_CHANNELS.has(body.channel)) {
      return res.status(400).json({
        error: `invalid channel — must be one of: ${[...VALID_CHANNELS].join(", ")}`,
      })
    }
    if (!body.identifier?.trim())   return res.status(400).json({ error: "identifier is required" })
    if (!body.pool_id?.trim())      return res.status(400).json({ error: "pool_id is required" })
    if (!body.display_name?.trim()) return res.status(400).json({ error: "display_name is required" })

    // Enforce uniqueness (tenant, channel, identifier) — Prisma unique constraint will also catch it
    const existing = await channelEndpoint.findFirst({
      where: { tenant_id: tenantId, channel: body.channel, identifier: body.identifier.trim() },
    })
    if (existing) {
      return res.status(409).json({
        error: `A channel endpoint for ${body.channel}/${body.identifier} already exists`,
      })
    }

    const ep = await channelEndpoint.create({
      data: {
        tenant_id:         tenantId,
        channel:           body.channel,
        identifier:        body.identifier.trim(),
        pool_id:           body.pool_id.trim(),
        display_name:      body.display_name.trim(),
        settings:          body.settings ?? {},
        active:            body.active ?? true,
        gateway_config_id: body.gateway_config_id ?? null,
      },
    })

    await publishRegistryChanged(tenantId, "channel_endpoint", ep.id, "created")

    return res.status(201).json(ep)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PUT /v1/channel-endpoints/:id
// Partial update — channel and identifier are immutable after creation
// ─────────────────────────────────────────────
channelEndpointsRouter.put("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!
    const body     = req.body as {
      pool_id?:            string
      display_name?:       string
      settings?:           Record<string, unknown>
      active?:             boolean
      gateway_config_id?:  string | null
    }

    const existing = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!existing) return res.status(404).json({ error: "Channel endpoint not found" })

    const updates: Record<string, unknown> = {}
    if (body.pool_id           !== undefined) updates["pool_id"]           = body.pool_id.trim()
    if (body.display_name      !== undefined) updates["display_name"]      = body.display_name.trim()
    if (body.settings          !== undefined) updates["settings"]          = body.settings
    if (body.active            !== undefined) updates["active"]            = body.active
    if (body.gateway_config_id !== undefined) updates["gateway_config_id"] = body.gateway_config_id ?? null

    if (Object.keys(updates).length === 0) {
      return res.status(400).json({ error: "No updatable fields provided" })
    }

    const updated = await channelEndpoint.update({ where: { id }, data: updates })

    await publishRegistryChanged(tenantId, "channel_endpoint", id, "updated")

    return res.json(updated)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// DELETE /v1/channel-endpoints/:id
// ─────────────────────────────────────────────
channelEndpointsRouter.delete("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const existing = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!existing) return res.status(404).json({ error: "Channel endpoint not found" })

    await channelEndpoint.delete({ where: { id } })
    await publishRegistryChanged(tenantId, "channel_endpoint", id, "deleted")

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
