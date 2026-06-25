/**
 * ingestion-event.ts
 * `ingestion_event_v1` — the OPEN event interface of the `quality-ingest` module
 * (anti-corruption boundary). An external importer (third-party CCaaS) or the
 * internal history exporter emits this event family; the module is the sole
 * translator into the internal canonical events (conversations.events /
 * conversations.participants / agent.lifecycle). Neither side touches the
 * internal event infra (Kafka topics / internal schemas) or the stores.
 *
 * Design decisions (closed — see docs/arcos/quality-ingest.md):
 *   - Interface is a STREAM of events correlated by `external_contact_id`
 *     (→ deterministic session_id), not a batch document. The module translates
 *     per event; the "batch" is invisible to it.
 *   - The POOL is the unit: events stamp `pool_id` (never campaign_id), so
 *     ingestion is decoupled from evaluation. Any campaign targeting the pool
 *     samples its contacts — internal or imported — with no special path.
 *   - `contact.closed` is the finalization trigger (sampling + delta_ms recompute
 *     in the Y consumer). A contact without `closed` never finalizes.
 *   - Idempotency: a stable `event_id` per event (sender-provided OR derived by
 *     the module from external_contact_id + index) → re-send does not duplicate.
 *   - Anti-corruption: this contract uses the MODULE's own vocabulary (small
 *     enums + free strings for channel/outcome/pool_id/masked_categories). It is
 *     intentionally NOT coupled to internal enums (ChannelSchema, SegmentOutcome,
 *     DataCategory); the source map (R13c) and masking net pass (R13a-2) reconcile.
 *   - tenant_id is NOT carried in the event body — it is resolved from the ingest
 *     request context (X-Tenant-ID header) by the module (R13a-2).
 *
 * Scope is grade-transcript: tier-1 qualitative + transcript-derivable
 * session_metric.*. Tier-2 (tool correctness, faithfulness-vs-tool,
 * policy-by-trajectory) is unavailable without mcp.audit/pipeline_state — always
 * the case for external data. `tool_trace` is accepted but only meaningful for
 * the internal exporter.
 */

import { z } from "zod"

/** Version tag of this event family. */
export const INGESTION_EVENT_SCHEMA_VERSION = "ingestion_event_v1" as const

// ─────────────────────────────────────────────
// Module vocabulary (anti-corruption — decoupled from internal enums)
// ─────────────────────────────────────────────

/** Whether the participant that joined is an AI agent or a human. */
export const IngestionAgentKindSchema = z.enum(["ai", "human"])
export type IngestionAgentKind = z.infer<typeof IngestionAgentKindSchema>

/** Minimal transcript author vocabulary. */
export const IngestionAuthorRoleSchema = z.enum(["customer", "agent", "system"])
export type IngestionAuthorRole = z.infer<typeof IngestionAuthorRoleSchema>

/** Message content type — mirrors the internal MessageContentType vocabulary. */
export const IngestionContentTypeSchema = z.enum([
  "text",
  "image",
  "audio",
  "video",
  "file",
  "location",
  "template",
])
export type IngestionContentType = z.infer<typeof IngestionContentTypeSchema>

/** Message visibility — only the two transcript-relevant modalities. */
export const IngestionVisibilitySchema = z.enum(["all", "agents_only"])
export type IngestionVisibility = z.infer<typeof IngestionVisibilitySchema>

/** Segment role — the standard participant roles (no synthetic `queue`). */
export const IngestionSegmentRoleSchema = z.enum([
  "primary",
  "specialist",
  "supervisor",
  "evaluator",
  "reviewer",
])
export type IngestionSegmentRole = z.infer<typeof IngestionSegmentRoleSchema>

// ─────────────────────────────────────────────
// ingestion_event_v1 — the five external event types
// ─────────────────────────────────────────────

/**
 * contact.opened → conversations.events `contact_open`.
 * Establishes `source` and `channel` for the external_contact_id; later events
 * may omit `source` (the module remembers it).
 */
export const IngestionContactOpenedSchema = z.object({
  event_type:         z.literal("contact.opened"),
  /** Stable idempotency key; module derives one when absent. */
  event_id:           z.string().min(1).optional(),
  /** Correlation key across the whole contact → deterministic session_id. */
  external_contact_id: z.string().min(1),
  /** Origin system, e.g. "ccaas:genesys" — drives the source identity/pool map. */
  source:             z.string().min(1),
  /** External channel name (module vocabulary; mapped downstream). */
  channel:            z.string().min(1),
  opened_at:          z.string().datetime(),

  // ── Optional (contato)
  medium:             z.string().optional(),
  customer_ref:       z.string().optional(),
})
export type IngestionContactOpened = z.infer<typeof IngestionContactOpenedSchema>

