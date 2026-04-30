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

import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

// ── Constants ──────────────────────────────────────────────────────────────────

const ORG_ID  = import.meta.env.VITE_CALENDAR_ORG_ID ?? 'org-default'
const TENANT  = import.meta.env.VITE_TENANT_ID       ?? 'tenant_demo'

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

const ENTITY_TYPES = ['pool', 'tenant', 'channel', 'workflow'] as const
type EntityType = typeof ENTITY_TYPES[number]

const ENTITY_LABELS: Record<EntityType, string> = {
  pool:     'Pool',
  tenant:   'Tenant',
  channel:  'Canal',
  workflow: 'Workflow',
}

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

interface WeeklySlot {
  day_of_week: string
  start_time:  string
  end_time:    string
}

interface CalendarObj {
  id:              string
  name:            string
  description:     string
  timezone:        string
  weekly_schedule: WeeklySlot[]
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

interface Association {
  id:          string
  tenant_id:   string
  entity_type: string
  entity_id:   string
  calendar_id: string
  operator:    'UNION' | 'INTERSECTION'
  priority:    number
  created_at:  string
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

const calApi = {
  // Holiday sets
  listHolidaySets: () =>
    apiFetch(`/v1/holiday-sets?organization_id=${ORG_ID}&tenant_id=${TENANT}`),
  createHolidaySet: (body: object) =>
    apiFetch('/v1/holiday-sets', { method: 'POST', body: JSON.stringify(body) }),
  updateHolidaySet: (id: string, body: object) =>
    apiFetch(`/v1/holiday-sets/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteHolidaySet: (id: string) =>
    apiFetch(`/v1/holiday-sets/${id}`, { method: 'DELETE' }),

  // Calendars
  listCalendars: () =>
    apiFetch(`/v1/calendars?organization_id=${ORG_ID}&tenant_id=${TENANT}`),
  createCalendar: (body: object) =>
    apiFetch('/v1/calendars', { method: 'POST', body: JSON.stringify(body) }),
  updateCalendar: (id: string, body: object) =>
    apiFetch(`/v1/calendars/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteCalendar: (id: string) =>
    apiFetch(`/v1/calendars/${id}`, { method: 'DELETE' }),

  // Associations
  listAssociations: (entityType: string, entityId: string) =>
    apiFetch(`/v1/associations?tenant_id=${TENANT}&entity_type=${entityType}&entity_id=${entityId}`),
  createAssociation: (body: object) =>
    apiFetch('/v1/associations', { method: 'POST', body: JSON.stringify(body) }),
  deleteAssociation: (id: string) =>
    apiFetch(`/v1/associations/${id}`, { method: 'DELETE' }),
}

// ── Shared components ─────────────────────────────────────────────────────────

function TabBar({
  tabs, active, onChange
}: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div className="flex border-b border-gray-200 bg-white px-4">
      {tabs.map(tab => (
        <button
          key={tab}
          onClick={() => onChange(tab)}
          className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors
            ${active === tab
              ? 'border-primary text-primary'
              : 'border-transparent text-gray-500 hover:text-gray-700'}`}
        >
          {tab}
        </button>
      ))}
    </div>
  )
}

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
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
        <p className="text-sm text-gray-700 mb-4">
          Excluir <strong>{label}</strong>? Esta ação não pode ser desfeita.
        </p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancelar</button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700">Excluir</button>
        </div>
      </div>
    </div>
  )
}

// ── Weekly Schedule editor ────────────────────────────────────────────────────

interface WeeklyEditorProps {
  schedule: WeeklySlot[]
  onChange: (s: WeeklySlot[]) => void
}

function WeeklyEditor({ schedule, onChange }: WeeklyEditorProps) {
  const slotFor = (day: string) => schedule.find(s => s.day_of_week === day)

  const toggle = (day: string) => {
    if (slotFor(day)) {
      onChange(schedule.filter(s => s.day_of_week !== day))
    } else {
      onChange([...schedule, { day_of_week: day, start_time: '08:00', end_time: '18:00' }])
    }
  }

  const update = (day: string, field: 'start_time' | 'end_time', val: string) => {
    onChange(schedule.map(s => s.day_of_week === day ? { ...s, [field]: val } : s))
  }

  return (
    <div className="space-y-1">
      {DAYS.map(day => {
        const slot = slotFor(day)
        const active = !!slot
        return (
          <div key={day} className={`flex items-center gap-3 px-3 py-2 rounded-lg
            ${active ? 'bg-indigo-50 border border-indigo-200' : 'bg-gray-50'}`}>
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
            {active ? (
              <>
                <input
                  type="time"
                  value={slot.start_time}
                  onChange={e => update(day, 'start_time', e.target.value)}
                  className="text-sm border border-gray-300 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                />
                <span className="text-xs text-gray-400">até</span>
                <input
                  type="time"
                  value={slot.end_time}
                  onChange={e => update(day, 'end_time', e.target.value)}
                  className="text-sm border border-gray-300 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                />
              </>
            ) : (
              <span className="text-xs text-gray-400 italic">Fechado</span>
            )}
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
          <p className="text-xs text-gray-400 italic">Nenhum feriado adicionado</p>
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
          placeholder="Nome do feriado"
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
  const [calendars, setCalendars] = useState<CalendarObj[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [showForm,  setShowForm]  = useState(false)
  const [editing,   setEditing]   = useState<CalendarObj | null>(null)
  const [delTarget, setDelTarget] = useState<CalendarObj | null>(null)
  const [saving,    setSaving]    = useState(false)

  // Form state
  const [fName,     setFName]     = useState('')
  const [fDesc,     setFDesc]     = useState('')
  const [fTz,       setFTz]       = useState('America/Sao_Paulo')
  const [fSched,    setFSched]    = useState<WeeklySlot[]>([])
  const [fHsIds,    setFHsIds]    = useState<string[]>([])

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
    setFName(''); setFDesc(''); setFTz('America/Sao_Paulo'); setFSched([]); setFHsIds([])
    setShowForm(true)
  }

  const openEdit = (c: CalendarObj) => {
    setEditing(c)
    setFName(c.name); setFDesc(c.description); setFTz(c.timezone)
    setFSched(c.weekly_schedule); setFHsIds(c.holiday_set_ids)
    setShowForm(true)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      const body = {
        organization_id: ORG_ID,
        tenant_id:       TENANT,
        name:            fName,
        description:     fDesc,
        timezone:        fTz,
        weekly_schedule: fSched,
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
    const active = c.weekly_schedule.map(s => DAY_LABELS[s.day_of_week]).join(', ')
    return active || 'Sem horário definido'
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          Defina calendários com horários semanais e feriados vinculados.
        </p>
        <button
          onClick={openCreate}
          className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 transition-colors"
        >
          + Novo Calendário
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {error   && <p className="text-sm text-red-600">{error}</p>}

      {!loading && calendars.length === 0 && (
        <EmptyState
          icon="📅"
          title="Nenhum calendário"
          description="Crie o primeiro calendário para definir horários de atendimento."
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
                  <span className="text-[11px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full truncate max-w-xs">
                    📅 {scheduleLabel(c)}
                  </span>
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
                  Editar
                </button>
                <button onClick={() => setDelTarget(c)}
                  className="px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 rounded-lg transition-colors">
                  Excluir
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <Modal title={editing ? `Editar: ${editing.name}` : 'Novo Calendário'} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Nome *</label>
              <input
                required
                value={fName}
                onChange={e => setFName(e.target.value)}
                placeholder="ex: Atendimento Comercial"
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Descrição</label>
              <input
                value={fDesc}
                onChange={e => setFDesc(e.target.value)}
                placeholder="Opcional"
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Fuso horário</label>
              <select
                value={fTz}
                onChange={e => setFTz(e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-700 mb-2">Horários semanais</label>
              <WeeklyEditor schedule={fSched} onChange={setFSched} />
            </div>

            {holidaySets.length > 0 && (
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-2">Conjuntos de feriados</label>
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
                Cancelar
              </button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 disabled:opacity-50">
                {saving ? 'Salvando…' : (editing ? 'Salvar' : 'Criar')}
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
        tenant_id:       TENANT,
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
          Conjuntos de datas de feriado vinculáveis a calendários.
        </p>
        <button
          onClick={openCreate}
          className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 transition-colors"
        >
          + Novo Conjunto
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {error   && <p className="text-sm text-red-600">{error}</p>}

      {!loading && sets.length === 0 && (
        <EmptyState
          icon="🏖️"
          title="Nenhum conjunto de feriados"
          description="Crie um conjunto para marcar os feriados do seu calendário."
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
                      {(hs.holidays ?? []).length} feriados
                    </span>
                  </div>
                  {hs.description && (
                    <p className="text-xs text-gray-500 mt-0.5 text-left">{hs.description}</p>
                  )}
                </button>
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => openEdit(hs)}
                    className="px-3 py-1.5 text-xs text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors">
                    Editar
                  </button>
                  <button onClick={() => setDelTarget(hs)}
                    className="px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 rounded-lg transition-colors">
                    Excluir
                  </button>
                  <span className="text-gray-300 text-sm self-center">
                    {expanded === hs.id ? '▲' : '▼'}
                  </span>
                </div>
              </div>

              {expanded === hs.id && (
                <div className="border-t border-gray-100 px-4 py-3 bg-gray-50">
                  {(hs.holidays ?? []).length === 0 ? (
                    <p className="text-xs text-gray-400 italic">Nenhum feriado neste conjunto.</p>
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
        <Modal title={editing ? `Editar: ${editing.name}` : 'Novo Conjunto de Feriados'} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-700 mb-1">Nome *</label>
                <input
                  required
                  value={fName}
                  onChange={e => setFName(e.target.value)}
                  placeholder="ex: Feriados Nacionais 2025"
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">Ano</label>
                <input
                  type="number"
                  value={fYear}
                  onChange={e => setFYear(e.target.value)}
                  placeholder="2025"
                  min="2000" max="2100"
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Descrição</label>
              <input
                value={fDesc}
                onChange={e => setFDesc(e.target.value)}
                placeholder="Opcional"
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-2">Feriados</label>
              <HolidaysEditor holidays={fHols} onChange={setFHols} />
            </div>
            <div className="flex gap-2 justify-end pt-2 border-t border-gray-100">
              <button type="button" onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
                Cancelar
              </button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-800 disabled:opacity-50">
                {saving ? 'Salvando…' : (editing ? 'Salvar' : 'Criar')}
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
// Tab: Associações
// ═══════════════════════════════════════════════════════════════════════════════

interface AssociationsTabProps { calendars: CalendarObj[] }

function AssociationsTab({ calendars }: AssociationsTabProps) {
  const [entityType, setEntityType] = useState<EntityType>('pool')
  const [entityId,   setEntityId]   = useState('')
  const [assocs,     setAssocs]     = useState<Association[]>([])
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState('')

  // New assoc form
  const [fCalId,   setFCalId]   = useState('')
  const [fOp,      setFOp]      = useState<'UNION' | 'INTERSECTION'>('UNION')
  const [fPriority, setFPriority] = useState('1')
  const [saving,   setSaving]   = useState(false)
  const [delId,    setDelId]    = useState<string | null>(null)

  const search = async () => {
    if (!entityId.trim()) return
    setLoading(true)
    setError('')
    try {
      const data = await calApi.listAssociations(entityType, entityId.trim())
      setAssocs(data ?? [])
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const addAssoc = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!fCalId || !entityId.trim()) return
    setSaving(true)
    try {
      await calApi.createAssociation({
        tenant_id:   TENANT,
        entity_type: entityType,
        entity_id:   entityId.trim(),
        calendar_id: fCalId,
        operator:    fOp,
        priority:    parseInt(fPriority, 10) || 1,
      })
      await search()
      setFCalId(''); setFOp('UNION'); setFPriority('1')
    } catch (e: unknown) {
      alert(String(e))
    } finally {
      setSaving(false)
    }
  }

  const removeAssoc = async (id: string) => {
    try {
      await calApi.deleteAssociation(id)
      await search()
    } catch (e: unknown) {
      alert(String(e))
    } finally {
      setDelId(null)
    }
  }

  const calName = (id: string) => calendars.find(c => c.id === id)?.name ?? id.slice(0, 8)

  return (
    <div className="p-4 space-y-4">
      <p className="text-sm text-gray-500">
        Associe um calendário a um pool, tenant, canal ou workflow para definir horários de operação.
      </p>

      {/* Lookup */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
        <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Buscar associações</p>
        <div className="flex gap-3">
          <select
            value={entityType}
            onChange={e => setEntityType(e.target.value as EntityType)}
            className="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            {ENTITY_TYPES.map(et => (
              <option key={et} value={et}>{ENTITY_LABELS[et]}</option>
            ))}
          </select>
          <input
            type="text"
            value={entityId}
            onChange={e => setEntityId(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            placeholder={`ID do ${ENTITY_LABELS[entityType].toLowerCase()}…`}
            className="flex-1 text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
          <button
            onClick={search}
            disabled={!entityId.trim() || loading}
            className="px-4 py-2 text-sm bg-gray-700 text-white rounded-lg hover:bg-gray-900 disabled:opacity-50"
          >
            {loading ? '…' : 'Buscar'}
          </button>
        </div>
        {error && <p className="text-xs text-red-600">{error}</p>}
      </div>

      {/* Results */}
      {assocs.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-2 border-b border-gray-100 bg-gray-50">
            {assocs.length} associação{assocs.length !== 1 ? 'ões' : ''} encontrada{assocs.length !== 1 ? 's' : ''}
          </p>
          <div className="divide-y divide-gray-100">
            {assocs.map(a => (
              <div key={a.id} className="flex items-center gap-3 px-4 py-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">
                    📅 {calName(a.calendar_id)}
                  </p>
                  <div className="flex gap-2 mt-0.5">
                    <span className={`text-[11px] px-1.5 py-0.5 rounded font-medium
                      ${a.operator === 'UNION' ? 'bg-green-50 text-green-700' : 'bg-purple-50 text-purple-700'}`}>
                      {a.operator}
                    </span>
                    <span className="text-[11px] text-gray-400">prioridade {a.priority}</span>
                  </div>
                </div>
                <button
                  onClick={() => setDelId(a.id)}
                  className="px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 rounded-lg transition-colors flex-shrink-0"
                >
                  Remover
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {assocs.length === 0 && entityId && !loading && (
        <p className="text-sm text-gray-400 text-center py-4 italic">
          Nenhuma associação encontrada para este ID.
        </p>
      )}

      {/* Add form */}
      {entityId.trim() && calendars.length > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-4 space-y-3">
          <p className="text-xs font-semibold text-indigo-700 uppercase tracking-wide">Adicionar associação</p>
          <form onSubmit={addAssoc} className="flex gap-3 flex-wrap items-end">
            <div className="flex-1 min-w-40">
              <label className="block text-xs text-indigo-700 mb-1">Calendário</label>
              <select
                required
                value={fCalId}
                onChange={e => setFCalId(e.target.value)}
                className="w-full text-sm border border-indigo-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
              >
                <option value="">Selecione…</option>
                {calendars.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-indigo-700 mb-1">Operador</label>
              <select
                value={fOp}
                onChange={e => setFOp(e.target.value as 'UNION' | 'INTERSECTION')}
                className="text-sm border border-indigo-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
              >
                <option value="UNION">UNION (OR)</option>
                <option value="INTERSECTION">INTERSECTION (AND)</option>
              </select>
            </div>
            <div className="w-24">
              <label className="block text-xs text-indigo-700 mb-1">Prioridade</label>
              <input
                type="number"
                min="1" max="100"
                value={fPriority}
                onChange={e => setFPriority(e.target.value)}
                className="w-full text-sm border border-indigo-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
              />
            </div>
            <button
              type="submit"
              disabled={saving || !fCalId}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? '…' : '+ Associar'}
            </button>
          </form>
          <p className="text-[11px] text-indigo-600">
            💡 UNION: aberto se qualquer calendário estiver aberto. INTERSECTION: aberto somente se todos estiverem abertos.
          </p>
        </div>
      )}

      {delId && (
        <ConfirmDelete
          label="esta associação"
          onCancel={() => setDelId(null)}
          onConfirm={() => removeAssoc(delId)}
        />
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Root page
// ═══════════════════════════════════════════════════════════════════════════════

const TABS = ['Calendários', 'Feriados', 'Associações']

export default function CalendarsPage() {
  const { session } = useAuth()
  const [tab, setTab]             = useState('Calendários')
  const [calendars, setCalendars] = useState<CalendarObj[]>([])
  const [holidaySets, setHolidaySets] = useState<HolidaySet[]>([])

  // Re-fetch calendars whenever the Calendários tab is active — lifted so
  // AssociationsTab can reference the full list for the calendar name lookup.
  const handleCalendarsLoad = useCallback(async () => {
    try {
      const data = await calApi.listCalendars()
      setCalendars(data ?? [])
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    handleCalendarsLoad()
    calApi.listHolidaySets().then(d => setHolidaySets(d ?? [])).catch(() => {})
  }, [handleCalendarsLoad])

  if (!session || session.role === 'business') {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-gray-500">Acesso restrito a administradores.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Page header */}
      <div className="px-6 py-4 bg-white border-b border-gray-200">
        <h1 className="text-lg font-semibold text-gray-800">Calendários</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Gerencie horários de operação, feriados e associações por pool ou canal.
        </p>
      </div>

      <TabBar tabs={TABS} active={tab} onChange={setTab} />

      <div className="flex-1 overflow-y-auto">
        {tab === 'Calendários' && (
          <CalendarsTab holidaySets={holidaySets} />
        )}
        {tab === 'Feriados' && (
          <HolidaysTab onSetsChange={setHolidaySets} />
        )}
        {tab === 'Associações' && (
          <AssociationsTab calendars={calendars} />
        )}
      </div>
    </div>
  )
}
