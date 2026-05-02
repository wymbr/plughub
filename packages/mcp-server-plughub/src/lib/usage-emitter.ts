/**
 * lib/usage-emitter.ts
 * Utilitário de emissão de eventos de consumo (usage.events).
 *
 * Princípio: metering ≠ pricing.
 * As funções aqui apenas publicam o fato do consumo.
 * Nenhuma lógica de plano, tarifa ou quota vive aqui.
 *
 * Tópico Kafka: usage.events
 */

import { randomUUID } from "crypto"
import type { KafkaProducer } from "../infra/kafka"
import type { RedisClient }   from "../infra/redis"
import type { UsageEvent }    from "@plughub/schemas"

// ─────────────────────────────────────────────
// Tipo auxiliar — subset do UsageEvent sem os campos calculados
// ─────────────────────────────────────────────

type EmitParams = Omit<UsageEvent, "event_id" | "timestamp">

// ─────────────────────────────────────────────
// Emissão simples
// ─────────────────────────────────────────────

/**
 * Publica um evento de consumo em usage.events.
 * Fire-and-forget: erros são silenciosos para não bloquear o caminho operacional.
 */
export async function emitUsage(
  kafka: KafkaProducer,
  params: EmitParams
): Promise<void> {
  const event: UsageEvent = {
    event_id:  randomUUID(),
    timestamp: new Date().toISOString(),
    ...params,
  }
  try {
    await kafka.publish("usage.events", event as unknown as Record<string, unknown>)
  } catch {
    // Metering nunca bloqueia operação — falha silenciosa
  }
}

// ─────────────────────────────────────────────
// Helpers específicos por dimensão
// ─────────────────────────────────────────────

/**
 * Emite sessions (qty: 1) na primeira vez que uma sessão é servida.
 * Usa uma chave Redis como guard de idempotência para não duplicar em conference mode
 * (múltiplos agentes chamando agent_busy na mesma sessão).
 *
 * Chave: {tenant_id}:usage:session:{session_id}:counted  TTL: 5h
 */
export async function emitSessionOpened(
  kafka: KafkaProducer,
  redis: RedisClient,
  params: {
    tenant_id:  string
    session_id: string
    channel:    string
  }
): Promise<void> {
  const guardKey = `${params.tenant_id}:usage:session:${params.session_id}:counted`

  // SET NX — só o primeiro agent_busy para esta sessão publica
  const wasSet = await redis.set(guardKey, "1", "EX", 18000, "NX")  // 5h
  if (!wasSet) return   // já contabilizado

  await emitUsage(kafka, {
    tenant_id:        params.tenant_id,
    session_id:       params.session_id,
    dimension:        "sessions",
    quantity:         1,
    source_component: "core",
    metadata: {
      channel: params.channel,
      outcome: null,
    },
  })
}

/**
 * Emite messages (qty: 1) para mensagens com visibility: "all" enviadas ao stream.
 * Somente mensagens inbound (visíveis ao cliente) são contabilizadas.
 */
export async function emitMessageSent(
  kafka: KafkaProducer,
  params: {
    tenant_id:  string
    session_id: string
    channel:    string
    visibility: unknown
  }
): Promise<void> {
  // Apenas mensagens visíveis ao cliente (all) são contabilizadas
  if (params.visibility !== "all") return

  await emitUsage(kafka, {
    tenant_id:        params.tenant_id,
    session_id:       params.session_id,
    dimension:        "messages",
    quantity:         1,
    source_component: "core",
    metadata: {
      channel:    params.channel,
      visibility: params.visibility,
    },
  })
}
