import { apiFetch } from '@/api/apiFetch'
/**
 * schedules/api.ts
 * Types + REST client + formatting helpers for the Scheduler / Agenda module (Fase 3).
 *
 * Backend: scheduler-api (port 3650) proxied under /v1/agendas (header X-Tenant-ID).
 * Webhook pools come from agent-registry (/v1/pools, header x-tenant-id); calendars
 * from calendar-api (/v1/calendars). Shared by SchedulesPage (authoring) and
 * SchedulesMonitorPage (operation).
 */

const ORG_ID = import.meta.env.VITE_CALENDAR_ORG_ID ?? 'org-default'

// ── Types (mirror @plughub/schemas/scheduler.ts + scheduler-api router models) ──

export type DayOfWeek =
  | 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday'

export type AgendaStatus = 'active' | 'paused' | 'completed' | 'expired' | 'cancelled'
export type MisfirePolicy = 'fire_late' | 'skip' | 'fire_all_missed'
export type BusinessDayPolicy = 'ignore' | 'only_business_days' | 'shift_next' | 'shift_previous'
export type MonthOverflow = 'clamp' | 'skip'
export type Frequency = 'daily' | 'weekly' | 'monthly'

export type MonthBy =
  | { kind: 'by_date'; days: (number | 'last')[] }
  | { kind: 'by_position'; nth: number | 'last'; weekday: DayOfWeek }

export interface RecurrenceRule {
  frequency: Frequency
  interval: number
  weekdays?: DayOfWeek[]
  month_by?: MonthBy
  times: string[]
  business_day_policy: BusinessDayPolicy
  month_overflow: MonthOverflow
}

export type Schedule =
  | { mode: 'once'; fire_at: string }
  | { mode: 'recurring'; rule: RecurrenceRule }

export interface Validity {
  starts_at: string
  ends_at?: string | null
}

export interface Agenda {
  id: string
  tenant_id: string
  name: string
  target_pool_id: string
  payload: Record<string, unknown>
  timezone: string | null
  calendar_id: string | null
  status: AgendaStatus
  validity: Validity
  schedule: Schedule
  misfire_policy: MisfirePolicy | null
  next_fire_at?: string | null
  last_fired_at?: string | null
}

export interface AgendaDispatch {
  id: string
  agenda_id: string
  tenant_id: string
  scheduled_for: string | null
  fired_at: string | null
  result: 'dispatched' | 'failed' | 'skipped'
  session_id: string | null
  root_session_id: string | null
  error: string | null
  created_at: string
}

export interface WebhookPool {
  pool_id: string
  channel_types: string[]
}

// ── Fetch helpers ────────────────────────────────────────────────────────────

// ⚠️ RENOMEADO em 2026-08-31 (AUT-19). Este helper JA se chamava `apiFetch`, e a
// varredura da AUT-18 (2026-08-30) trocou o `fetch(` do corpo dele por `apiFetch(`,
// criando **auto-recursao infinita**, mais um `import { apiFetch }` no topo que colide
// com o nome. Hoje ele DELEGA ao apiFetch compartilhado — que e o que a AUT-18 queria —
// e mantem o que so ele fazia: Content-Type e levantar em nao-2xx.
async function jsonFetch(path: string, opts?: RequestInit) {
  const res = await apiFetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts?.headers ?? {}) },
    ...opts,
  })
  if (!res.ok) {
    const msg = await res.text().catch(() => String(res.status))
    throw new Error(msg)
  }
  if (res.status === 204) return null
  return res.json()
}

/** Scheduler-api client. Note: scheduler-api reads the tenant from X-Tenant-ID. */
export function makeSchedApi(tenantId: string) {
  const th = { 'X-Tenant-ID': tenantId }
  return {
    list: (): Promise<{ agendas: Agenda[]; total: number }> =>
      jsonFetch('/v1/agendas', { headers: th }),
    create: (body: object): Promise<Agenda> =>
      jsonFetch('/v1/agendas', { method: 'POST', headers: th, body: JSON.stringify(body) }),
    update: (id: string, body: object): Promise<Agenda> =>
      jsonFetch(`/v1/agendas/${id}`, { method: 'PATCH', headers: th, body: JSON.stringify(body) }),
    remove: (id: string) =>
      jsonFetch(`/v1/agendas/${id}`, { method: 'DELETE', headers: th }),
    pause:  (id: string) => jsonFetch(`/v1/agendas/${id}/pause`,  { method: 'POST', headers: th }),
    resume: (id: string) => jsonFetch(`/v1/agendas/${id}/resume`, { method: 'POST', headers: th }),
    cancel: (id: string) => jsonFetch(`/v1/agendas/${id}/cancel`, { method: 'POST', headers: th }),
    fire:   (id: string) => jsonFetch(`/v1/agendas/${id}/fire`,   { method: 'POST', headers: th }),
    dispatches: (id: string): Promise<{ dispatches: AgendaDispatch[]; total: number }> =>
      jsonFetch(`/v1/agendas/${id}/dispatches`, { headers: th }),
  }
}

/** Webhook-only pools from agent-registry (hard filter — D3). */
export async function fetchWebhookPools(tenantId: string): Promise<WebhookPool[]> {
  const data = await jsonFetch('/v1/pools', { headers: { 'x-tenant-id': tenantId } })
  const pools = (data?.pools ?? []) as Array<{ pool_id: string; channel_types?: string[] }>
  return pools
    .filter(p => (p.channel_types ?? []).includes('webhook'))
    .map(p => ({ pool_id: p.pool_id, channel_types: p.channel_types ?? [] }))
}

/** Calendars from calendar-api (for the business-day calendar_id dropdown). */
export async function fetchCalendars(tenantId: string): Promise<Array<{ id: string; name: string }>> {
  try {
    const data = await jsonFetch(`/v1/calendars?organization_id=${ORG_ID}&tenant_id=${tenantId}`)
    return (data ?? []).map((c: { id: string; name: string }) => ({ id: c.id, name: c.name }))
  } catch {
    return []
  }
}

// ── datetime-local <-> ISO ──────────────────────────────────────────────────

/** "YYYY-MM-DDTHH:MM" (browser-local) → full ISO in UTC. Empty → undefined. */
export function localToIso(v: string): string | undefined {
  if (!v) return undefined
  const d = new Date(v)
  return isNaN(d.getTime()) ? undefined : d.toISOString()
}

/** ISO → "YYYY-MM-DDTHH:MM" in browser-local (for datetime-local inputs). */
export function isoToLocal(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Short human date-time for cards (browser locale). */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}
