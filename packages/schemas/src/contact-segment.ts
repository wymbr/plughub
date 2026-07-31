/**
 * contact-segment.ts
 * ContactSegment — Arc 5 entity representing a single agent's participation window
 * within a session. Enables per-agent SLA, quality evaluation and sentiment analytics.
 *
 * ADR: docs/adr/adr-contact-segments.md
 */

import { z } from "zod"

// ─── Outcome domain ──────────────────────────────────────────────────────────

// Fase A (queue-attended-model): closed domain for the segment ledger —
// the segment is the single source of truth for outcome. Includes both
// agent-declared outcomes (resolved/escalated_*/transferred/callback/failed/
// suspended) and platform-detected ones (abandoned/timeout/outage). The agent
// contract (OutcomeSchema in context-package.ts) is intentionally narrower:
// agents cannot declare platform-detected outcomes.
export const SegmentOutcomeSchema = z.enum([
  "resolved",
  "escalated",          // legacy generic escalation
  "escalated_human",
  "escalated_ai",
  "transferred",
  "callback",
  "failed",
  "suspended",          // Arc 19 webhook sessions
  "abandoned",          // platform-detected: customer left
  "timeout",            // platform-detected: max wait / session TTL
  "outage",             // platform-detected: no contracted resource (synthetic segment)
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
  // Fase C (queue-attended-model): `queue` = queue-treatment agent segment.
  // pool_id stays = target pool (the reporting dimension); queue segments are
  // excluded from agent metrics by construction (they are not primary/specialist).
  // Analytic invariant: "atendido" = first `primary` segment of the session.
  role:              z.enum(["primary", "specialist", "supervisor", "evaluator", "reviewer", "queue"]),
  agent_type:        z.enum(["ai", "human"]).default("ai"),
  // Identity beyond the (deprecated) synthetic agent_type_id:
  //   flow_id — AI: the deployed skill the agent ran ("" for humans)
  //   user_id — human: the login user_id ("" for AI)
  flow_id:           z.string().default(""),
  // deploy_version — AI: versão do skill (deploy) que rodou no segmento, resolvida no
  // início (do corpo do skill); "" para humanos. Insumo de cota por versão (ADR amostragem),
  // do núcleo epoch (Arc 6 Fase 2) e do condicionamento por canal no backfill.
  deploy_version:    z.string().default(""),
  channel:           z.string().default(""),   // canal da sessão, carimbado no segmento
  user_id:           z.string().default(""),
  user_login:        z.string().default(""),   // human: login/email for display

  // ── Timing
  started_at:        z.string(),          // ISO-8601
  ended_at:          z.string().nullable().default(null),
  duration_ms:       z.number().int().nonnegative().nullable().default(null),

  // ── Result (populated on participant.left)
  outcome:           SegmentOutcomeSchema.nullable().default(null),
  close_reason:      z.string().nullable().default(null),
  handoff_reason:    z.string().nullable().default(null),
  issue_status:      z.string().nullable().default(null),
  // F7: normalized escalation reason (id from config escalation_reasons). Set when
  // outcome is an escalate-family value. handoff_reason stays as the free-text note.
  escalation_reason: z.string().nullable().default(null),
  // Wrap-up prose — ALWAYS recorded, including when the contact was resolved.
  // Separate columns rather than handoff_reason: that field defines `handoff_rate`
  // (`countIf(handoff_reason != '') / count()`), so writing the summary there would
  // silently push the handoff rate to ~100%. Same precedent as escalation_reason,
  // which was split out of the same free-text note once it had its own meaning.
  wrapup_summary:    z.string().nullable().default(null),
  wrapup_next_steps: z.string().nullable().default(null),
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
  participant_role: z.enum(["primary", "specialist", "supervisor", "evaluator", "reviewer", "queue"]),
  agent_type_id:    z.string().nullable().default(null),
  instance_id:      z.string().nullable().default(null),
  pool_id:          z.string().nullable().default(null),
  agent_type:       z.enum(["ai", "human"]).default("ai"),
  // C1 identity: flow_id (AI, deployed skill) / user_id (human, login). Both optional.
  flow_id:          z.string().nullable().optional(),
  deploy_version:   z.string().nullable().optional(),   // AI: versão do skill (deploy) que rodou
  user_id:          z.string().nullable().optional(),
  user_login:       z.string().nullable().optional(),
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
  escalation_reason: z.string().nullable().optional(),   // F7
  wrapup_summary:    z.string().nullable().optional(),   // prosa do wrap-up, sempre gravada
  wrapup_next_steps: z.string().nullable().optional(),
})

export type ConversationParticipantEvent = z.infer<typeof ConversationParticipantEventSchema>
