/**
 * platform-events.ts
 * Zod schemas for cross-cutting Kafka events that were previously untyped.
 *
 * Covered topics:
 *   registry.changed         — Agent Registry mutations (agent-registry → bridge)
 *   config.changed           — Config API mutations    (config-api → bridge, routing-engine)
 *   sentiment.updated        — Per-turn LLM sentiment  (ai-gateway → analytics-api)
 *   queue.position_updated   — Queue position notify   (routing-engine → channel-gateway, analytics)
 *   conversations.routed     — Routing result (allocated)
 *   conversations.queued     — Routing result (queued, not yet allocated)
 *   agent.lifecycle          — Agent state machine events (mcp-server → routing-engine, analytics)
 *   conversations.events     — Core session lifecycle + messages
 *
 * Source files:
 *   packages/agent-registry/src/infra/kafka.ts          (registry.changed)
 *   packages/config-api/src/plughub_config_api/kafka_emitter.py (config.changed)
 *   packages/ai-gateway/src/plughub_ai_gateway/sentiment_emitter.py (sentiment.updated)
 *   packages/routing-engine/src/plughub_routing/router.py (queue.position_updated)
 *   packages/routing-engine/src/plughub_routing/models.py (ConversationRoutedEvent)
 *   packages/mcp-server-plughub/src/tools/runtime.ts    (agent.lifecycle events)
 *   packages/analytics-api (models.py)                  (conversations.events)
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// registry.changed
// ─────────────────────────────────────────────

/**
 * Published by Agent Registry on any AgentType, Pool, Skill, GatewayConfig,
 * or Instance mutation. Consumed by orchestrator-bridge to trigger immediate
 * reconciliation of Redis instance state.
 *
 * Note: `tenant_id` is carried in the Kafka message KEY (not the value) so
 * that consumers can partition-filter by tenant without parsing the JSON.
 * It is included here as optional for consumers that join the key into the
 * parsed payload (e.g. orchestrator-bridge).
 */
export const RegistryChangedEventSchema = z.object({
  event_type:  z.literal("registry.changed"),
  entity_type: z.string(), // "agent_type" | "pool" | "skill" | "gateway_config" | "instance"
  entity_id:   z.string(),
  operation:   z.enum(["created", "updated", "deleted"]),
  /** Present when the consumer merges the Kafka key into the payload */
  tenant_id:   z.string().optional(),
})
export type RegistryChangedEvent = z.infer<typeof RegistryChangedEventSchema>

// ─────────────────────────────────────────────
// config.changed
// ─────────────────────────────────────────────

/**
 * Published by Config API after every successful PUT or DELETE on a config key.
 * Consumers route by `namespace`:
 *   quota      → orchestrator-bridge  (bootstrap.request_refresh)
 *   routing    → routing-engine       (cache invalidation — future)
 *   others     → rely on Redis TTL (60s) for natural propagation
 *
 * `tenant_id` is `"__global__"` when the global platform default was changed.
 */
export const ConfigChangedEventSchema = z.object({
  event:      z.literal("config.changed"),
  tenant_id:  z.string(), // tenant_id or "__global__" for platform defaults
  namespace:  z.string(), // "routing" | "quota" | "sentiment" | "masking" | ...
  key:        z.string(),
  operation:  z.enum(["set", "delete"]),
  updated_at: z.string().datetime(),
})
export type ConfigChangedEvent = z.infer<typeof ConfigChangedEventSchema>

// ─────────────────────────────────────────────
// sentiment.updated
// ─────────────────────────────────────────────

/**
 * Published by AI Gateway after each LLM turn where sentiment was extracted.
 * Consumed by analytics-api to populate ClickHouse `sentiment_events` table
 * and to update `{tenant_id}:pool:{pool_id}:sentiment_live` Redis hash.
 *
 * `score` is rounded to 4 decimal places.
 * `category` is computed at publish time from configurable tenant ranges
 * (default: satisfied ≥ 0.3, neutral ≥ -0.3, frustrated ≥ -0.6, angry < -0.6).
 */
export const SentimentUpdatedEventSchema = z.object({
  event_id:   z.string().uuid(),
  tenant_id:  z.string(),
  session_id: z.string(),
  pool_id:    z.string(),
  score:      z.number().min(-1).max(1),
  category:   z.enum(["satisfied", "neutral", "frustrated", "angry"]),
  timestamp:  z.string().datetime(),
})
export type SentimentUpdatedEvent = z.infer<typeof SentimentUpdatedEventSchema>

// ─────────────────────────────────────────────
// queue.position_updated
// ─────────────────────────────────────────────

/**
 * Published by Routing Engine when a contact is queued (no agent available).
 * Consumed by Channel Gateway (to show queue position to customer) and
 * analytics-api (to populate queue_events in ClickHouse).
 *
 * `estimated_wait_ms = queue_length × (sla_target_ms × 0.7)` — conservative p70 estimate.
 */
