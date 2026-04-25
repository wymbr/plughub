/**
 * calendar-hooks.ts — wraps calendar-api (port 3700)
 *
 * Holiday Sets: GET/POST /v1/holiday-sets, PATCH/DELETE /v1/holiday-sets/{id}
 * Calendars:    GET/POST /v1/calendars,    PATCH/DELETE /v1/calendars/{id}
 */
import { useCallback, useEffect, useState } from 'react'

async function safeJson<T>(res: Response): Promise<T> {
  const ct = res.headers.get('content-type') ?? ''
  if (!ct.includes('application/json') && !ct.includes('text/json')) {
    throw new Error(`API indisponível (HTTP ${res.status})`)
  }
  return res.json() as Promise<T>
}

export interface HolidaySet {
  id:              string
  organization_id: string
  tenant_id:       string | null
  scope:           string
  name:            string
  description:     string
  year:            number | null
  holidays:        Holiday[]
  created_at:      string
  updated_at:      string
}

export interface Holiday {
  date:        string   // YYYY-MM-DD
  name:        string
  description: string
}

export interface CalendarRecord {
  id:              string
  organization_id: string
  tenant_id:       string | null
  scope:           string
  name:            string
  description:     string
  timezone:        string
  weekly_schedule: WeeklySlot[]
  holiday_set_ids: string[]
  exceptions:      CalendarException[]
  created_at:      string
  updated_at:      string
}

export interface WeeklySlot {
  day:        number   // 0=Mon … 6=Sun
  open_time:  string   // HH:MM
  close_time: string   // HH:MM
}

export interface CalendarException {
  date:      string
  open:      boolean
  open_time: string | null
  close_time: string | null
  name:      string
}

// ─── useHolidaySets ───────────────────────────────────────────────────────────

export function useHolidaySets(orgId: string, tenantId: string): {
  sets:    HolidaySet[]
  loading: boolean
  error:   string | null
  reload:  () => void
} {
  const [sets,    setSets]    = useState<HolidaySet[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [tick,    setTick]    = useState(0)

  const reload = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    if (!orgId) return
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ organization_id: orgId })
    if (tenantId) params.set('tenant_id', tenantId)
    fetch(`/v1/holiday-sets?${params.toString()}`)
      .then(r => safeJson<HolidaySet[]>(r).then(j => r.ok ? j : Promise.reject(`HTTP ${r.status}`)))
      .then(j => { setSets(j); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [orgId, tenantId, tick])

  return { sets, loading, error, reload }
}

export async function createHolidaySet(data: {
  organization_id: string
  tenant_id:       string | null
  name:            string
  description:     string
  year:            number | null
  holidays:        Holiday[]
}): Promise<HolidaySet> {
  const res = await fetch('/v1/holiday-sets', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope: 'tenant', ...data }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return safeJson(res)
}

export async function updateHolidaySet(id: string, patch: Partial<HolidaySet>): Promise<HolidaySet> {
  const res = await fetch(`/v1/holiday-sets/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return safeJson(res)
}

export async function deleteHolidaySet(id: string): Promise<void> {
  const res = await fetch(`/v1/holiday-sets/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

// ─── useCalendars ─────────────────────────────────────────────────────────────

export function useCalendars(orgId: string, tenantId: string): {
  calendars: CalendarRecord[]
  loading:   boolean
  error:     string | null
  reload:    () => void
} {
  const [calendars, setCalendars] = useState<CalendarRecord[]>([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)
  const [tick,      setTick]      = useState(0)

  const reload = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    if (!orgId) return
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ organization_id: orgId })
    if (tenantId) params.set('tenant_id', tenantId)
    fetch(`/v1/calendars?${params.toString()}`)
      .then(r => safeJson<CalendarRecord[]>(r).then(j => r.ok ? j : Promise.reject(`HTTP ${r.status}`)))
      .then(j => { setCalendars(j); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [orgId, tenantId, tick])

  return { calendars, loading, error, reload }
}

export async function createCalendar(data: {
  organization_id: string
  tenant_id:       string | null
  name:            string
  description:     string
  timezone:        string
  weekly_schedule: WeeklySlot[]
  holiday_set_ids: string[]
}): Promise<CalendarRecord> {
  const res = await fetch('/v1/calendars', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope: 'tenant', exceptions: [], ...data }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return safeJson(res)
}

export async function updateCalendar(id: string, patch: Partial<CalendarRecord>): Promise<CalendarRecord> {
  const res = await fetch(`/v1/calendars/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return safeJson(res)
}

export async function deleteCalendar(id: string): Promise<void> {
  const res = await fetch(`/v1/calendars/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}
