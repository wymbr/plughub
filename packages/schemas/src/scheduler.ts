/**
 * scheduler.ts
 * Agenda (scheduled job) schemas for the PlugHub Platform scheduler-api.
 *
 * An Agenda is a DOMAIN-AGNOSTIC scheduled resource: at a configured *when* and
 * *mode* it fires a POOL via webhook (Arc 19). It does not know what the pool does
 * (deploy promote, outbound campaign, …). Firing = "did it produce an admitted
 * session?"; execution status belongs to the fired session (by reference, never
 * mirrored here).
 *
 * Design (spec docs/product/scheduler-agenda-spec.md):
 *  - Addresses a POOL, never a skill (invariant S4). Pool must be webhook-capable.
 *  - business-day / holiday behavior is DELEGATED to a calendar (calendar_id +
 *    calendar-api). This schema does NOT re-model holidays/business hours.
 *  - Fire times are INSTANTS ("HH:MM"), distinct from calendar TimeSlot {open,close}.
 *  - Recurrence computes only the NEXT occurrence; validity.ends_at is the stop.
 */

import { z } from "zod"
import { DayOfWeekSchema } from "./calendar"

// ── Time-of-day instant (a fire moment, NOT an open/close interval) ───────────

export const FireTimeSchema = z.string().regex(/^\d{2}:\d{2}$/, "must be HH:MM")
export type FireTime = z.infer<typeof FireTimeSchema>

// ── Recurrence enums ──────────────────────────────────────────────────────────

export const FrequencySchema = z.enum(["daily", "weekly", "monthly"])
export type Frequency = z.infer<typeof FrequencySchema>

// What to do when a computed fire lands on a non-business day / holiday.
// Consulted against the associated calendar (calendar-api). `ignore` = wall-clock.
export const BusinessDayPolicySchema = z.enum([
  "ignore",
  "only_business_days",   // lands on closed/holiday → skip the occurrence
  "shift_next",           // move to the next open day/slot
  "shift_previous",       // move to the previous open day/slot
])
export type BusinessDayPolicy = z.infer<typeof BusinessDayPolicySchema>

// Monthly day-of-month overflow (e.g. day 31 in February).
export const MonthOverflowSchema = z.enum(["clamp", "skip"])
export type MonthOverflow = z.infer<typeof MonthOverflowSchema>

// Service was down when an occurrence was due.
export const MisfirePolicySchema = z.enum(["fire_late", "skip", "fire_all_missed"])
export type MisfirePolicy = z.infer<typeof MisfirePolicySchema>

// ── Monthly day selection ─────────────────────────────────────────────────────
// by_date: specific days of month (1..31 or "last")
// by_position: nth weekday of the month ("first monday", "last friday")

export const MonthDaySchema = z.union([
  z.number().int().min(1).max(31),
  z.literal("last"),
])
export type MonthDay = z.infer<typeof MonthDaySchema>

export const MonthByDateSchema = z.object({
  kind: z.literal("by_date"),
  days: z.array(MonthDaySchema).min(1),
})

export const MonthByPositionSchema = z.object({
  kind:    z.literal("by_position"),
  nth:     z.union([z.number().int().min(1).max(5), z.literal("last")]),
  weekday: DayOfWeekSchema,
})

export const MonthBySchema = z.discriminatedUnion("kind", [
  MonthByDateSchema,
  MonthByPositionSchema,
])
export type MonthBy = z.infer<typeof MonthBySchema>

// ── Recurrence rule ───────────────────────────────────────────────────────────
// Fire set = (selected days) × (times). A shift moves the WHOLE day (all times).

export const RecurrenceRuleSchema = z.object({
  frequency: FrequencySchema,
  // "every N" → fortnightly, bimonthly, etc. Default 1.
  interval:  z.number().int().min(1).default(1),
  // weekly: which weekdays are selected. Reuses DayOfWeekSchema (never redefine).
  weekdays:  z.array(DayOfWeekSchema).optional(),
  // monthly: by date or by position.
  month_by:  MonthBySchema.optional(),
  // ≥1 instant of the day at which to fire.
  times:     z.array(FireTimeSchema).min(1),
  business_day_policy: BusinessDayPolicySchema.default("ignore"),
  month_overflow:      MonthOverflowSchema.default("clamp"),
})
export type RecurrenceRule = z.infer<typeof RecurrenceRuleSchema>

// ── Validity envelope ─────────────────────────────────────────────────────────

export const AgendaValiditySchema = z.object({
  starts_at: z.string().datetime(),
  // null = open-ended (only meaningful for recurring).
  ends_at:   z.string().datetime().nullable().default(null),
})
export type AgendaValidity = z.infer<typeof AgendaValiditySchema>

// ── Schedule (once | recurring) ───────────────────────────────────────────────

