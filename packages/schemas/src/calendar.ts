/**
 * calendar.ts
 * Calendar, HolidaySet and scheduling schemas for PlugHub Platform.
 *
 * Calendars define when operations are open. They can be associated
 * with any entity (tenant, channel, pool, workflow) via CalendarAssociation.
 *
 * Multiple calendars can be aggregated per entity using UNION or INTERSECTION:
 *   UNION        — open if ANY associated calendar is open        (OR)
 *   INTERSECTION — open only if ALL associated calendars are open (AND)
 *
 * Evaluation order: associations sorted by priority (ascending).
 * All UNION items are grouped first, then INTERSECTION is applied over the result.
 *
 * Inheritance (when no explicit association exists):
 *   Pool → Channel → Tenant → Organization → Installation
 */

import { z } from "zod"
import { ResourceScopeSchema } from "./platform"

// ── Day of week ───────────────────────────────────────────────────────────────

export const DayOfWeekSchema = z.enum([
  "monday", "tuesday", "wednesday", "thursday",
  "friday", "saturday", "sunday",
])
export type DayOfWeek = z.infer<typeof DayOfWeekSchema>

// ── Time slot (within a single day) ──────────────────────────────────────────

export const TimeSlotSchema = z.object({
  open:  z.string().regex(/^\d{2}:\d{2}$/, "must be HH:MM"),
  close: z.string().regex(/^\d{2}:\d{2}$/, "must be HH:MM"),
})
export type TimeSlot = z.infer<typeof TimeSlotSchema>

// ── Weekly recurring schedule ─────────────────────────────────────────────────

export const DayScheduleSchema = z.object({
  day:   DayOfWeekSchema,
  open:  z.boolean().default(true),
  // Ignored when open=false. Default: full day open.
  slots: z.array(TimeSlotSchema).default([{ open: "00:00", close: "23:59" }]),
})
export type DaySchedule = z.infer<typeof DayScheduleSchema>

// ── Holiday (single date within a HolidaySet) ────────────────────────────────

export const HolidaySchema = z.object({
  date:  z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "must be YYYY-MM-DD"),
  name:  z.string(),
  // null = day is fully closed; array = override with specific slots
  override_slots: z.array(TimeSlotSchema).nullable().default(null),
})
export type Holiday = z.infer<typeof HolidaySchema>

// ── Holiday Set ───────────────────────────────────────────────────────────────
// A named, reusable set of holiday dates. Can be shared across calendars.

export const HolidaySetSchema = z.object({
  id:              z.string().uuid(),
  installation_id: z.string(),
  organization_id: z.string(),
  tenant_id:       z.string().nullable().default(null),  // null = org-level
  scope:           ResourceScopeSchema,
  name:            z.string(),         // e.g. "feriados_br_2026"
  description:     z.string().optional(),
  year:            z.number().int().optional(),  // optional — sets can span years
  holidays:        z.array(HolidaySchema),
  created_at:      z.string().datetime(),
  updated_at:      z.string().datetime(),
})
export type HolidaySet = z.infer<typeof HolidaySetSchema>

// ── Calendar exception (point-in-time override, highest priority) ─────────────

export const CalendarExceptionSchema = z.object({
  date:           z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "must be YYYY-MM-DD"),
  reason:         z.string().optional(),
  // null = fully closed on this date; array = override with specific slots
  override_slots: z.array(TimeSlotSchema).nullable(),
})
export type CalendarException = z.infer<typeof CalendarExceptionSchema>

// ── Calendar ──────────────────────────────────────────────────────────────────

