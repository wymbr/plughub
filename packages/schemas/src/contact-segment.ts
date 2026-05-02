/**
 * contact-segment.ts
 * ContactSegment — Arc 5 entity representing a single agent's participation window
 * within a session. Enables per-agent SLA, quality evaluation and sentiment analytics.
 *
 * ADR: docs/adr/adr-contact-segments.md
 */

import { z } from "zod"

// ─── Outcome domain ──────────────────────────────────────────────────────────

export const SegmentOutcomeSchema = z.enum([
  "resolved",
  "escalated",
  "transferred",
  "abandoned",
  "timeout",
])

export type SegmentOutcome = z.infer<typeof SegmentOutcomeSchema>

// ─── ContactSegment ───────────────────────────────────────────────────────────

/**
 * ContactSegment — one agent's contiguous participation window inside a session.
 *
 * Topology:
 *   - Sequential handoff:  parent_segment_id = null,  sequence_index = prev + 1
 *   - Conference/parallel: parent_segment_id = primary segment_id, sequence_index = N
 */
export const ContactSegmentSchema = z.object({
  segment_id:        z.string().uuid(),
  session_id:        z.string(),
  tenant_id:         z.string(),

  // ── Topology
  parent_segment_id: z.string().uuid().nullable().default(null),
  sequence_index:    z.number().int().nonnegative().default(0),

  // ── Who attended
  pool_id:           z.string(),
  agent_type_id:     z.string(),
  instance_id:       z.string(),
  participant_id:    z.string(),
  role:              z.enum(["primary", "specialist", "supervisor", "evaluator", "reviewer"]),
  agent_type:        z.enum(["ai", "human"]).default("ai"),

  // ── Timing
  started_at:        z.string(),          // ISO-8601
  ended_at:          z.string().nullable().default(null),
  duration_ms:       z.number().int().nonnegative().nullable().default(null),

  // ── Result (populated on participant.left)
  outcome:           SegmentOutcomeSchema.nullable().default(null),
  close_reason:      z.string().nullable().default(null),
  handoff_reason:    z.string().nullable().default(null),
  issue_status:      z.string().nullable().default(null),
})

export type ContactSegment = z.infer<typeof ContactSegmentSchema>

// ─── Kafka event: ConversationParticipantEvent ────────────────────────────────

/**
 * Published to Kafka topic `conversations.participants` by the orchestrator-bridge.
 * Adds `segment_id` to the existing participant lifecycle vocabulary.
 *
 * ADR § 3 — Tópico Kafka: `conversations.participants`
 */
export const ConversationParticipantEventSchema = z.object({
  event_type:       z.enum(["participant.joined", "participant.left"]),
  event_id:         z.string(),        // UUID
  session_id:       z.string(),
  tenant_id:        z.string(),
  segment_id:       z.string().uuid(),
  participant_id:   z.string(),
  participant_role: z.enum(["primary", "specialist", "supervisor", "evaluator", "reviewer"]),
  agent_type_id:    z.string().nullable().default(null),
  instance_id:      z.string().nullable().default(null),
  pool_id:          z.string().nullable().default(null),
  agent_type:       z.enum(["ai", "human"]).default("ai"),
  channel:          z.string().nullable().default(null),
  conference_id:    z.string().nullable().default(null),
  joined_at:        z.string().optional(),   // ISO-8601, present on both joined/left
  timestamp:        z.string(),              // ISO-8601

  // ── Only on participant.left
  outcome:          SegmentOutcomeSchema.nullable().optional(),
  duration_ms:      z.number().int().nonnegative().nullable().optional(),
  handoff_reason:   z.string().nullable().optional(),
  issue_status:     z.string().nullable().optional(),
  close_reason:     z.string().nullable().optional(),
})

export type ConversationParticipantEvent = z.infer<typeof ConversationParticipantEventSchema>
