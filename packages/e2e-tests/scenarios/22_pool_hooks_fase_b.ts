/**
 * 22_pool_hooks_fase_b.ts
 * Scenario 22: POOL LIFECYCLE HOOKS — FASE B + C (on_human_end + post_human)
 *
 * Validates the Fase B/C pool lifecycle hook pipeline:
 *   agent_done → on_human_end hooks → post_human hooks → _trigger_contact_close
 *
 * Part A — No hooks (immediate close path):
 *   Pool with empty on_human_end → agent_done → bridge detects no hooks →
 *   conversations.outbound session.closed published immediately →
 *   hook_pending key absent.
 *
 * Part B — Hooks dispatched (deferred close path):
 *   Pool with on_human_end: [{pool: "e2e_hook_target"}] → agent_done →
 *   bridge detects hooks → fires synthetic conversations.inbound with hook metadata →
 *   hook_pending:on_human_end key set in Redis → conversations.outbound NOT
 *   published within 2s (deferred until hook agent completes).
 *
 * Part C — Hook completion triggers contact close:
 *   Simulate hook agent completion by directly GETDEL hook_conf key + DECR
 *   hook_pending. Then publish conversations.events contact_closed reason=agent_done
 *   (the same event that _trigger_contact_close publishes). Bridge receives it and
 *   publishes conversations.outbound session.closed.
 *   Asserts: conversations.outbound session.closed arrives within timeout.
 *
 * Part D — post_human hook dispatch (Fase C):
 *   Pool with on_human_end: [{pool: hookEndPool}] AND post_human: [{pool: hookPostPool}] →
 *   agent_done → hook_pending:on_human_end set → simulate on_human_end completion
 *   (GETDEL + DECR → 0) → bridge detects post_human hooks in pool_config →
 *   fires synthetic conversations.inbound with hook_type=post_human → asserts
 *   hook_pending:post_human = 1 and inbound event with correct hook_type + pool_id.
 *
 * Part E — participation analytics pipeline (Arc 3 Fase C):
 *   Publish participant_joined + participant_left directly to conversations.participants →
 *   analytics-api consumer writes to ClickHouse participation_intervals (ReplacingMergeTree) →
 *   poll GET /reports/participation until row appears → assert duration_ms present
 *   (participant_left row selected via FINAL dedup).
 *
 * Modules exercised:
 *   mcp-server-plughub (POST /agent_done REST)
 *   orchestrator-bridge (process_contact_event, fire_pool_hooks, _trigger_contact_close,
 *                        post_human dispatch when on_human_end pending reaches 0)
 *   Redis tracking keys (hook_pending:on_human_end, hook_pending:post_human, hook_conf,
 *                        human_agent, human_agents, participant_joined_at)
 *   Kafka (conversations.inbound hook events, conversations.events, conversations.outbound,
 *          conversations.participants)
 *   analytics-api (consumer → ClickHouse participation_intervals, GET /reports/participation)
 *
 * Flags: --hooks
 * Timeout: 60s (bridge must be running and consuming Kafka)
 *
 * Assertions: 25
 */

import { randomUUID } from "crypto"
import type { ScenarioContext, ScenarioResult, Assertion } from "./types"
import type { Redis } from "ioredis"
import type { Kafka, Producer } from "kafkajs"
import { pass, fail } from "../lib/report"
import {
  genSessionId,
  writeAgentInstanceDirect,
} from "../lib/redis-client"
import { waitForInboundEvent } from "../lib/kafka-client"

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Write pool_config with optional hooks to Redis (extends writePoolConfigDirect). */
async function writePoolConfigWithHooks(
  redis: Redis,
  tenantId: string,
  poolId: string,
  channelTypes: string[],
  hooks: Record<string, Array<{ pool: string }>> = {},
  isHumanPool = true,
  ttlSeconds = 14400
): Promise<void> {
  const config = JSON.stringify({
    pool_id:       poolId,
    tenant_id:     tenantId,
    channel_types: channelTypes,
    sla_target_ms: 30000,
    routing_expression: { weights: {} },
    competency_weights: {},
    aging_factor:  0.4,
    breach_factor: 0.8,
    remote_sites:  [],
    is_human_pool: isHumanPool,
    hooks,
  })
  await redis.set(`${tenantId}:pool_config:${poolId}`, config, "EX", ttlSeconds)
  await redis.sadd(`${tenantId}:pools`, poolId)
}

