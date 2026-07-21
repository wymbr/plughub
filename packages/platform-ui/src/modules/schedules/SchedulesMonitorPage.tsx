/**
 * SchedulesMonitorPage
 * Route: /monitor/schedules — live Agendas + dispatch ledger (Scheduler Fase 3).
 *
 * Grant-first (strict ABAC): gated by scheduler.operacao (no admin bypass — D2).
 * Operates the live instances — fire now, pause/resume, cancel — and shows the
 * dispatch ledger with drill-through to the fired session. Recurrence policy is
 * authored in Configuration › Schedules (this view does not duplicate editing).
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  Agenda, AgendaStatus, AgendaDispatch, makeSchedApi, fmtDateTime,
} from './api'

const STATUS_FILTERS: (AgendaStatus | 'all')[] =
  ['all', 'active', 'paused', 'completed', 'expired', 'cancelled']

// ── Local presentational helpers (small; mirror the authoring page) ─────────

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

function describeSchedule(a: Agenda, t: TFunction): string {
  if (a.schedule.mode === 'once') return t('when.onceAt', { when: fmtDateTime(a.schedule.fire_at) })
  const r = a.schedule.rule
  const freq = t(`when.${r.frequency}`)
  const every = r.interval > 1 ? ` (${t('when.everyN', { n: r.interval })})` : ''
  const times = r.times.length ? ` ${t('when.atTimes', { times: r.times.join(', ') })}` : ''
  return `${freq}${every}${times}`
}

function ResultBadge({ result }: { result: AgendaDispatch['result'] }) {
  const { t } = useTranslation('scheduler')
  const styles: Record<string, string> = {
    dispatched: 'bg-green/10 text-green',
    failed:     'bg-red-light text-red-text',
    skipped:    'bg-surface-alt text-muted',
  }
  return <span className={`text-xs px-2 py-0.5 rounded-full ${styles[result]}`}>{t(`monitor.res${result[0].toUpperCase()}${result.slice(1)}`)}</span>
}

function ConfirmModal({ message, confirmLabel, danger, onCancel, onConfirm }: {
  message: string; confirmLabel: string; danger?: boolean; onCancel: () => void; onConfirm: () => void
}) {
  const { t } = useTranslation('scheduler')
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
        <p className="text-sm text-dark mb-4">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
          <button onClick={onConfirm}
            className={`px-4 py-2 text-sm text-white rounded-lg ${danger ? 'bg-red hover:bg-red-text' : 'bg-primary hover:bg-primary-dark'}`}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Dispatch ledger (lazy-loaded on expand) ─────────────────────────────────

function DispatchLedger({ agenda }: { agenda: Agenda }) {
  const { t } = useTranslation('scheduler')
  const { tenantId } = useAuth()
  const api = useMemo(() => makeSchedApi(tenantId), [tenantId])
  const [rows, setRows] = useState<AgendaDispatch[] | null>(null)

  useEffect(() => {
    let alive = true
    api.dispatches(agenda.id).then(r => { if (alive) setRows(r.dispatches ?? []) }).catch(() => { if (alive) setRows([]) })
    return () => { alive = false }
  }, [api, agenda.id])

  if (rows === null) return <div className="py-3 flex justify-center"><Spinner /></div>
  if (rows.length === 0) return <p className="text-xs text-muted-light italic py-2">{t('monitor.noDispatches')}</p>

  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_1fr_auto_1.4fr] gap-2 text-2xs uppercase tracking-wide text-muted-light px-2">
        <span>{t('monitor.dScheduledFor')}</span>
        <span>{t('monitor.dFiredAt')}</span>
        <span>{t('monitor.dResult')}</span>
        <span>{t('monitor.dSession')}</span>
      </div>
      {rows.map(d => (
        <div key={d.id} className="grid grid-cols-[1fr_1fr_auto_1.4fr] gap-2 items-center px-2 py-1.5 bg-white rounded text-xs">
          <span className="text-muted">{fmtDateTime(d.scheduled_for)}</span>
          <span className="text-muted">{fmtDateTime(d.fired_at)}</span>
          <ResultBadge result={d.result} />
          <span className="min-w-0">
            {d.session_id ? (
              <Link to={`/analise/sessions?session_id=${d.session_id}`}
                title={d.session_id}
                className="text-primary hover:text-primary-dark font-mono truncate block">
                {d.session_id.slice(0, 8)}… ↗
              </Link>
            ) : d.error ? (
              <span className="text-red-text truncate block" title={d.error}>{d.error}</span>
            ) : <span className="text-muted-light">—</span>}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Root ────────────────────────────────────────────────────────────────────

type Confirm =
  | { kind: 'fire' | 'cancel'; agenda: Agenda }
  | null

export default function SchedulesMonitorPage() {
  const { t } = useTranslation('scheduler')
  const { session, tenantId, perms } = useAuth()
  const api = useMemo(() => makeSchedApi(tenantId), [tenantId])

  const [agendas, setAgendas] = useState<Agenda[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Confirm>(null)
  const [busy,    setBusy]    = useState<string | null>(null)
  const [search,  setSearch]  = useState('')
  const [statusFilter, setStatusFilter] = useState<AgendaStatus | 'all'>('all')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const r = await api.list()
      setAgendas(r.agendas ?? [])
    } catch (e: unknown) {
      setError(t('errors.loadFailed')); console.error(e)
    } finally { setLoading(false) }
  }, [api, t])

  useEffect(() => { if (perms.can('scheduler', 'operacao')) load() }, [load, perms])

  // Filters (client-side — the list is small; instant). Status chips + name/pool search.
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: agendas.length }
    for (const a of agendas) c[a.status] = (c[a.status] ?? 0) + 1
    return c
  }, [agendas])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return agendas.filter(a =>
      (statusFilter === 'all' || a.status === statusFilter) &&
      (!q || a.name.toLowerCase().includes(q) || a.target_pool_id.toLowerCase().includes(q))
    )
  }, [agendas, statusFilter, search])

  const runOp = async (fn: () => Promise<unknown>, id: string) => {
    setBusy(id)
    try { await fn(); await load() }
    catch (e: unknown) { alert(String(e)) }
    finally { setBusy(null); setConfirm(null) }
  }

  if (!session || !perms.can('scheduler', 'operacao')) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-muted">{t('restricted')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-surface-muted">
      <div className="bg-white flex-shrink-0 px-6 pt-4 pb-3 border-b border-border">
        <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
        <p className="text-sm text-muted mt-0.5">{t('monitor.info')}</p>

        {/* Filters: name/pool search + status chips (client-side) */}
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('monitor.searchPlaceholder')}
            className="flex-1 min-w-[200px] text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          <div className="flex gap-1 flex-wrap">
            {STATUS_FILTERS.map(s => (
              <button key={s} type="button" onClick={() => setStatusFilter(s)}
                className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${statusFilter === s
                  ? 'bg-primary text-white border-primary'
                  : 'bg-white text-muted border-border-strong hover:text-dark'}`}>
                {s === 'all' ? t('monitor.filterAll') : t(`status.${s}`)}
                {counts[s] != null && <span className="ml-1 opacity-70">({counts[s]})</span>}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <p className="text-sm text-red-text">{error}</p>}
        {!loading && agendas.length === 0 && (
          <EmptyState icon="⏰" title={t('monitor.empty')} description={t('info')} />
        )}
        {!loading && agendas.length > 0 && filtered.length === 0 && (
          <p className="text-sm text-muted-light italic text-center py-6">{t('monitor.emptyFiltered')}</p>
        )}

        {!loading && filtered.map(a => {
          const isOpen = expanded === a.id
          const opBusy = busy === a.id
          return (
            <div key={a.id} className="bg-white border border-border rounded-xl overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3">
                <button onClick={() => setExpanded(isOpen ? null : a.id)} className="flex-1 text-left min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold text-dark">{a.name}</span>
                    <StatusPill status={a.status} />
                    <span className="text-xs bg-surface-alt text-muted px-2 py-0.5 rounded-full">🎯 {a.target_pool_id}</span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-muted-light flex-wrap">
                    <span>🕘 {describeSchedule(a, t)}</span>
                    <span>{t('monitor.colNext')}: {fmtDateTime(a.next_fire_at)}</span>
                    <span>{t('monitor.colLast')}: {fmtDateTime(a.last_fired_at)}</span>
                  </div>
                </button>

                <div className="flex gap-1 flex-shrink-0 items-center">
                  {/* Fire now só em agendas VIVAS (active/paused) — não re-dispara terminais. */}
                  {['active', 'paused'].includes(a.status) && (
                    <button disabled={opBusy} onClick={() => setConfirm({ kind: 'fire', agenda: a })}
                      className="px-2.5 py-1.5 text-xs text-primary hover:bg-primary-light rounded-lg disabled:opacity-40">
                      ⚡ {t('actions.fireNow')}
                    </button>
                  )}
                  {a.status === 'active' && (
                    <button disabled={opBusy} onClick={() => runOp(() => api.pause(a.id), a.id)}
                      className="px-2.5 py-1.5 text-xs text-warning-text hover:bg-warning-light rounded-lg disabled:opacity-40">
                      ⏸ {t('actions.pause')}
                    </button>
                  )}
                  {a.status === 'paused' && (
                    <button disabled={opBusy} onClick={() => runOp(() => api.resume(a.id), a.id)}
                      className="px-2.5 py-1.5 text-xs text-green hover:bg-green/10 rounded-lg disabled:opacity-40">
                      ▶ {t('actions.resume')}
                    </button>
                  )}
                  {['active', 'paused'].includes(a.status) && (
                    <button disabled={opBusy} onClick={() => setConfirm({ kind: 'cancel', agenda: a })}
                      className="px-2.5 py-1.5 text-xs text-red hover:bg-red-light rounded-lg disabled:opacity-40">
                      {t('actions.cancelAgenda')}
                    </button>
                  )}
                  <span className="text-border-strong text-sm ml-1">{isOpen ? '▲' : '▼'}</span>
                </div>
              </div>

              {isOpen && (
                <div className="border-t border-border px-4 py-3 bg-surface-muted">
                  <p className="text-xs font-medium text-dark mb-2">{t('monitor.dispatchesTitle', { name: a.name })}</p>
                  <DispatchLedger agenda={a} />
                </div>
              )}
            </div>
          )
        })}
      </div>

      {confirm && (
        <ConfirmModal
          danger={confirm.kind === 'cancel'}
          message={t(confirm.kind === 'fire' ? 'confirm.fire' : 'confirm.cancel', { name: confirm.agenda.name })}
          confirmLabel={t(confirm.kind === 'fire' ? 'actions.fireNow' : 'actions.cancelAgenda')}
          onCancel={() => setConfirm(null)}
          onConfirm={() => runOp(
            () => confirm.kind === 'fire' ? api.fire(confirm.agenda.id) : api.cancel(confirm.agenda.id),
            confirm.agenda.id,
          )}
        />
      )}
    </div>
  )
}
