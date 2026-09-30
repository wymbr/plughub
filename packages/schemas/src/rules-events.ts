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
  /** null = NOT MEASURED (ai-gateway since 2026-08-23); 0 is neutral, a real point of the scale */
  sentiment_score:    z.number().min(-1).max(1).nullable(),
  intent_confidence:  z.number().min(0).max(1).default(0),
  /** Arbitrary string flags set by the rule engine (e.g. "high_value_customer") */
  flags:              z.array(z.string()).default([]),
  // `sentiment_history` saiu na RUL-04: não há série de sentimento MEDIDO por sessão.
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
 * RUL-02 (2026-09-30): the rules-engine publishes both, keyed by `session_id`. The
 * consumer of `rules.escalation.events` is the orchestrator-bridge (owner of agent
 * activation), NOT the Routing Engine: it checks that an AI conducts the session with no
 * human in it, marks the preemption once per session, and the skill-flow engine stops the
 * AI at the next step boundary — the AI then escalates itself through the regular
 * `escalate`. Nobody consumes `rules.shadow.events` yet (measurement only).
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
  /** RUL-02 — what the customer reads before the handoff; the rule's, optional (the platform
   *  never invents customer-facing text). Sent verbatim, without interpolation. */
  customer_notice: z.string().max(500).nullish(),
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

// ─────────────────────────────────────────────
// rules.turn_contexts — RUL-05
// ─────────────────────────────────────────────

/**
 * The context a rule SAW on one turn, published by the rules-engine for EVERY update of every
 * tenant (with or without rules) and written by analytics-api to `rule_turn_contexts` (TTL 90
 * days). The historical dry-run re-evaluates these rows with the same evaluator.
 *
 * `trigger`: `turn` (the ai-gateway's per-turn update) or `sentiment_measured` (the measurement
 * landed after the turn — RUL-04). Only numbers and flag names; `sentiment_score: null` = not
 * measured. Keyed by `session_id`.
 */
export const RulesTurnContextEventSchema = z.object({
  event_id:          z.string().uuid(),
  tenant_id:         z.string().min(1),
  session_id:        z.string().min(1),
  observed_at:       z.string().datetime({ offset: true }),
  trigger:           z.enum(["turn", "sentiment_measured"]),
  turn_count:        z.number().int().nonnegative(),
  elapsed_ms:        z.number().int().nonnegative(),
  sentiment_score:   z.number().min(-1).max(1).nullable(),
  intent_confidence: z.number().min(0).max(1),
  flags:             z.array(z.string()),
}).strict()
export type RulesTurnContextEvent = z.infer<typeof RulesTurnContextEventSchema>
