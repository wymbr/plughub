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

  /** JSON do contato enfileirado (escrito pelo Routing Engine) */
  queueContact: (tenantId: string, sessionId: string) =>
    `${tenantId}:queue_contact:${sessionId}`,

  /** P3 — epoch ms do PRIMEIRO enqueue (NX+TTL, escrito pelo Routing Engine);
   * preserva a espera real do contato através de re-enfileiramentos. */
  firstQueued: (tenantId: string, sessionId: string) =>
    `${tenantId}:queue:first_queued:${sessionId}`,

  /** I5 — ledger do item de trabalho parqueado por um `delegate` (escrito pelo
   * channel-gateway no despacho): { pool_id, queue_session_id, resume_token,
   * step_id, assigned_to, deadline }. É onde o gatilho de supervisor acha o
   * resume_token a partir do session_id. */
  workTask: (tenantId: string, sessionId: string) =>
    `${tenantId}:work_task:${sessionId}`,

  /** Frente 1 (pull): lease do claim — {instance_id, claimed_at} */
  claimLease: (tenantId: string, poolId: string, sessionId: string) =>
    `${tenantId}:pool:${poolId}:claim:${sessionId}`,

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
