/**
 * outbound.ts
 * Outbound substrate — Mailing (audience) + Campaign (orchestration) + per-campaign
 * Delivery. Canonical store: mailing-api (schema PG `outbound`, port 3660). These
 * schemas are the shape of the JSONB blobs + the REST payloads; Pydantic models in
 * mailing-api validate against the same contract on ingest.
 *
 * Design (docs/product/outbound-mailing-campaign-design.md,
 *         docs/product/outbound-fase1-implementation-spec.md):
 *  - Substrate is GENERIC. Survey is the first consumer, not the owner.
 *  - `entry.metadata` is an OPAQUE blob — producer↔consumer contract between the skill
 *    that inserts (mailing_add) and the outbound skill that drains/contacts.
 *  - Membership (mailing_entries) ≠ suppression (campaign_deliveries): a shared mailing
 *    is not consumed by one campaign at the expense of another.
 *  - The addressable unit is the POOL (invariant S4): campaign.pool_id, never a skill.
 *  - Fase 1 covers mailings/entries/campaigns/deliveries. contact_log/contact_policy
 *    (governance) are Fase 2.
 */

import { z } from "zod"

// ── Mailing (the audience) ────────────────────────────────────────────────────

// How duplicate entries collapse in a mailing (drives dedup_key derivation).
export const DedupPolicySchema = z.enum(["customer", "customer_context", "none"])
export type DedupPolicy = z.infer<typeof DedupPolicySchema>

export const MailingSchema = z.object({
  id:                z.string(),
  tenant_id:         z.string(),
  name:              z.string().min(1),
  description:       z.string().nullable().default(null),
  dedup_policy:      DedupPolicySchema.default("customer_context"),
  // Doc-only label of the producer↔consumer contract (e.g. "survey_context_v1").
  // Not enforced (the metadata blob stays opaque to the platform).
  metadata_contract: z.string().nullable().default(null),
  // Default retention of entries (null = persistent).
  entry_ttl_seconds: z.number().int().positive().nullable().default(null),
  created_at:        z.string().datetime(),
  updated_at:        z.string().datetime(),
})
export type Mailing = z.infer<typeof MailingSchema>

// ── Mailing entry ((person, context) — the unit) ──────────────────────────────

// Global lifecycle of the entry (distinct from the per-campaign delivery result).
export const EntryStatusSchema = z.enum(["active", "expired", "unsubscribed", "invalid"])
export type EntryStatus = z.infer<typeof EntryStatusSchema>

// Addresses per channel — {whatsapp, email, sms, voice, ...}. Values are channel
// handles; the outbound skill picks one per channel_policy.
export const EntryContactsSchema = z.record(z.string())
export type EntryContacts = z.infer<typeof EntryContactsSchema>

export const MailingEntrySchema = z.object({
  id:          z.string(),
  mailing_id:  z.string(),
  tenant_id:   z.string(),
  // Native customer id (resolver). null = raw contact not yet resolved.
  customer_id: z.string().nullable().default(null),
  contacts:    EntryContactsSchema.default({}),
  // OPAQUE context (producer↔consumer contract). For survey: {grain,
  // grain_instance_id, origin_session_id, outcome, verbatim?, survey_form_id}.
  metadata:    z.record(z.unknown()).default({}),
  // Derived from the mailing's dedup_policy (see spec §5). UNIQUE per (mailing, key).
  dedup_key:   z.string(),
  // Provenance: 'skill:{skill_id}' | 'import:{import_id}'.
  source:      z.string().nullable().default(null),
  status:      EntryStatusSchema.default("active"),
  added_at:    z.string().datetime(),
  expires_at:  z.string().datetime().nullable().default(null),
  updated_at:  z.string().datetime(),
})
export type MailingEntry = z.infer<typeof MailingEntrySchema>

// ── Campaign (HOW the mailing is used — thin orchestrator) ─────────────────────

export const CampaignStatusSchema = z.enum(["active", "paused", "completed", "archived"])
export type CampaignStatus = z.infer<typeof CampaignStatusSchema>

// Channel order / restrictions for delivery. Fase 1 stores it; the enforcement of
// possessed-only / per-channel rules is the outbound skill's concern (+ Fase 3 gates).
export const ChannelPolicySchema = z.object({
  order:          z.array(z.string()).optional(),   // preferred channel order
  possessed_only: z.boolean().optional(),           // only verified (possessed) anchors
}).passthrough()
export type ChannelPolicy = z.infer<typeof ChannelPolicySchema>

// Declarative drain ordering over entry.metadata paths (+ added_at tiebreaker,
// always appended server-side). `path` is a single-level metadata key (safe token —
// the platform reads only the paths the campaign names; metadata stays opaque). `type`
// selects text (default) or numeric ordering (guarded cast, non-numeric → NULLS LAST).
export const OrderDirSchema = z.enum(["asc", "desc"])
export type OrderDir = z.infer<typeof OrderDirSchema>

