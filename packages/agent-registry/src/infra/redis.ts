/**
 * infra/redis.ts
 * Redis client for agent-registry — read-only access to operational snapshots
 * written by the Routing Engine.
 *
 * Key patterns (tenant-prefixed, mirrors mcp-server-plughub):
 *   {tenant}:pools                       SET  — pool IDs registered for tenant
 *   {tenant}:pool:{pool_id}:snapshot     STR  — JSON PoolSnapshot (TTL 120s)
 *   {tenant}:pool:{pool_id}:queue        ZSET — session_ids scored by queued_at_ms
 */

import Redis from "ioredis"
import { config } from "../config"

let _client: Redis | null = null

export function getRedis(): Redis {
  if (!_client) {
    _client = new Redis(config.redis_url, { lazyConnect: true, maxRetriesPerRequest: 1 })
    _client.on("error", (err: Error) => {
      // Non-fatal — operational data degrades gracefully
      if (process.env["NODE_ENV"] !== "test") {
        console.warn("[agent-registry] Redis error:", err.message)
      }
    })
  }
  return _client
}

// ── Key helpers ────────────────────────────────────────────────────────────────

export const opKeys = {
  /** SET of pool_ids for a tenant */
  tenantPools: (tenantId: string) => `${tenantId}:pools`,

  /** Pool operational snapshot (JSON, TTL 120s) written by Routing Engine */
  poolSnapshot: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:snapshot`,

  /** ZSET of queued session_ids (score = queued_at_ms) */
  poolQueue: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:queue`,

  /** SET of all instance_ids registered to a pool (written by orchestrator-bridge) */
  poolInstances: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:instances`,
}

// ── Pool snapshot type ─────────────────────────────────────────────────────────

export interface PoolSnapshot {
  pool_id:       string
  tenant_id:     string
  available:     number
  queue_length:  number
  sla_target_ms: number
  channel_types: string[]
  updated_at:    string
}

export async function getPoolSnapshot(
  tenantId: string,
  poolId:   string,
): Promise<PoolSnapshot | null> {
  try {
    const raw = await getRedis().get(opKeys.poolSnapshot(tenantId, poolId))
    if (!raw) return null
    return JSON.parse(raw) as PoolSnapshot
  } catch {
    return null
  }
}
