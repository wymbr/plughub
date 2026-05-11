/**
 * journey.ts
 * Arc 10 — Journey: Multi-Session Service Automation
 *
 * Journey is the business-level unit that transcends the session.
 * It groups all contacts (session_ids) involved in resolving a single
 * service process and enables end-to-end KPIs.
 *
 * journey_id ≠ workflow_instance_id — distinct entities with independent lifecycles.
 */
import { z } from "zod"

// ── Status ────────────────────────────────────────────────────────────────────

export const JourneyStatusSchema = z.enum([
  "active",
  "suspended",
  "completed",
  "failed",
  "cancelled",
  "merged",       // absorbed by another journey via journey_merge
])
export type JourneyStatus = z.infer<typeof JourneyStatusSchema>

// ── Entity ────────────────────────────────────────────────────────────────────

export const JourneySchema = z.object({
  journey_id:              z.string().uuid(),
  tenant_id:               z.string(),
  skill_id:                z.string(),
  workflow_instance_id:    z.string().uuid().nullable(),
  customer_id:             z.string().nullable(),
  origin_session_id:       z.string(),
  status:                  JourneyStatusSchema,
  merged_into_journey_id:  z.string().uuid().nullable(),
  metadata:                z.record(z.unknown()).nullable(),
  created_at:              z.string().datetime(),
  updated_at:              z.string().datetime(),
  completed_at:            z.string().datetime().nullable(),
})
export type Journey = z.infer<typeof JourneySchema>

// ── Kafka event types ─────────────────────────────────────────────────────────

export const JourneyEventTypeSchema = z.enum([
  "journey_started",          // journey created, origin_session_id defined
  "journey_session_linked",   // additional session associated to the journey
  "journey_suspended",        // workflow suspended (waiting for input/timer)
  "journey_resumed",          // workflow resumed
  "journey_completed",        // process concluded successfully
  "journey_failed",           // process failed
  "journey_cancelled",        // cancelled by agent or timeout
  "journey_merged",           // secondary journey absorbed by primary
  "journey_split",            // new journey created from extracted sessions (future)
])
export type JourneyEventType = z.infer<typeof JourneyEventTypeSchema>

// ── Kafka event schema ────────────────────────────────────────────────────────

export const JourneyEventSchema = z.object({
  event_type:              JourneyEventTypeSchema,
  timestamp:               z.string().datetime(),
  tenant_id:               z.string(),
  journey_id:              z.string().uuid(),
  skill_id:                z.string(),
  customer_id:             z.string().nullable().optional(),
  origin_session_id:       z.string().optional(),
  /** Populated for journey_session_linked events */
  session_id:              z.string().optional(),
  /**
   * Populated for journey_session_linked — workflow step at the moment of linking.
   * Allows reconstructing the workflow progression across contacts.
   */
  current_step:            z.string().optional(),
  /**
   * Populated for journey_session_linked — outcome of this specific session
   * within the journey (resolved / escalated / abandoned / …).
   */
  session_outcome:         z.string().optional(),
  /** ISO datetime when the linked session was opened. */
  session_started_at:      z.string().datetime().optional(),
  /** ISO datetime when the linked session was closed. */
  session_ended_at:        z.string().datetime().optional(),
  /** Current or new workflow_instance_id */
  workflow_instance_id:    z.string().uuid().nullable().optional(),
  /** Populated for journey_merged — the primary journey that absorbed this one */
  merged_into_journey_id:  z.string().uuid().optional(),
  metadata:                z.record(z.unknown()).optional(),
})
export type JourneyEvent = z.infer<typeof JourneyEventSchema>

// ── MCP tool input schemas ────────────────────────────────────────────────────

export const JourneyStartInputSchema = z.object({
  skill_id:   z.string().describe("Skill-flow that governs this service process"),
  session_id: z.string().describe("Current session — becomes origin_session_id"),
  metadata:   z.record(z.unknown()).optional().describe("Additional context passed to the workflow"),
})
export type JourneyStartInput = z.infer<typeof JourneyStartInputSchema>

export const JourneyStartOutputSchema = z.object({
  journey_id:           z.string().uuid(),
  workflow_instance_id: z.string().uuid(),
})
export type JourneyStartOutput = z.infer<typeof JourneyStartOutputSchema>

export const JourneyLinkSessionInputSchema = z.object({
  journey_id: z.string().uuid().describe("Journey to associate the session with"),
  session_id: z.string().describe("Session to link to the journey"),
})
export type JourneyLinkSessionInput = z.infer<typeof JourneyLinkSessionInputSchema>

export const JourneyMergeInputSchema = z.object({
  journey_id_primary:   z.string().uuid().describe("Primary journey — absorbs the secondary"),
  journey_id_secondary: z.string().uuid().describe("Secondary journey — becomes merged"),
})
export type JourneyMergeInput = z.infer<typeof JourneyMergeInputSchema>
