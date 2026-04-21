/**
 * lib/quota-check.ts
 * Quota check mínimo sem pricing — lê limites configurados pelo operador no Redis.
 *
 * Princípio: o quota check não sabe nada sobre planos ou tarifas.
 * Lê apenas dois tipos de chave Redis:
 *   {tenant_id}:usage:current:{dimension}   — contador atual (escrito pelo Usage Aggregator)
 *   {tenant_id}:quota:limit:{dimension}      — limite configurado pelo operador
 *
 * Quando o módulo de pricing existir, ele passa a escrever os limites nessas mesmas
 * chaves baseado no plano do tenant. Os gateways não precisam mudar.
 *
 * Padrão INCRBY-check-rollback:
 *   Incrementa primeiro → checa o valor retornado → reverte se excedeu.
 *   Isso evita race conditions em bursts de requisições simultâneas.
 */

import type { RedisClient } from "../infra/redis"

// ─── Tipos ────────────────────────────────────────────────────────────────────

export type QuotaDimension =
  | "sessions"
  | "messages"
  | "llm_tokens_input"
  | "llm_tokens_output"
  | "whatsapp_conversations"
  | "voice_minutes"
  | "sms_segments"
  | "email_messages"

export class QuotaExceededError extends Error {
  constructor(
    public readonly dimension: QuotaDimension,
    public readonly current:   number,
    public readonly limit:     number,
    public readonly tenantId:  string,
  ) {
    super(
      `quota_exceeded: tenant=${tenantId} dimension=${dimension} ` +
      `current=${current} limit=${limit}`
    )
    this.name = "QuotaExceededError"
  }
}

// ─── Quota check principal ────────────────────────────────────────────────────

/**
 * Verifica e incrementa o contador de uma dimensão.
 *
 * Se {tenant_id}:quota:limit:{dimension} não existir no Redis, não há limite configurado
 * e a operação prossegue sem bloqueio.
 *
 * Throws QuotaExceededError se o limite for ultrapassado (com rollback automático).
 */
export async function assertQuota(
  redis:     RedisClient,
  tenantId:  string,
  dimension: QuotaDimension,
  quantity:  number = 1,
): Promise<void> {
  const limitKey   = `${tenantId}:quota:limit:${dimension}`
  const counterKey = `${tenantId}:usage:current:${dimension}`

  // Lê o limite configurado — se não existe, sem limite (retorna)
  const limitRaw = await redis.get(limitKey)
  if (limitRaw === null) return   // sem limite configurado para esta dimensão

  const limit = parseFloat(limitRaw)
  if (!isFinite(limit) || limit <= 0) return  // valor inválido — ignora

  // INCRBY primeiro, checa o valor retornado
  const current = await (redis as any).incrbyfloat(counterKey, quantity) as number

  if (current > limit) {
    // Rollback — reverte o incremento antes de lançar o erro
    await (redis as any).incrbyfloat(counterKey, -quantity)
    throw new QuotaExceededError(dimension, current, limit, tenantId)
  }
}

/**
 * Verifica o gauge de sessões simultâneas sem incrementar.
 * Usado pelo Channel Gateway antes de aceitar um inbound.
 *
 * Returns true se dentro do limite (ou sem limite configurado).
 */
export async function checkConcurrentSessions(
  redis:    RedisClient,
  tenantId: string,
): Promise<boolean> {
  const limitRaw   = await redis.get(`${tenantId}:quota:max_concurrent_sessions`)
  if (limitRaw === null) return true

  const limit      = parseInt(limitRaw, 10)
  if (!isFinite(limit) || limit <= 0) return true

  const currentRaw = await redis.get(`${tenantId}:quota:concurrent_sessions`)
  const current    = currentRaw ? parseInt(currentRaw, 10) : 0

  return current < limit
}
