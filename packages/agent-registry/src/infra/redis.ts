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

  /** SET of all instance_ids registered to a pool (written by orchestrator-bridge).
   *  É PERTENCIMENTO, não capacidade: `SCARD` conta instância lotada como disponível e
   *  ignora a vaga que o recurso gastou em pool irmão. */
  poolInstances: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:instances`,

  /** Rollup de capacidade do tenant por TIPO de licença (F4), escrito pelo Routing
   *  Engine sobre instâncias DISTINTAS. Única fonte válida para "quantos agentes há":
   *  somar `available` das linhas de pool conta o mesmo recurso uma vez por pool. */
  tenantCapacity: (tenantId: string) => `${tenantId}:capacity:snapshot`,
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

  // Capacidade compartilhada (fatia 2). Opcionais porque há DOIS escritores:
  //   · routing-engine  → model "resource_semaphore", publica todos os campos;
  //   · bootstrap (NX)  → model "bootstrap_placeholder", publica só available/
  //     total_instances — ele não parseia a tag de pool do membro do semáforo e
  //     não pode separar o consumo deste pool do consumo dos irmãos.
  // Ausente ≠ zero: quem lê `busy` sem snapshot do routing-engine não SABE, e
  // apresentar 0 seria inventar. Discriminar por `model`, nunca por `?? 0`.
  busy?:            number   // sessões servidas NESTE pool (projeção pela tag)
  busy_elsewhere?:  number   // vagas do MESMO recurso consumidas por pools irmãos
  untagged?:        number   // ocupantes sem tag — devem ir a zero em ≤24h
  total_instances?: number   // CAPACIDADE (Σ max_concurrent), não contagem
  model?:           "resource_semaphore" | "bootstrap_placeholder"
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