/** Seeds the Redis tracking state for a human agent that is currently busy. */
async function seedHumanAgentBusy(
  redis: Redis,
  sessionId: string,
  tenantId: string,
  instanceId: string,
  poolId: string
): Promise<void> {
  // session:meta includes pool_id and instance_id for bridge hook logic
  const meta = JSON.stringify({
    tenant_id:   tenantId,
    customer_id: randomUUID(),
    channel:     "webchat",
    pool_id:     poolId,
    instance_id: instanceId,
    started_at:  new Date().toISOString(),
  })
  await redis.set(`session:${sessionId}:meta`, meta, "EX", 14400)

  // human_agent flag (fast check — deleted when last human drops)
  await redis.set(`session:${sessionId}:human_agent`, "1", "EX", 14400)

  // human_agents SET — bridge tracks individual agents
  await redis.sadd(`session:${sessionId}:human_agents`, instanceId)
  await redis.expire(`session:${sessionId}:human_agents`, 14400)

  // instance in Redis for restore
  await writeAgentInstanceDirect(redis, tenantId, instanceId, "agente_retencao_humano_v1", [poolId])
}

/** Polls Redis for a key until it exists or timeout. */
async function waitForRedisKey(
  redis: Redis,
  key: string,
  timeoutMs: number
): Promise<string | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const val = await redis.get(key)
    if (val !== null) return val
    await new Promise((r) => setTimeout(r, 150))
  }
  return null
}

/** Polls Redis until a key is ABSENT (deleted) or timeout. */
async function waitForRedisKeyAbsent(
  redis: Redis,
  key: string,
  timeoutMs: number
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const exists = await redis.exists(key)
    if (exists === 0) return true
    await new Promise((r) => setTimeout(r, 150))
  }
  return false
}

