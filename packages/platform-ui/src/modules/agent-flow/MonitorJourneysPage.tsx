/**
 * MonitorJourneysPage — /monitor/journeys  (Arc 18 A2)
 *
 * Operational journey view moved from ProcessosPage/JourneysTab.
 * Shows live KPI strip, L1 journey-type chip filter, pool dropdown,
 * journey list + detail panel with Merge and Split actions.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { GitMerge, Scissors, X, Map } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { Link } from 'react-router-dom'
import { useJourneys, useJourney } from '@/modules/workflows/api/hooks'
import type { Journey, JourneyStatus, JourneyKpi } from '@/modules/workflows/api/hooks'
import type { JourneyType, Pool } from '@/types'
import * as registryApi from '@/api/registry'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDt(ts: string | null | undefined, locale?: string) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(locale)
}

function fmtDuration(ms: number | null | undefined) {
  if (!ms || ms === 0) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}min`
  return `${(ms / 3_600_000).toFixed(1)}h`
}

// ── Journey status colours ────────────────────────────────────────────────────

const JOURNEY_STATUS_COLORS: Record<JourneyStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  cancelled: '#6b7280',
}

// ── KPI card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }: {
  label: string; value: string | number; sub?: string; color?: string
}) {
  return (
    <div className="bg-white border border-border rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[130px]">
      <span className="text-xs text-muted-light uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold leading-none" style={{ color: color ?? '#1e293b' }}>
        {value}
      </span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── KPI strip aggregated from kpis[] ─────────────────────────────────────────

function KpiStrip({ kpis, journeys }: { kpis: JourneyKpi[]; journeys: Journey[] }) {
  const { t, i18n } = useTranslation('contacts')
  const totalJourneys  = journeys.length
  const activeCount    = journeys.filter(j => j.status === 'active').length
  const suspendedCount = journeys.filter(j => j.status === 'suspended').length
  const completedCount = journeys.filter(j => j.status === 'completed').length

  const wResolution = kpis.length > 0
    ? kpis.reduce((s, k) => s + k.resolution_rate * k.total_journeys, 0) /
      Math.max(kpis.reduce((s, k) => s + k.total_journeys, 0), 1)
    : null
  const wP50 = kpis.length > 0
    ? kpis.filter(k => k.median_duration_ms !== null)
        .reduce((s, k) => s + (k.median_duration_ms ?? 0), 0) /
      Math.max(kpis.filter(k => k.median_duration_ms !== null).length, 1)
    : null

  return (
    <div className="flex gap-3 px-5 py-3 flex-shrink-0 flex-wrap">
      <KpiCard
        label={t('processes.journeys.kpiTotal')}
        value={totalJourneys.toLocaleString(i18n.language)}
      />
      <KpiCard
        label={t('processes.journeyStatus.active')}
        value={activeCount.toLocaleString(i18n.language)}
        color="#3b82f6"
      />
      <KpiCard
        label={t('processes.journeyStatus.suspended')}
        value={suspendedCount.toLocaleString(i18n.language)}
        color="#eab308"
      />
      <KpiCard
        label={t('processes.journeyStatus.completed')}
        value={completedCount.toLocaleString(i18n.language)}
        color="#22c55e"
      />
      <KpiCard
        label={t('processes.journeys.kpiResolution')}
        value={wResolution !== null ? `${(wResolution * 100).toFixed(1)}%` : '—'}
        color={wResolution !== null && wResolution >= 0.8 ? '#059669'
             : wResolution !== null && wResolution >= 0.6 ? '#1B4F8A' : '#D97706'}
      />
      <KpiCard
        label={t('processes.journeys.kpiP50')}
        value={fmtDuration(wP50)}
      />
    </div>
  )
}

// ── Merge drawer ──────────────────────────────────────────────────────────────

function MergeDrawer({
  targetJourney, allJourneys, onClose, onMerged,
}: {
  targetJourney: Journey
  allJourneys: Journey[]
  onClose: () => void
  onMerged: () => void
}) {
  const { t }          = useTranslation('contacts')
  const [selected, setSelected] = useState<string | null>(null)
  const [merging, setMerging]   = useState(false)

  const candidates = allJourneys.filter(j =>
    j.journey_id !== targetJourney.journey_id &&
    ['active', 'suspended'].includes(j.status)
  )

  async function handleMerge() {
    if (!selected) return
    setMerging(true)
    try {
      const res = await fetch(`/v1/journeys/${encodeURIComponent(targetJourney.journey_id)}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_journey_id: selected }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      onMerged()
      onClose()
    } catch (e) {
      alert(String(e))
    } finally {
      setMerging(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-end" onClick={onClose}>
      <div
        className="bg-white border-l border-border w-96 h-full shadow-xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border flex-shrink-0">
          <span className="font-semibold text-dark text-sm">
            {t('processes.journeys.merge.header')}
          </span>
          <button onClick={onClose} className="text-muted hover:text-dark">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <div className="text-xs text-muted mb-3">
            {t('processes.journeys.merge.button')}:{' '}
            <code className="text-secondary">{targetJourney.journey_id.slice(0, 8)}…</code>
          </div>
          {candidates.length === 0 ? (
            <div className="text-xs text-muted-light italic py-4 text-center">
              {t('processes.journeys.empty')}
            </div>
          ) : (
            candidates.map(j => {
              const color = JOURNEY_STATUS_COLORS[j.status]
              return (
                <div
                  key={j.journey_id}
                  onClick={() => setSelected(j.journey_id === selected ? null : j.journey_id)}
                  className="px-3 py-2.5 rounded-lg border cursor-pointer mb-2 transition-all"
                  style={{
                    borderColor: selected === j.journey_id ? color : '#e2e8f0',
                    background:  selected === j.journey_id ? color + '11' : 'transparent',
                  }}
                >
                  <div className="flex items-center gap-2">
                    <code className="text-xs text-secondary font-semibold">
                      {j.journey_id.slice(0, 8)}…
                    </code>
                    <span className="text-xs px-1.5 py-0.5 rounded font-medium"
                      style={{ background: color + '22', color }}>
                      {t(`processes.journeyStatus.${j.status}`, { defaultValue: j.status })}
                    </span>
                    {j.journey_type_id && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
                        {j.journey_type_id}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-light mt-1">{j.skill_id}</div>
                </div>
              )
            })
          )}
        </div>
        <div className="flex gap-2 px-4 py-3 border-t border-border flex-shrink-0">
          <button onClick={onClose}
            className="flex-1 py-1.5 rounded border border-border text-muted text-sm hover:bg-surface-muted transition-colors">
            {t('processes.journeys.split.cancel')}
          </button>
          <button onClick={handleMerge} disabled={!selected || merging}
            className="flex-1 py-1.5 rounded bg-primary text-white text-sm font-semibold disabled:opacity-40 hover:bg-primary-dark transition-colors">
            {merging
              ? t('processes.journeys.merge.merging')
              : t('processes.journeys.merge.button')}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Split drawer ──────────────────────────────────────────────────────────────

interface CollectSession { session_id: string; type: string; started_at?: string }

function SplitDrawer({
  journey, onClose, onSplit,
}: {
  journey: Journey
  onClose: () => void
  onSplit: () => void
}) {
  const { t }                    = useTranslation('contacts')
  const [sessions, setSessions]  = useState<CollectSession[]>([])
  const [loading, setLoading]    = useState(false)
  const [selected, setSelected]  = useState<Set<string>>(new Set())
  const [skillId, setSkillId]    = useState('')
  const [splitting, setSplitting] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetch(`/v1/journeys/${encodeURIComponent(journey.journey_id)}/sessions`)
      .then(r => r.ok ? r.json() : Promise.resolve({ sessions: [] }))
      .then((d: { sessions?: CollectSession[] }) => setSessions(
        (d.sessions ?? []).filter(s => s.type === 'collect')
      ))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false))
  }, [journey.journey_id])

  function toggleSession(id: string) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleSplit() {
    if (selected.size === 0) return
    setSplitting(true)
    try {
      const body: Record<string, unknown> = { session_ids: [...selected] }
      if (skillId.trim()) body.skill_id = skillId.trim()
      const res = await fetch(`/v1/journeys/${encodeURIComponent(journey.journey_id)}/split`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      onSplit()
      onClose()
    } catch (e) {
      alert(String(e))
    } finally {
      setSplitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-end" onClick={onClose}>
      <div
        className="bg-white border-l border-border w-96 h-full shadow-xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border flex-shrink-0">
          <span className="font-semibold text-dark text-sm">
            {t('processes.journeys.split.title')}
          </span>
          <button onClick={onClose} className="text-muted hover:text-dark">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Collect sessions */}
          <div>
            <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
              {t('processes.journeys.split.sessionsLabel')}
            </div>
            {loading ? (
              <div className="flex items-center gap-2 text-xs text-muted-light py-2">
                <Spinner />
                <span>{t('processes.journeys.split.loading')}</span>
              </div>
            ) : sessions.length === 0 ? (
              <div className="text-xs text-muted-light italic py-2">
                {t('processes.journeys.split.noSessions')}
              </div>
            ) : (
              sessions.map(s => (
                <label key={s.session_id}
                  className="flex items-center gap-2 px-3 py-2 rounded hover:bg-surface-muted cursor-pointer text-xs">
                  <input type="checkbox" checked={selected.has(s.session_id)}
                    onChange={() => toggleSession(s.session_id)}
                    className="rounded border-border-strong" />
                  <code className="text-secondary">{s.session_id.slice(0, 10)}…</code>
                  {s.type === 'origin' && (
                    <span className="text-muted-light">({t('processes.journeys.split.originLabel')})</span>
                  )}
                </label>
              ))
            )}
          </div>

          {/* Skill-flow for new journey */}
          <div>
            <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">
              {t('processes.journeys.split.skillLabel')}{' '}
              <span className="font-normal text-muted-light">
                {t('processes.journeys.split.skillOptional')}
              </span>
            </div>
            <input
              type="text"
              value={skillId}
              onChange={e => setSkillId(e.target.value)}
              placeholder="skill_id…"
              className="w-full text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40"
            />
            <p className="text-xs text-muted-light mt-1">
              {t('processes.journeys.split.skillHint')}
            </p>
          </div>
        </div>

        <div className="flex gap-2 px-4 py-3 border-t border-border flex-shrink-0">
          <button onClick={onClose}
            className="flex-1 py-1.5 rounded border border-border text-muted text-sm hover:bg-surface-muted transition-colors">
            {t('processes.journeys.split.cancel')}
          </button>
          <button onClick={handleSplit} disabled={selected.size === 0 || splitting}
            className="flex-1 py-1.5 rounded bg-primary text-white text-sm font-semibold disabled:opacity-40 hover:bg-primary-dark transition-colors">
            {splitting
              ? t('processes.journeys.split.splitting')
              : selected.size > 0
                ? t('processes.journeys.split.confirmCount', { count: selected.size })
                : t('processes.journeys.split.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function DetailPanel({
  journeyId, allJourneys, onRefresh,
}: {
  journeyId: string
  allJourneys: Journey[]
  onRefresh: () => void
}) {
  const { t, i18n }   = useTranslation('contacts')
  const { journey }   = useJourney(journeyId)
  const [showMerge, setShowMerge] = useState(false)
  const [showSplit, setShowSplit] = useState(false)

  if (!journey) return (
    <div className="flex-1 flex items-center justify-center text-muted-light">
      <Spinner />
    </div>
  )

  const color = JOURNEY_STATUS_COLORS[journey.status]

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between px-5 py-3.5 bg-white border-b border-border flex-shrink-0">
        <div>
          <code className="text-xs text-secondary">{journey.journey_id}</code>
          <div className="text-xs text-muted mt-0.5">{journey.skill_id}</div>
          {journey.journey_type_id && (
            <span className="inline-block mt-1 text-xs px-2 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
              {journey.journey_type_id}
            </span>
          )}
        </div>
        <span className="text-xs font-bold px-2.5 py-1 rounded flex-shrink-0"
          style={{ background: color + '33', color }}>
          {t(`processes.journeyStatus.${journey.status}`, { defaultValue: journey.status })}
        </span>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5">

        {/* Timeline */}
        <div>
          <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
            {t('processes.journeys.detail.timeline')}
          </div>
          <div className="space-y-1.5">
            {[
              { dot: '#22c55e', label: t('processes.journeys.detail.started'),   ts: journey.created_at },
              journey.last_event_at
                ? { dot: '#3b82f6', label: t('processes.journeys.detail.lastEvent'), ts: journey.last_event_at }
                : null,
            ].filter(Boolean).map((e, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: e!.dot }} />
                <span className="text-muted w-24 flex-shrink-0">{e!.label}</span>
                <span className="text-muted-light">{fmtDt(e!.ts, i18n.language)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Detail fields */}
        {journey.pool_id && (
          <div>
            <div className="text-xs font-bold text-muted uppercase tracking-wider mb-1">
              {t('processes.journeys.detail.pool')}
            </div>
            <code className="text-xs text-dark">{journey.pool_id}</code>
          </div>
        )}

        <div>
          <div className="text-xs font-bold text-muted uppercase tracking-wider mb-1">
            {t('processes.journeys.detail.linkedSessions')}
          </div>
          <span className="text-sm font-semibold text-dark">{journey.session_count}</span>
        </div>

        {journey.origin_session_id && (
          <div>
            <div className="text-xs font-bold text-muted uppercase tracking-wider mb-1">
              {t('processes.journeys.detail.originSession')}
            </div>
            <Link
              to={`/analise/sessions?sessionId=${journey.origin_session_id}`}
              className="text-xs text-secondary font-mono hover:underline">
              {journey.origin_session_id}
            </Link>
          </div>
        )}

        {journey.workflow_instance_id && (
          <div>
            <div className="text-xs font-bold text-muted uppercase tracking-wider mb-1">
              {t('processes.journeys.detail.workflowInstance')}
            </div>
            <code className="text-xs text-dark">{journey.workflow_instance_id}</code>
          </div>
        )}

        {journey.customer_id && (
          <div>
            <div className="text-xs font-bold text-muted uppercase tracking-wider mb-1">
              {t('processes.journeys.detail.customer')}
            </div>
            <code className="text-xs text-dark">{journey.customer_id}</code>
          </div>
        )}
      </div>

      {/* Actions */}
      {['active', 'suspended'].includes(journey.status) && (
        <div className="px-4 py-3 bg-white border-t border-border flex gap-2 flex-shrink-0">
          <button onClick={() => { setShowMerge(true); setShowSplit(false) }}
            className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded border border-border text-muted text-xs hover:bg-surface-muted transition-colors">
            <GitMerge className="w-3.5 h-3.5" aria-hidden="true" />
            {t('processes.journeys.merge.button')}
          </button>
          <button onClick={() => { setShowSplit(true); setShowMerge(false) }}
            className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded border border-border text-muted text-xs hover:bg-surface-muted transition-colors">
            <Scissors className="w-3.5 h-3.5" aria-hidden="true" />
            {t('processes.journeys.detail.splitButton')}
          </button>
        </div>
      )}

      {showMerge && (
        <MergeDrawer
          targetJourney={journey}
          allJourneys={allJourneys}
          onClose={() => setShowMerge(false)}
          onMerged={onRefresh}
        />
      )}
      {showSplit && (
        <SplitDrawer
          journey={journey}
          onClose={() => setShowSplit(false)}
          onSplit={onRefresh}
        />
      )}
    </div>
  )
}

