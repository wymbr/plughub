/**
 * workflow.ts
 * Workflow instance schemas for PlugHub Platform Arc 4.
 *
 * A WorkflowInstance is a Skill Flow execution that can be suspended
 * indefinitely, waiting for an external signal (approval, input, webhook, timer).
 *
 * Lifecycle:
 *   trigger → active → suspended ⇄ active → completed
 *                              ↓
 *                         timed_out / failed / cancelled
 *
 * The pipeline_state is persisted to PostgreSQL on suspend and reloaded
 * on resume — allowing workflows to survive restarts and long waits.
 * Redis is used as a hot cache for active instances only (TTL = session TTL).
 *
 * Timeout calculation uses the calendar-engine with business hours
 * when business_hours=true on the suspend step.
 */

import { z } from "zod"

// ── Suspend Step type (added to Skill Flow FlowStepSchema) ────────────────────

export const SuspendReasonSchema = z.enum([
  "approval",  // waiting for human approval (approved / rejected)
  "input",     // waiting for structured data input
  "webhook",   // waiting for an external webhook callback
  "timer",     // waiting until a specific datetime
])
export type SuspendReason = z.infer<typeof SuspendReasonSchema>

export const SuspendNotifySchema = z.object({
  visibility: z.enum(["all", "agents_only"]).default("agents_only"),
  text:       z.string(),  // supports {{resume_token}} interpolation
})
export type SuspendNotify = z.infer<typeof SuspendNotifySchema>

export const SuspendStepSchema = z.object({
  type:           z.literal("suspend"),
  id:             z.string(),
  reason:         SuspendReasonSchema,
  // Timeout duration. Interpreted as business hours when business_hours=true.
  timeout_hours:  z.number().positive().default(48),
  // Use calendar engine to calculate deadline in business hours.
  business_hours: z.boolean().default(true),
  // Calendar to use for business-hours calculation.
  // Defaults to the pool's calendar association if omitted.
  calendar_id:    z.string().uuid().optional(),
  // Optional message sent while suspended (e.g. to notify a supervisor)
  notify:         SuspendNotifySchema.optional(),
  // Required transition targets
  on_resume:      z.object({ next: z.string() }),
  on_timeout:     z.object({ next: z.string() }),
  // Optional rejection path (for approval flows)
  on_reject:      z.object({ next: z.string() }).optional(),
  // Arbitrary metadata stored on the instance
  metadata:       z.record(z.unknown()).optional(),
})
export type SuspendStep = z.infer<typeof SuspendStepSchema>

// ── Workflow Instance ─────────────────────────────────────────────────────────

export const WorkflowStatusSchema = z.enum([
  "active",     // flow is running
  "suspended",  // flow is paused, waiting for external signal
  "completed",  // flow reached a complete step successfully
  "failed",     // unrecoverable error
  "timed_out",  // resume_expires_at exceeded, on_timeout path triggered
  "cancelled",  // manually cancelled via API
])
export type WorkflowStatus = z.infer<typeof WorkflowStatusSchema>

export const WorkflowInstanceSchema = z.object({
  id:                z.string().uuid(),
  installation_id:   z.string(),
  organization_id:   z.string(),
  tenant_id:         z.string(),
  flow_id:           z.string(),
  session_id:        z.string(),
  /**
   * The real customer session that originated this workflow.
   * When present, the Skill Flow worker uses this as the ContextStore key
   * ({tenant}:ctx:{origin_session_id}) so that @ctx.* reads/writes target
   * the originating session — not the workflow instance UUID.
   * Populated from the trigger request's session_id when the workflow is
   * launched directly from a session (e.g. on escalation or collect step).
   */
  origin_session_id: z.string().nullable().default(null),
  pool_id:           z.string().nullable().default(null),
  /** Groups N instances triggered from the same campaign. */
  campaign_id:       z.string().nullable().default(null),
  status:            WorkflowStatusSchema,
  current_step:      z.string().nullable(),
  // Full Skill Flow pipeline_state — serialized to PostgreSQL on suspend
  pipeline_state:    z.record(z.unknown()),
  suspend_reason:    SuspendReasonSchema.nullable(),
  // Opaque token for external resume. UUID v4 generated on suspend.
  resume_token:      z.string().nullable(),
  resume_expires_at: z.string().datetime().nullable(),
  suspended_at:      z.string().datetime().nullable(),
  resumed_at:        z.string().datetime().nullable(),
  completed_at:      z.string().datetime().nullable(),
  created_at:        z.string().datetime(),
  // Business context — e.g. { invoice_id: "INV-001", amount: 15000 }
  metadata:          z.record(z.unknown()).default({}),
})
export type WorkflowInstance = z.infer<typeof WorkflowInstanceSchema>

