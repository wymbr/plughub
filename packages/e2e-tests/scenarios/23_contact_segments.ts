/**
 * 23_contact_segments.ts
 * Scenario 23: ARC 5 — ContactSegment analytics pipeline
 *
 * Validates the segment-level analytics backbone introduced in Arc 5:
 *   conversations.participants → analytics-api consumer
 *                             → ClickHouse segments table (ReplacingMergeTree)
 *                             → GET /reports/segments
 *
 * Part A — Primary agent segment lifecycle (4 assertions):
 *   Publish participant_joined (segment_id, sequence_index=0) +
 *   participant_left (same segment_id, duration_ms, outcome=resolved)
 *   to conversations.participants →
 *   analytics-api consumer writes two segment rows (joined then left) →
 *   ReplacingMergeTree FINAL selects the "left" row (ended_at + outcome populated) →
 *   GET /reports/segments returns the row with correct segment_id,
 *   sequence_index=0, duration_ms, and outcome.
 *
 * Part B — Conference specialist topology (4 assertions):
 *   Publish participant_joined for a primary agent (segment_id=A, parent_segment_id=null)
 *   and a specialist (segment_id=B, parent_segment_id=A) to the same session →
 *   GET /reports/segments returns both rows →
 *   specialist row has parent_segment_id == primary segment_id →
 *   primary row has sequence_index=0, specialist row has parent_segment_id populated.
 *
 * Part C — Sequential handoff (sequence_index ordering) (3 assertions):
 *   Publish two primary participant_left events for the same session with
 *   sequence_index=0 and sequence_index=1 (representing a first agent + transfer) →
 *   GET /reports/segments returns both rows →
 *   rows are distinguishable by sequence_index.
 *
 * Modules exercised:
 *   Kafka (conversations.participants producer)
 *   analytics-api (consumer → ClickHouse segments, GET /reports/segments)
 *   ClickHouse ReplacingMergeTree FINAL deduplication
 *
 * Flags: --segments
 * Timeout: 60s (Kafka consumer lag + ClickHouse FINAL merge)
 *
 * Assertions: 11
 */

import { randomUUID } from "crypto"
import type { ScenarioContext, ScenarioResult, Assertion } from "./types"
import type { Kafka } from "kafkajs"
import { pass, fail } from "../lib/report"
import { genSessionId } from "../lib/redis-client"

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Build a participant_joined payload. */
function buildJoined(opts: {
  sessionId:       string
  tenantId:        string
  participantId:   string
  poolId:          string
  agentTypeId:     string
  role:            string
  segmentId:       string
  sequenceIndex:   number
  parentSegmentId: string | null
  conferenceId:    string | null
  joinedAt:        Date
}): Record<string, unknown> {
  return {
    type:              "participant_joined",
    event_id:          randomUUID(),
    segment_id:        opts.segmentId,
    sequence_index:    opts.sequenceIndex,
    parent_segment_id: opts.parentSegmentId,
    session_id:        opts.sessionId,
    tenant_id:         opts.tenantId,
    participant_id:    opts.participantId,
    pool_id:           opts.poolId,
    agent_type_id:     opts.agentTypeId,
    role:              opts.role,
    agent_type:        "ai",
    conference_id:     opts.conferenceId,
    joined_at:         opts.joinedAt.toISOString(),
    duration_ms:       null,
    timestamp:         opts.joinedAt.toISOString(),
  }
}

