/**
 * CalendarManager.tsx
 * CRUD de Calendários e Listas de Feriados (calendar-api port 3700).
 *
 * Two sub-tabs:
 *   Calendars     — list/create/edit/delete
 *   Holiday List  — list/create/edit/delete
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useCalendars, useHolidaySets,
  createCalendar, updateCalendar, deleteCalendar,
  createHolidaySet, updateHolidaySet, deleteHolidaySet,
} from '../api/calendar-hooks'
import type { CalendarRecord, HolidaySet, WeeklySlot, Holiday } from '../api/calendar-hooks'

const TIMEZONES = ['America/Sao_Paulo', 'America/Manaus', 'America/Belem', 'America/Fortaleza', 'America/Recife', 'America/Cuiaba', 'UTC']

interface Props {
  orgId:    string
  tenantId: string
}

export function CalendarManager({ orgId, tenantId }: Props) {
  const { t } = useTranslation('configPlataforma')
  const [tab, setTab] = useState<'calendars' | 'holidays'>('calendars')

  return (
    <div className="flex flex-col h-full">
      {/* Sub-tabs */}
      <div className="flex border-b border-border bg-white shrink-0 px-2">
        {(['calendars', 'holidays'] as const).map(id => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 py-3 px-4 text-sm font-medium border-b-2 transition-colors ${
              tab === id
                ? 'border-primary text-primary'
                : 'border-transparent text-muted hover:text-dark'
            }`}
          >
            {id === 'calendars' ? '📅' : '🎉'}{' '}
            {t(id === 'calendars' ? 'calendarManager.tabCalendars' : 'calendarManager.tabHolidaySets')}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-hidden">
        {tab === 'calendars' && <CalendarsList orgId={orgId} tenantId={tenantId} />}
        {tab === 'holidays'  && <HolidaySetsList orgId={orgId} tenantId={tenantId} />}
      </div>
    </div>
  )
}

// ─── Calendars ────────────────────────────────────────────────────────────────

function CalendarsList({ orgId, tenantId }: { orgId: string; tenantId: string }) {
  const { t } = useTranslation('configPlataforma')
  const { calendars, loading, error, reload } = useCalendars(orgId, tenantId)
  const [modal, setModal] = useState<'create' | CalendarRecord | null>(null)

  async function handleDelete(id: string) {
    if (!confirm(t('calendarManager.removeCalendarConfirm'))) return
    await deleteCalendar(id).catch(e => alert(String(e)))
    reload()
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-5 py-3 border-b border-border bg-white shrink-0">
        <span className="text-xs text-muted">{calendars.length}</span>
        {loading && <span className="text-xs text-muted-light">⟳</span>}
        {error   && <span className="text-xs text-red-text">⚠ {error}</span>}
        <button
          onClick={() => setModal('create')}
          className="ml-auto px-3 py-1 text-xs font-semibold rounded bg-primary text-white hover:bg-primary/90 transition-colors"
        >
          {t('calendarManager.addNew')}
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {calendars.map(cal => (
          <div key={cal.id} className="flex items-center gap-3 px-5 py-3.5 border-b border-border hover:bg-surface-muted/50 transition-colors">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-dark truncate">{cal.name}</div>
              <div className="text-xs text-muted mt-0.5">
                {cal.timezone} · {cal.weekly_schedule.length} slots · {cal.holiday_set_ids.length} {t('calendarManager.tabHolidaySets').toLowerCase()}
              </div>
              {cal.description && <div className="text-xs text-muted-light mt-0.5 truncate">{cal.description}</div>}
            </div>
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={() => setModal(cal)}
                className="px-2 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark transition-colors"
              >✏</button>
              <button
                onClick={() => handleDelete(cal.id)}
                className="px-2 py-1 text-xs border border-red/30 rounded text-red hover:bg-red-light transition-colors"
              >✕</button>
            </div>
          </div>
        ))}
        {!loading && calendars.length === 0 && (
          <div className="py-10 text-center text-sm text-muted-light">{t('calendarManager.emptyCalendars')}</div>
        )}
      </div>

      {/* Modal */}
      {modal && (
        <CalendarModal
          initial={modal === 'create' ? null : modal}
          orgId={orgId}
          tenantId={tenantId}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); reload() }}
        />
      )}
    </div>
  )
}

function CalendarModal({ initial, orgId, tenantId, onClose, onSaved }: {
  initial:  CalendarRecord | null
  orgId:    string
  tenantId: string
  onClose:  () => void
  onSaved:  () => void
}) {
  const { t } = useTranslation('configPlataforma')
  const days = t('calendarManager.days', { returnObjects: true }) as string[]
  const [name,        setName]        = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [timezone,    setTimezone]    = useState(initial?.timezone ?? 'America/Sao_Paulo')
  const [slots,       setSlots]       = useState<WeeklySlot[]>(initial?.weekly_schedule ?? [])
  const [error,       setError]       = useState<string | null>(null)
  const [saving,      setSaving]      = useState(false)

  function addSlot() {
    setSlots(prev => [...prev, { day: 0, open_time: '08:00', close_time: '18:00' }])
  }
  function updateSlot(i: number, field: keyof WeeklySlot, value: string | number) {
    setSlots(prev => prev.map((s, idx) => idx === i ? { ...s, [field]: value } : s))
  }
  function removeSlot(i: number) {
    setSlots(prev => prev.filter((_, idx) => idx !== i))
  }

  async function handleSave() {
    if (!name.trim()) { setError(t('calendarManager.nameRequired')); return }
    setSaving(true); setError(null)
    try {
      if (initial) {
        await updateCalendar(initial.id, { name, description, timezone, weekly_schedule: slots })
      } else {
        await createCalendar({ organization_id: orgId, tenant_id: tenantId, name, description, timezone, weekly_schedule: slots, holiday_set_ids: [] })
      }
      onSaved()
    } catch (e) { setError(String(e)) }
    finally { setSaving(false) }
  }

  return (
    <Modal title={initial ? t('calendarManager.editCalendar') : t('calendarManager.newCalendar')} onClose={onClose}>
      <FieldRow label={t('calendarManager.fieldName')}>
        <input className={inputCls} value={name} onChange={e => setName(e.target.value)} placeholder="ex: Horário Comercial SP" />
      </FieldRow>
      <FieldRow label={t('calendarManager.fieldDescription')}>
        <input className={inputCls} value={description} onChange={e => setDescription(e.target.value)} />
      </FieldRow>
      <FieldRow label={t('calendarManager.fieldTimezone')}>
        <select className={inputCls} value={timezone} onChange={e => setTimezone(e.target.value)}>
          {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
        </select>
      </FieldRow>

      {/* Weekly schedule */}
      <div className="mt-4">
        <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-2">
          {t('calendarManager.weeklySchedule')}
        </div>
        {slots.map((slot, i) => (
          <div key={i} className="flex gap-2 mb-1.5 items-center">
            <select className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white text-dark focus:outline-none focus:border-primary w-20 shrink-0"
              value={slot.day} onChange={e => updateSlot(i, 'day', Number(e.target.value))}>
              {days.map((d, idx) => <option key={idx} value={idx}>{d}</option>)}
            </select>
            <input className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white text-dark focus:outline-none focus:border-primary w-24 shrink-0"
              type="time" value={slot.open_time} onChange={e => updateSlot(i, 'open_time', e.target.value)} />
            <span className="text-xs text-muted-light">{t('calendarManager.slotTo')}</span>
            <input className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white text-dark focus:outline-none focus:border-primary w-24 shrink-0"
              type="time" value={slot.close_time} onChange={e => updateSlot(i, 'close_time', e.target.value)} />
            <button className="text-xs border border-red/30 rounded px-2 py-1 text-red hover:bg-red-light transition-colors"
              onClick={() => removeSlot(i)}>✕</button>
          </div>
        ))}
        <button className="mt-1 text-xs text-muted hover:text-dark border border-dashed border-border-strong rounded px-3 py-1 transition-colors"
          onClick={addSlot}>{t('calendarManager.addSlot')}</button>
      </div>

      {error && <p className="mt-3 text-xs text-red-text">⚠ {error}</p>}
      <div className="flex justify-end gap-2 mt-5">
        <button className="px-4 py-1.5 text-xs font-semibold rounded bg-primary text-white hover:bg-primary/90 disabled:opacity-40 transition-colors"
          onClick={handleSave} disabled={saving}>
          {saving ? t('namespace.saving') : t('namespace.save')}
        </button>
        <button className="px-3 py-1.5 text-xs rounded border border-border-strong text-muted hover:text-dark transition-colors"
          onClick={onClose}>{t('namespace.cancel')}</button>
      </div>
    </Modal>
  )
}

// ─── Holiday List ─────────────────────────────────────────────────────────────

function HolidaySetsList({ orgId, tenantId }: { orgId: string; tenantId: string }) {
  const { t } = useTranslation('configPlataforma')
  const { sets, loading, error, reload } = useHolidaySets(orgId, tenantId)
  const [modal, setModal] = useState<'create' | HolidaySet | null>(null)

  async function handleDelete(id: string) {
    if (!confirm(t('calendarManager.removeHolidaySetConfirm'))) return
    await deleteHolidaySet(id).catch(e => alert(String(e)))
    reload()
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-5 py-3 border-b border-border bg-white shrink-0">
        <span className="text-xs text-muted">{sets.length}</span>
        {loading && <span className="text-xs text-muted-light">⟳</span>}
        {error   && <span className="text-xs text-red-text">⚠ {error}</span>}
        <button
          onClick={() => setModal('create')}
          className="ml-auto px-3 py-1 text-xs font-semibold rounded bg-primary text-white hover:bg-primary/90 transition-colors"
        >
          {t('calendarManager.addNew')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {sets.map(set => (
          <div key={set.id} className="flex items-center gap-3 px-5 py-3.5 border-b border-border hover:bg-surface-muted/50 transition-colors">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-dark">
                {set.name}{' '}
                {set.year ? <span className="text-xs text-muted font-normal">({set.year})</span> : null}
              </div>
              <div className="text-xs text-muted mt-0.5">
                {set.holidays.length} {t('calendarManager.holidays').toLowerCase()}
              </div>
              {set.description && <div className="text-xs text-muted-light mt-0.5 truncate">{set.description}</div>}
            </div>
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={() => setModal(set)}
                className="px-2 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark transition-colors"
              >✏</button>
              <button
                onClick={() => handleDelete(set.id)}
                className="px-2 py-1 text-xs border border-red/30 rounded text-red hover:bg-red-light transition-colors"
              >✕</button>
            </div>
          </div>
        ))}
        {!loading && sets.length === 0 && (
          <div className="py-10 text-center text-sm text-muted-light">{t('calendarManager.emptyHolidaySets')}</div>
        )}
      </div>

      {modal && (
        <HolidaySetModal
          initial={modal === 'create' ? null : modal}
          orgId={orgId}
          tenantId={tenantId}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); reload() }}
        />
      )}
    </div>
  )
}

function HolidaySetModal({ initial, orgId, tenantId, onClose, onSaved }: {
  initial:  HolidaySet | null
  orgId:    string
  tenantId: string
  onClose:  () => void
  onSaved:  () => void
}) {
  const { t } = useTranslation('configPlataforma')
  const [name,        setName]        = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [year,        setYear]        = useState<string>(initial?.year ? String(initial.year) : '')
  const [holidays,    setHolidays]    = useState<Holiday[]>(initial?.holidays ?? [])
  const [error,       setError]       = useState<string | null>(null)
  const [saving,      setSaving]      = useState(false)

  function addHoliday() {
    setHolidays(prev => [...prev, { date: '', name: '', description: '' }])
  }
  function updateHoliday(i: number, field: keyof Holiday, value: string) {
    setHolidays(prev => prev.map((h, idx) => idx === i ? { ...h, [field]: value } : h))
  }
  function removeHoliday(i: number) {
    setHolidays(prev => prev.filter((_, idx) => idx !== i))
  }

  async function handleSave() {
    if (!name.trim()) { setError(t('calendarManager.nameRequired')); return }
    const yearNum = year ? parseInt(year) : null
    setSaving(true); setError(null)
    try {
      if (initial) {
        await updateHolidaySet(initial.id, { name, description, year: yearNum, holidays })
      } else {
        await createHolidaySet({ organization_id: orgId, tenant_id: tenantId, name, description, year: yearNum, holidays })
      }
      onSaved()
    } catch (e) { setError(String(e)) }
    finally { setSaving(false) }
  }

  return (
    <Modal title={initial ? t('calendarManager.editHolidaySet') : t('calendarManager.newHolidaySet')} onClose={onClose}>
      <FieldRow label={t('calendarManager.fieldName')}>
        <input className={inputCls} value={name} onChange={e => setName(e.target.value)} placeholder="ex: Feriados Nacionais 2026" />
      </FieldRow>
      <FieldRow label={t('calendarManager.fieldDescription')}>
        <input className={inputCls} value={description} onChange={e => setDescription(e.target.value)} />
      </FieldRow>
      <FieldRow label={t('calendarManager.fieldYear')}>
        <input className={`${inputCls} w-24`} type="number" value={year} onChange={e => setYear(e.target.value)} placeholder="ex: 2026" />
      </FieldRow>

      {/* Holidays */}
      <div className="mt-4">
        <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-2">
          {t('calendarManager.holidays')}
        </div>
        {holidays.map((h, i) => (
          <div key={i} className="flex gap-2 mb-1.5 items-center">
            <input className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white text-dark focus:outline-none focus:border-primary w-32 shrink-0"
              type="date" value={h.date} onChange={e => updateHoliday(i, 'date', e.target.value)} />
            <input className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white text-dark focus:outline-none focus:border-primary flex-1"
              type="text" value={h.name} onChange={e => updateHoliday(i, 'name', e.target.value)}
              placeholder={t('calendarManager.holidayNamePlaceholder')} />
            <input className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white text-dark focus:outline-none focus:border-primary flex-1"
              type="text" value={h.description} onChange={e => updateHoliday(i, 'description', e.target.value)}
              placeholder={t('calendarManager.holidayDescPlaceholder')} />
            <button className="text-xs border border-red/30 rounded px-2 py-1 text-red hover:bg-red-light transition-colors"
              onClick={() => removeHoliday(i)}>✕</button>
          </div>
        ))}
        <button className="mt-1 text-xs text-muted hover:text-dark border border-dashed border-border-strong rounded px-3 py-1 transition-colors"
          onClick={addHoliday}>{t('calendarManager.addHoliday')}</button>
      </div>

      {error && <p className="mt-3 text-xs text-red-text">⚠ {error}</p>}
      <div className="flex justify-end gap-2 mt-5">
        <button className="px-4 py-1.5 text-xs font-semibold rounded bg-primary text-white hover:bg-primary/90 disabled:opacity-40 transition-colors"
          onClick={handleSave} disabled={saving}>
          {saving ? t('namespace.saving') : t('namespace.save')}
        </button>
        <button className="px-3 py-1.5 text-xs rounded border border-border-strong text-muted hover:text-dark transition-colors"
          onClick={onClose}>{t('namespace.cancel')}</button>
      </div>
    </Modal>
  )
}

// ─── Shared components ────────────────────────────────────────────────────────

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl p-6 w-[540px] max-w-[95vw]">
        <div className="flex justify-between items-center mb-5">
          <h3 className="text-base font-bold text-dark">{title}</h3>
          <button className="text-muted hover:text-dark transition-colors text-lg leading-none" onClick={onClose}>✕</button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {children}
        </div>
      </div>
    </div>
  )
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="block text-xs font-semibold text-muted mb-1">{label}</label>
      {children}
    </div>
  )
}

// ─── Shared style ─────────────────────────────────────────────────────────────

const inputCls = 'w-full px-2.5 py-1.5 text-xs border border-border-strong rounded bg-white text-dark focus:outline-none focus:border-primary'
