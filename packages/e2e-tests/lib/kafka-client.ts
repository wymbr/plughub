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
 * Payload matches ConversationInboundEvent (routing-engine Pydantic model):
 *   required: session_id, tenant_id, customer_id, channel, started_at
 *   optional: pool_id, intent, confidence, customer_profile
 */
export async function publishInboundEventsBatch(
  kafka: Kafka,
  events: Array<{
    session_id: string;
    tenant_id: string;
    channel: string;
    customer_id: string;
    pool_id?: string;
    intent?: string;
    confidence?: number;
    customer_profile?: Record<string, unknown>;
  }>
): Promise<void> {
  const producer = kafka.producer();
  await producer.connect();
  const now = new Date().toISOString();
  try {
    await producer.send({
      topic: "conversations.inbound",
      messages: events.map((event) => ({
        key: event.session_id,
        value: JSON.stringify({
          session_id:       event.session_id,
          tenant_id:        event.tenant_id,
          customer_id:      event.customer_id,
          channel:          event.channel,
          started_at:       now,
          pool_id:          event.pool_id ?? null,
          intent:           event.intent ?? null,
          confidence:       event.confidence ?? 0.0,
          customer_profile: event.customer_profile ?? {},
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
 * Two-phase inbound event waiter using Admin-offset snapshot to eliminate the
 * race condition between GROUP_JOIN and the consumer's first ListOffsets call.
 *
 * Flow:
 *   1. Admin fetches current end-offset of conversations.inbound (snapshot).
 *   2. Consumer connects + subscribes (fromBeginning: false).
 *   3. On GROUP_JOIN: explicitly seek() every assigned partition to the snapshot
 *      offset.  Any message published AFTER the snapshot will be at an offset
 *      >= snapshot → guaranteed to be received.
 *   4. Resolve `ready` — caller is now safe to publish.
 *
 * Returns `{ ready, result }`:
 *   - `ready`  — resolves after seek() + GROUP_JOIN (or 7 s fallback).
 *   - `result` — resolves with the first matching event, or null on timeout.
 */
export function waitForInboundEvent(
  kafka: Kafka,
  sessionId: string,
  timeoutMs: number = 15000,
  contentType?: string
): { ready: Promise<void>; result: Promise<unknown | null> } {
  const TOPIC   = "conversations.inbound";
  const tag     = `[inbound:${sessionId.slice(0, 8)}]`;
  const groupId = `e2e-inbound-${randomUUID()}`;
  const consumer = kafka.consumer({ groupId });
  const admin    = kafka.admin();

  let resolveReady!: () => void;
  const ready = new Promise<void>((res) => { resolveReady = res; });

  // Hard outer fallback — fires regardless of what happens inside the IIFE.
  const readyTimer = setTimeout(() => {
    console.log(`${tag} ready-fallback fired (7s)`);
    resolveReady();
  }, 7000);

  const result = new Promise<unknown | null>((resolve) => {
    const cleanup = () => {
      consumer.disconnect().catch(() => undefined);
      admin.disconnect().catch(() => undefined);
    };

    // Timeout: NOT async — resolve immediately, disconnect in background.
    const timeout = setTimeout(() => {
      console.log(`${tag} result-timeout (${timeoutMs}ms) — resolving null`);
      clearTimeout(readyTimer);
      resolveReady();
      cleanup();
      resolve(null);
    }, timeoutMs);

    (async () => {
      try {
        // Step 1: snapshot end-offsets before subscribing
        console.log(`${tag} fetching topic offsets…`);
        await admin.connect();
        const topicOffsets = await admin.fetchTopicOffsets(TOPIC);
        await admin.disconnect().catch(() => undefined);
        console.log(`${tag} offsets: ${JSON.stringify(topicOffsets)}`);

        // Step 2: connect consumer + subscribe
        console.log(`${tag} connecting consumer…`);
        await consumer.connect();
        await consumer.subscribe({ topic: TOPIC, fromBeginning: false });
        console.log(`${tag} subscribed — waiting for GROUP_JOIN`);

        // Step 3: on GROUP_JOIN seek every assigned partition to snapshot offset
        consumer.on(consumer.events.GROUP_JOIN, (event: any) => {
          const assigned: number[] =
            event?.payload?.memberAssignment?.[TOPIC] ??
            topicOffsets.map((o: { partition: number }) => o.partition);

          for (const partition of assigned) {
            const info = topicOffsets.find(
              (o: { partition: number }) => o.partition === partition
            );
            const offset = info?.offset ?? "0";
            console.log(`${tag} seek partition=${partition} → offset=${offset}`);
            consumer.seek({ topic: TOPIC, partition, offset });
          }
          clearTimeout(readyTimer);
          resolveReady();
          console.log(`${tag} ready — consumer seeked to snapshot offsets`);
        });

        consumer.run({
          eachMessage: async ({ message }) => {
            if (!message.value) return;
            try {
              const parsed = JSON.parse(message.value.toString()) as Record<string, unknown>;
              if (parsed["session_id"] !== sessionId) return;
              if (contentType) {
                const content = parsed["content"] as Record<string, unknown> | undefined;
                if (content?.["type"] !== contentType) return;
              }
              console.log(`${tag} event matched (type=${contentType ?? "any"}) — resolving`);
              clearTimeout(timeout);
              clearTimeout(readyTimer);
              resolveReady();
              cleanup();
              resolve(parsed);
            } catch { /* ignore parse errors */ }
          },
        }).catch((err) => {
          console.log(`${tag} consumer.run() error: ${err}`);
          clearTimeout(readyTimer);
          resolveReady();
          clearTimeout(timeout);
          cleanup();
          resolve(null);
        });

      } catch (err) {
        console.log(`${tag} setup error: ${err}`);
        clearTimeout(readyTimer);
        resolveReady();
        clearTimeout(timeout);
        cleanup();
        resolve(null);
      }
    })();
  });

  return { ready, result };
}

/**
 * Consumes up to `count` messages from a topic, returns them as parsed objects.
 * Uses a fixed delay before returning (legacy).
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
type RoutedBatchResult = Map<
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
>;

/**
 * Two-phase batch routed-event waiter using Admin-offset snapshot (same pattern
 * as waitForInboundEvent) to eliminate the offset-race with fromBeginning: false.
 *
 * Returns `{ ready, result }`:
 *   - `ready`  — resolves after seek() + GROUP_JOIN (or 7 s fallback).
 *   - `result` — resolves with Map<session_id → routing result> (partial on timeout).
 *                latency_ms = absolute receive timestamp; scenario computes delta.
 */
export function waitForRoutedEventsBatch(
  kafka: Kafka,
  sessionIds: string[],
  timeoutMs: number = 20000
): { ready: Promise<void>; result: Promise<RoutedBatchResult> } {
  const TOPIC   = "conversations.routed";
  const tag     = `[routed-batch:${sessionIds.length}]`;
  const groupId = `e2e-batch-routed-${randomUUID()}`;
  const consumer = kafka.consumer({ groupId });
  const admin    = kafka.admin();

  let resolveReady!: () => void;
  const ready = new Promise<void>((res) => { resolveReady = res; });

  const readyTimer = setTimeout(() => {
    console.log(`${tag} ready-fallback fired (7s)`);
    resolveReady();
  }, 7000);

  const results: RoutedBatchResult = new Map();
  const sessionSet = new Set(sessionIds);

  const result = new Promise<RoutedBatchResult>((resolve, reject) => {
    const cleanup = () => {
      consumer.disconnect().catch(() => undefined);
      admin.disconnect().catch(() => undefined);
    };

    const timeout = setTimeout(() => {
      console.log(`${tag} result-timeout (${timeoutMs}ms) — ${results.size}/${sessionIds.length} results`);
      clearTimeout(readyTimer);
      resolveReady();
      cleanup();
      resolve(results);
    }, timeoutMs);

    (async () => {
      try {
        console.log(`${tag} fetching topic offsets…`);
        await admin.connect();
        const topicOffsets = await admin.fetchTopicOffsets(TOPIC);
        await admin.disconnect().catch(() => undefined);
        console.log(`${tag} offsets: ${JSON.stringify(topicOffsets)}`);

        await consumer.connect();
        await consumer.subscribe({ topic: TOPIC, fromBeginning: false });
        console.log(`${tag} subscribed — waiting for GROUP_JOIN`);

        consumer.on(consumer.events.GROUP_JOIN, (event: any) => {
          const assigned: number[] =
            event?.payload?.memberAssignment?.[TOPIC] ??
            topicOffsets.map((o: { partition: number }) => o.partition);

          for (const partition of assigned) {
            const info = topicOffsets.find(
              (o: { partition: number }) => o.partition === partition
            );
            const offset = info?.offset ?? "0";
            console.log(`${tag} seek partition=${partition} → offset=${offset}`);
            consumer.seek({ topic: TOPIC, partition, offset });
          }
          clearTimeout(readyTimer);
          resolveReady();
          console.log(`${tag} ready — seeked to snapshot offsets`);
        });

        consumer.run({
          eachMessage: async ({ message }) => {
            if (!message.value) return;
            try {
              const payload = JSON.parse(message.value.toString());
              const sid = payload.session_id as string;
              if (sessionSet.has(sid) && !results.has(sid)) {
                const latency_ms = Date.now();
                results.set(sid, {
                  ...(payload.result ?? { allocated: false }),
                  latency_ms,
                  publishedAt: latency_ms,
                });
                console.log(`${tag} routed: ${results.size}/${sessionIds.length}`);
                if (results.size >= sessionIds.length) {
                  clearTimeout(timeout);
                  clearTimeout(readyTimer);
                  resolveReady();
                  cleanup();
                  resolve(results);
                }
              }
            } catch { /* ignore parse errors */ }
          },
        }).catch((err) => {
          console.log(`${tag} consumer.run() error: ${err}`);
          clearTimeout(readyTimer);
          resolveReady();
          clearTimeout(timeout);
          cleanup();
          reject(err);
        });

      } catch (err) {
        console.log(`${tag} setup error: ${err}`);
        clearTimeout(readyTimer);
        resolveReady();
        clearTimeout(timeout);
        cleanup();
        reject(err);
      }
    })();
  });

  return { ready, result };
}

export async function disconnectAll(kafka: Kafka): Promise<void> {
  // KafkaJS manages its own connections per producer/consumer instance;
  // the Kafka object itself has no persistent connections to close.
  // This is a no-op placeholder for cleanup symmetry.
}