export const OrderFieldTypeSchema = z.enum(["text", "number"])
export type OrderFieldType = z.infer<typeof OrderFieldTypeSchema>

export const CampaignOrderFieldSchema = z.object({
  path: z.string().regex(/^[a-zA-Z0-9_]+$/, "single-level metadata key ([a-zA-Z0-9_])"),
  dir:  OrderDirSchema.default("asc"),
  type: OrderFieldTypeSchema.default("text"),
})
export type CampaignOrderField = z.infer<typeof CampaignOrderFieldSchema>

export const CampaignOrderingSchema = z.array(CampaignOrderFieldSchema)
export type CampaignOrdering = z.infer<typeof CampaignOrderingSchema>

// Per-campaign retry over a delivery (drain re-picks a failed delivery until attempts
// reach max_attempts). Fase 1 wires max_attempts into the drain SQL.
export const CampaignRetrySchema = z.object({
  max_attempts: z.number().int().min(1).default(1),
  backoff:      z.string().optional(),   // opaque hint (e.g. "5m", "1h") — Fase 2+
}).passthrough()
export type CampaignRetry = z.infer<typeof CampaignRetrySchema>

export const CampaignSchema = z.object({
  id:                  z.string(),
  tenant_id:           z.string(),
  name:                z.string().min(1),
  mailing_id:          z.string(),
  // Webhook outbound pool whose skill contacts (invariant S4 — addresses a POOL).
  pool_id:             z.string(),
  // Predicate over entry.metadata (slices a shared mailing). null = whole mailing.
  selection:           z.record(z.unknown()).nullable().default(null),
  // Declarative drain ordering over entry.metadata (+ added_at tiebreaker). [] = FIFO.
  ordering:            CampaignOrderingSchema.default([]),
  channel_policy:      ChannelPolicySchema.default({}),
  // Fase 3 — contact window via calendar-api.
  contact_calendar_id: z.string().nullable().default(null),
  // Fase 2 — contact governance (fatigue). null = inherit tenant default.
  contact_policy_id:   z.string().nullable().default(null),
  // If true, may bypass soft opt-out (legal/mandatory notification).
  transactional:       z.boolean().default(false),
  // Cap of entries drained per execution.
  batch_size:          z.number().int().min(1).default(50),
  // Fase 3 — {max_concurrent, rate}; drains ≤ available pool capacity.
  pacing:              z.record(z.unknown()).default({}),
  retry:               CampaignRetrySchema.default({ max_attempts: 1 }),
  // Cadence: the Agenda (scheduler) that fires this pool with payload {campaign_id}.
  agenda_id:           z.string().nullable().default(null),
  status:              CampaignStatusSchema.default("active"),
  created_at:          z.string().datetime(),
  updated_at:          z.string().datetime(),
})
export type Campaign = z.infer<typeof CampaignSchema>

// ── Campaign delivery (per-campaign state per entry) ──────────────────────────

// 'claimed'  — drain reserved the entry (atomic, avoids double drain)
// 'contacted'/'responded'/'failed' — set by the skill after the collect
// 'skipped_ineligible'/'suppressed' — Fase 2 (governance gates)
export const DeliveryResultSchema = z.enum([
  "claimed",
  "pending",
  "contacted",
  "responded",
  "failed",
  "skipped_ineligible",
  "suppressed",
])
export type DeliveryResult = z.infer<typeof DeliveryResultSchema>

export const CampaignDeliverySchema = z.object({
  id:               z.string(),
  campaign_id:      z.string(),
  mailing_entry_id: z.string(),
  tenant_id:        z.string(),
  claimed_at:       z.string().datetime().nullable().default(null),
  contacted_at:     z.string().datetime().nullable().default(null),
  // The outbound session created for this delivery — the Monitor drills through.
  session_id:       z.string().nullable().default(null),
  root_session_id:  z.string().nullable().default(null),
  result:           DeliveryResultSchema.default("claimed"),
  attempts:         z.number().int().min(0).default(0),
  error:            z.string().nullable().default(null),
  created_at:       z.string().datetime(),
  updated_at:       z.string().datetime(),
})
export type CampaignDelivery = z.infer<typeof CampaignDeliverySchema>

// ── REST inputs (server owns id / status-derivation / timestamps) ─────────────

export const CreateMailingSchema = z.object({
  name:              z.string().min(1),
  description:       z.string().optional(),
  dedup_policy:      DedupPolicySchema.optional(),
  metadata_contract: z.string().optional(),
  entry_ttl_seconds: z.number().int().positive().optional(),
})
export type CreateMailing = z.infer<typeof CreateMailingSchema>

