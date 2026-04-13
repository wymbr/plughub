/**
 * infra/kafka.ts
 * Interface e factory do produtor Kafka para o mcp-server-plughub.
 *
 * Tópicos publicados:
 *   agent.lifecycle       — transições de ciclo de vida do agente
 *   conversations.events  — conclusão de conversas (após agent_done)
 */

import { Kafka, Producer, logLevel } from "kafkajs"

export interface KafkaProducer {
  publish(topic: string, message: Record<string, unknown>): Promise<void>
  disconnect(): Promise<void>
}

// ─── Produtor de produção ──────────────────────────────────────────────────────

export function createKafkaProducer(): KafkaProducer {
  const brokers = (process.env["KAFKA_BROKERS"] ?? "localhost:9092").split(",")

  const kafka = new Kafka({
    clientId: "mcp-server-plughub",
    brokers,
    logLevel: logLevel.ERROR,
  })

  const producer: Producer = kafka.producer()
  let connected = false

  return {
    async publish(topic, message) {
      if (!connected) {
        await producer.connect()
        connected = true
      }
      await producer.send({
        topic,
        messages: [{ value: JSON.stringify(message) }],
      })
    },

    async disconnect() {
      if (connected) {
        await producer.disconnect()
        connected = false
      }
    },
  }
}

// ─── Produtor no-op para testes ────────────────────────────────────────────────

export function createNoOpKafkaProducer(): KafkaProducer {
  return {
    async publish() {},
    async disconnect() {},
  }
}

// ─── Produtor de captura para testes de integração ────────────────────────────

export interface CapturedEvent {
  topic: string
  message: Record<string, unknown>
}

export interface CapturingKafkaProducer extends KafkaProducer {
  readonly events: CapturedEvent[]
  clear(): void
}

export function createCapturingKafkaProducer(): CapturingKafkaProducer {
  const events: CapturedEvent[] = []
  return {
    events,
    async publish(topic, message) {
      events.push({ topic, message })
    },
    async disconnect() {},
    clear() {
      events.length = 0
    },
  }
}