// ── Workflow Trigger Request ──────────────────────────────────────────────────

export const WorkflowTriggerTypeSchema = z.enum([
  "webhook",   // external system POSTed to /workflow/v1/trigger
  "schedule",  // calendar-engine fired a scheduled trigger
  "event",     // Kafka event triggered this workflow
  "manual",    // operator triggered manually via console
])
export type WorkflowTriggerType = z.infer<typeof WorkflowTriggerTypeSchema>

export const WorkflowTriggerSchema = z.object({
  tenant_id:         z.string(),
  flow_id:           z.string(),
  pool_id:           z.string().optional(),   // pool to route the session to
  trigger_type:      WorkflowTriggerTypeSchema,
  // When triggered from an active session, pass the session_id here so
  // the worker can read @ctx.* from the originating session's ContextStore.
  session_id:        z.string().optional(),
  // Business context passed as contact_context to the Skill Flow
  context:           z.record(z.unknown()).default({}),
  metadata:          z.record(z.unknown()).default({}),
})
export type WorkflowTrigger = z.infer<typeof WorkflowTriggerSchema>

// ── Resume Request ────────────────────────────────────────────────────────────

export const WorkflowDecisionSchema = z.enum([
  "approved",  // approval granted — follows on_resume
  "rejected",  // approval denied — follows on_reject
  "input",     // structured input provided — follows on_resume with payload
  "timeout",   // system-generated timeout signal — follows on_timeout
])
export type WorkflowDecision = z.infer<typeof WorkflowDecisionSchema>

export const WorkflowResumeSchema = z.object({
  token:    z.string(),
  decision: WorkflowDecisionSchema,
  // Data provided by the resuming party (form values, approval notes, etc.)
  payload:  z.record(z.unknown()).default({}),
})
export type WorkflowResume = z.infer<typeof WorkflowResumeSchema>

// ── Kafka Events ──────────────────────────────────────────────────────────────

export const WorkflowStartedSchema = z.object({
  event_type:      z.literal("workflow.started"),
  timestamp:       z.string().datetime(),
  installation_id: z.string(),
  organization_id: z.string(),
  tenant_id:       z.string(),
  instance_id:     z.string().uuid(),
  flow_id:         z.string(),
  session_id:      z.string(),
  trigger_type:    WorkflowTriggerTypeSchema,
})
export type WorkflowStarted = z.infer<typeof WorkflowStartedSchema>

export const WorkflowSuspendedSchema = z.object({
  event_type:        z.literal("workflow.suspended"),
  timestamp:         z.string().datetime(),
  tenant_id:         z.string(),
  instance_id:       z.string().uuid(),
  flow_id:           z.string(),
  current_step:      z.string(),
  suspend_reason:    SuspendReasonSchema,
  resume_expires_at: z.string().datetime(),
  // Token is NOT included in the Kafka event — delivered via notify only
})
export type WorkflowSuspended = z.infer<typeof WorkflowSuspendedSchema>

export const WorkflowResumedSchema = z.object({
  event_type:    z.literal("workflow.resumed"),
  timestamp:     z.string().datetime(),
  tenant_id:     z.string(),
  instance_id:   z.string().uuid(),
  flow_id:       z.string(),
  decision:      WorkflowDecisionSchema,
  resumed_from:  z.string(),   // step name where it was suspended
  next_step:     z.string(),   // step name it will resume at
  wait_duration_ms: z.number(), // actual wait time from suspended_at to now
})
export type WorkflowResumed = z.infer<typeof WorkflowResumedSchema>

export const WorkflowCompletedSchema = z.object({
  event_type:   z.literal("workflow.completed"),
  timestamp:    z.string().datetime(),
  tenant_id:    z.string(),
  instance_id:  z.string().uuid(),
  flow_id:      z.string(),
  outcome:      z.string(),
  duration_ms:  z.number(),
})
export type WorkflowCompleted = z.infer<typeof WorkflowCompletedSchema>

export const WorkflowTimedOutSchema = z.object({
  event_type:   z.literal("workflow.timed_out"),
  timestamp:    z.string().datetime(),
  tenant_id:    z.string(),
  instance_id:  z.string().uuid(),
  flow_id:      z.string(),
  current_step: z.string(),
  suspended_at: z.string().datetime(),
  // next_open: when the calendar next opens (for notification scheduling)
  next_open:    z.string().datetime().nullable(),
})
export type WorkflowTimedOut = z.infer<typeof WorkflowTimedOutSchema>

