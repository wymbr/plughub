/**
 * infra/redis.ts
 * Factory do cliente Redis e helpers de chaves para o mcp-server-plughub.
 * Todas as chaves são prefixadas com {tenant_id}: conforme spec 14 (multi-tenant).
 */

import Redis from "ioredis"

export type RedisClient = Redis

export function createRedisClient(): RedisClient {
  const url = process.env["REDIS_URL"] ?? "redis://localhost:6379"
  return new Redis(url, { lazyConnect: true })
}

// ─── Key helpers (tenant-prefixed) ────────────────────────────────────────────

export const keys = {
  /** Estado e metadata da instância do agente */
  agentInstance: (tenantId: string, instanceId: string) =>
    `${tenantId}:agent:instance:${instanceId}`,

  /** session_token → instance_id (TTL = expiração do JWT) */
  agentToken: (tenantId: string, sessionToken: string) =>
    `${tenantId}:agent:token:${sessionToken}`,

  /** SET de instance_ids disponíveis num pool */
  poolAvailable: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:available`,

  /** SET de conversation_ids ativos para uma instância */
  agentConversations: (tenantId: string, instanceId: string) =>
    `${tenantId}:agent:instance:${instanceId}:conversations`,

  /** Insight de sessão: chave → JSON da SessionItem */
  insight: (tenantId: string, conversationId: string, itemId: string) =>
    `${tenantId}:insight:${conversationId}:${itemId}`,

  /** Snapshot operacional de pool — escrito pelo Routing Engine após cada roteamento */
  poolQueueSnapshot: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:snapshot`,

  /** ZSET de sessões na fila de um pool (score = queued_at_ms) */
  poolQueue: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:queue`,

  /** SET de instance_ids disponíveis (prontos) num pool */
  poolInstances: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:instances`,
}

// ─── Estado canônico da instância ──────────────────────────────────────────────

export type AgentInstanceState =
  | "logged_in"
  | "ready"
  | "busy"
  | "paused"
  | "draining"
  | "logged_out"