export const UpdateMailingSchema = z.object({
  name:              z.string().min(1).optional(),
  description:       z.string().nullable().optional(),
  dedup_policy:      DedupPolicySchema.optional(),
  metadata_contract: z.string().nullable().optional(),
  entry_ttl_seconds: z.number().int().positive().nullable().optional(),
})
export type UpdateMailing = z.infer<typeof UpdateMailingSchema>

// Backing of the `mailing_add` tool — upsert by dedup_key.
export const AddEntrySchema = z.object({
  customer_id: z.string().nullable().optional(),
  contacts:    EntryContactsSchema.optional(),
  metadata:    z.record(z.unknown()),
  // Explicit dedup_key overrides the derivation from the mailing's dedup_policy.
  dedup_key:   z.string().optional(),
  source:      z.string().optional(),
  ttl_seconds: z.number().int().positive().optional(),
})
export type AddEntry = z.infer<typeof AddEntrySchema>

export const AddEntryResultSchema = z.object({
  entry_id: z.string(),
  deduped:  z.boolean(),   // true = an existing entry was updated (upsert hit)
})
export type AddEntryResult = z.infer<typeof AddEntryResultSchema>

export const CreateCampaignSchema = z.object({
  name:                z.string().min(1),
  mailing_id:          z.string(),
  pool_id:             z.string(),
  selection:           z.record(z.unknown()).nullable().optional(),
  ordering:            CampaignOrderingSchema.optional(),
  channel_policy:      ChannelPolicySchema.optional(),
  // Fase 3a — contact window: calendar-api calendar id. null = no window gate.
  contact_calendar_id: z.string().nullable().optional(),
  transactional:       z.boolean().optional(),
  batch_size:          z.number().int().min(1).optional(),
  retry:               CampaignRetrySchema.optional(),
  agenda_id:           z.string().nullable().optional(),
})
export type CreateCampaign = z.infer<typeof CreateCampaignSchema>

export const UpdateCampaignSchema = z.object({
  name:                z.string().min(1).optional(),
  pool_id:             z.string().optional(),
  selection:           z.record(z.unknown()).nullable().optional(),
  ordering:            CampaignOrderingSchema.optional(),
  channel_policy:      ChannelPolicySchema.optional(),
  contact_calendar_id: z.string().nullable().optional(),
  transactional:       z.boolean().optional(),
  batch_size:          z.number().int().min(1).optional(),
  retry:               CampaignRetrySchema.optional(),
  agenda_id:           z.string().nullable().optional(),
  status:              CampaignStatusSchema.optional(),
})
export type UpdateCampaign = z.infer<typeof UpdateCampaignSchema>

// ── Drain (claim a batch) + delivery result ───────────────────────────────────

export const DrainRequestSchema = z.object({
  // Cap on this drain; default = campaign.batch_size (server-side).
  limit: z.number().int().min(1).optional(),
})
export type DrainRequest = z.infer<typeof DrainRequestSchema>

// One drained+claimed entry handed to the outbound skill (built from the entry).
export const DrainedEntrySchema = z.object({
  delivery_id: z.string(),
  entry_id:    z.string(),
  customer_id: z.string().nullable().default(null),
  contacts:    EntryContactsSchema.default({}),
  metadata:    z.record(z.unknown()).default({}),
})
export type DrainedEntry = z.infer<typeof DrainedEntrySchema>

export const DrainResponseSchema = z.object({
  campaign_id: z.string(),
  drained:     z.array(DrainedEntrySchema),
})
export type DrainResponse = z.infer<typeof DrainResponseSchema>

// Skill reports the outcome of a delivery after the collect.
export const DeliveryResultInputSchema = z.object({
  result:          DeliveryResultSchema,
  session_id:      z.string().optional(),
  root_session_id: z.string().optional(),
  error:           z.string().optional(),
})
export type DeliveryResultInput = z.infer<typeof DeliveryResultInputSchema>

// ── Fase 2 — Contact governance (fact × rule × decision) ──────────────────────
// Generic fatigue engine. Survey is a caller (contact_eligibility_check SUBSTITUTES
// survey_eligibility_check — decision 2026-07-21). Opt-out global (customer registry)
// and calendar window / soft preference are Fase 3.

// A window is a duration string: "24h" | "7d" | "60m" | "30s", or a plain integer of
// seconds. Parsed server-side.
export const ContactWindowSchema = z.union([z.string(), z.number().int().positive()])
export type ContactWindow = z.infer<typeof ContactWindowSchema>

// A frequency cap: at most `max` contacts within `window`; per_channel scopes the
// count to the same channel.
export const FrequencyCapSchema = z.object({
  window:      ContactWindowSchema,
  max:         z.number().int().min(1),
  per_channel: z.boolean().default(false),
})
export type FrequencyCap = z.infer<typeof FrequencyCapSchema>