export const WorkflowFailedSchema = z.object({
  event_type:   z.literal("workflow.failed"),
  timestamp:    z.string().datetime(),
  tenant_id:    z.string(),
  instance_id:  z.string().uuid(),
  flow_id:      z.string(),
  current_step: z.string().nullable(),
  error:        z.string(),
})
export type WorkflowFailed = z.infer<typeof WorkflowFailedSchema>

export const WorkflowCancelledSchema = z.object({
  event_type:    z.literal("workflow.cancelled"),
  timestamp:     z.string().datetime(),
  tenant_id:     z.string(),
  instance_id:   z.string().uuid(),
  flow_id:       z.string(),
  cancelled_by:  z.string(),  // operator user id or "system"
  reason:        z.string().optional(),
})
export type WorkflowCancelled = z.infer<typeof WorkflowCancelledSchema>

export const WorkflowEventSchema = z.discriminatedUnion("event_type", [
  WorkflowStartedSchema,
  WorkflowSuspendedSchema,
  WorkflowResumedSchema,
  WorkflowCompletedSchema,
  WorkflowTimedOutSchema,
  WorkflowFailedSchema,
  WorkflowCancelledSchema,
])
export type WorkflowEvent = z.infer<typeof WorkflowEventSchema>

// ── Collect Events (topic: collect.events) ────────────────────────────────────
// Published by workflow-api scheduler / channel-gateway when the collect cycle
// produces a status transition.  Consumed by analytics-api for time-series.

export const CollectStatusSchema = z.enum([
  "requested",   // collect_instance created, waiting for send_at
  "sent",        // outbound contact initiated via channel-gateway
  "responded",   // target replied — workflow resumed
  "timed_out",   // no response before expires_at — workflow on_timeout path
])
export type CollectStatus = z.infer<typeof CollectStatusSchema>

export const CollectRequestedSchema = z.object({
  event_type:    z.literal("collect.requested"),
  timestamp:     z.string().datetime(),
  tenant_id:     z.string(),
  instance_id:   z.string().uuid(),
  flow_id:       z.string(),
  campaign_id:   z.string().nullable().optional(),
  step_id:       z.string(),
  collect_token: z.string().uuid(),
  target_type:   z.enum(["customer", "agent", "external"]),
  target_id:     z.string(),
  channel:       z.string(),
  interaction:   z.string(),
  prompt:        z.string(),
  options:       z.array(z.object({ id: z.string(), label: z.string() })).optional(),
  fields:        z.array(z.object({ id: z.string(), label: z.string(), type: z.string() })).optional(),
  send_at:       z.string().datetime(),
  expires_at:    z.string().datetime(),
})
export type CollectRequested = z.infer<typeof CollectRequestedSchema>

export const CollectSentSchema = z.object({
  event_type:    z.literal("collect.sent"),
  timestamp:     z.string().datetime(),
  tenant_id:     z.string(),
  instance_id:   z.string().uuid(),
  collect_token: z.string().uuid(),
  channel:       z.string(),
  session_id:    z.string().optional(),
})
export type CollectSent = z.infer<typeof CollectSentSchema>

export const CollectRespondedSchema = z.object({
  event_type:    z.literal("collect.responded"),
  timestamp:     z.string().datetime(),
  tenant_id:     z.string(),
  instance_id:   z.string().uuid(),
  collect_token: z.string().uuid(),
  channel:       z.string(),
  response_data: z.record(z.unknown()),
  elapsed_ms:    z.number().nonnegative(),
})
export type CollectResponded = z.infer<typeof CollectRespondedSchema>

export const CollectTimedOutSchema = z.object({
  event_type:    z.literal("collect.timed_out"),
  timestamp:     z.string().datetime(),
  tenant_id:     z.string(),
  instance_id:   z.string().uuid(),
  collect_token: z.string().uuid(),
  channel:       z.string(),
  elapsed_ms:    z.number().nonnegative(),
})
export type CollectTimedOut = z.infer<typeof CollectTimedOutSchema>

export const CollectEventSchema = z.discriminatedUnion("event_type", [
  CollectRequestedSchema,
  CollectSentSchema,
  CollectRespondedSchema,
  CollectTimedOutSchema,
])
export type CollectEvent = z.infer<typeof CollectEventSchema>
