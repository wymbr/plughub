/**
 * CalendarsPage
 * Route: /config/calendars (role: admin)
 *
 * Three tabs:
 *   Calendários  — CRUD de calendários com horários semanais e feriados vinculados
 *   Feriados     — CRUD de conjuntos de feriados
 *   Associações  — Vincula um calendário a um pool / tenant / canal / workflow
 *
 * Backend: calendar-api (port 3700) proxied via Vite under /v1/
 */

import React, { useCallback, useEffect, useState } from 'react' // useCallback used inside tabs
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

// ── Constants ──────────────────────────────────────────────────────────────────

const ORG_ID  = import.meta.env.VITE_CALENDAR_ORG_ID ?? 'org-default'

const DAY_LABELS: Record<string, string> = {
  monday:    'Seg',
  tuesday:   'Ter',
  wednesday: 'Qua',
  thursday:  'Qui',
  friday:    'Sex',
  saturday:  'Sáb',
  sunday:    'Dom',
}
const DAYS = Object.keys(DAY_LABELS)

const TIMEZONES = [
  'America/Sao_Paulo',
  'America/Manaus',
  'America/Belem',
  'America/Recife',
  'America/Fortaleza',
  'America/Noronha',
  'America/Campo_Grande',
  'America/Porto_Velho',
  'America/Boa_Vista',
  'America/Rio_Branco',
  'America/Araguaina',
  'UTC',
]

// ── Types ──────────────────────────────────────────────────────────────────────

/** A single open/close interval within a day */
interface TimeInterval {
  open:  string   // "HH:MM"
  close: string   // "HH:MM"
}

/** One entry in weekly_schedule — matches the engine format exactly */
interface WeeklyDaySchedule {
  day:   string          // "monday" | "tuesday" | …
  open:  boolean         // false → closed all day
  slots: TimeInterval[]  // empty if open=false; multiple = split day (e.g. lunch break)
}

interface CalendarObj {
  id:              string
  name:            string
  description:     string
  timezone:        string
  always_open:     boolean
  weekly_schedule: WeeklyDaySchedule[]
  holiday_set_ids: string[]
  exceptions:      unknown[]
  created_at:      string
  updated_at:      string
}

interface HolidayEntry {
  date: string
  name: string
}

interface HolidaySet {
  id:          string
  name:        string
  description: string
  year:        number | null
  holidays:    HolidayEntry[]
  created_at:  string
  updated_at:  string
}


// ── Fetch helpers ──────────────────────────────────────────────────────────────

