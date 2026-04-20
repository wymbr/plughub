/**
 * kafka-client.ts
 * KafkaJS helpers for the E2E test suite.
 */

import { Kafka, Producer, Consumer, logLevel } from "kafkajs";
import { randomUUID } from "crypto";

export { Kafka };

export function createTestKafka(brokers: string[]): Kafka {
  return new Kafka({
    clientId: `e2e-test-${randomUUID()}`,
    brokers,
    logLevel: logLevel.WARN,
    connectionTimeout: 5000,
    requestTimeout: 10000,
    retry: { retries: 5 },
  });
}

/**
 * Publishes an event to conversations.inbound.
 * Routing Engine consumes this and publishes to conversations.routed.
 */
export async function publishInboundEvent(
  kafka: Kafka,
  event: {
    session_id: string;
    tenant_id: string;
    channel: string;
    customer_id: string;
    intent_data?: { confidence: number; intent: string };
    customer_profile?: { tier: string };
  }
): Promise<void> {
  const producer = kafka.producer();
  await producer.connect();
  try {
    await producer.send({
      topic: "conversations.inbound",
      messages: [
        {
          key: event.session_id,
          value: JSON.stringify({
            ...event,
            timestamp: new Date().toISOString(),
          }),
        },
      ],
    });
  } finally {
    await producer.disconnect();
  }
}

/**
 * Publishes multiple inbound events concurrently using a single producer.
 */
export async function publishInboundEventsBatch(
  kafka: Kafka,
  events: Array<{
    session_id: string;
    tenant_id: string;
    channel: string;
    customer_id: string;
    intent_data?: { confidence: number; intent: string };
    customer_profile?: { tier: string };
  }>
): Promise<void> {
  const producer = kafka.producer();
  await producer.connect();
  try {
    await producer.send({
      topic: "conversations.inbound",
      messages: events.map((event) => ({
        key: event.session_id,
        value: JSON.stringify({
          ...event,
          timestamp: new Date().toISOString(),
        }),
      })),
    });
  } finally {
    await producer.disconnect();
  }
}

/**
 * Subscribes to conversations.routed and waits for a message matching sessionId.
 * Returns the `result` field of the routing decision.
 */
export async function waitForRoutedEvent(
  kafka: Kafka,
  sessionId: string,
  timeoutMs: number = 5000
): Promise<{
  allocated: boolean;
  instance_id?: string;
  pool_id?: string;
  routing_mode?: string;
  priority_score?: number;
}> {
  const groupId = `e2e-wait-routed-${randomUUID()}`;
  const consumer = kafka.consumer({ groupId });
  await consumer.connect();
  await consumer.subscribe({ topic: "conversations.routed", fromBeginning: false });

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(async () => {
      await consumer.disconnect().catch(() => undefined);
      reject(new Error(`Timeout waiting for routed event for session ${sessionId}`));
    }, timeoutMs);

    consumer.run({
      eachMessage: async ({ message }) => {
        if (!message.value) return;
        try {
          const payload = JSON.parse(message.value.toString());
          if (payload.session_id === sessionId) {
            clearTimeout(timeout);
            await consumer.disconnect().catch(() => undefined);
            resolve(payload.result ?? { allocated: false });
          }
        } catch {
          // ignore parse errors
        }
      },
    }).catch((err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

/**
 * Consumes up to `count` messages from a topic, returns them as parsed objects.
 */
export async function consumeEvents(
  kafka: Kafka,
  topic: string,
  count: number,
  timeoutMs: number = 5000
): Promise<unknown[]> {
  const groupId = `e2e-consume-${randomUUID()}`;
  const consumer = kafka.consumer({ groupId });
  await consumer.connect();
  await consumer.subscribe({ topic, fromBeginning: false });

  const collected: unknown[] = [];

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(async () => {
      await consumer.disconnect().catch(() => undefined);
      resolve(collected);
    }, timeoutMs);

    consumer.run({
      eachMessage: async ({ message }) => {
        if (!message.value) return;
        try {
          collected.push(JSON.parse(message.value.toString()));
        } catch {
          collected.push(message.value.toString());
        }
        if (collected.length >= count) {
          clearTimeout(timeout);
          await consumer.disconnect().catch(() => undefined);
          resolve(collected);
        }
      },
    }).catch((err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

/**
 * Waits for multiple routed events, one per session_id in the provided list.
 * Returns a map of session_id → routing result.
 */
export async function waitForRoutedEventsBatch(
  kafka: Kafka,
  sessionIds: string[],
  timeoutMs: number = 5000
): Promise<
  Map<
    string,
    {
      allocated: boolean;
      instance_id?: string;
      pool_id?: string;
      routing_mode?: string;
      priority_score?: number;
      latency_ms: number;
      publishedAt: number;
    }
  >
> {
  const groupId = `e2e-batch-routed-${randomUUID()}`;
  const consumer = kafka.consumer({ groupId });
  await consumer.connect();
  await consumer.subscribe({ topic: "conversations.routed", fromBeginning: false });

  const results = new Map<
    string,
    {
      allocated: boolean;
      instance_id?: string;
      pool_id?: string;
      routing_mode?: string;
      priority_score?: number;
      latency_ms: number;
      publishedAt: number;
    }
  >();

  const sessionSet = new Set(sessionIds);
  const publishTimes = new Map<string, number>();
  const now = Date.now();
  for (const id of sessionIds) publishTimes.set(id, now);

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(async () => {
      await consumer.disconnect().catch(() => undefined);
      // Return partial results on timeout
      resolve(results);
    }, timeoutMs);

    consumer.run({
      eachMessage: async ({ message }) => {
        if (!message.value) return;
        try {
          const payload = JSON.parse(message.value.toString());
          const sid = payload.session_id as string;
          if (sessionSet.has(sid) && !results.has(sid)) {
            const publishedAt = publishTimes.get(sid) ?? now;
            const latency_ms = Date.now() - publishedAt;
            results.set(sid, {
              ...(payload.result ?? { allocated: false }),
              latency_ms,
              publishedAt,
            });
            if (results.size >= sessionIds.length) {
              clearTimeout(timeout);
              await consumer.disconnect().catch(() => undefined);
              resolve(results);
            }
          }
        } catch {
          // ignore
        }
      },
    }).catch((err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

export async function disconnectAll(kafka: Kafka): Promise<void> {
  // KafkaJS manages its own connections per producer/consumer instance;
  // the Kafka object itself has no persistent connections to close.
  // This is a no-op placeholder for cleanup symmetry.
}
