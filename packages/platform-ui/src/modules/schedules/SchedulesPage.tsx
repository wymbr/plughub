/**
 * SchedulesPage
 * Route: /config/schedules — authoring of Agendas (Scheduler Fase 3).
 *
 * Grant-first (strict ABAC): gated by scheduler.configurar (no admin bypass — D2).
 * List of schedules + create/edit drawer. Target pool is filtered to webhook pools
 * (D3). Recurrence editor (frequency / weekdays / monthly / times / business-day
 * policy) + validity + calendar + payload (JSON). Backend: scheduler-api via /v1/agendas.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  Agenda, DayOfWeek, Frequency, BusinessDayPolicy, MonthOverflow, MisfirePolicy,
  WebhookPool, makeSchedApi, fetchWebhookPools, fetchCalendars,
  localToIso, isoToLocal, fmtDateTime,
} from './api'

const TIMEZONES = [
  'America/Sao_Paulo', 'America/Manaus', 'America/Fortaleza', 'America/Recife',
  'America/Cuiaba', 'America/Porto_Velho', 'America/Rio_Branco', 'UTC',
]
const WEEKDAYS: DayOfWeek[] = [
  'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
]

// ── Small shared modals (same style as CalendarsPage) ───────────────────────

function Modal({ title, onClose, children }: {
  title: string; onClose: () => void; children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-base font-semibold text-dark">{title}</h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted text-xl leading-none">×</button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

function ConfirmModal({ message, confirmLabel, onCancel, onConfirm }: {
  message: string; confirmLabel: string; onCancel: () => void; onConfirm: () => void
}) {
  const { t } = useTranslation('scheduler')
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
        <p className="text-sm text-dark mb-4">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm bg-red text-white rounded-lg hover:bg-red-text">{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

// ── Status pill ─────────────────────────────────────────────────────────────

function StatusPill({ status }: { status: Agenda['status'] }) {
  const { t } = useTranslation('scheduler')
  const styles: Record<string, string> = {
    active:    'bg-green/10 text-green',
    paused:    'bg-warning-light text-warning-text',
    completed: 'bg-surface-alt text-muted',
    expired:   'bg-surface-alt text-muted',
    cancelled: 'bg-red-light text-red-text',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${styles[status] ?? 'bg-surface-alt text-muted'}`}>
      {t(`status.${status}`)}
    </span>
  )
}

// ── Human-readable schedule summary for the card ────────────────────────────

function describeSchedule(a: Agenda, t: TFunction): string {
  if (a.schedule.mode === 'once') {
    return t('when.onceAt', { when: fmtDateTime(a.schedule.fire_at) })
  }
  const r = a.schedule.rule
  const freq = t(`when.${r.frequency}`)
  const every = r.interval > 1 ? ` (${t('when.everyN', { n: r.interval })})` : ''
  const times = r.times.length ? ` ${t('when.atTimes', { times: r.times.join(', ') })}` : ''
  return `${freq}${every}${times}`
}

export default function SchedulesPage() {
  const { t } = useTranslation('scheduler')
  const { session, tenantId, perms } = useAuth()
  const api = useMemo(() => makeSchedApi(tenantId), [tenantId])

  const [agendas,  setAgendas]  = useState<Agenda[]>([])
  const [pools,    setPools]    = useState<WebhookPool[]>([])
  const [calendars, setCalendars] = useState<Array<{ id: string; name: string }>>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editing,  setEditing]  = useState<Agenda | null>(null)
  const [delTarget, setDelTarget] = useState<Agenda | null>(null)
  const [saving,   setSaving]   = useState(false)

  // ── Form state ──
  const [fName,   setFName]   = useState('')
  const [fPool,   setFPool]   = useState('')
  const [fMode,   setFMode]   = useState<'once' | 'recurring'>('once')
  const [fFireAt, setFFireAt] = useState('')
  const [fFreq,   setFFreq]   = useState<Frequency>('daily')
  const [fInterval, setFInterval] = useState(1)
  const [fWeekdays, setFWeekdays] = useState<DayOfWeek[]>(['monday'])
  const [fMonthKind, setFMonthKind] = useState<'by_date' | 'by_position'>('by_date')
  const [fMonthDays, setFMonthDays] = useState('1')
  const [fNth,    setFNth]    = useState<string>('1')       // "1".."5" | "last"
  const [fNthWeekday, setFNthWeekday] = useState<DayOfWeek>('friday')
  const [fTimes,  setFTimes]  = useState<string[]>(['09:00'])
  const [fBizPolicy, setFBizPolicy] = useState<BusinessDayPolicy>('ignore')
  const [fOverflow, setFOverflow] = useState<MonthOverflow>('clamp')
  const [fStartsAt, setFStartsAt] = useState('')
  const [fEndsAt, setFEndsAt] = useState('')
  const [fTz,     setFTz]     = useState('America/Sao_Paulo')
  const [fCalendar, setFCalendar] = useState('')
  const [fMisfire, setFMisfire] = useState<MisfirePolicy>('skip')
  const [fPayload, setFPayload] = useState('{}')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [ags, wp, cals] = await Promise.all([
        api.list(),
        fetchWebhookPools(tenantId).catch(() => []),
        fetchCalendars(tenantId).catch(() => []),
      ])
      setAgendas(ags.agendas ?? [])
      setPools(wp)
      setCalendars(cals)
    } catch (e: unknown) {
      setError(t('errors.loadFailed'))
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [api, tenantId, t])

  useEffect(() => { if (perms.can('scheduler', 'configurar')) load() }, [load, perms])

  const resetForm = () => {
    setFName(''); setFPool(pools[0]?.pool_id ?? ''); setFMode('once'); setFFireAt('')
    setFFreq('daily'); setFInterval(1); setFWeekdays(['monday'])
    setFMonthKind('by_date'); setFMonthDays('1'); setFNth('1'); setFNthWeekday('friday')
    setFTimes(['09:00']); setFBizPolicy('ignore'); setFOverflow('clamp')
    setFStartsAt(''); setFEndsAt(''); setFTz('America/Sao_Paulo'); setFCalendar('')
    setFMisfire('skip'); setFPayload('{}')
  }

  const openCreate = () => { setEditing(null); resetForm(); setShowForm(true) }

  const openEdit = (a: Agenda) => {
    setEditing(a)
    setFName(a.name); setFPool(a.target_pool_id); setFTz(a.timezone ?? 'America/Sao_Paulo')
    setFCalendar(a.calendar_id ?? ''); setFMisfire(a.misfire_policy ?? 'skip')
    setFPayload(JSON.stringify(a.payload ?? {}, null, 2))
    setFStartsAt(isoToLocal(a.validity?.starts_at)); setFEndsAt(isoToLocal(a.validity?.ends_at))
    if (a.schedule.mode === 'once') {
      setFMode('once'); setFFireAt(isoToLocal(a.schedule.fire_at))
    } else {
      const r = a.schedule.rule
      setFMode('recurring'); setFFreq(r.frequency); setFInterval(r.interval ?? 1)
      setFWeekdays(r.weekdays ?? ['monday'])
      setFTimes(r.times?.length ? r.times : ['09:00'])
      setFBizPolicy(r.business_day_policy ?? 'ignore'); setFOverflow(r.month_overflow ?? 'clamp')
      if (r.month_by?.kind === 'by_position') {
        setFMonthKind('by_position'); setFNth(String(r.month_by.nth)); setFNthWeekday(r.month_by.weekday)
      } else if (r.month_by?.kind === 'by_date') {
        setFMonthKind('by_date'); setFMonthDays(r.month_by.days.map(String).join(', '))
      }
    }
    setShowForm(true)
  }

  const toggleWeekday = (d: DayOfWeek) =>
    setFWeekdays(prev => prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d])
  const setTime = (i: number, v: string) => setFTimes(prev => prev.map((x, idx) => idx === i ? v : x))
  const addTime = () => setFTimes(prev => [...prev, '12:00'])
  const removeTime = (i: number) => setFTimes(prev => prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev)

  const buildSchedule = () => {
    if (fMode === 'once') {
      return { mode: 'once', fire_at: localToIso(fFireAt) }
    }
    const rule: Record<string, unknown> = {
      frequency: fFreq,
      interval:  Math.max(1, fInterval),
      times:     fTimes,
      business_day_policy: fBizPolicy,
      month_overflow: fOverflow,
    }
    if (fFreq === 'weekly') rule.weekdays = fWeekdays
    if (fFreq === 'monthly') {
      if (fMonthKind === 'by_date') {
        const days = fMonthDays.split(',').map(s => s.trim()).filter(Boolean)
          .map(s => s.toLowerCase() === 'last' ? 'last' : parseInt(s, 10))
          .filter(v => v === 'last' || (typeof v === 'number' && v >= 1 && v <= 31))
        rule.month_by = { kind: 'by_date', days: days.length ? days : [1] }
      } else {
        rule.month_by = {
          kind: 'by_position',
          nth: fNth === 'last' ? 'last' : parseInt(fNth, 10),
          weekday: fNthWeekday,
        }
      }
    }
    return { mode: 'recurring', rule }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(fPayload || '{}')
    } catch {
      alert(t('form.payloadInvalid'))
      return
    }
    setSaving(true)
    try {
      const body = {
        name: fName,
        target_pool_id: fPool,
        payload,
        timezone: fTz,
        calendar_id: fCalendar || null,
        validity: {
          starts_at: localToIso(fStartsAt) ?? new Date().toISOString(),
          ends_at:   localToIso(fEndsAt) ?? null,
        },
        schedule: buildSchedule(),
        misfire_policy: fMisfire,
      }
      if (editing) await api.update(editing.id, body)
      else         await api.create(body)
      setShowForm(false)
      load()
    } catch (e: unknown) {
      alert(`${t('errors.saveFailed')}: ${String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = async () => {
    if (!delTarget) return
    try { await api.remove(delTarget.id); setDelTarget(null); load() }
    catch (e: unknown) { alert(String(e)) }
  }

  // ── Access guard (grant-first, no admin bypass — D2) ──
  if (!session || !perms.can('scheduler', 'configurar')) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-muted">{t('restricted')}</p>
      </div>
    )
  }

  const poolName = (id: string) => id

  return (
    <div className="flex flex-col h-full bg-surface-muted">
      <div className="bg-white flex-shrink-0 px-6 pt-4 pb-3 border-b border-border">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
            <p className="text-sm text-muted mt-0.5">{t('info')}</p>
          </div>
          <button onClick={openCreate}
            className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors">
            + {t('new')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <p className="text-sm text-red-text">{error}</p>}

        {!loading && agendas.length === 0 && (
          <EmptyState icon="⏰" title={t('empty.title')} description={t('empty.desc')} />
        )}

        {!loading && agendas.map(a => (
          <div key={a.id}
            className="flex items-start gap-3 px-4 py-3 bg-white border border-border rounded-xl hover:border-primary/30 transition-colors">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-semibold text-dark">{a.name}</p>
                <StatusPill status={a.status} />
              </div>
              <div className="flex items-center gap-3 mt-1.5 flex-wrap text-xs">
                <span className="bg-surface-alt text-muted px-2 py-0.5 rounded-full">🎯 {poolName(a.target_pool_id)}</span>
                <span className="bg-primary-light text-primary px-2 py-0.5 rounded-full">🕘 {describeSchedule(a, t)}</span>
                <span className="text-muted-light">{t('card.nextFire')}: {fmtDateTime(a.next_fire_at)}</span>
              </div>
            </div>
            <div className="flex gap-1 flex-shrink-0">
              <button onClick={() => openEdit(a)}
                className="px-3 py-1.5 text-xs text-primary hover:bg-primary-light rounded-lg transition-colors">{t('actions.edit')}</button>
              <button onClick={() => setDelTarget(a)}
                className="px-3 py-1.5 text-xs text-red hover:bg-red-light rounded-lg transition-colors">{t('actions.delete')}</button>
            </div>
          </div>
        ))}
      </div>

      {showForm && (
        <Modal title={editing ? `${t('actions.edit')} — ${editing.name}` : t('new')} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            {/* Name */}
            <Field label={t('form.name')}>
              <input required value={fName} onChange={e => setFName(e.target.value)}
                placeholder={t('form.namePlaceholder')} className={inputCls} />
            </Field>

            {/* Target pool (webhook only) */}
            <Field label={t('form.targetPool')} hint={t('form.targetPoolHint')}>
              {pools.length === 0 ? (
                <p className="text-xs text-warning-text">{t('form.noWebhookPools')}</p>
              ) : (
                <select required value={fPool} onChange={e => setFPool(e.target.value)} className={inputCls}>
                  <option value="" disabled>—</option>
                  {pools.map(p => <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>)}
                </select>
              )}
            </Field>

            {/* Mode */}
            <Field label={t('form.mode')}>
              <div className="flex gap-2">
                {(['once', 'recurring'] as const).map(m => (
                  <button key={m} type="button" onClick={() => setFMode(m)}
                    className={`px-3 py-1.5 text-sm rounded-lg border ${fMode === m
                      ? 'bg-primary text-white border-primary'
                      : 'bg-white text-muted border-border-strong hover:text-dark'}`}>
                    {t(m === 'once' ? 'form.modeOnce' : 'form.modeRecurring')}
                  </button>
                ))}
              </div>
            </Field>

            {fMode === 'once' && (
              <Field label={t('form.fireAt')}>
                <input type="datetime-local" required value={fFireAt}
                  onChange={e => setFFireAt(e.target.value)} className={inputCls} />
              </Field>
            )}

            {fMode === 'recurring' && (
              <div className="space-y-4 border border-border rounded-lg p-3 bg-surface-muted/40">
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t('form.frequency')}>
                    <select value={fFreq} onChange={e => setFFreq(e.target.value as Frequency)} className={inputCls}>
                      <option value="daily">{t('form.freqDaily')}</option>
                      <option value="weekly">{t('form.freqWeekly')}</option>
                      <option value="monthly">{t('form.freqMonthly')}</option>
                    </select>
                  </Field>
                  <Field label={t('form.interval')}>
                    <div className="flex items-center gap-2">
                      <input type="number" min={1} value={fInterval}
                        onChange={e => setFInterval(parseInt(e.target.value || '1', 10))}
                        className={`${inputCls} w-20`} />
                      <span className="text-xs text-muted">
                        {t(fFreq === 'daily' ? 'form.intervalDay' : fFreq === 'weekly' ? 'form.intervalWeek' : 'form.intervalMonth')}
                      </span>
                    </div>
                  </Field>
                </div>

                {fFreq === 'weekly' && (
                  <Field label={t('form.weekdays')}>
                    <div className="flex gap-1 flex-wrap">
                      {WEEKDAYS.map(d => (
                        <button key={d} type="button" onClick={() => toggleWeekday(d)}
                          className={`px-2 py-1 text-xs rounded border ${fWeekdays.includes(d)
                            ? 'bg-primary text-white border-primary'
                            : 'bg-white text-muted border-border-strong'}`}>
                          {t(`days.${d}`)}
                        </button>
                      ))}
                    </div>
                  </Field>
                )}

                {fFreq === 'monthly' && (
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      {(['by_date', 'by_position'] as const).map(k => (
                        <button key={k} type="button" onClick={() => setFMonthKind(k)}
                          className={`px-3 py-1 text-xs rounded-lg border ${fMonthKind === k
                            ? 'bg-primary text-white border-primary'
                            : 'bg-white text-muted border-border-strong'}`}>
                          {t(k === 'by_date' ? 'form.monthByDate' : 'form.monthByPosition')}
                        </button>
                      ))}
                    </div>
                    {fMonthKind === 'by_date' ? (
                      <Field label={t('form.monthDays')}>
                        <input value={fMonthDays} onChange={e => setFMonthDays(e.target.value)}
                          placeholder="1, 15, last" className={inputCls} />
                      </Field>
                    ) : (
                      <div className="grid grid-cols-2 gap-3">
                        <Field label={t('form.nth')}>
                          <select value={fNth} onChange={e => setFNth(e.target.value)} className={inputCls}>
                            {['1', '2', '3', '4', '5'].map(n => <option key={n} value={n}>{n}</option>)}
                            <option value="last">{t('form.nthLast')}</option>
                          </select>
                        </Field>
                        <Field label={t('form.weekday')}>
                          <select value={fNthWeekday} onChange={e => setFNthWeekday(e.target.value as DayOfWeek)} className={inputCls}>
                            {WEEKDAYS.map(d => <option key={d} value={d}>{t(`days.${d}`)}</option>)}
                          </select>
                        </Field>
                      </div>
                    )}
                  </div>
                )}

                <Field label={t('form.times')}>
                  <div className="space-y-1">
                    {fTimes.map((tm, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <input type="time" value={tm} onChange={e => setTime(i, e.target.value)}
                          className={`${inputCls} w-32`} />
                        {fTimes.length > 1 && (
                          <button type="button" onClick={() => removeTime(i)}
                            className="text-muted-light hover:text-red text-sm">×</button>
                        )}
                      </div>
                    ))}
                    <button type="button" onClick={addTime} className="text-xs text-primary hover:text-primary-dark">{t('form.addTime')}</button>
                  </div>
                </Field>

                <div className="grid grid-cols-2 gap-3">
                  <Field label={t('form.businessDayPolicy')}>
                    <select value={fBizPolicy} onChange={e => setFBizPolicy(e.target.value as BusinessDayPolicy)} className={inputCls}>
                      <option value="ignore">{t('form.policyIgnore')}</option>
                      <option value="only_business_days">{t('form.policyOnly')}</option>
                      <option value="shift_next">{t('form.policyShiftNext')}</option>
                      <option value="shift_previous">{t('form.policyShiftPrevious')}</option>
                    </select>
                  </Field>
                  {fFreq === 'monthly' && (
                    <Field label={t('form.monthOverflow')}>
                      <select value={fOverflow} onChange={e => setFOverflow(e.target.value as MonthOverflow)} className={inputCls}>
                        <option value="clamp">{t('form.overflowClamp')}</option>
                        <option value="skip">{t('form.overflowSkip')}</option>
                      </select>
                    </Field>
                  )}
                </div>
              </div>
            )}

            {/* Validity + tz + calendar */}
            <div className="grid grid-cols-2 gap-3">
              <Field label={t('form.validityStart')}>
                <input type="datetime-local" value={fStartsAt} onChange={e => setFStartsAt(e.target.value)} className={inputCls} />
              </Field>
              <Field label={t('form.validityEnd')}>
                <input type="datetime-local" value={fEndsAt} onChange={e => setFEndsAt(e.target.value)} className={inputCls} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t('form.timezone')}>
                <select value={fTz} onChange={e => setFTz(e.target.value)} className={inputCls}>
                  {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
                </select>
              </Field>
              <Field label={t('form.calendar')}>
                <select value={fCalendar} onChange={e => setFCalendar(e.target.value)} className={inputCls}>
                  <option value="">{t('form.calendarNone')}</option>
                  {calendars.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </Field>
            </div>

            <Field label={t('form.misfire')}>
              <select value={fMisfire} onChange={e => setFMisfire(e.target.value as MisfirePolicy)} className={inputCls}>
                <option value="skip">{t('form.misfireSkip')}</option>
                <option value="fire_late">{t('form.misfireFireLate')}</option>
                <option value="fire_all_missed">{t('form.misfireFireAll')}</option>
              </select>
            </Field>

            <Field label={t('form.payload')} hint={t('form.payloadHint')}>
              <textarea value={fPayload} onChange={e => setFPayload(e.target.value)} rows={4}
                spellCheck={false} className={`${inputCls} font-mono text-xs`} />
            </Field>

            <div className="flex gap-2 justify-end pt-2 border-t border-border">
              <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
              <button type="submit" disabled={saving || pools.length === 0}
                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50">
                {saving ? t('actions.saving') : (editing ? t('actions.save') : t('actions.create'))}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {delTarget && (
        <ConfirmModal
          message={t('confirm.delete', { name: delTarget.name })}
          confirmLabel={t('actions.delete')}
          onCancel={() => setDelTarget(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  )
}

// ── tiny presentational helpers ─────────────────────────────────────────────

const inputCls = 'w-full text-sm border border-border-strong rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white'

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-dark mb-1">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted-light mt-1">{hint}</p>}
    </div>
  )
}
