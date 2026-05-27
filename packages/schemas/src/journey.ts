/**
 * journey.ts
 * Arc 10 — Journey: Multi-Session Service Automation
 * Arc 17 — JourneyType: Platform-level business process definitions
 *
 * Journey is the business-level unit that transcends the session.
 * It groups all contacts (session_ids) involved in resolving a single
 * service process and enables end-to-end KPIs.
 *
 * journey_id ≠ workflow_instance_id — distinct entities with independent lifecycles.
 *
 * Arc 17 adds JourneyType — the platform-level definition that must exist before
 * any Journey instance can be created. journey_type_id is a slug (e.g. "portabilidade_telco")
 * unique per tenant. Pools declare which types they are authorized to create.
 */
import { z } from "zod"

// ── Journey Type (Arc 17) ─────────────────────────────────────────────────────

/**
 * JourneyType is the platform-level definition of a business process type.
 * Configured in Config/Resources by platform admins (per tenant).
 *
 * journey_type_id follows the same naming convention as pool_id and agent_type_id:
 * snake_case slug, e.g. "portabilidade_telco", "cancelamento_retention".
 *
 * Governance rule: Journey instances may only be created with a journey_type_id
 * that is registered AND declared in the originating pool's authorized_journey_types[].
 */
export const JourneyTypeSchema = z.object({
  journey_type_id: z.string().regex(/^[a-z0-9_]+$/).describe(
    "Unique slug per tenant identifying this business process type (e.g. 'portabilidade_telco')"
  ),
  tenant_id:   z.string(),
  sla_ms:      z.number().int().positive().describe(
    "Expected total completion time in ms. Journeys exceeding this threshold are reported as above-SLA."
  ),
  description: z.string().optional(),
  created_at:  z.string().datetime(),
  updated_at:  z.string().datetime(),
})
export type JourneyType = z.infer<typeof JourneyTypeSchema>

/** Input schema for POST /v1/journey-types */
export const CreateJourneyTypeSchema = JourneyTypeSchema.omit({
  tenant_id:  true,
  created_at: true,
  updated_at: true,
})
export type CreateJourneyType = z.infer<typeof CreateJourneyTypeSchema>

/** Input schema for PATCH /v1/journey-types/:id */
export const UpdateJourneyTypeSchema = CreateJourneyTypeSchema.partial().omit({
  journey_type_id: true,
})
export type UpdateJourneyType = z.infer<typeof UpdateJourneyTypeSchema>

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
  /**
   * Arc 17: references the registered JourneyType definition.
   * Nullable for migration safety — existing journeys pre-Arc 17 have null.
   */
  journey_type_id:         z.string().nullable(),
  /**
   * Arc 17: sla_ms denormalized from JourneyType at creation time.
   * Stored on the instance to enable SLA calculation without joining JourneyType at read time.
   */
  sla_ms:                  z.number().int().positive().nullable(),
  workflow_instance_id:    z.string().uuid().nullable(),
  customer_id:             z.string().nullable(),
  origin_session_id:       z.string(),
  status:                  JourneyStatusSchema,
  /**
   * Arc 16: pool that owns this journey.
   * Stable across skill version upgrades — resolves internally to active skill_ids.
   * Used by journey_list_suspended to filter by accessible_pools[].
   */
  pool_id:                 z.string().nullable(),
  /**
   * Arc 16: machine-readable reason for terminal failures.
   * Distinguishes collect_timeout vs workflow_error without expanding the status enum.
   */
  failure_reason:          z.string().nullable(),
  merged_into_journey_id:  z.string().uuid().nullable(),
  /** Set when this journey was derived from a split — points to the source journey */
  split_from_journey_id:   z.string().uuid().nullable(),
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
  "journey_split",            // sessions extracted from a journey into a new one
])
export type JourneyEventType = z.infer<typeof JourneyEventTypeSchema>

// ── Kafka event schema ────────────────────────────────────────────────────────

export const JourneyEventSchema = z.object({
  event_type:              JourneyEventTypeSchema,
  timestamp:               z.string().datetime(),
  tenant_id:               z.string(),
  journey_id:              z.string().uuid(),
  /** Arc 17: propagated from Journey instance for analytics filtering */
  journey_type_id:         z.string().optional(),
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
  /**
   * Populated for journey_split events.
   * source_journey_id: the journey sessions were extracted from.
   * new_journey_id: the newly created journey.
   * session_ids: the sessions that were moved.
   * session_count: convenience count of session_ids.
   */
  source_journey_id:       z.string().uuid().optional(),
  new_journey_id:          z.string().uuid().optional(),
  session_ids:             z.array(z.string()).optional(),
  session_count:           z.number().int().optional(),
  /** Set on journey_started when journey was created via split */
  split_from_journey_id:   z.string().uuid().optional(),
  metadata:                z.record(z.unknown()).optional(),
})
export type JourneyEvent = z.infer<typeof JourneyEventSchema>

