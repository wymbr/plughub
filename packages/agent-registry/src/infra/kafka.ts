/**
 * infra/kafka.ts
 * Kafka producer para o Agent Registry.
 * Publica eventos agent.registry.events quando pools e agent types são criados/atualizados.
 * Spec: PlugHub v24.0 seção 4.5
 *
 * Falhas de Kafka nunca bloqueiam a operação REST — são logadas e descartadas.
 */

import { Kafka, Producer, logLevel } from "kafkajs"
import { config } from "../config"

let _producer: Producer | null = null

async function getProducer(): Promise<Producer> {
  if (_producer) return _producer

  const kafka = new Kafka({
    clientId: "agent-registry",
    brokers:  config.kafka_brokers.split(",").map(b => b.trim()),
    logLevel: logLevel.ERROR,
  })

  _producer = kafka.producer({
    allowAutoTopicCreation: true,
    retry: { retries: 2 },
  })

  await _producer.connect()
  return _producer
}

/**
 * Publica um evento no tópico Kafka especificado.
 * Falhas são capturadas e logadas — nunca propagadas ao chamador.
 */
export async function publishRegistryEvent(event: unknown): Promise<void> {
  try {
    const producer = await getProducer()
    await producer.send({
      topic:    config.kafka_topic_registry,
      messages: [{ value: JSON.stringify(event) }],
    })
  } catch (err) {
    // Kafka failure must not break REST API — log and continue
    console.error("[agent-registry] Kafka publish failed:", err instanceof Error ? err.message : err)
  }
}

/**
 * Publica um evento no tópico `registry.changed`.
 * Consumido pelo orchestrator-bridge para disparar reconciliação imediata.
 * Deve ser chamado após qualquer mutação de AgentType ou Pool que afete
 * o estado desejado de instâncias no Redis.
 * Falhas são capturadas e logadas — nunca propagadas ao chamador.
 */
export async function publishRegistryChanged(
  tenantId:   string,
  entityType: string,
  entityId:   string,
  operation:  "created" | "updated" | "deleted",
): Promise<void> {
  try {
    const producer = await getProducer()
    await producer.send({
      topic:    "registry.changed",
      messages: [{
        key:   tenantId,
        value: JSON.stringify({
          event_type:  "registry.changed",
          entity_type: entityType,
          entity_id:   entityId,
          operation,
          tenant_id:   tenantId,
          timestamp:   new Date().toISOString(),
          source:      "agent-registry",
        }),
      }],
    })
  } catch (err) {
    console.error("[agent-registry] registry.changed publish failed:", err instanceof Error ? err.message : err)
  }
}

export async function disconnectKafka(): Promise<void> {
  if (_producer) {
    try { await _producer.disconnect() } catch { /* ignore */ }
    _producer = null
  }
}