/** Build a participant_left payload. */
function buildLeft(opts: {
  sessionId:       string
  tenantId:        string
  participantId:   string
  poolId:          string
  agentTypeId:     string
  role:            string
  segmentId:       string
  sequenceIndex:   number
  parentSegmentId: string | null
  conferenceId:    string | null
  joinedAt:        Date
  leftAt:          Date
  durationMs:      number
  outcome:         string
}): Record<string, unknown> {
  return {
    type:              "participant_left",
    event_id:          randomUUID(),
    segment_id:        opts.segmentId,
    sequence_index:    opts.sequenceIndex,
    parent_segment_id: opts.parentSegmentId,
    session_id:        opts.sessionId,
    tenant_id:         opts.tenantId,
    participant_id:    opts.participantId,
    pool_id:           opts.poolId,
    agent_type_id:     opts.agentTypeId,
    role:              opts.role,
    agent_type:        "ai",
    conference_id:     opts.conferenceId,
    joined_at:         opts.joinedAt.toISOString(),
    left_at:           opts.leftAt.toISOString(),
    duration_ms:       opts.durationMs,
    outcome:           opts.outcome,
    timestamp:         opts.leftAt.toISOString(),
  }
}

/**
 * Publish Kafka messages to conversations.participants and poll
 * GET /reports/segments until at least one row appears for the given session,
 * or the deadline expires.
 */
async function publishAndPoll(
  kafka: Kafka,
  analyticsUrl: string,
  tenantId: string,
  sessionId: string,
  messages: Record<string, unknown>[],
  timeoutMs = 30000
): Promise<{ publishOk: boolean; status: number | null; rows: Array<Record<string, unknown>> }> {
  const producer = kafka.producer()
  let publishOk = false

  try {
    await producer.connect()
    await producer.send({
      topic:    "conversations.participants",
      messages: messages.map((m) => ({
        key:   sessionId,
        value: JSON.stringify(m),
      })),
    })
    publishOk = true
  } finally {
    await producer.disconnect().catch(() => undefined)
  }

  const pollUrl = `${analyticsUrl}/reports/segments?tenant_id=${encodeURIComponent(tenantId)}&session_id=${encodeURIComponent(sessionId)}`
  let lastStatus: number | null = null
  let lastRows: Array<Record<string, unknown>> = []
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    try {
      const resp = await fetch(pollUrl)
      lastStatus = resp.status
      if (resp.ok) {
        const body = await resp.json() as Record<string, unknown>
        const rows = (body["data"] as Array<Record<string, unknown>>) ?? []
        if (rows.length > 0) {
          lastRows = rows
          break
        }
      }
    } catch { /* analytics-api may not be running in minimal CI environments */ }
    await new Promise((r) => setTimeout(r, 2000))
  }

  return { publishOk, status: lastStatus, rows: lastRows }
}

// ─────────────────────────────────────────────────────────────────────────────
// Part A — Primary agent segment lifecycle
// ─────────────────────────────────────────────────────────────────────────────