// ── MCP tool input schemas ────────────────────────────────────────────────────

export const JourneyStartInputSchema = z.object({
  /**
   * Arc 17: registered journey type. Must exist in the platform AND be listed in
   * the originating pool's authorized_journey_types[]. Required — no ad-hoc creation.
   */
  journey_type_id: z.string().describe("Registered journey type (e.g. 'portabilidade_telco')"),
  skill_id:        z.string().describe("Skill-flow that governs this service process"),
  session_id:      z.string().describe("Current session — becomes origin_session_id"),
  metadata:        z.record(z.unknown()).optional().describe("Additional context passed to the workflow"),
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

export const JourneySplitInputSchema = z.object({
  journey_id:  z.string().uuid().describe("Source journey to extract sessions from"),
  session_ids: z.array(z.string()).min(1).describe(
    "Collect session IDs to move to the new journey. Must not include the source journey's origin_session_id.",
  ),
  skill_id:    z.string().optional().describe(
    "If provided, triggers a new workflow for the new journey immediately after split",
  ),
  metadata:    z.record(z.unknown()).optional().describe("Additional context for the new journey"),
})
export type JourneySplitInput = z.infer<typeof JourneySplitInputSchema>

export const JourneySplitOutputSchema = z.object({
  new_journey_id:           z.string().uuid(),
  new_workflow_instance_id: z.string().uuid().nullable(),
})
export type JourneySplitOutput = z.infer<typeof JourneySplitOutputSchema>

// ── Arc 16 MCP tool input/output schemas ─────────────────────────────────────

/**
 * journey_list_suspended — lists journeys in suspended status for a given pool.
 * pool_id is stable across skill version upgrades; resolves internally to active skill_ids.
 * Used by Tier 1 poller workflows and pool agents implementing inbound resume (Fase E).
 */
export const JourneyListSuspendedInputSchema = z.object({
  pool_id:     z.string().describe("Pool that owns the journeys to list"),
  customer_id: z.string().optional().describe("Filter to journeys for a specific customer"),
  limit:       z.number().int().positive().max(100).default(20).describe("Max results to return"),
})
export type JourneyListSuspendedInput = z.infer<typeof JourneyListSuspendedInputSchema>

export const JourneyListSuspendedOutputSchema = z.object({
  journeys: z.array(z.object({
    journey_id:    z.string().uuid(),
    skill_id:      z.string(),
    customer_id:   z.string().nullable(),
    suspended_at:  z.string().datetime().nullable(),
    resume_token:  z.string().nullable(),
    metadata:      z.record(z.unknown()).nullable(),
  })),
})
export type JourneyListSuspendedOutput = z.infer<typeof JourneyListSuspendedOutputSchema>

/**
 * journey_resume — resumes a suspended journey from the MCP layer.
 * Wraps the workflow-api /resume endpoint, hiding the resume_token from callers.
 */
export const JourneyResumeInputSchema = z.object({
  journey_id: z.string().uuid().describe("Journey to resume"),
  decision:   z.enum(["approved", "rejected", "input"]).describe("Resume decision"),
  payload:    z.record(z.unknown()).default({}).describe("Data provided by the resuming party"),
  session_id: z.string().optional().describe(
    "Current session to link to the journey on resume (for inbound Fase E pattern)",
  ),
})
export type JourneyResumeInput = z.infer<typeof JourneyResumeInputSchema>

export const JourneyResumeOutputSchema = z.object({
  journey_id:           z.string().uuid(),
  workflow_instance_id: z.string().uuid(),
  resumed:              z.boolean(),
})
export type JourneyResumeOutput = z.infer<typeof JourneyResumeOutputSchema>

/**
 * journey_check_pending — checks whether a customer has suspended journeys
 * compatible with the current channel. Called by pool agents on inbound contacts
 * to implement Arc 16 Fase E (inbound journey resume).
 * Only pools that opt in pay the processing cost (Option A — agent-explicit).
 */
export const JourneyCheckPendingInputSchema = z.object({
  customer_id: z.string().describe("Customer to check for pending journeys"),
  pool_id:     z.string().describe("Pool scope — only journeys owned by this pool are returned"),
  channel:     z.string().optional().describe(
    "Current contact channel — filters journeys by channel capability if provided",
  ),
})
export type JourneyCheckPendingInput = z.infer<typeof JourneyCheckPendingInputSchema>

export const JourneyCheckPendingOutputSchema = z.object({
  has_pending:  z.boolean(),
  journeys:     z.array(z.object({
    journey_id:   z.string().uuid(),
    skill_id:     z.string(),
    suspended_at: z.string().datetime().nullable(),
    metadata:     z.record(z.unknown()).nullable(),
  })),
})
export type JourneyCheckPendingOutput = z.infer<typeof JourneyCheckPendingOutputSchema>