// ── MonitorJourneysPage ───────────────────────────────────────────────────────

export default function MonitorJourneysPage() {
  const { tenantId }   = useAuth()
  const { t }          = useTranslation('contacts')

  // Filters
  const [activeTypeId,   setActiveTypeId]   = useState<string | null>(null)
  const [poolId,         setPoolId]         = useState<string>('')
  const [filterStatus,   setFilterStatus]   = useState<JourneyStatus | 'all'>('all')
  const [selectedId,     setSelectedId]     = useState<string | null>(null)

  // Resource lists
  const [journeyTypes, setJourneyTypes] = useState<JourneyType[]>([])
  const [pools,        setPools]        = useState<Pool[]>([])

  useEffect(() => {
    if (!tenantId) return
    registryApi.listJourneyTypes(tenantId).then(setJourneyTypes).catch(() => {})
    registryApi.listPools(tenantId).then(r => setPools(r.items)).catch(() => {})
  }, [tenantId])

  const { journeys, kpis, loading, refresh } = useJourneys(
    tenantId ?? '',
    undefined,
    undefined,      // status applied client-side — analytics-api ignores it for Monitor
    15_000,
    undefined,      // journey_type_id applied client-side
    poolId || undefined,
  )

  // Client-side filtering (status + journey_type_id)
  const filteredJourneys = journeys.filter(j => {
    if (filterStatus !== 'all' && j.status !== filterStatus) return false
    if (activeTypeId && j.journey_type_id !== activeTypeId) return false
    return true
  })

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('processes.noTenant')}
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">

        {/* Journey-type dropdown */}
        {journeyTypes.length > 0 && (
          <select
            value={activeTypeId ?? ''}
            onChange={e => { setActiveTypeId(e.target.value || null); setSelectedId(null) }}
            className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            <option value="">{t('processes.journeys.allTypes')}</option>
            {journeyTypes.map(jt => (
              <option key={jt.journey_type_id} value={jt.journey_type_id}>
                {jt.journey_type_id}
              </option>
            ))}
          </select>
        )}

        {/* Pool dropdown */}
        <select
          value={poolId}
          onChange={e => { setPoolId(e.target.value); setSelectedId(null) }}
          className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
          <option value="">{t('processes.journeys.filters.allPools')}</option>
          {pools.map(p => (
            <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
          ))}
        </select>

        <div className="flex-1" />
        {loading ? <Spinner /> : (
          <button onClick={refresh}
            className="text-xs text-muted-light hover:text-muted transition-colors px-2 py-1">
            {t('processes.refresh')}
          </button>
        )}
      </div>

      {/* KPI strip — scoped to filtered journeys so type/pool selection is reflected */}
      <KpiStrip kpis={kpis} journeys={filteredJourneys} />

      {/* Status filter + list + detail */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left: status filter + list */}
        <div className="w-80 flex-shrink-0 border-r border-border bg-white flex flex-col overflow-hidden">

          {/* Status badges */}
          <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-border flex-shrink-0">
            {(['all', 'active', 'suspended', 'completed', 'failed', 'cancelled'] as const).map(s => {
              const active = filterStatus === s
              const color  = s === 'all' ? '#3b82f6' : JOURNEY_STATUS_COLORS[s as JourneyStatus]
              return (
                <button key={s}
                  onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                  className="text-xs px-2.5 py-1 rounded-md font-medium transition-all"
                  style={{
                    border:     `1px solid ${active ? color : '#e2e8f0'}`,
                    background: active ? color + '22' : 'transparent',
                    color:      active ? color : '#94a3b8',
                  }}>
                  {t(`processes.journeyStatus.${s}`, { defaultValue: s })}
                </button>
              )
            })}
          </div>

          {/* Journey list */}
          <div className="flex-1 overflow-y-auto">
            {loading && journeys.length === 0 && (
              <div className="flex items-center justify-center py-12 text-muted-light text-sm animate-pulse">
                {t('processes.journeys.loading')}
              </div>
            )}
            {!loading && filteredJourneys.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 text-muted-light text-sm gap-2">
                <Map className="w-8 h-8" aria-hidden="true" />
                <span>{t('processes.journeys.empty')}</span>
              </div>
            )}
            {filteredJourneys.map(j => {
              const color      = JOURNEY_STATUS_COLORS[j.status]
              const isSelected = j.journey_id === selectedId
              return (
                <div key={j.journey_id}
                  onClick={() => setSelectedId(j.journey_id === selectedId ? null : j.journey_id)}
                  className="px-4 py-3 cursor-pointer transition-colors hover:bg-primary/5"
                  style={{
                    borderBottom: '1px solid #e2e8f0',
                    background:   isSelected ? '#EBF2FA' : 'transparent',
                    borderLeft:   isSelected ? `3px solid ${color}` : '3px solid transparent',
                  }}>
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <code className="text-xs font-semibold text-secondary">
                        {j.journey_id.slice(0, 8)}…
                      </code>
                      <div className="text-xs text-muted mt-0.5 truncate">{j.skill_id}</div>
                      {j.journey_type_id && (
                        <span className="inline-block mt-0.5 text-xs px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
                          {j.journey_type_id}
                        </span>
                      )}
                    </div>
                    <span className="text-xs font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                      style={{ background: color + '33', color }}>
                      {t(`processes.journeyStatus.${j.status}`, { defaultValue: j.status })}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-xs text-muted-light">
                      {fmtDt(j.created_at)}
                    </span>
                    <span className="text-xs text-muted-light">
                      {t('processes.journeys.sessions_one', {
                        count: j.session_count,
                        defaultValue: `${j.session_count}`,
                      })}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Right: detail panel */}
        {selectedId ? (
          <DetailPanel
            journeyId={selectedId}
            allJourneys={journeys}
            onRefresh={() => { refresh(); setSelectedId(null) }}
          />
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-light">
            <Map className="w-10 h-10 mb-3" aria-hidden="true" />
            <div className="text-sm">{t('processes.journeys.selectPrompt')}</div>
          </div>
        )}
      </div>
    </div>
  )
}
