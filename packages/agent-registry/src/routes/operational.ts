/**
 * routes/operational.ts
 * Real-time operational visibility — pool capacity and queue state.
 * Data sourced from Redis snapshots written by the Routing Engine.
 *
 * Endpoints (all require x-tenant-id header):
 *   GET /v1/operational/pools
 *     Returns all pools enriched with their live operational snapshot.
 *     Falls back gracefully when Redis has no snapshot yet.
 *
 *   GET /v1/operational/pools/:pool_id/queue
 *     Returns the list of sessions currently queued in a pool,
 *     with position, queued_at, and estimated_wait_ms.
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma }          from "../db"
import { getRedis, opKeys, getPoolSnapshot } from "../infra/redis"

export const operationalRouter = Router()

function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

// ── Live fallback helpers ──────────────────────────────────────────────────────
// When no Routing Engine snapshot exists (no active traffic yet), read directly
// from the orchestrator-bridge Redis keys.
// Key: {tenant}:pool:{pool_id}:instances  — SET of all instance_ids in the pool
// Key: {tenant}:pool:{pool_id}:queue      — ZSET of queued session_ids

async function _liveCount(tenantId: string, poolId: string): Promise<{ instances: number; queue: number }> {
  try {
    const redis = getRedis()
    const [instances, queue] = await Promise.all([
      redis.scard(opKeys.poolInstances(tenantId, poolId)),
      redis.zcard(opKeys.poolQueue(tenantId, poolId)),
    ])
    return { instances, queue }
  } catch {
    return { instances: 0, queue: 0 }
  }
}

// ── GET /v1/operational/pools ──────────────────────────────────────────────────
//
// Returns all pools for the tenant joined with their live Redis data.
// Priority: Routing Engine snapshot → live Redis fallback (instances SET + queue ZSET)
// Pool config (description, sla_target_ms, channel_types) comes from PostgreSQL.

operationalRouter.get("/pools", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)

    // 1. Load pool configurations from PostgreSQL
    const pools = await prisma.pool.findMany({
      where:   { tenant_id: tenantId, status: "active" },
      orderBy: { pool_id: "asc" },
    })

    // 2. Load snapshots + live counts in parallel
    const [snapshots, liveCounts] = await Promise.all([
      Promise.all(pools.map(p => getPoolSnapshot(tenantId, p.pool_id))),
      Promise.all(pools.map(p => _liveCount(tenantId, p.pool_id))),
    ])

    // 3. Merge: snapshot takes priority; fall back to live counts
    const result = pools.map((p, i) => {
      const snap      = snapshots[i]
      const live      = liveCounts[i]!
      const hasSnap   = snap !== null

      const snapshotAgeMs = hasSnap ? Date.now() - Date.parse(snap!.updated_at) : null

      // Use snapshot values if available, else live counts
      const available   = hasSnap ? snap!.available    : live.instances
      const queueLength = hasSnap ? snap!.queue_length : live.queue

      const status =
        available > 0   ? "available"
        : queueLength > 0 ? "queued"
        : "empty"

      // Estimated wait: each position waits ~sla*0.7 (p70 handle time heuristic)
      const slaMs           = p.sla_target_ms
      const avgHandleMs     = slaMs * 0.7
      const estimatedWaitMs = queueLength > 0 ? Math.round(queueLength * avgHandleMs) : null

      return {
        // Pool config
        pool_id:          p.pool_id,
        description:      p.description ?? null,
        sla_target_ms:    slaMs,
        channel_types:    p.channel_types,
        pool_status:      p.status,

        // Operational state
        op_status:        status,
        available:        available,
        queue_length:     queueLength,
        estimated_wait_ms: estimatedWaitMs,
        snapshot_age_ms:     snapshotAgeMs,
        snapshot_updated_at: hasSnap ? snap!.updated_at : null,

        // Source flags
        has_snapshot:  hasSnap,
        live_fallback: !hasSnap,  // true = data from :instances/:queue keys directly
      }
    })

    return res.json({ items: result, total: result.length })
  } catch (err) {
    return next(err)
  }
})

// ── GET /v1/operational/pools/:pool_id/queue ───────────────────────────────────
//
// Returns the ordered list of sessions currently waiting in the pool's queue.
// ZSET score = queued_at_ms (Unix timestamp in milliseconds).

operationalRouter.get("/pools/:pool_id/queue", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    // Verify pool exists
    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const redis  = getRedis()
    const qKey   = opKeys.poolQueue(tenantId, poolId)

    // ZRANGE with WITHSCORES — returns [member, score, member, score, ...]
    // Use ZRANGE key 0 -1 WITHSCORES (oldest first = lowest score first)
    let raw: string[] = []
    try {
      raw = await redis.zrange(qKey, 0, -1, "WITHSCORES") as string[]
    } catch {
      // Redis unreachable — return empty queue
      return res.json({ pool_id: poolId, queue: [], total: 0 })
    }

    // Parse pairs into queue entries
    const slaMs       = pool.sla_target_ms
    const avgHandleMs = slaMs * 0.7
    const now         = Date.now()

    const queue: {
      position:          number
      session_id:        string
      queued_at:         string
      wait_ms:           number
      estimated_wait_ms: number
    }[] = []

    for (let i = 0; i < raw.length; i += 2) {
      const sessionId  = raw[i]!
      const score      = parseFloat(raw[i + 1]!)
      const queuedAt   = new Date(score).toISOString()
      const waitMs     = now - score
      const position   = (i / 2) + 1

      // Estimated remaining wait: ahead agents * avg handle time
      const aheadCount = position - 1
      const estimatedWaitMs = Math.max(0, Math.round(aheadCount * avgHandleMs))

      queue.push({ position, session_id: sessionId, queued_at: queuedAt, wait_ms: Math.round(waitMs), estimated_wait_ms: estimatedWaitMs })
    }

    return res.json({ pool_id: poolId, queue, total: queue.length })
  } catch (err) {
    return next(err)
  }
})
