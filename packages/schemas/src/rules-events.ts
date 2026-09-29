/**
 * rules-events.ts
 * Zod schemas for events published by the Rules Engine.
 *
 * Topics:
 *   rules.escalation.events — active escalation trigger (shadow_mode: false)
 *   rules.shadow.events     — shadow / dry-run trigger  (shadow_mode: true)
 *
 * Note: CLAUDE.md documents these as `rules.escalation_triggered` and
 * `rules.notification_triggered` (legacy names). The actual topic strings in
 * the codebase are `rules.escalation.events` and `rules.shadow.events`.
 * Both topics carry the same `RulesEscalationEventSchema` payload; the
 * `shadow_mode` boolean flag distinguishes active from shadow publishes.
 *
 * Source: packages/rules-engine/src/plughub_rules/models.py (EscalationTrigger)
 *         packages/rules-engine/src/plughub_rules/kafka_publisher.py
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// EvaluationContext — runtime metrics at trigger time
// ─────────────────────────────────────────────

/**
 * Snapshot of session metrics that were evaluated when the rule triggered.
 * Embedded inside every escalation event for downstream consumers that need
 * the context without fetching from Redis.
 */
export const RulesEvaluationContextSchema = z.object({
  session_id:         z.string(),
  tenant_id:          z.string(),
  turn_count:         z.number().int().nonnegative().default(0),
  elapsed_ms:         z.number().int().nonnegative().default(0),
  sentiment_score:    z.number().min(-1).max(1).default(0),
  intent_confidence:  z.number().min(0).max(1).default(0),
  /** Arbitrary string flags set by the rule engine (e.g. "high_value_customer") */
  flags:              z.array(z.string()).default([]),
  sentiment_history:  z.array(z.number().min(-1).max(1)).default([]),
})
export type RulesEvaluationContext = z.infer<typeof RulesEvaluationContextSchema>

// ─────────────────────────────────────────────
// RulesEscalationEvent — shared payload for both topics
// ─────────────────────────────────────────────

/**
 * Escalation trigger event.
 *
 * Published to two topics depending on the rule's mode:
 *   • `rules.escalation.events` — shadow_mode: false (rule is ACTIVE)
 *   • `rules.shadow.events`     — shadow_mode: true  (rule is in SHADOW/monitoring mode)
 *
 * ⚠️ RUL-01 (2026-09-29): nobody consumes `rules.escalation.events`, and nobody
 * publishes to either topic today — the rules-engine builds its Escalator without a
 * Kafka publisher, and neither topic exists on the broker. The claim that the Routing
 * Engine re-routes on this event was false. Rule-driven escalation has no path yet:
 * the active mode refuses (and the API refuses to activate a rule with target_pool)
 * until RUL-02 designs who stops the running agent before routing.
 */
export const RulesEscalationEventSchema = z.object({
  session_id:   z.string(),
  tenant_id:    z.string(),
  rule_id:      z.string(),
  rule_name:    z.string(),
  target_pool:  z.string(),
  /** false → published to rules.escalation.events (active); true → rules.shadow.events */
  shadow_mode:  z.boolean().default(false),
  triggered_at: z.string().datetime(),
  context:      RulesEvaluationContextSchema,
})
export type RulesEscalationEvent = z.infer<typeof RulesEscalationEventSchema>

// ─────────────────────────────────────────────
// Convenience discriminated wrapper (optional)
// ─────────────────────────────────────────────

/**
 * Typed union for consumers that read from both topics in a single handler.
 * Discriminated by `shadow_mode`.
 */
export const RulesActiveEventSchema = RulesEscalationEventSchema.extend({
  shadow_mode: z.literal(false),
})
export type RulesActiveEvent = z.infer<typeof RulesActiveEventSchema>

export const RulesShadowEventSchema = RulesEscalationEventSchema.extend({
  shadow_mode: z.literal(true),
})
export type RulesShadowEvent = z.infer<typeof RulesShadowEventSchema>

/** Union — parse any rules event regardless of topic */
export const RulesEventSchema = z.discriminatedUnion("shadow_mode", [
  RulesActiveEventSchema,
  RulesShadowEventSchema,
])
export type RulesEvent = z.infer<typeof RulesEventSchema>