async function runPartA(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const label       = "A"
  const sessionId   = genSessionId()
  const segmentId   = randomUUID()
  const participantId = `e2e-agent-seg-${randomUUID().slice(0, 8)}`
  const poolId      = `e2e_seg_pool_${randomUUID().slice(0, 8)}`
  const tenantId    = ctx.tenantId
  const joinedAt    = new Date()
  const leftAt      = new Date(joinedAt.getTime() + 60000)   // 60 s
  const durationMs  = 60000

  const messages = [
    buildJoined({
      sessionId, tenantId, participantId, poolId,
      agentTypeId:     "agente_e2e_seg_v1",
      role:            "primary",
      segmentId,
      sequenceIndex:   0,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt,
    }),
    buildLeft({
      sessionId, tenantId, participantId, poolId,
      agentTypeId:     "agente_e2e_seg_v1",
      role:            "primary",
      segmentId,
      sequenceIndex:   0,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt,
      leftAt,
      durationMs,
      outcome:         "resolved",
    }),
  ]

  const { publishOk, status, rows } = await publishAndPoll(
    ctx.kafka, ctx.analyticsApiUrl, tenantId, sessionId, messages
  )

  // A1: events published successfully
  assertions.push(
    publishOk
      ? pass(`${label}1: participant events with segment_id published to conversations.participants`, {
          session_id:  sessionId,
          segment_id:  segmentId,
        })
      : fail(`${label}1: participant events with segment_id published to conversations.participants`, {
          reason: "Kafka producer error",
        })
  )

  // A2: endpoint returns 200
  assertions.push(
    status === 200
      ? pass(`${label}2: GET /reports/segments returns 200`, { status })
      : fail(`${label}2: GET /reports/segments returns 200`, {
          status,
          reason: status === null
            ? "analytics-api unreachable — may not be running in this environment"
            : `unexpected status ${status}`,
        })
  )

  const row = rows[0]

  // A3: segment row found with matching segment_id and sequence_index=0
  assertions.push(
    row && row["segment_id"] === segmentId && Number(row["sequence_index"]) === 0
      ? pass(`${label}3: segment row found with correct segment_id and sequence_index=0`, {
          segment_id:     row["segment_id"],
          sequence_index: row["sequence_index"],
        })
      : fail(`${label}3: segment row found with correct segment_id and sequence_index=0`, {
          reason:         rows.length === 0
            ? "no rows returned within 30s — analytics consumer or ClickHouse may not be running"
            : `row found but segment_id=${row?.["segment_id"]} sequence_index=${row?.["sequence_index"]}`,
          expected_segment_id: segmentId,
        })
  )

  // A4: duration_ms and outcome populated by ReplacingMergeTree FINAL (participant_left wins)
  const actualDuration = row?.["duration_ms"]
  const actualOutcome  = row?.["outcome"]
  assertions.push(
    typeof actualDuration === "number" && actualDuration === durationMs && actualOutcome === "resolved"
      ? pass(`${label}4: duration_ms and outcome populated via ReplacingMergeTree FINAL`, {
          duration_ms: actualDuration,
          outcome:     actualOutcome,
        })
      : fail(`${label}4: duration_ms and outcome populated via ReplacingMergeTree FINAL`, {
          expected_duration: durationMs,
          actual_duration:   actualDuration,
          expected_outcome:  "resolved",
          actual_outcome:    actualOutcome,
          reason: row
            ? "participant_left row not yet merged — background merge may be pending"
            : "no segment row found",
        })
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Part B — Conference specialist topology
// ─────────────────────────────────────────────────────────────────────────────

async function runPartB(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const label          = "B"
  const sessionId      = genSessionId()
  const primarySegId   = randomUUID()
  const specialistSegId = randomUUID()
  const primaryId      = `e2e-primary-${randomUUID().slice(0, 8)}`
  const specialistId   = `e2e-spec-${randomUUID().slice(0, 8)}`
  const poolId         = `e2e_conf_pool_${randomUUID().slice(0, 8)}`
  const tenantId       = ctx.tenantId
  const conferenceId   = randomUUID()
  const joinedAt       = new Date()
  const leftAt         = new Date(joinedAt.getTime() + 30000)

  const messages = [
    // Primary joins
    buildJoined({
      sessionId, tenantId,
      participantId:   primaryId,
      poolId,
      agentTypeId:     "agente_e2e_primary_v1",
      role:            "primary",
      segmentId:       primarySegId,
      sequenceIndex:   0,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt,
    }),
    // Specialist joins with parent_segment_id pointing to primary
    buildJoined({
      sessionId, tenantId,
      participantId:   specialistId,
      poolId,
      agentTypeId:     "agente_e2e_spec_v1",
      role:            "specialist",
      segmentId:       specialistSegId,
      sequenceIndex:   0,
      parentSegmentId: primarySegId,
      conferenceId,
      joinedAt:        new Date(joinedAt.getTime() + 5000),
    }),
    // Specialist leaves
    buildLeft({
      sessionId, tenantId,
      participantId:   specialistId,
      poolId,
      agentTypeId:     "agente_e2e_spec_v1",
      role:            "specialist",
      segmentId:       specialistSegId,
      sequenceIndex:   0,
      parentSegmentId: primarySegId,
      conferenceId,
      joinedAt:        new Date(joinedAt.getTime() + 5000),
      leftAt,
      durationMs:      25000,
      outcome:         "resolved",
    }),
  ]

  const { publishOk, status, rows } = await publishAndPoll(
    ctx.kafka, ctx.analyticsApiUrl, tenantId, sessionId, messages
  )

  // B1: events published
  assertions.push(
    publishOk
      ? pass(`${label}1: primary + specialist events published to conversations.participants`, {
          session_id:       sessionId,
          primary_seg_id:   primarySegId,
          specialist_seg_id: specialistSegId,
        })
      : fail(`${label}1: primary + specialist events published to conversations.participants`, {
          reason: "Kafka producer error",
        })
  )

  // B2: at least 2 rows returned (primary + specialist)
  assertions.push(
    rows.length >= 2
      ? pass(`${label}2: GET /reports/segments returns both primary and specialist rows`, {
          row_count: rows.length,
        })
      : fail(`${label}2: GET /reports/segments returns both primary and specialist rows`, {
          row_count: rows.length,
          status,
          reason: rows.length === 0
            ? "no rows returned — analytics-api may not be running"
            : "only one row returned; specialist row may not have arrived yet",
        })
  )

  const specialistRow = rows.find((r) => r["segment_id"] === specialistSegId)
  const primaryRow    = rows.find((r) => r["segment_id"] === primarySegId)

  // B3: specialist row has parent_segment_id matching the primary segment_id
  assertions.push(
    specialistRow && specialistRow["parent_segment_id"] === primarySegId
      ? pass(`${label}3: specialist segment has parent_segment_id pointing to primary segment`, {
          specialist_segment_id: specialistSegId,
          parent_segment_id:     specialistRow["parent_segment_id"],
          primary_segment_id:    primarySegId,
        })
      : fail(`${label}3: specialist segment has parent_segment_id pointing to primary segment`, {
          specialist_row_found: !!specialistRow,
          actual_parent:        specialistRow?.["parent_segment_id"],
          expected_parent:      primarySegId,
        })
  )

  // B4: primary row has sequence_index=0 and no parent_segment_id
  assertions.push(
    primaryRow && Number(primaryRow["sequence_index"]) === 0 &&
      (primaryRow["parent_segment_id"] === null || primaryRow["parent_segment_id"] === undefined || primaryRow["parent_segment_id"] === "")
      ? pass(`${label}4: primary segment has sequence_index=0 and no parent_segment_id`, {
          segment_id:     primarySegId,
          sequence_index: primaryRow["sequence_index"],
        })
      : fail(`${label}4: primary segment has sequence_index=0 and no parent_segment_id`, {
          primary_row_found:  !!primaryRow,
          sequence_index:     primaryRow?.["sequence_index"],
          parent_segment_id:  primaryRow?.["parent_segment_id"],
        })
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Part C — Sequential handoff (sequence_index ordering)
// ─────────────────────────────────────────────────────────────────────────────

async function runPartC(
  ctx: ScenarioContext,
  assertions: Assertion[]
): Promise<void> {
  const label       = "C"
  const sessionId   = genSessionId()
  const seg0Id      = randomUUID()
  const seg1Id      = randomUUID()
  const agent0Id    = `e2e-seq0-${randomUUID().slice(0, 8)}`
  const agent1Id    = `e2e-seq1-${randomUUID().slice(0, 8)}`
  const poolId      = `e2e_seq_pool_${randomUUID().slice(0, 8)}`
  const tenantId    = ctx.tenantId
  const t0          = new Date()
  const t1          = new Date(t0.getTime() + 90000)   // second agent joins 90s later

  const messages = [
    // First primary agent joined + left (resolved as transferred)
    buildJoined({
      sessionId, tenantId,
      participantId:   agent0Id,
      poolId,
      agentTypeId:     "agente_e2e_seq_v1",
      role:            "primary",
      segmentId:       seg0Id,
      sequenceIndex:   0,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt:        t0,
    }),
    buildLeft({
      sessionId, tenantId,
      participantId:   agent0Id,
      poolId,
      agentTypeId:     "agente_e2e_seq_v1",
      role:            "primary",
      segmentId:       seg0Id,
      sequenceIndex:   0,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt:        t0,
      leftAt:          new Date(t0.getTime() + 85000),
      durationMs:      85000,
      outcome:         "transferred",
    }),
    // Second primary agent joined + left (resolved)
    buildJoined({
      sessionId, tenantId,
      participantId:   agent1Id,
      poolId,
      agentTypeId:     "agente_e2e_seq_v1",
      role:            "primary",
      segmentId:       seg1Id,
      sequenceIndex:   1,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt:        t1,
    }),
    buildLeft({
      sessionId, tenantId,
      participantId:   agent1Id,
      poolId,
      agentTypeId:     "agente_e2e_seq_v1",
      role:            "primary",
      segmentId:       seg1Id,
      sequenceIndex:   1,
      parentSegmentId: null,
      conferenceId:    null,
      joinedAt:        t1,
      leftAt:          new Date(t1.getTime() + 60000),
      durationMs:      60000,
      outcome:         "resolved",
    }),
  ]

  const { publishOk, status, rows } = await publishAndPoll(
    ctx.kafka, ctx.analyticsApiUrl, tenantId, sessionId, messages
  )

  // C1: events published
  assertions.push(
    publishOk
      ? pass(`${label}1: sequential agent events (seq_idx 0 + 1) published to conversations.participants`, {
          session_id: sessionId,
          seg0_id:    seg0Id,
          seg1_id:    seg1Id,
        })
      : fail(`${label}1: sequential agent events (seq_idx 0 + 1) published to conversations.participants`, {
          reason: "Kafka producer error",
        })
  )

  // C2: both segment rows returned
  assertions.push(
    rows.length >= 2
      ? pass(`${label}2: GET /reports/segments returns both sequential segment rows`, {
          row_count: rows.length,
          status,
        })
      : fail(`${label}2: GET /reports/segments returns both sequential segment rows`, {
          row_count: rows.length,
          status,
          reason: rows.length === 0
            ? "no rows returned — analytics-api may not be running"
            : "only one sequential row; second may not have arrived yet",
        })
  )

  const seq0Row = rows.find((r) => r["segment_id"] === seg0Id)
  const seq1Row = rows.find((r) => r["segment_id"] === seg1Id)

  // C3: sequence_index values are 0 and 1 respectively
  assertions.push(
    seq0Row && seq1Row &&
      Number(seq0Row["sequence_index"]) === 0 &&
      Number(seq1Row["sequence_index"]) === 1
      ? pass(`${label}3: sequence_index correctly ordered (0 and 1) for sequential handoff`, {
          seg0_sequence_index: seq0Row["sequence_index"],
          seg1_sequence_index: seq1Row["sequence_index"],
          seg0_outcome:        seq0Row["outcome"],
          seg1_outcome:        seq1Row["outcome"],
        })
      : fail(`${label}3: sequence_index correctly ordered (0 and 1) for sequential handoff`, {
          seq0_found:          !!seq0Row,
          seq1_found:          !!seq1Row,
          seq0_sequence_index: seq0Row?.["sequence_index"],
          seq1_sequence_index: seq1Row?.["sequence_index"],
        })
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const assertions: Assertion[] = []

  try {
    // Part A — Primary segment lifecycle
    await runPartA(ctx, assertions)

    // Part B — Conference specialist topology
    await runPartB(ctx, assertions)

    // Part C — Sequential handoff (sequence_index ordering)
    await runPartC(ctx, assertions)
  } catch (err) {
    assertions.push(fail("scenario 23 unhandled error", { error: String(err) }))
  }

  return {
    scenario:     "23",
    description:  "Arc 5 — ContactSegment analytics pipeline",
    passed:       assertions.every((a) => a.passed),
    assertions,
    duration_ms:  0,  // filled in by runner
  }
}