export const QueuePositionUpdatedEventSchema = z.object({
  event:             z.literal("queue.position_updated"),
  tenant_id:         z.string(),
  session_id:        z.string(),
  pool_id:           z.string(),
  queue_length:      z.number().int().nonnegative(),
  available_agents:  z.number().int().nonnegative(),
  estimated_wait_ms: z.number().int().nonnegative(),
  sla_target_ms:     z.number().int().positive(),
  published_at:      z.string().datetime(),
})
export type QueuePositionUpdatedEvent = z.infer<typeof QueuePositionUpdatedEventSchema>

// ─────────────────────────────────────────────
// conversations.routed / conversations.queued
// ─────────────────────────────────────────────

/**
 * RoutingResult — embedded in ConversationRoutedEvent.
 * `allocated: true`  → published to conversations.routed
 * `allocated: false` → published to conversations.queued
 */
export const RoutingResultEventSchema = z.object({
  session_id:      z.string(),
  tenant_id:       z.string(),
  allocated:       z.boolean(),
  instance_id:     z.string().nullable().optional(),
  agent_type_id:   z.string().nullable().optional(),
  pool_id:         z.string().nullable().optional(),
  resource_score:  z.number().default(0),
  priority_score:  z.number().default(0),
  routing_mode:    z.string().default("autonomous"), // "autonomous" | "manual" | ...
  cross_site:      z.boolean().default(false),
  allocated_site:  z.string().nullable().optional(),
  queued:          z.boolean().default(false),
  queue_eta_ms:    z.number().int().nullable().optional(),
  routed_at:       z.string().datetime(),
  conference_id:   z.string().nullable().optional(),
  channel_identity: z.record(z.string()).nullable().optional(),
})
export type RoutingResultEvent = z.infer<typeof RoutingResultEventSchema>

/**
 * ConversationRoutedEvent — published to conversations.routed or conversations.queued.
 * Consumed by orchestrator-bridge (to activate agents), analytics-api, and Rules Engine.
 */
export const ConversationRoutedEventSchema = z.object({
  session_id: z.string(),
  tenant_id:  z.string(),
  result:     RoutingResultEventSchema,
  routed_at:  z.string().datetime(),
})
export type ConversationRoutedEvent = z.infer<typeof ConversationRoutedEventSchema>

// ─────────────────────────────────────────────
// agent.lifecycle
// ─────────────────────────────────────────────

/**
 * Individual agent state machine event schemas.
 * All published to Kafka topic: agent.lifecycle
 * Source: mcp-server-plughub/src/tools/runtime.ts
 */

export const AgentLoginEventSchema = z.object({
  event:         z.literal("agent_login"),
  tenant_id:     z.string(),
  instance_id:   z.string(),
  agent_type_id: z.string(),
  timestamp:     z.string().datetime(),
})
export type AgentLoginEvent = z.infer<typeof AgentLoginEventSchema>

export const AgentReadyEventSchema = z.object({
  event:                    z.literal("agent_ready"),
  tenant_id:                z.string(),
  instance_id:              z.string(),
  agent_type_id:            z.string(),
  pools:                    z.array(z.string()),
  status:                   z.literal("ready"),
  execution_model:          z.string(), // "stateless" | "stateful"
  max_concurrent_sessions:  z.number().int().positive(),
  current_sessions:         z.number().int().nonnegative(),
  timestamp:                z.string().datetime(),
})
export type AgentReadyEvent = z.infer<typeof AgentReadyEventSchema>

export const AgentBusyEventSchema = z.object({
  event:            z.literal("agent_busy"),
  tenant_id:        z.string(),
  instance_id:      z.string(),
  participant_id:   z.string(),
  session_id:       z.string(),
  current_sessions: z.number().int().nonnegative(),
  timestamp:        z.string().datetime(),
})
export type AgentBusyEvent = z.infer<typeof AgentBusyEventSchema>

export const AgentDoneEventSchema = z.object({
  event:            z.literal("agent_done"),
  tenant_id:        z.string(),
  instance_id:      z.string(),
  participant_id:   z.string(),
  session_id:       z.string(),
  current_sessions: z.number().int().nonnegative(),
  timestamp:        z.string().datetime(),
})
export type AgentDoneEvent = z.infer<typeof AgentDoneEventSchema>

export const AgentPauseEventSchema = z.object({
  event:         z.literal("agent_pause"),
  tenant_id:     z.string(),
  instance_id:   z.string(),
  agent_type_id: z.string().optional(),
  pool_id:       z.string().optional(),
  /** Pause reason code from agent_activity.pause_reasons Config API namespace */
  reason_id:     z.string(),
  /** Human-readable label for the reason, e.g. "Intervalo" */
  reason_label:  z.string(),
  /** Optional free-text note — required when reason.requires_note = true */
  note:          z.string().optional(),
  timestamp:     z.string().datetime(),
})
export type AgentPauseEvent = z.infer<typeof AgentPauseEventSchema>