/** Waits for a matching message on a Kafka topic using a short-lived consumer. */
async function waitForKafkaEvent(
  kafka: Kafka,
  topic: string,
  predicate: (msg: Record<string, unknown>) => boolean,
  timeoutMs: number
): Promise<Record<string, unknown> | null> {
  const groupId = `e2e-hooks22-${randomUUID()}`
  const consumer = kafka.consumer({ groupId })
  await consumer.connect()
  await consumer.subscribe({ topic, fromBeginning: false })

  return new Promise<Record<string, unknown> | null>((resolve) => {
    const timer = setTimeout(async () => {
      await consumer.disconnect().catch(() => undefined)
      resolve(null)
    }, timeoutMs)

    consumer.run({
      eachMessage: async ({ message }) => {
        if (!message.value) return
        try {
          const parsed = JSON.parse(message.value.toString()) as Record<string, unknown>
          if (predicate(parsed)) {
            clearTimeout(timer)
            await consumer.disconnect().catch(() => undefined)
            resolve(parsed)
          }
        } catch { /* ignore */ }
      },
    }).catch(() => {
      clearTimeout(timer)
      resolve(null)
    })
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Part A: No hooks → immediate close
// ─────────────────────────────────────────────────────────────────────────────

async function runPartA(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const { redis, kafka } = ctx
  const sessionId   = genSessionId()
  const instanceId  = `e2e-hooks22-human-no-hooks-${randomUUID()}`
  const poolId      = `e2e_hooks22_no_hooks_${randomUUID().slice(0, 8)}`
  const tenantId    = ctx.tenantId

  // Seed pool with NO on_human_end hooks
  await writePoolConfigWithHooks(redis, tenantId, poolId, ["webchat"], {
    on_human_start: [],
    on_human_end:   [],
    post_human:     [],
  })

  await seedHumanAgentBusy(redis, sessionId, tenantId, instanceId, poolId)

  // Subscribe to conversations.outbound BEFORE triggering agent_done
  const outboundWatcher = waitForKafkaEvent(
    kafka,
    "conversations.outbound",
    (m) => m["session_id"] === sessionId && m["type"] === "session.closed",
    12000
  )

  // Trigger agent_done via REST
  const res = await fetch(`${ctx.mcpServerUrl}/agent_done/${sessionId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ outcome: "resolved" }),
  })
  assertions.push(
    res.ok
      ? pass("A1: POST /agent_done returns 200 (no hooks pool)", { status: res.status })
      : fail("A1: POST /agent_done returns 200 (no hooks pool)", { status: res.status })
  )

  // Bridge should publish conversations.outbound immediately (no hooks)
  const outboundEvent = await outboundWatcher
  assertions.push(
    outboundEvent !== null
      ? pass("A2: conversations.outbound session.closed published immediately (no hooks)", {
          session_id: outboundEvent["session_id"],
          type:       outboundEvent["type"],
        })
      : fail("A2: conversations.outbound session.closed published immediately (no hooks)", {
          reason: "timeout — event not received within 12s",
        })
  )

  // hook_pending key must be absent (no hooks dispatched)
  const hookPending = await redis.get(`session:${sessionId}:hook_pending:on_human_end`)
  assertions.push(
    hookPending === null
      ? pass("A3: hook_pending key absent (no hooks configured)", { key_value: null })
      : fail("A3: hook_pending key absent (no hooks configured)", { actual: hookPending })
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Part B: Hooks configured → deferred close
// ─────────────────────────────────────────────────────────────────────────────

async function runPartB(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<{ sessionId: string; hookPool: string } | null> {
  const { redis, kafka } = ctx
  const sessionId   = genSessionId()
  const instanceId  = `e2e-hooks22-human-with-hooks-${randomUUID()}`
  const poolId      = `e2e_hooks22_with_hooks_${randomUUID().slice(0, 8)}`
  const hookPool    = `e2e_hook_finalizacao_${randomUUID().slice(0, 8)}`
  const tenantId    = ctx.tenantId

  try {
    // Seed pool WITH on_human_end hook pointing to hookPool
    await writePoolConfigWithHooks(redis, tenantId, poolId, ["webchat"], {
      on_human_start: [],
      on_human_end:   [{ pool: hookPool }],
      post_human:     [],
    })

    await seedHumanAgentBusy(redis, sessionId, tenantId, instanceId, poolId)

    // Set up two Kafka watchers before triggering agent_done:
    // 1. conversations.inbound — should receive the hook event
    // 2. conversations.outbound — should NOT receive session.closed within 2s
    const { ready: inboundReady, result: inboundResult } = waitForInboundEvent(
      kafka,
      sessionId,
      15000
    )
    await inboundReady   // wait for consumer to be seeked before publishing

    // Subscribe to outbound to detect premature close
    let outboundReceivedEarly = false
    const outboundGroupId = `e2e-hooks22-outbound-${randomUUID()}`
    const outboundConsumer = kafka.consumer({ groupId: outboundGroupId })
    await outboundConsumer.connect()
    await outboundConsumer.subscribe({ topic: "conversations.outbound", fromBeginning: false })
    outboundConsumer.run({
      eachMessage: async ({ message }) => {
        if (!message.value) return
        try {
          const m = JSON.parse(message.value.toString())
          if (m.session_id === sessionId && m.type === "session.closed") {
            outboundReceivedEarly = true
          }
        } catch { /* ignore */ }
      },
    }).catch(() => undefined)

    // Trigger agent_done
    const res = await fetch(`${ctx.mcpServerUrl}/agent_done/${sessionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ outcome: "resolved" }),
    })
    assertions.push(
      res.ok
        ? pass("B1: POST /agent_done returns 200 (hooks pool)", { status: res.status })
        : fail("B1: POST /agent_done returns 200 (hooks pool)", { status: res.status })
    )

    // B2: hook_pending key must appear in Redis
    const hookPendingVal = await waitForRedisKey(
      redis,
      `session:${sessionId}:hook_pending:on_human_end`,
      10000
    )
    assertions.push(
      hookPendingVal !== null
        ? pass("B2: hook_pending:on_human_end key set (hooks dispatched)", {
            value: hookPendingVal,
          })
        : fail("B2: hook_pending:on_human_end key set (hooks dispatched)", {
            reason: "key not found within 10s after agent_done",
          })
    )
    const hookCount = parseInt(hookPendingVal ?? "0", 10)
    assertions.push(
      hookCount === 1
        ? pass("B3: hook_pending counter = 1 (one hook configured)", { counter: hookCount })
        : fail("B3: hook_pending counter = 1 (one hook configured)", { actual: hookCount })
    )

    // B4: conversations.inbound must contain hook event
    const inboundMsg = (await inboundResult) as Record<string, unknown> | null
    const hasConferenceId = typeof inboundMsg?.["conference_id"] === "string" && inboundMsg["conference_id"] !== ""
    const hookTypeMatches = inboundMsg?.["hook_type"] === "on_human_end"
    const targetPoolMatches = inboundMsg?.["pool_id"] === hookPool

    assertions.push(
      inboundMsg !== null
        ? pass("B4: hook event published to conversations.inbound", {
            hook_type:     inboundMsg["hook_type"],
            pool_id:       inboundMsg["pool_id"],
            origin_pool:   inboundMsg["origin_pool"],
            conference_id: inboundMsg["conference_id"],
          })
        : fail("B4: hook event published to conversations.inbound", {
            reason: "no matching inbound event received within 15s",
          })
    )
    assertions.push(
      hasConferenceId
        ? pass("B5: hook event has conference_id (routing as conference specialist)", {
            conference_id: inboundMsg?.["conference_id"],
          })
        : fail("B5: hook event has conference_id (routing as conference specialist)", {
            actual: inboundMsg?.["conference_id"],
          })
    )
    assertions.push(
      hookTypeMatches
        ? pass("B6: hook event hook_type = on_human_end", { hook_type: "on_human_end" })
        : fail("B6: hook event hook_type = on_human_end", { actual: inboundMsg?.["hook_type"] })
    )
    assertions.push(
      targetPoolMatches
        ? pass("B7: hook event pool_id = hook target pool", { pool_id: hookPool })
        : fail("B7: hook event pool_id = hook target pool", {
            expected: hookPool,
            actual:   inboundMsg?.["pool_id"],
          })
    )

    // B8: hook_conf key must exist for the conference_id
    const confId = inboundMsg?.["conference_id"] as string | undefined
    if (confId) {
      const hookConfVal = await waitForRedisKey(
        redis,
        `session:${sessionId}:hook_conf:${confId}`,
        5000
      )
      assertions.push(
        hookConfVal !== null
          ? pass("B8: hook_conf:{conference_id} key set (tracking hook completion)", {
              value: hookConfVal,
            })
          : fail("B8: hook_conf:{conference_id} key set (tracking hook completion)", {
              reason: "key not found",
            })
      )
    } else {
      assertions.push(fail("B8: hook_conf:{conference_id} key set", { reason: "no conference_id to check" }))
    }

    // B9: conversations.outbound session.closed must NOT have arrived within 2s
    await new Promise((r) => setTimeout(r, 2000))
    assertions.push(
      !outboundReceivedEarly
        ? pass("B9: conversations.outbound session.closed NOT published immediately (deferred)", {
            closed_early: false,
          })
        : fail("B9: conversations.outbound session.closed NOT published immediately (deferred)", {
            closed_early: true,
            reason: "bridge closed customer WS before hooks completed — Fase B broken",
          })
    )

    await outboundConsumer.disconnect().catch(() => undefined)
    return { sessionId, hookPool }

  } catch (err) {
    throw err
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Part C: Simulate hook completion → contact close fires
// ─────────────────────────────────────────────────────────────────────────────

async function runPartC(
  ctx: ScenarioContext,
  partBResult: { sessionId: string; hookPool: string },
  assertions: Assertion[]
): Promise<void> {
  const { redis, kafka } = ctx
  const { sessionId } = partBResult

  {
    // Find the hook_conf key (there should be exactly one for this session)
    let cursor = "0"
    let confKey = ""
    let confId  = ""
    do {
      const [nextCursor, keys] = await redis.scan(
        cursor, "MATCH", `session:${sessionId}:hook_conf:*`, "COUNT", "50"
      )
      cursor = nextCursor
      if (keys.length > 0) {
        confKey = keys[0]
        confId  = confKey.split(":").pop() ?? ""
        break
      }
    } while (cursor !== "0")

    assertions.push(
      confKey !== ""
        ? pass("C1: hook_conf key found for simulation", { key: confKey, conference_id: confId })
        : fail("C1: hook_conf key found for simulation", {
            reason: "no hook_conf key found — Part B may have not dispatched hooks",
          })
    )

    if (!confKey) {
      return
    }

    // Set up outbound watcher before simulating completion
    const outboundWatcher = waitForKafkaEvent(
      kafka,
      "conversations.outbound",
      (m) => m["session_id"] === sessionId && m["type"] === "session.closed",
      20000
    )

    // Simulate hook agent completion:
    // 1. GETDEL hook_conf → this would be done by process_routed
    await redis.getdel(confKey)

    // 2. DECR hook_pending → hits 0
    const remaining = await redis.decr(`session:${sessionId}:hook_pending:on_human_end`)
    assertions.push(
      remaining === 0
        ? pass("C2: hook_pending counter decremented to 0 (all hooks completed)", { remaining })
        : fail("C2: hook_pending counter decremented to 0 (all hooks completed)", { remaining })
    )

    // 3. Bridge's _trigger_contact_close publishes conversations.events contact_closed.
    //    Simulate it by publishing that event directly — same as what process_routed does.
    const meta = JSON.parse((await redis.get(`session:${sessionId}:meta`)) ?? "{}")
    const producer: Producer = kafka.producer()
    await producer.connect()
    await producer.send({
      topic:    "conversations.events",
      messages: [{
        key:   sessionId,
        value: JSON.stringify({
          event_type: "contact_closed",
          session_id: sessionId,
          tenant_id:  meta.tenant_id ?? ctx.tenantId,
          reason:     "agent_done",
        }),
      }],
    })
    await producer.disconnect()

    // C3: conversations.outbound session.closed must arrive after bridge processes contact_closed
    const outboundEvent = await outboundWatcher
    assertions.push(
      outboundEvent !== null
        ? pass("C3: conversations.outbound session.closed published after hook completion", {
            session_id: outboundEvent["session_id"],
            reason:     outboundEvent["reason"],
          })
        : fail("C3: conversations.outbound session.closed published after hook completion", {
            reason: "timeout — bridge did not publish close after contact_closed event",
          })
    )

    // C4: human_agent tracking keys must be cleaned up
    const humanAgentKey   = await redis.get(`session:${sessionId}:human_agent`)
    const humanAgentsCard = await redis.scard(`session:${sessionId}:human_agents`)
    assertions.push(
      humanAgentKey === null && humanAgentsCard === 0
        ? pass("C4: human_agent tracking keys cleaned up after close", {
            human_agent:  null,
            human_agents: 0,
          })
        : fail("C4: human_agent tracking keys cleaned up after close", {
            human_agent:  humanAgentKey,
            human_agents: humanAgentsCard,
          })
    )

  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Part D: post_human hook dispatch (Fase C)
// ─────────────────────────────────────────────────────────────────────────────

async function runPartD(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const { redis, kafka } = ctx
  const sessionId    = genSessionId()
  const instanceId   = `e2e-hooks22-phooks-${randomUUID()}`
  const poolId       = `e2e_hooks22_phooks_${randomUUID().slice(0, 8)}`
  const hookEndPool  = `e2e_hook_end_${randomUUID().slice(0, 8)}`   // on_human_end target
  const hookPostPool = `e2e_hook_post_${randomUUID().slice(0, 8)}`  // post_human target
  const tenantId     = ctx.tenantId

  // Seed pool with BOTH on_human_end AND post_human hooks
  await writePoolConfigWithHooks(redis, tenantId, poolId, ["webchat"], {
    on_human_start: [],
    on_human_end:   [{ pool: hookEndPool }],
    post_human:     [{ pool: hookPostPool }],
  })

  await seedHumanAgentBusy(redis, sessionId, tenantId, instanceId, poolId)

  // Subscribe to conversations.inbound for post_human event BEFORE triggering anything.
  // Predicate filters by hook_type so on_human_end events are ignored.
  const postHumanInboundWatcher = waitForKafkaEvent(
    kafka,
    "conversations.inbound",
    (m) => m["session_id"] === sessionId && m["hook_type"] === "post_human",
    25000
  )

  // Trigger agent_done to start the hook chain
  await fetch(`${ctx.mcpServerUrl}/agent_done/${sessionId}`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ outcome: "resolved" }),
  }).catch(() => undefined)  // bridge must be running; ignore network errors here

  // D1: hook_pending:on_human_end must appear — confirms on_human_end chain started
  const hookPendingEnd = await waitForRedisKey(
    redis,
    `session:${sessionId}:hook_pending:on_human_end`,
    12000
  )
  assertions.push(
    hookPendingEnd !== null
      ? pass("D1: hook_pending:on_human_end set after agent_done (post_human pool)", {
          value: hookPendingEnd,
        })
      : fail("D1: hook_pending:on_human_end set after agent_done (post_human pool)", {
          reason: "key not found within 12s — bridge may not be running or pool not seeded correctly",
        })
  )

  if (hookPendingEnd === null) {
    // Can't continue without the on_human_end hook being active
    for (const label of ["D2", "D3", "D4", "D5"]) {
      assertions.push(fail(`${label}: post_human assertion`, { reason: "skipped — D1 failed" }))
    }
    return
  }

  // Find the on_human_end hook_conf key so we can simulate completion
  let confKey = ""
  let confId  = ""
  const scanDeadline = Date.now() + 8000
  while (Date.now() < scanDeadline && confKey === "") {
    let cursor = "0"
    do {
      const [nextCursor, keys] = await redis.scan(
        cursor, "MATCH", `session:${sessionId}:hook_conf:*`, "COUNT", "50"
      )
      cursor = nextCursor
      if (keys.length > 0) {
        confKey = keys[0]
        confId  = confKey.split(":").pop() ?? ""
        break
      }
    } while (cursor !== "0")
    if (!confKey) await new Promise((r) => setTimeout(r, 200))
  }

  if (!confKey) {
    for (const label of ["D2", "D3", "D4", "D5"]) {
      assertions.push(fail(`${label}: post_human assertion`, {
        reason: "skipped — hook_conf key not found after D1 passed",
      }))
    }
    return
  }

  // Simulate on_human_end hook agent completion (mirrors process_routed GETDEL + DECR)
  await redis.getdel(confKey)
  await redis.decr(`session:${sessionId}:hook_pending:on_human_end`)
  // → counter hits 0 → bridge reads pool_config → fires post_human hooks

  // D2-D4: Wait for the post_human inbound event
  const postHumanEvent = await postHumanInboundWatcher

  assertions.push(
    postHumanEvent !== null
      ? pass("D2: conversations.inbound with post_human hook event published", {
          hook_type: postHumanEvent["hook_type"],
          pool_id:   postHumanEvent["pool_id"],
        })
      : fail("D2: conversations.inbound with post_human hook event published", {
          reason: "no post_human inbound event received within 25s after simulating on_human_end completion",
        })
  )
  assertions.push(
    postHumanEvent?.["hook_type"] === "post_human"
      ? pass("D3: post_human hook event has hook_type = post_human", { hook_type: "post_human" })
      : fail("D3: post_human hook event has hook_type = post_human", {
          actual: postHumanEvent?.["hook_type"],
        })
  )
  assertions.push(
    postHumanEvent?.["pool_id"] === hookPostPool
      ? pass("D4: post_human hook event targets the post_human pool", { pool_id: hookPostPool })
      : fail("D4: post_human hook event targets the post_human pool", {
          expected: hookPostPool,
          actual:   postHumanEvent?.["pool_id"],
        })
  )

  // D5: hook_pending:post_human = 1 in Redis (one hook enqueued)
  const hookPendingPost = await waitForRedisKey(
    redis,
    `session:${sessionId}:hook_pending:post_human`,
    10000
  )
  const pendingCount = parseInt(hookPendingPost ?? "0", 10)
  assertions.push(
    hookPendingPost !== null && pendingCount === 1
      ? pass("D5: hook_pending:post_human = 1 (Fase C hook chain active)", { value: pendingCount })
      : fail("D5: hook_pending:post_human = 1", {
          actual: hookPendingPost,
          reason: hookPendingPost === null ? "key not found" : `expected 1, got ${pendingCount}`,
        })
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Part E: participation analytics pipeline (conversations.participants → ClickHouse)
// ─────────────────────────────────────────────────────────────────────────────

async function runPartE(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const { kafka } = ctx
  const sessionId    = genSessionId()
  const participantId = `e2e-agent-part-${randomUUID()}`
  const poolId       = `e2e_part_pool_${randomUUID().slice(0, 8)}`
  const tenantId     = ctx.tenantId

  const joinedAt    = new Date()
  const leftAt      = new Date(joinedAt.getTime() + 45000)   // 45 s later
  const durationMs  = 45000

  // E1: Publish participant_joined + participant_left to conversations.participants
  const producer = kafka.producer()
  let publishOk = false
  try {
    await producer.connect()
    await producer.send({
      topic:    "conversations.participants",
      messages: [
        {
          key:   sessionId,
          value: JSON.stringify({
            type:           "participant_joined",
            event_id:       randomUUID(),
            session_id:     sessionId,
            tenant_id:      tenantId,
            participant_id: participantId,
            pool_id:        poolId,
            agent_type_id:  "agente_e2e_v1",
            role:           "primary",
            agent_type:     "ai",
            conference_id:  null,
            joined_at:      joinedAt.toISOString(),
            duration_ms:    null,
            timestamp:      joinedAt.toISOString(),
          }),
        },
        {
          key:   sessionId,
          value: JSON.stringify({
            type:           "participant_left",
            event_id:       randomUUID(),
            session_id:     sessionId,
            tenant_id:      tenantId,
            participant_id: participantId,
            pool_id:        poolId,
            agent_type_id:  "agente_e2e_v1",
            role:           "primary",
            agent_type:     "ai",
            conference_id:  null,
            joined_at:      joinedAt.toISOString(),
            left_at:        leftAt.toISOString(),
            duration_ms:    durationMs,
            timestamp:      leftAt.toISOString(),
          }),
        },
      ],
    })
    publishOk = true
  } catch (err) {
    assertions.push(fail("E1: participant events published to conversations.participants", {
      error: String(err),
    }))
    return
  } finally {
    await producer.disconnect().catch(() => undefined)
  }

  assertions.push(
    publishOk
      ? pass("E1: participant events published to conversations.participants", {
          session_id:     sessionId,
          participant_id: participantId,
          duration_ms:    durationMs,
        })
      : fail("E1: participant events published to conversations.participants", {})
  )

  // E2-E4: Poll GET /reports/participation until the row appears (max 30s).
  // analytics-api consumer batch-commits; allow ~30s for Kafka lag + ClickHouse insert.
  const analyticsUrl = ctx.analyticsApiUrl
  const pollUrl      = `${analyticsUrl}/reports/participation?tenant_id=${encodeURIComponent(tenantId)}&session_id=${encodeURIComponent(sessionId)}`

  let lastStatus: number | null = null
  let lastBody: Record<string, unknown> | null = null
  const deadline = Date.now() + 30000

  while (Date.now() < deadline) {
    try {
      const resp = await fetch(pollUrl)
      lastStatus = resp.status
      if (resp.ok) {
        const body = await resp.json() as Record<string, unknown>
        lastBody = body
        const rows = (body["data"] as unknown[]) ?? []
        if (rows.length > 0) break
      }
    } catch { /* analytics-api may not be running in minimal CI environments */ }
    await new Promise((r) => setTimeout(r, 2000))
  }

  assertions.push(
    lastStatus === 200
      ? pass("E2: GET /reports/participation returns 200", { status: 200 })
      : fail("E2: GET /reports/participation returns 200", {
          status:  lastStatus,
          reason:  lastStatus === null
            ? "analytics-api unreachable — may not be running in this environment"
            : `unexpected status ${lastStatus}`,
        })
  )

  const rows = (lastBody?.["data"] as Array<Record<string, unknown>>) ?? []
  const row  = rows[0]

  assertions.push(
    rows.length > 0
      ? pass("E3: participation row found in ClickHouse for session", {
          participant_id: row?.["participant_id"],
          session_id:     row?.["session_id"],
        })
      : fail("E3: participation row found in ClickHouse for session", {
          reason: "no rows returned within 30s — analytics consumer or ClickHouse may not be running",
        })
  )

  // E4: duration_ms must be present (participant_left row selected via ReplacingMergeTree FINAL)
  const actualDuration = row?.["duration_ms"]
  assertions.push(
    typeof actualDuration === "number" && actualDuration === durationMs
      ? pass("E4: duration_ms present in participation row (participant_left persisted via FINAL)", {
          duration_ms: actualDuration,
        })
      : fail("E4: duration_ms present in participation row (participant_left persisted via FINAL)", {
          expected: durationMs,
          actual:   actualDuration,
          reason:   row
            ? "participant_left row not yet merged — ReplacingMergeTree background merge may be pending"
            : "no row found",
        })
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const assertions: Assertion[] = []
  const startAt = Date.now()

  try {
    // Part A — no hooks (immediate close)
    await runPartA(ctx, assertions)

    // Part B — hooks dispatched (deferred close)
    const partBResult = await runPartB(ctx, assertions)

    // Part C — hook completion triggers close
    if (partBResult) {
      await runPartC(ctx, partBResult, assertions)
    } else {
      // Part B failed — skip Part C with explicit markers
      assertions.push(fail("C1: hook_conf key found for simulation", { reason: "skipped — Part B failed" }))
      assertions.push(fail("C2: hook_pending counter decremented to 0", { reason: "skipped" }))
      assertions.push(fail("C3: conversations.outbound published after hook completion", { reason: "skipped" }))
      assertions.push(fail("C4: human_agent tracking keys cleaned up", { reason: "skipped" }))
    }

    // Part D — post_human hook dispatch (Fase C)
    await runPartD(ctx, assertions)

    // Part E — participation analytics pipeline
    await runPartE(ctx, assertions)

  } catch (err) {
    assertions.push(fail("scenario 22 unhandled error", { error: String(err) }))
  }

  return {
    scenario_id:  "22",
    name:         "Pool Lifecycle Hooks — Fase B + C (on_human_end + post_human)",
    passed:       assertions.every((a) => a.passed),
    assertions,
    duration_ms:  Date.now() - startAt,
  }
}
