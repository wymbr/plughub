/**
 * redis-client.ts
 * Redis helpers for the E2E test suite.
 */

import Redis from "ioredis";

export { Redis };

export function createTestRedis(url: string = "redis://localhost:6379"): Redis {
  return new Redis(url, {
    lazyConnect: false,
    maxRetriesPerRequest: 3,
  });
}

/**
 * Deletes all keys matching `${tenantId}:*` using SCAN + DEL.
 * Also cleans session keys used by ai-gateway and rules-engine.
 */
export async function flushTestData(
  redis: Redis,
  tenantId: string
): Promise<void> {
  const patterns = [`${tenantId}:*`, `session:*`];

  for (const pattern of patterns) {
    let cursor = "0";
    do {
      const [nextCursor, keys] = await redis.scan(
        cursor,
        "MATCH",
        pattern,
        "COUNT",
        "100"
      );
      cursor = nextCursor;
      if (keys.length > 0) {
        await redis.del(...keys);
      }
    } while (cursor !== "0");
  }
}

/**
 * Reads and parses the pipeline_state for a session.
 * Key: `{tenantId}:pipeline:{sessionId}`
 */
export async function getPipelineState(
  redis: Redis,
  tenantId: string,
  sessionId: string
): Promise<unknown | null> {
  const raw = await redis.get(`${tenantId}:pipeline:${sessionId}`);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Writes a pipeline_state JSON directly to Redis.
 * Used in Scenario 3 to simulate partial execution.
 */
export async function setPipelineState(
  redis: Redis,
  tenantId: string,
  sessionId: string,
  state: unknown,
  ttlSeconds: number = 86400
): Promise<void> {
  await redis.set(
    `${tenantId}:pipeline:${sessionId}`,
    JSON.stringify(state),
    "EX",
    ttlSeconds
  );
}

/**
 * Reads the agent instance hash from Redis.
 * Key: `{tenantId}:agent:instance:{instanceId}`
 */
export async function getAgentInstanceState(
  redis: Redis,
  tenantId: string,
  instanceId: string
): Promise<Record<string, string> | null> {
  const data = await redis.hgetall(
    `${tenantId}:agent:instance:${instanceId}`
  );
  if (!data || Object.keys(data).length === 0) return null;
  return data;
}

/**
 * Returns the list of available agent instance_ids in a pool.
 * Key: `{tenantId}:pool:{poolId}:available`
 */
export async function getPoolAvailableAgents(
  redis: Redis,
  tenantId: string,
  poolId: string
): Promise<string[]> {
  return redis.smembers(`${tenantId}:pool:${poolId}:available`);
}

/**
 * Directly writes an agent instance state to Redis.
 * Used in Scenario 5 for fast test setup (bypasses MCP).
 */
export async function writeAgentInstanceDirect(
  redis: Redis,
  tenantId: string,
  instanceId: string,
  agentTypeId: string,
  pools: string[],
  maxConcurrentSessions: number = 5,
  ttlSeconds: number = 3600
): Promise<void> {
  const key = `${tenantId}:agent:instance:${instanceId}`;
  await redis.hset(key, {
    state: "ready",
    agent_type_id: agentTypeId,
    current_sessions: "0",
    max_concurrent_sessions: String(maxConcurrentSessions),
    pools: JSON.stringify(pools),
    logged_in_at: new Date().toISOString(),
  });
  await redis.expire(key, ttlSeconds);

  for (const poolId of pools) {
    await redis.sadd(`${tenantId}:pool:${poolId}:available`, instanceId);
  }
}

/**
 * Publishes a session update to the Rules Engine pub/sub channel.
 * Channel: `session:updates:{sessionId}`
 */
export async function publishSessionUpdate(
  redis: Redis,
  sessionId: string,
  tenantId: string,
  params: {
    sentiment_score: number;
    intent_confidence: number;
    turn_count?: number;
    elapsed_ms?: number;
    flags?: string[];
  }
): Promise<void> {
  const message = JSON.stringify({
    session_id: sessionId,
    tenant_id: tenantId,
    sentiment_score: params.sentiment_score,
    intent_confidence: params.intent_confidence,
    turn_count: params.turn_count ?? 1,
    elapsed_ms: params.elapsed_ms ?? 5000,
    flags: params.flags ?? [],
  });
  await redis.publish(`session:updates:${sessionId}`, message);
}

/**
 * Writes AI Gateway session state to Redis for Rules Engine evaluation.
 * Key: `session:{sessionId}:ai`
 */
export async function writeAiSessionState(
  redis: Redis,
  sessionId: string,
  sentimentScore: number,
  intentConfidence: number,
  flags: string[] = [],
  ttlSeconds: number = 3600
): Promise<void> {
  const state = {
    consolidated_turns: [
      {
        turn: 1,
        intent: "retention",
        confidence: intentConfidence,
        sentiment_score: sentimentScore,
        flags,
      },
    ],
    current_turn: {
      llm_calls: [],
      partial_parameters: {
        intent: "retention",
        confidence: intentConfidence,
        sentiment_score: sentimentScore,
      },
      detected_flags: flags,
    },
  };
  await redis.set(
    `session:${sessionId}:ai`,
    JSON.stringify(state),
    "EX",
    ttlSeconds
  );
}