/** agent_ready is also used to signal return from pause (when preceded by agent_pause) */
export const AgentResumeEventSchema = z.object({
  event:         z.literal("agent_ready"),
  tenant_id:     z.string(),
  instance_id:   z.string(),
  agent_type_id: z.string(),
  pools:                   z.array(z.string()),
  status:                  z.literal("ready"),
  execution_model:         z.string(),
  max_concurrent_sessions: z.number().int().positive(),
  current_sessions:        z.number().int().nonnegative(),
  timestamp:               z.string().datetime(),
})

export const AgentLogoutEventSchema = z.object({
  event:           z.literal("agent_logout"),
  tenant_id:       z.string(),
  instance_id:     z.string(),
  /** "draining" → still serving active sessions; "logged_out" → fully offline */
  state:           z.enum(["draining", "logged_out"]),
  active_sessions: z.number().int().nonnegative(),
  timestamp:       z.string().datetime(),
})
export type AgentLogoutEvent = z.infer<typeof AgentLogoutEventSchema>

export const AgentHeartbeatEventSchema = z.object({
  event:       z.literal("agent_heartbeat"),
  tenant_id:   z.string(),
  instance_id: z.string(),
  /** "ready" | "busy" — only valid states for a heartbeat */
  status:      z.enum(["ready", "busy"]),
  timestamp:   z.string().datetime(),
})
export type AgentHeartbeatEvent = z.infer<typeof AgentHeartbeatEventSchema>

/**
 * Discriminated union of all agent.lifecycle event variants.
 * Use this in consumers that handle multiple event types in a single handler.
 */
export const AgentLifecycleEventSchema = z.discriminatedUnion("event", [
  AgentLoginEventSchema,
  AgentReadyEventSchema,
  AgentBusyEventSchema,
  AgentDoneEventSchema,
  AgentPauseEventSchema,
  AgentLogoutEventSchema,
  AgentHeartbeatEventSchema,
])
export type AgentLifecycleEvent = z.infer<typeof AgentLifecycleEventSchema>

// ─────────────────────────────────────────────
// conversations.events
// ─────────────────────────────────────────────

/**
 * Multi-type topic published by the Core (orchestrator-bridge) for session
 * lifecycle and message events. Consumed by analytics-api.
 *
 * Recognised event_type values:
 *   contact_open    — contact arrived and session created
 *   contact_closed  — session ended (with outcome/close_reason)
 *   message_sent    — message was delivered to participants
 *
 * All other values are silently skipped by consumers.
 */

export const ConversationContactOpenSchema = z.object({
  event_type:   z.literal("contact_open"),
  session_id:   z.string(),
  tenant_id:    z.string(),
  channel:      z.string().optional(),
  contact_id:   z.string().nullable().optional(),
  customer_id:  z.string().nullable().optional(),
  started_at:   z.string().datetime().optional(),
  timestamp:    z.string().datetime().optional(),
})
export type ConversationContactOpen = z.infer<typeof ConversationContactOpenSchema>

export const ConversationContactClosedSchema = z.object({
  event_type:   z.literal("contact_closed"),
  session_id:   z.string(),
  tenant_id:    z.string(),
  channel:      z.string().optional(),
  contact_id:   z.string().nullable().optional(),
  customer_id:  z.string().nullable().optional(),
  started_at:   z.string().datetime().optional(),
  ended_at:     z.string().datetime().optional(),
  /** Primary close reason field */
  reason:       z.string().optional(),
  /** Legacy alias — maps to the same ClickHouse column */
  close_reason: z.string().optional(),
  outcome:      z.string().nullable().optional(),
})
export type ConversationContactClosed = z.infer<typeof ConversationContactClosedSchema>

export const ConversationMessageSentSchema = z.object({
  event_type:   z.literal("message_sent"),
  session_id:   z.string(),
  tenant_id:    z.string(),
  message_id:   z.string().optional(),
  /** Canonical author role field */
  author_role:  z.string().optional(),
  /** Legacy alias */
  role:         z.string().optional(),
  channel:      z.string().optional(),
  content_type: z.string().optional(),
  visibility:   z.enum(["all", "agents_only"]).optional(),
  timestamp:    z.string().datetime().optional(),
})
export type ConversationMessageSent = z.infer<typeof ConversationMessageSentSchema>

/**
 * Discriminated union of all conversations.events payloads.
 * Unknown event_type values are handled by consumers via a catch-all fallback.
 */
export const ConversationsEventSchema = z.discriminatedUnion("event_type", [
  ConversationContactOpenSchema,
  ConversationContactClosedSchema,
  ConversationMessageSentSchema,
])
export type ConversationsEvent = z.infer<typeof ConversationsEventSchema>