export const OnceScheduleSchema = z.object({
  mode:    z.literal("once"),
  fire_at: z.string().datetime(),
})

export const RecurringScheduleSchema = z.object({
  mode: z.literal("recurring"),
  rule: RecurrenceRuleSchema,
})

export const AgendaScheduleSchema = z.discriminatedUnion("mode", [
  OnceScheduleSchema,
  RecurringScheduleSchema,
])
export type AgendaSchedule = z.infer<typeof AgendaScheduleSchema>

// ── Agenda status (lifecycle of the schedule itself) ──────────────────────────

export const AgendaStatusSchema = z.enum([
  "active",
  "paused",
  "completed",   // one-shot fired, or recurring reached ends_at
  "expired",     // validity elapsed without firing
  "cancelled",
])
export type AgendaStatus = z.infer<typeof AgendaStatusSchema>

// ── Agenda ────────────────────────────────────────────────────────────────────

export const AgendaSchema = z.object({
  id:             z.string(),
  tenant_id:      z.string(),
  name:           z.string().min(1),
  // Pool webhook that gets fired. Must be webhook-capable (validated server-side).
  target_pool_id: z.string(),
  // Generic JSON delivered to the webhook; interpreted by the pool's skill.
  payload:        z.record(z.unknown()).default({}),
  // Defaults from the associated calendar's timezone when calendar_id is set.
  timezone:       z.string().default("America/Sao_Paulo"),
  // References calendar-api (brings weekly hours + holidays + exceptions).
  calendar_id:    z.string().nullable().default(null),
  status:         AgendaStatusSchema.default("active"),

  validity:       AgendaValiditySchema,
  schedule:       AgendaScheduleSchema,
  misfire_policy: MisfirePolicySchema.default("skip"),

  // Runtime / derived (computed by scheduler-api).
  next_fire_at:   z.string().datetime().nullable().default(null),
  last_fired_at:  z.string().datetime().nullable().default(null),

  created_at:     z.string().datetime(),
  updated_at:     z.string().datetime(),
})
export type Agenda = z.infer<typeof AgendaSchema>

// ── Dispatch ledger — one record per occurrence ───────────────────────────────
// "dispatched" only when the webhook returned an ADMITTED session (session_id).
// "skipped"    = misfire policy skipped it, or only_business_days on a closed day.
// "failed"     = 5xx / unreachable / no capacity / empty `next` slot (with reason).

export const DispatchResultSchema = z.enum(["dispatched", "failed", "skipped"])
export type DispatchResult = z.infer<typeof DispatchResultSchema>

export const AgendaDispatchSchema = z.object({
  id:              z.string(),
  agenda_id:       z.string(),
  tenant_id:       z.string(),
  // Planned instant (after calendar shift).
  scheduled_for:   z.string().datetime(),
  // Actual instant of the POST attempt (null when skipped before firing).
  fired_at:        z.string().datetime().nullable().default(null),
  result:          DispatchResultSchema,
  // Correlation with the created session — the Monitor drills through to it.
  // NEVER mirrors the session's execution status here.
  session_id:      z.string().nullable().default(null),
  root_session_id: z.string().nullable().default(null),
  // Reason when result = failed | skipped.
  error:           z.string().nullable().default(null),
  created_at:      z.string().datetime(),
})
export type AgendaDispatch = z.infer<typeof AgendaDispatchSchema>

// ── REST inputs (create / update) ─────────────────────────────────────────────
// Server owns id, status derivation, next/last_fired_at, timestamps.

export const CreateAgendaSchema = z.object({
  name:           z.string().min(1),
  target_pool_id: z.string(),
  payload:        z.record(z.unknown()).optional(),
  timezone:       z.string().optional(),
  calendar_id:    z.string().nullable().optional(),
  validity:       AgendaValiditySchema,
  schedule:       AgendaScheduleSchema,
  misfire_policy: MisfirePolicySchema.optional(),
})
export type CreateAgenda = z.infer<typeof CreateAgendaSchema>

// All fields optional; `schedule`/`validity` replaced wholesale when present.
export const UpdateAgendaSchema = z.object({
  name:           z.string().min(1).optional(),
  target_pool_id: z.string().optional(),
  payload:        z.record(z.unknown()).optional(),
  timezone:       z.string().optional(),
  calendar_id:    z.string().nullable().optional(),
  validity:       AgendaValiditySchema.optional(),
  schedule:       AgendaScheduleSchema.optional(),
  misfire_policy: MisfirePolicySchema.optional(),
  // Explicit lifecycle ops (pause/resume/cancel) go through dedicated routes,
  // but status may be set here for pause/resume convenience.
  status:         AgendaStatusSchema.optional(),
})
export type UpdateAgenda = z.infer<typeof UpdateAgendaSchema>