/**
 * participant.joined → conversations.participants `participant_joined`.
 * `segment_ref` is the sender's per-segment correlation handle.
 * `skill_id`/`deploy_version` are AI-only (enable analytics / per-version quota).
 */
export const IngestionParticipantJoinedSchema = z.object({
  event_type:         z.literal("participant.joined"),
  event_id:           z.string().min(1).optional(),
  external_contact_id: z.string().min(1),
  source:             z.string().min(1).optional(),
  segment_ref:        z.string().min(1),
  external_agent_id:  z.string().min(1),
  agent_kind:         IngestionAgentKindSchema,
  pool_id:            z.string().min(1),
  started_at:         z.string().datetime(),

  // ── Optional (segment)
  role:               IngestionSegmentRoleSchema.default("primary"),
  skill_id:           z.string().optional(),       // AI only
  deploy_version:     z.string().optional(),       // AI only
})
export type IngestionParticipantJoined = z.infer<typeof IngestionParticipantJoinedSchema>

/**
 * message.sent → conversations.events `message_sent`.
 * `content` MUST already be masked (`masked=true` + `masked_categories`); the
 * module also runs a masking net pass on ingest (§5). `original_content=null`.
 */
export const IngestionMessageSentSchema = z.object({
  event_type:         z.literal("message.sent"),
  event_id:           z.string().min(1).optional(),
  external_contact_id: z.string().min(1),
  source:             z.string().min(1).optional(),
  ts:                 z.string().datetime(),
  author_role:        IngestionAuthorRoleSchema,
  content:            z.string(),
  masked:             z.boolean(),

  // ── Optional (msg)
  author_id:          z.string().optional(),
  segment_ref:        z.string().optional(),
  content_type:       IngestionContentTypeSchema.default("text"),
  visibility:         IngestionVisibilitySchema.default("all"),
  masked_categories:  z.array(z.string()).default([]),
})
export type IngestionMessageSent = z.infer<typeof IngestionMessageSentSchema>

/**
 * participant.left → agent.lifecycle `agent_done` + participant_left.
 * `tool_trace` is accepted but only meaningful for the internal exporter
 * (tier-2 unavailable for external data).
 */
export const IngestionParticipantLeftSchema = z.object({
  event_type:         z.literal("participant.left"),
  event_id:           z.string().min(1).optional(),
  external_contact_id: z.string().min(1),
  source:             z.string().min(1).optional(),
  segment_ref:        z.string().min(1),
  ended_at:           z.string().datetime(),

  // ── Optional
  outcome:            z.string().optional(),
  /** Execution evidence — tier-2, internal only. Opaque to the contract. */
  tool_trace:         z.array(z.unknown()).optional(),
  /** Pre-computed segment-scoped session_metric.* values. */
  precomputed_metrics: z.record(z.number()).optional(),
})
export type IngestionParticipantLeft = z.infer<typeof IngestionParticipantLeftSchema>

/**
 * contact.closed → conversations.events `contact_closed` → DISPATCHES sampling.
 * The single finalization trigger for the contact.
 */
export const IngestionContactClosedSchema = z.object({
  event_type:         z.literal("contact.closed"),
  event_id:           z.string().min(1).optional(),
  external_contact_id: z.string().min(1),
  source:             z.string().min(1).optional(),
  outcome:            z.string().min(1),
  closed_at:          z.string().datetime(),

  // ── Optional (contato)
  close_reason:       z.string().optional(),
  /** Pre-computed contact-scoped session_metric.* values. */
  precomputed_metrics: z.record(z.number()).optional(),
})
export type IngestionContactClosed = z.infer<typeof IngestionContactClosedSchema>

/**
 * Discriminated union of the whole ingestion_event_v1 family.
 * Use this at the module ingest boundary to parse any incoming event.
 */
export const IngestionEventSchema = z.discriminatedUnion("event_type", [
  IngestionContactOpenedSchema,
  IngestionParticipantJoinedSchema,
  IngestionMessageSentSchema,
  IngestionParticipantLeftSchema,
  IngestionContactClosedSchema,
])
export type IngestionEvent = z.infer<typeof IngestionEventSchema>

// ─────────────────────────────────────────────
// Idempotency helper
// ─────────────────────────────────────────────

/**
 * Derive a stable, deterministic event_id when the sender did not provide one.
 * Stable across re-sends of the same logical event (same contact + position),
 * so downstream ReplacingMergeTree / ON CONFLICT dedup naturally.
 */
export function deriveIngestionEventId(
  externalContactId: string,
  eventType: IngestionEvent["event_type"],
  index: number,
): string {
  return `ext:${externalContactId}:${eventType}:${index}`
}