// Per-channel cap: {channel: {window, max}}.
export const ChannelCapSchema = z.object({
  window: ContactWindowSchema,
  max:    z.number().int().min(1),
})
export type ChannelCap = z.infer<typeof ChannelCapSchema>

export const ContactPolicyScopeSchema = z.enum(["tenant", "campaign"])
export type ContactPolicyScope = z.infer<typeof ContactPolicyScopeSchema>

// The rule (layered): tenant default + per-campaign override. Effective policy =
// the campaign-scoped policy if present, else the tenant policy, else no rules.
export const ContactPolicySchema = z.object({
  id:               z.string(),
  tenant_id:        z.string(),
  scope:            ContactPolicyScopeSchema,
  // null for tenant scope; campaign_id for campaign scope.
  scope_id:         z.string().nullable().default(null),
  frequency_caps:   z.array(FrequencyCapSchema).default([]),
  // Do not re-contact for this window after any contact (a global max-1 cap).
  quarantine_after: ContactWindowSchema.nullable().default(null),
  // Per-channel caps: {whatsapp: {window, max}, ...}.
  channel_caps:     z.record(ChannelCapSchema).default({}),
  created_at:       z.string().datetime(),
  updated_at:       z.string().datetime(),
})
export type ContactPolicy = z.infer<typeof ContactPolicySchema>

// The fact (universal): every outbound contact is logged here.
export const ContactLogSchema = z.object({
  id:           z.string(),
  tenant_id:    z.string(),
  customer_id:  z.string(),
  channel:      z.string(),
  campaign_id:  z.string().nullable().default(null),
  contacted_at: z.string().datetime(),
  result:       z.string().default("sent"),
  created_at:   z.string().datetime(),
})
export type ContactLog = z.infer<typeof ContactLogSchema>

// ── REST inputs ───────────────────────────────────────────────────────────────

export const CreateContactPolicySchema = z.object({
  scope:            ContactPolicyScopeSchema,
  scope_id:         z.string().nullable().optional(),
  frequency_caps:   z.array(FrequencyCapSchema).optional(),
  quarantine_after: ContactWindowSchema.nullable().optional(),
  channel_caps:     z.record(ChannelCapSchema).optional(),
})
export type CreateContactPolicy = z.infer<typeof CreateContactPolicySchema>

export const UpdateContactPolicySchema = z.object({
  frequency_caps:   z.array(FrequencyCapSchema).optional(),
  quarantine_after: ContactWindowSchema.nullable().optional(),
  channel_caps:     z.record(ChannelCapSchema).optional(),
})
export type UpdateContactPolicy = z.infer<typeof UpdateContactPolicySchema>

// The decision. claim=true writes a contact_log fact when allowed (window starts at
// send, not at response — same semantics the survey quarantine used).
export const EligibilityRequestSchema = z.object({
  customer_id: z.string(),
  channel:     z.string(),
  campaign_id: z.string().nullable().optional(),
  claim:       z.boolean().default(true),
  // Evaluation instant (ISO); default now server-side.
  at:          z.string().datetime().optional(),
})
export type EligibilityRequest = z.infer<typeof EligibilityRequestSchema>

export const EligibilityResultSchema = z.object({
  allowed:     z.boolean(),
  // Machine reason when denied: "outside_window" (Fase 3a) | "quarantine" |
  // "frequency_cap" | "channel_cap" | "opt_out" (Fase 3b). null/absent when allowed.
  reason:      z.string().nullable().default(null),
  // Seconds until the blocking window frees up (best-effort), when denied.
  retry_after: z.number().int().nullable().default(null),
  // Whether a contact_log fact was written (claim=true and allowed).
  claimed:     z.boolean().default(false),
})
export type EligibilityResult = z.infer<typeof EligibilityResultSchema>

// mailing_unsubscribe — suppression. scope 'mailing' (default) flips entry.status;
// scope 'global' (Fase 3b) writes do_not_contact in the customer cadastro (identity).
export const UnsubscribeScopeSchema = z.enum(["mailing", "global"])
export type UnsubscribeScope = z.infer<typeof UnsubscribeScopeSchema>

export const UnsubscribeInputSchema = z.object({
  customer_id: z.string(),
  scope:       UnsubscribeScopeSchema.default("mailing"),
  // mailing scope: omit = all mailings of the customer.
  mailing_id:  z.string().nullable().optional(),
  // global scope: omit/'all' = full opt-out; a channel = per-channel opt-out.
  channel:     z.string().nullable().optional(),
})
export type UnsubscribeInput = z.infer<typeof UnsubscribeInputSchema>

export const UnsubscribeResultSchema = z.object({
  unsubscribed: z.number().int(),   // count of entries flipped to unsubscribed
})
export type UnsubscribeResult = z.infer<typeof UnsubscribeResultSchema>
