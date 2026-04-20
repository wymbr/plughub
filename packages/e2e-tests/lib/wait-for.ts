/**
 * wait-for.ts
 * Utility functions to wait until services are ready before running tests.
 */

import Redis from "ioredis";
import { Kafka } from "kafkajs";

/**
 * Polls GET {url} every second until a 200 response is received or timeout.
 */
export async function waitForService(
  url: string,
  name: string,
  timeoutMs: number = 30000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;

  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) {
        console.log(`[wait-for] ${name} is ready (${url})`);
        return;
      }
      lastError = new Error(`HTTP ${res.status}`);
    } catch (err) {
      lastError = err;
    }
    await sleep(1000);
  }

  throw new Error(
    `[wait-for] Timeout waiting for ${name} at ${url} after ${timeoutMs}ms. Last error: ${lastError}`
  );
}

/**
 * Creates a Redis client and pings it until success or timeout.
 */
export async function waitForRedis(
  url: string,
  timeoutMs: number = 30000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  const redis = new Redis(url, {
    lazyConnect: true,
    enableReadyCheck: false,
    maxRetriesPerRequest: 0,
    retryStrategy: () => null,
  });

  try {
    while (Date.now() < deadline) {
      try {
        await redis.connect();
        await redis.ping();
        console.log(`[wait-for] Redis is ready (${url})`);
        return;
      } catch {
        // disconnect if partially connected so we can retry
        try {
          redis.disconnect();
        } catch {
          // ignore
        }
        await sleep(1000);
      }
    }
    throw new Error(`[wait-for] Timeout waiting for Redis at ${url} after ${timeoutMs}ms`);
  } finally {
    try {
      redis.disconnect();
    } catch {
      // ignore
    }
  }
}

/**
 * Tries to connect a Kafka admin client and list topics until success or timeout.
 */
export async function waitForKafka(
  brokers: string[],
  timeoutMs: number = 30000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const kafka = new Kafka({
      clientId: "e2e-wait-probe",
      brokers,
      connectionTimeout: 3000,
      requestTimeout: 3000,
      retry: { retries: 0 },
    });
    const admin = kafka.admin();
    try {
      await admin.connect();
      await admin.listTopics();
      await admin.disconnect();
      console.log(`[wait-for] Kafka is ready (${brokers.join(",")})`);
      return;
    } catch {
      try {
        await admin.disconnect();
      } catch {
        // ignore
      }
      await sleep(1000);
    }
  }

  throw new Error(
    `[wait-for] Timeout waiting for Kafka at ${brokers.join(",")} after ${timeoutMs}ms`
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