export const CalendarSchema = z.object({
  id:              z.string().uuid(),
  installation_id: z.string(),
  organization_id: z.string(),
  tenant_id:       z.string().nullable().default(null),  // null = org-level
  scope:           ResourceScopeSchema,
  name:            z.string(),
  description:     z.string().optional(),
  timezone:        z.string().default("America/Sao_Paulo"),
  // 7 entries recommended (one per day); missing days treated as closed
  weekly_schedule: z.array(DayScheduleSchema),
  // Linked holiday sets — applied in order; later sets can override earlier ones
  holiday_set_ids: z.array(z.string().uuid()).default([]),
  // Point-in-time exceptions — always override weekly schedule and holiday sets
  exceptions:      z.array(CalendarExceptionSchema).default([]),
  created_at:      z.string().datetime(),
  updated_at:      z.string().datetime(),
})
export type Calendar = z.infer<typeof CalendarSchema>

// ── Calendar association operator ─────────────────────────────────────────────

export const CalendarOperatorSchema = z.enum(["UNION", "INTERSECTION"])
export type CalendarOperator = z.infer<typeof CalendarOperatorSchema>

// ── Entity types that can have calendar associations ──────────────────────────

export const CalendarEntityTypeSchema = z.enum([
  "tenant", "channel", "pool", "workflow",
])
export type CalendarEntityType = z.infer<typeof CalendarEntityTypeSchema>

// ── Calendar Association ──────────────────────────────────────────────────────
// Links one calendar to one entity with an aggregation operator and priority.
// Multiple associations per entity are evaluated in priority order.

export const CalendarAssociationSchema = z.object({
  id:          z.string().uuid(),
  tenant_id:   z.string(),
  entity_type: CalendarEntityTypeSchema,
  entity_id:   z.string(),
  calendar_id: z.string().uuid(),
  operator:    CalendarOperatorSchema.default("UNION"),
  // Lower priority = evaluated first. Tie-broken by creation order.
  priority:    z.number().int().min(1).default(1),
  created_at:  z.string().datetime(),
})
export type CalendarAssociation = z.infer<typeof CalendarAssociationSchema>

// ── Engine query types (used by calendar-engine) ──────────────────────────────

export const CalendarQuerySchema = z.object({
  entity_type: CalendarEntityTypeSchema,
  entity_id:   z.string(),
  tenant_id:   z.string(),
  at:          z.string().datetime().optional(),  // default: now()
})
export type CalendarQuery = z.infer<typeof CalendarQuerySchema>

export const IsOpenResultSchema = z.object({
  open:        z.boolean(),
  evaluated_at: z.string().datetime(),
  next_change:  z.string().datetime().nullable(),  // next open or close event
  calendars_used: z.array(z.string().uuid()),
})
export type IsOpenResult = z.infer<typeof IsOpenResultSchema>

export const BusinessDurationResultSchema = z.object({
  business_hours:  z.number(),   // total business hours between from/to
  business_minutes: z.number(),
  calendar_id:     z.string().uuid(),
})
export type BusinessDurationResult = z.infer<typeof BusinessDurationResultSchema>

// ── Kafka Events ──────────────────────────────────────────────────────────────

export const CalendarWindowOpenedSchema = z.object({
  event_type:   z.literal("calendar.window_opened"),
  timestamp:    z.string().datetime(),
  tenant_id:    z.string(),
  entity_type:  CalendarEntityTypeSchema,
  entity_id:    z.string(),
  next_close:   z.string().datetime().nullable(),
})
export type CalendarWindowOpened = z.infer<typeof CalendarWindowOpenedSchema>

export const CalendarWindowClosedSchema = z.object({
  event_type:   z.literal("calendar.window_closed"),
  timestamp:    z.string().datetime(),
  tenant_id:    z.string(),
  entity_type:  CalendarEntityTypeSchema,
  entity_id:    z.string(),
  next_open:    z.string().datetime().nullable(),
})
export type CalendarWindowClosed = z.infer<typeof CalendarWindowClosedSchema>

export const CalendarEventSchema = z.discriminatedUnion("event_type", [
  CalendarWindowOpenedSchema,
  CalendarWindowClosedSchema,
])
export type CalendarEvent = z.infer<typeof CalendarEventSchema>