async function apiFetch(path: string, opts?: RequestInit) {
  const res = await fetch(path, {
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

function makeCalApi(tenantId: string) {
  return {
    // Holiday sets
    listHolidaySets: () =>
      apiFetch(`/v1/holiday-sets?organization_id=${ORG_ID}&tenant_id=${tenantId}`),
    createHolidaySet: (body: object) =>
      apiFetch('/v1/holiday-sets', { method: 'POST', body: JSON.stringify(body) }),
    updateHolidaySet: (id: string, body: object) =>
      apiFetch(`/v1/holiday-sets/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteHolidaySet: (id: string) =>
      apiFetch(`/v1/holiday-sets/${id}`, { method: 'DELETE' }),

    // Calendars
    listCalendars: () =>
      apiFetch(`/v1/calendars?organization_id=${ORG_ID}&tenant_id=${tenantId}`),
    createCalendar: (body: object) =>
      apiFetch('/v1/calendars', { method: 'POST', body: JSON.stringify(body) }),
    updateCalendar: (id: string, body: object) =>
      apiFetch(`/v1/calendars/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteCalendar: (id: string) =>
      apiFetch(`/v1/calendars/${id}`, { method: 'DELETE' }),

  }
}

// ── Shared components ─────────────────────────────────────────────────────────


function Modal({
  title, onClose, children
}: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

function ConfirmDelete({
  label, onCancel, onConfirm
}: { label: string; onCancel: () => void; onConfirm: () => void }) {
  const { t } = useTranslation('calendars')
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
        <p className="text-sm text-gray-700 mb-4">
          {t('messages.deleteConfirm', { label })}
        </p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">{t('calendar.cancel')}</button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700">{t('calendar.delete')}</button>
        </div>
      </div>
    </div>
  )
}

// ── Weekly Schedule editor ────────────────────────────────────────────────────

interface WeeklyEditorProps {
  schedule: WeeklyDaySchedule[]
  onChange: (s: WeeklyDaySchedule[]) => void
}

function WeeklyEditor({ schedule, onChange }: WeeklyEditorProps) {
  const { t } = useTranslation('calendars')

  const dayEntry = (day: string): WeeklyDaySchedule | undefined =>
    schedule.find(s => s.day === day)

  /** Toggle a day on/off */
  const toggle = (day: string) => {
    const existing = dayEntry(day)
    if (existing) {
      // remove day entirely
      onChange(schedule.filter(s => s.day !== day))
    } else {
      // add day with a single default slot
      onChange([...schedule, { day, open: true, slots: [{ open: '08:00', close: '18:00' }] }])
    }
  }

  /** Update a specific slot field within a day */
  const updateSlot = (day: string, idx: number, field: 'open' | 'close', val: string) => {
    onChange(schedule.map(s => {
      if (s.day !== day) return s
      const newSlots = s.slots.map((sl, i) => i === idx ? { ...sl, [field]: val } : sl)
      return { ...s, slots: newSlots }
    }))
  }

  /** Add a new interval to a day */
  const addInterval = (day: string) => {
    onChange(schedule.map(s => {
      if (s.day !== day) return s
      const last = s.slots[s.slots.length - 1]
      // default: new interval starts 1h after last close
      const newOpen  = last?.close ?? '18:00'
      const newClose = '23:00'
      return { ...s, slots: [...s.slots, { open: newOpen, close: newClose }] }
    }))
  }

  /** Remove an interval from a day (keep at least 1) */
  const removeInterval = (day: string, idx: number) => {
    onChange(schedule.map(s => {
      if (s.day !== day) return s
      const newSlots = s.slots.filter((_, i) => i !== idx)
      // if all slots removed, fall back to a single default slot rather than empty
      return { ...s, slots: newSlots.length > 0 ? newSlots : [{ open: '08:00', close: '18:00' }] }
    }))
  }

  return (
    <div className="space-y-1">
      {DAYS.map(day => {
        const entry  = dayEntry(day)
        const active = !!entry
        return (
          <div key={day} className={`rounded-lg border ${active ? 'bg-indigo-50 border-indigo-200' : 'bg-gray-50 border-transparent'}`}>
            {/* Day header row */}
            <div className="flex items-center gap-3 px-3 py-2">
              <button
                type="button"
                onClick={() => toggle(day)}
                className={`w-5 h-5 rounded border-2 flex-shrink-0 flex items-center justify-center
                  ${active ? 'bg-indigo-500 border-indigo-500 text-white' : 'border-gray-300'}`}
              >
                {active && <span className="text-xs">✓</span>}
              </button>
              <span className={`w-8 text-sm font-medium ${active ? 'text-gray-800' : 'text-gray-400'}`}>
                {DAY_LABELS[day]}
              </span>
              {!active && (
                <span className="text-xs text-gray-400 italic">{t('calendar.closed')}</span>
              )}
              {active && entry.slots.length < 4 && (
                <button
                  type="button"
                  onClick={() => addInterval(day)}
                  title="Adicionar intervalo"
                  className="ml-auto text-xs text-indigo-500 hover:text-indigo-700 px-2 py-0.5 rounded hover:bg-indigo-100 transition-colors"
                >
                  + intervalo
                </button>
              )}
            </div>

            {/* Time intervals */}
            {active && entry.slots.map((sl, idx) => (
              <div key={idx} className="flex items-center gap-2 px-3 pb-2 pl-10">
                <input
                  type="time"
                  value={sl.open}
                  onChange={e => updateSlot(day, idx, 'open', e.target.value)}
                  className="text-sm border border-gray-300 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 bg-white"
                />
                <span className="text-xs text-gray-400">{t('messages.until')}</span>
                <input
                  type="time"
                  value={sl.close}
                  onChange={e => updateSlot(day, idx, 'close', e.target.value)}
                  className="text-sm border border-gray-300 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 bg-white"
                />
                {entry.slots.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeInterval(day, idx)}
                    title="Remover intervalo"
                    className="text-gray-400 hover:text-red-500 text-sm leading-none transition-colors"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

// ── Holidays editor ───────────────────────────────────────────────────────────

interface HolidaysEditorProps {
  holidays: HolidayEntry[]
  onChange: (h: HolidayEntry[]) => void
}

function HolidaysEditor({ holidays, onChange }: HolidaysEditorProps) {
  const { t } = useTranslation('calendars')
  const [newDate, setNewDate] = useState('')
  const [newName, setNewName] = useState('')

  const add = () => {
    if (!newDate || !newName.trim()) return
    onChange([...holidays, { date: newDate, name: newName.trim() }])
    setNewDate('')
    setNewName('')
  }

  const remove = (i: number) => onChange(holidays.filter((_, idx) => idx !== i))

  return (
    <div className="space-y-3">
      <div className="max-h-40 overflow-y-auto space-y-1">
        {holidays.length === 0 && (
          <p className="text-xs text-gray-400 italic">{t('messages.noHolidaysAdded')}</p>
        )}
        {holidays.map((h, i) => (
          <div key={i} className="flex items-center gap-2 px-2 py-1 bg-gray-50 rounded text-sm">
            <span className="text-gray-500 font-mono text-xs w-20 flex-shrink-0">{h.date}</span>
            <span className="flex-1 text-gray-800 truncate">{h.name}</span>
            <button onClick={() => remove(i)} className="text-red-400 hover:text-red-600 text-xs flex-shrink-0">✕</button>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="date"
          value={newDate}
          onChange={e => setNewDate(e.target.value)}
          className="text-sm border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-indigo-400"
        />
        <input
          type="text"
          placeholder={t('holidaySet.holidayNamePlaceholder')}
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && add()}
          className="flex-1 text-sm border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-indigo-400"
        />
        <button
          type="button"
          onClick={add}
          className="px-3 py-1 text-sm bg-indigo-500 text-white rounded hover:bg-indigo-600"
        >
          +
        </button>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Tab: Calendários
// ═══════════════════════════════════════════════════════════════════════════════

interface CalendarsTabProps { holidaySets: HolidaySet[] }

function CalendarsTab({ holidaySets }: CalendarsTabProps) {
  const { t } = useTranslation('calendars')
  const { tenantId } = useAuth()
  const calApi = makeCalApi(tenantId)
  const [calendars, setCalendars] = useState<CalendarObj[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [showForm,  setShowForm]  = useState(false)
  const [editing,   setEditing]   = useState<CalendarObj | null>(null)
  const [delTarget, setDelTarget] = useState<CalendarObj | null>(null)
  const [saving,    setSaving]    = useState(false)

  // Form state
  const [fName,       setFName]       = useState('')
  const [fDesc,       setFDesc]       = useState('')
  const [fTz,         setFTz]         = useState('America/Sao_Paulo')
  const [fAlwaysOpen, setFAlwaysOpen] = useState(false)
  const [fSched,      setFSched]      = useState<WeeklyDaySchedule[]>([])
  const [fHsIds,      setFHsIds]      = useState<string[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await calApi.listCalendars()
      setCalendars(data ?? [])
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    setFName(''); setFDesc(''); setFTz('America/Sao_Paulo')
    setFAlwaysOpen(false); setFSched([]); setFHsIds([])
    setShowForm(true)
  }

  const openEdit = (c: CalendarObj) => {
    setEditing(c)
    setFName(c.name); setFDesc(c.description); setFTz(c.timezone)
    setFAlwaysOpen(c.always_open ?? false)
    setFSched(c.weekly_schedule); setFHsIds(c.holiday_set_ids)
    setShowForm(true)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      const body = {
        organization_id: ORG_ID,
        tenant_id:       tenantId,
        name:            fName,
        description:     fDesc,
        timezone:        fTz,
        always_open:     fAlwaysOpen,
        weekly_schedule: fAlwaysOpen ? [] : fSched,
        holiday_set_ids: fHsIds,
        exceptions:      editing?.exceptions ?? [],
      }
      if (editing) {
        await calApi.updateCalendar(editing.id, body)
      } else {
        await calApi.createCalendar(body)
      }
      setShowForm(false)
      load()
    } catch (e: unknown) {
      alert(String(e))
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = async () => {
    if (!delTarget) return
    try {
      await calApi.deleteCalendar(delTarget.id)
      setDelTarget(null)
      load()
    } catch (e: unknown) {
      alert(String(e))
    }
  }

  const toggleHolidaySet = (id: string) => {
    setFHsIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  const scheduleLabel = (c: CalendarObj) => {
    if (c.always_open) return '24×7'
    const active = c.weekly_schedule
      .filter(s => s.open)
      .map(s => {
        const label = DAY_LABELS[s.day] ?? s.day
        if (s.slots.length > 1) return `${label}(${s.slots.length})`
        return label
      })
      .join(', ')
    return active || t('calendar.noSchedule')
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          {t('calendar.info')}
        </p>
        <button
          onClick={openCreate}
          className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 transition-colors"
        >
          + {t('calendar.new')}
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {error   && <p className="text-sm text-red-600">{error}</p>}

      {!loading && calendars.length === 0 && (
        <EmptyState
          icon="📅"
          title={t('calendar.noItems')}
          description={t('calendar.noItemsDesc')}
        />
      )}

      {!loading && calendars.length > 0 && (
        <div className="space-y-2">
          {calendars.map(c => (
            <div key={c.id}
              className="flex items-start gap-3 px-4 py-3 bg-white border border-gray-200 rounded-xl hover:border-indigo-200 transition-colors">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-gray-800">{c.name}</p>
                {c.description && <p className="text-xs text-gray-500 mt-0.5">{c.description}</p>}
                <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                  <span className="text-[11px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                    🌍 {c.timezone}
                  </span>
                  {c.always_open ? (
                    <span className="text-[11px] bg-indigo-600 text-white px-2 py-0.5 rounded-full font-medium">
                      ⚡ 24×7
                    </span>
                  ) : (
                    <span className="text-[11px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full truncate max-w-xs">
                      📅 {scheduleLabel(c)}
                    </span>
                  )}
                  {c.holiday_set_ids.length > 0 && (
                    <span className="text-[11px] bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full">
                      🏖️ {c.holiday_set_ids.length} conj. feriados
                    </span>
                  )}
                </div>
              </div>
              <div className="flex gap-1 flex-shrink-0">
                <button onClick={() => openEdit(c)}
                  className="px-3 py-1.5 text-xs text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors">
                  {t('calendar.edit')}
                </button>
                <button onClick={() => setDelTarget(c)}
                  className="px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 rounded-lg transition-colors">
                  {t('calendar.delete')}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <Modal title={editing ? t('calendar.editTitle', { name: editing.name }) : t('calendar.new')} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">{t('calendar.name')}</label>
              <input
                required
                value={fName}
                onChange={e => setFName(e.target.value)}
                placeholder={t('calendar.namePlaceholder')}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">{t('calendar.description')}</label>
              <input
                value={fDesc}
                onChange={e => setFDesc(e.target.value)}
                placeholder={t('calendar.descPlaceholder')}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">{t('calendar.timezone')}</label>
              <select
                value={fTz}
                onChange={e => setFTz(e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
              </select>
            </div>

            {/* 24x7 toggle */}
            <div className="flex items-center justify-between px-3 py-2.5 bg-indigo-50 rounded-lg border border-indigo-100">
              <div>
                <p className="text-sm font-medium text-indigo-800">Funcionamento 24×7</p>
                <p className="text-xs text-indigo-600 mt-0.5">
                  Sempre aberto — feriados e exceções ainda se aplicam
                </p>
              </div>
              <button
                type="button"
                onClick={() => setFAlwaysOpen(v => !v)}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full transition-colors duration-200 ease-in-out focus:outline-none ${
                  fAlwaysOpen ? 'bg-indigo-600' : 'bg-gray-300'
                }`}
              >
                <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out mt-0.5 ${
                  fAlwaysOpen ? 'translate-x-5' : 'translate-x-0.5'
                }`} />
              </button>
            </div>

            {!fAlwaysOpen && (
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-2">{t('calendar.schedule')}</label>
                <WeeklyEditor schedule={fSched} onChange={setFSched} />
              </div>
            )}

            {holidaySets.length > 0 && (
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-2">{t('calendar.holidaySets')}</label>
                <div className="space-y-1 max-h-36 overflow-y-auto">
                  {holidaySets.map(hs => (
                    <label key={hs.id} className="flex items-center gap-2 cursor-pointer px-2 py-1 rounded hover:bg-gray-50">
                      <input
                        type="checkbox"
                        checked={fHsIds.includes(hs.id)}
                        onChange={() => toggleHolidaySet(hs.id)}
                        className="rounded border-gray-300 text-indigo-500 focus:ring-indigo-400"
                      />
                      <span className="text-sm text-gray-700">{hs.name}</span>
                      {hs.year && <span className="text-xs text-gray-400">({hs.year})</span>}
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-2 justify-end pt-2 border-t border-gray-100">
              <button type="button" onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
                {t('calendar.cancel')}
              </button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 disabled:opacity-50">
                {saving ? t('calendar.saving') : (editing ? t('calendar.save') : t('calendar.create'))}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {delTarget && (
        <ConfirmDelete
          label={delTarget.name}
          onCancel={() => setDelTarget(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Tab: Feriados
// ═══════════════════════════════════════════════════════════════════════════════

interface HolidaysTabProps { onSetsChange: (sets: HolidaySet[]) => void }

function HolidaysTab({ onSetsChange }: HolidaysTabProps) {
  const { t } = useTranslation('calendars')
  const { tenantId } = useAuth()
  const calApi = makeCalApi(tenantId)
  const [sets,      setSets]      = useState<HolidaySet[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [expanded,  setExpanded]  = useState<string | null>(null)
  const [showForm,  setShowForm]  = useState(false)
  const [editing,   setEditing]   = useState<HolidaySet | null>(null)
  const [delTarget, setDelTarget] = useState<HolidaySet | null>(null)
  const [saving,    setSaving]    = useState(false)

  // Form state
  const [fName,     setFName]     = useState('')
  const [fDesc,     setFDesc]     = useState('')
  const [fYear,     setFYear]     = useState<string>('')
  const [fHols,     setFHols]     = useState<HolidayEntry[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await calApi.listHolidaySets()
      setSets(data ?? [])
      onSetsChange(data ?? [])
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [onSetsChange])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    setFName(''); setFDesc(''); setFYear(String(new Date().getFullYear())); setFHols([])
    setShowForm(true)
  }

  const openEdit = (hs: HolidaySet) => {
    setEditing(hs)
    setFName(hs.name); setFDesc(hs.description)
    setFYear(hs.year != null ? String(hs.year) : '')
    setFHols([...(hs.holidays ?? [])])
    setShowForm(true)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      const body = {
        organization_id: ORG_ID,
        tenant_id:       tenantId,
        name:            fName,
        description:     fDesc,
        year:            fYear ? parseInt(fYear, 10) : null,
        holidays:        fHols,
      }
      if (editing) {
        await calApi.updateHolidaySet(editing.id, body)
      } else {
        await calApi.createHolidaySet(body)
      }
      setShowForm(false)
      load()
    } catch (e: unknown) {
      alert(String(e))
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = async () => {
    if (!delTarget) return
    try {
      await calApi.deleteHolidaySet(delTarget.id)
      setDelTarget(null)
      load()
    } catch (e: unknown) {
      alert(String(e))
    }
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          {t('holidaySet.info')}
        </p>
        <button
          onClick={openCreate}
          className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 transition-colors"
        >
          + {t('holidaySet.new')}
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {error   && <p className="text-sm text-red-600">{error}</p>}

      {!loading && sets.length === 0 && (
        <EmptyState
          icon="🏖️"
          title={t('holidaySet.noItems')}
          description={t('holidaySet.noItemsDesc')}
        />
      )}

      {!loading && sets.length > 0 && (
        <div className="space-y-2">
          {sets.map(hs => (
            <div key={hs.id} className="bg-white border border-gray-200 rounded-xl overflow-hidden hover:border-amber-200 transition-colors">
              <div className="flex items-center gap-3 px-4 py-3">
                <button
                  onClick={() => setExpanded(expanded === hs.id ? null : hs.id)}
                  className="flex-1 text-left min-w-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-gray-800">{hs.name}</span>
                    {hs.year && (
                      <span className="text-[11px] bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full">
                        {hs.year}
                      </span>
                    )}
                    <span className="text-[11px] text-gray-400">
                      {t('holidaySet.holidayCount', { count: (hs.holidays ?? []).length })}
                    </span>
                  </div>
                  {hs.description && (
                    <p className="text-xs text-gray-500 mt-0.5 text-left">{hs.description}</p>
                  )}
                </button>
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => openEdit(hs)}
                    className="px-3 py-1.5 text-xs text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors">
                    {t('calendar.edit')}
                  </button>
                  <button onClick={() => setDelTarget(hs)}
                    className="px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 rounded-lg transition-colors">
                    {t('calendar.delete')}
                  </button>
                  <span className="text-gray-300 text-sm self-center">
                    {expanded === hs.id ? '▲' : '▼'}
                  </span>
                </div>
              </div>

              {expanded === hs.id && (
                <div className="border-t border-gray-100 px-4 py-3 bg-gray-50">
                  {(hs.holidays ?? []).length === 0 ? (
                    <p className="text-xs text-gray-400 italic">{t('holidaySet.noHolidays')}</p>
                  ) : (
                    <div className="space-y-1">
                      {(hs.holidays ?? []).map((h, i) => (
                        <div key={i} className="flex items-center gap-3 text-sm">
                          <span className="font-mono text-xs text-gray-500 w-24 flex-shrink-0">{h.date}</span>
                          <span className="text-gray-700">{h.name}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <Modal title={editing ? t('holidaySet.editTitle', { name: editing.name }) : t('holidaySet.new')} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-700 mb-1">{t('holidaySet.name')}</label>
                <input
                  required
                  value={fName}
                  onChange={e => setFName(e.target.value)}
                  placeholder={t('holidaySet.namePlaceholder')}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">{t('holidaySet.year')}</label>
                <input
                  type="number"
                  value={fYear}
                  onChange={e => setFYear(e.target.value)}
                  placeholder={t('holidaySet.yearPlaceholder')}
                  min="2000" max="2100"
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">{t('holidaySet.description')}</label>
              <input
                value={fDesc}
                onChange={e => setFDesc(e.target.value)}
                placeholder={t('holidaySet.descPlaceholder')}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-2">{t('holidaySet.holidays')}</label>
              <HolidaysEditor holidays={fHols} onChange={setFHols} />
            </div>
            <div className="flex gap-2 justify-end pt-2 border-t border-gray-100">
              <button type="button" onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
                {t('calendar.cancel')}
              </button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 disabled:opacity-50">
                {saving ? t('calendar.saving') : (editing ? t('calendar.save') : t('calendar.create'))}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {delTarget && (
        <ConfirmDelete
          label={delTarget.name}
          onCancel={() => setDelTarget(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Root page
// ═══════════════════════════════════════════════════════════════════════════════

type CalTab = 'calendars' | 'holiday-sets'

export default function CalendarsPage() {
  const { t } = useTranslation('calendars')
  const { session, tenantId } = useAuth()
  const calApi = makeCalApi(tenantId)
  const [tab, setTab]             = useState<CalTab>('calendars')
  const [holidaySets, setHolidaySets] = useState<HolidaySet[]>([])

  const TABS: { id: CalTab; label: string }[] = [
    { id: 'calendars',    label: t('tabs.calendars')   },
    { id: 'holiday-sets', label: t('tabs.holidaySets') },
  ]

  useEffect(() => {
    calApi.listHolidaySets().then(d => setHolidaySets(d ?? [])).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId])

  if (!session || session.role === 'business') {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-gray-500">{t('association.restricted')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Page header */}
      <div className="bg-white flex-shrink-0">
        <div className="px-6 pt-4 pb-1">
          <h1 className="text-lg font-semibold text-gray-800">{t('title')}</h1>
        </div>
        <div className="flex border-b border-gray-200 px-4">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors
                ${tab === id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-700'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {tab === 'calendars' && (
          <CalendarsTab holidaySets={holidaySets} />
        )}
        {tab === 'holiday-sets' && (
          <HolidaysTab onSetsChange={setHolidaySets} />
        )}
      </div>
    </div>
  )
}
