/**
 * ProcessosPage — /flow/processos
 *
 * Two-tab view for Arc 10 (Journey) + Arc 4 (Workflow Instances).
 *
 * Tab "journeys"  — Journey list from analytics-api + detail from workflow-api
 * Tab "instances" — Workflow instance lifecycle (existing view)
 */
import React, { useState, useRef, useEffect } from 'react'
import { X, Settings, Map, ClipboardList, FolderOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useWorkflowInstances, useWorkflowInstance, cancelWorkflow,
  useJourneys, useJourney,
} from '@/modules/workflows/api/hooks'
import type { WorkflowStatus, JourneyStatus, Journey } from '@/modules/workflows/api/hooks'
import { Link } from 'react-router-dom'

// ── Shared helpers ────────────────────────────────────────────────────────────

function fmtDt(ts: string | null | undefined, locale?: string) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(locale)
}

function fmtDuration(ms: number | null | undefined) {
  if (!ms) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}min`
  return `${(ms / 3_600_000).toFixed(1)}h`
}

// ── Journey tab ───────────────────────────────────────────────────────────────

const JOURNEY_STATUS_COLORS: Record<JourneyStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  cancelled: '#6b7280',
}

const WF_STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

// ── Journey merge helper ──────────────────────────────────────────────────────

function MergeButton({ primary, candidates, tenantId, onMerged }: {
  primary:    Journey
  candidates: Journey[]
  tenantId:   string
  onMerged:   () => void
}) {
  const { t } = useTranslation('contacts')
  const [open,    setOpen]    = useState(false)
  const [merging, setMerging] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  const others = candidates.filter(
    j => j.journey_id !== primary.journey_id &&
         (j.status === 'active' || j.status === 'suspended')
  )
  if (others.length === 0) return null

  async function merge(sourceId: string) {
    setMerging(true)
    setOpen(false)
    try {
      const res = await fetch('/v1/journeys/merge', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          tenant_id:         tenantId,
          journey_id:        primary.journey_id,
          source_journey_id: sourceId,
        }),
      })
      if (res.ok) onMerged()
    } catch { /* non-fatal */ }
    finally { setMerging(false) }
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        disabled={merging}
        className="w-full py-1.5 rounded border border-violet-300 bg-violet-50 text-violet-700 text-xs font-medium hover:bg-violet-100 transition-colors disabled:opacity-40"
      >
        {merging ? t('processes.journeys.merge.merging') : t('processes.journeys.merge.button')}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 right-0 mb-1 bg-white border border-border rounded-lg shadow-lg overflow-hidden z-50">
          <div className="px-2.5 py-1.5 text-2xs font-bold text-muted uppercase tracking-wide border-b border-border">
            {t('processes.journeys.merge.header')}
          </div>
          {others.map(j => (
            <button key={j.journey_id} onClick={() => merge(j.journey_id)}
              className="w-full text-left px-3 py-2 text-xs text-dark hover:bg-primary/5 transition-colors border-b border-border last:border-0">
              <div className="font-mono text-muted">{j.journey_id.slice(0, 12)}…</div>
              <div className="text-muted-light truncate mt-0.5">{j.skill_id}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Journey split drawer ──────────────────────────────────────────────────────

function SplitDrawer({ journey, tenantId, onSplit, onClose }: {
  journey:  Journey
  tenantId: string
  onSplit:  (newJourneyId: string) => void
  onClose:  () => void
}) {
  const { t } = useTranslation('contacts')
  const [sessions,     setSessions]     = useState<string[]>([])
  const [selected,     setSelected]     = useState<Set<string>>(new Set())
  const [skillId,      setSkillId]      = useState('')
  const [loading,      setLoading]      = useState(true)
  const [splitting,    setSplitting]    = useState(false)
  const [error,        setError]        = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`/v1/journeys/${journey.journey_id}/collect-sessions`, {
      headers: { 'x-tenant-id': tenantId },
    })
      .then(r => r.json())
      .then(d => { setSessions(d.collect_sessions ?? []); setLoading(false) })
      .catch(() => { setSessions([]); setLoading(false) })
  }, [journey.journey_id, tenantId])

  function toggle(sid: string) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(sid)) next.delete(sid)
      else next.add(sid)
      return next
    })
  }

  async function doSplit() {
    if (selected.size === 0) return
    setSplitting(true)
    setError(null)
    try {
      const res = await fetch(`/v1/journeys/${journey.journey_id}/split`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'x-tenant-id': tenantId },
        body:    JSON.stringify({
          session_ids: [...selected],
          skill_id:    skillId.trim() || null,
        }),
      })
      const data = await res.json()
      if (!res.ok) { setError(data.detail ?? String(res.status)); return }
      onSplit(data.new_journey_id)
    } catch (e) {
      setError(String(e))
    } finally {
      setSplitting(false)
    }
  }

  const confirmLabel = splitting
    ? t('processes.journeys.split.splitting')
    : selected.size > 0
      ? t('processes.journeys.split.confirmCount', { count: selected.size })
      : t('processes.journeys.split.confirm')

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="bg-white border border-border rounded-t-2xl sm:rounded-2xl w-full max-w-md p-5 shadow-2xl">

        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-bold text-dark">{t('processes.journeys.split.title')}</h3>
          <button onClick={onClose} className="text-muted hover:text-dark" aria-label="Close"><X className="w-4 h-4" /></button>
        </div>

        {/* Session picker */}
        <div className="mb-4">
          <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
            {t('processes.journeys.split.sessionsLabel')}
          </div>
          {loading ? (
            <div className="text-xs text-muted-light animate-pulse py-3 text-center">
              {t('processes.journeys.split.loading')}
            </div>
          ) : sessions.length === 0 ? (
            <div className="text-xs text-muted-light py-3 text-center">
              {t('processes.journeys.split.noSessions')}
            </div>
          ) : (
            <div className="space-y-1 max-h-40 overflow-y-auto border border-border rounded-lg p-2">
              {sessions.map(sid => {
                const isOrigin  = sid === journey.origin_session_id
                const isChecked = selected.has(sid)
                return (
                  <label key={sid}
                    className={`flex items-center gap-2.5 px-2 py-1.5 rounded cursor-pointer transition-colors ${
                      isOrigin ? 'opacity-40 cursor-not-allowed' : 'hover:bg-primary/5'
                    }`}>
                    <input
                      type="checkbox"
                      disabled={isOrigin}
                      checked={isChecked}
                      onChange={() => !isOrigin && toggle(sid)}
                      className="rounded border-border-strong bg-white text-primary"
                    />
                    <code className="text-xs text-dark flex-1 truncate">{sid}</code>
                    {isOrigin && (
                      <span className="text-2xs text-muted flex-shrink-0">
                        {t('processes.journeys.split.originLabel')}
                      </span>
                    )}
                  </label>
                )
              })}
            </div>
          )}
        </div>

        {/* Optional skill_id */}
        <div className="mb-4">
          <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
            {t('processes.journeys.split.skillLabel')}{' '}
            <span className="font-normal text-muted-light">{t('processes.journeys.split.skillOptional')}</span>
          </div>
          <input
            value={skillId}
            onChange={e => setSkillId(e.target.value)}
            placeholder="skill_portabilidade_v1"
            className="w-full text-xs bg-white border border-border-strong rounded-lg px-3 py-2 text-dark placeholder-muted-light focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
          <div className="text-2xs text-muted-light mt-1">
            {t('processes.journeys.split.skillHint')}
          </div>
        </div>

        {error && (
          <div className="mb-3 text-xs text-red-text bg-red-light border border-red/30 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2">
          <button onClick={onClose}
            className="flex-1 py-2 rounded-lg border border-border text-xs text-muted hover:text-dark hover:border-border-strong transition-colors">
            {t('processes.journeys.split.cancel')}
          </button>
          <button
            onClick={doSplit}
            disabled={selected.size === 0 || splitting}
            className="flex-1 py-2 rounded-lg bg-primary text-xs font-semibold text-white hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

function JourneysTab({ tenantId }: { tenantId: string }) {
  const { t, i18n } = useTranslation('contacts')
  const [filterStatus, setFilterStatus] = useState<JourneyStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)
  const [splitOpen,    setSplitOpen]    = useState(false)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { journeys, kpis, loading, refresh } = useJourneys(tenantId, undefined, statusParam)
  const { journey: detail } = useJourney(selectedId)

  const sorted = [...journeys].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* KPI strip */}
      {kpis.length > 0 && (
        <div className="flex gap-3 px-4 py-2.5 bg-white border-b border-border flex-shrink-0 overflow-x-auto">
          {kpis.slice(0, 5).map(k => (
            <div key={k.skill_id}
              className="flex-shrink-0 bg-surface-muted border border-border rounded-lg px-3 py-2 min-w-[140px]">
              <div className="text-2xs text-muted truncate font-mono">{k.skill_id}</div>
              <div className="flex items-center gap-3 mt-1">
                <div className="text-center">
                  <div className="text-xs font-bold text-dark">{k.total_journeys}</div>
                  <div className="text-micro text-muted-light">{t('processes.journeys.kpiTotal')}</div>
                </div>
                <div className="text-center">
                  <div className="text-xs font-bold text-green-600">
                    {(k.resolution_rate * 100).toFixed(0)}%
                  </div>
                  <div className="text-micro text-muted-light">{t('processes.journeys.kpiResolution')}</div>
                </div>
                <div className="text-center">
                  <div className="text-xs font-bold text-dark">
                    {fmtDuration(k.median_duration_ms)}
                  </div>
                  <div className="text-micro text-muted-light">{t('processes.journeys.kpiP50')}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">

        {/* Left: list */}
        <div className="w-80 flex-shrink-0 border-r border-border bg-white flex flex-col overflow-hidden">

          {/* Status filter */}
          <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-border flex-shrink-0">
            {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(s => {
              const active = filterStatus === s
              const color  = s === 'all' ? '#3b82f6' : JOURNEY_STATUS_COLORS[s as JourneyStatus]
              return (
                <button key={s} onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                  className="text-xs px-2.5 py-1 rounded-md font-medium transition-all"
                  style={{
                    border:     `1px solid ${active ? color : '#e2e8f0'}`,
                    background: active ? color + '22' : 'transparent',
                    color:      active ? color : '#94a3b8',
                  }}>
                  {t(`processes.journeyStatus.${s}`)}
                </button>
              )
            })}
          </div>

          {/* Journey list */}
          <div className="flex-1 overflow-y-auto">
            {loading && sorted.length === 0 && (
              <div className="flex items-center justify-center py-12 text-muted-light text-sm animate-pulse">
                {t('processes.journeys.loading')}
              </div>
            )}
            {!loading && sorted.length === 0 && (
              <div className="flex flex-col items-center justify-center py-12 text-muted-light text-sm gap-2">
                <FolderOpen className="w-8 h-8" aria-hidden="true" />
                <span>{t('processes.journeys.empty')}</span>
              </div>
            )}
            {sorted.map(j => {
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
                      <div className="text-xs text-muted mt-0.5 truncate font-mono">
                        {j.skill_id}
                      </div>
                      {j.customer_id && (
                        <div className="text-xs text-muted-light mt-0.5 truncate">
                          {t('processes.journeys.customerLabel')}: {j.customer_id}
                        </div>
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-1 flex-shrink-0">
                      <span className="text-xs font-bold px-1.5 py-0.5 rounded"
                        style={{ background: color + '33', color }}>
                        {t(`processes.journeyStatus.${j.status}`, { defaultValue: j.status })}
                      </span>
                      <span className="text-2xs text-muted-light">
                        {t('processes.journeys.sessions', { count: j.session_count })}
                      </span>
                    </div>
                  </div>
                  <div className="text-xs text-muted-light mt-1.5">{fmtDt(j.created_at, i18n.language)}</div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Right: detail */}
        {detail ? (
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="flex justify-between items-start px-5 py-3.5 bg-white border-b border-border flex-shrink-0">
              <div>
                <code className="text-xs text-secondary">{detail.journey_id}</code>
                <div className="text-xs text-muted mt-0.5 font-mono">{detail.skill_id}</div>
              </div>
              <button onClick={() => setSelectedId(null)}
                className="text-muted hover:text-dark" aria-label="Close"><X className="w-4 h-4" /></button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-5">

              {/* Status */}
              <div>
                <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                  {t('processes.journeys.detail.status')}
                </div>
                <span className="text-xs font-bold px-2.5 py-1 rounded"
                  style={{
                    background: JOURNEY_STATUS_COLORS[detail.status] + '33',
                    color: JOURNEY_STATUS_COLORS[detail.status],
                  }}>
                  {t(`processes.journeyStatus.${detail.status}`, { defaultValue: detail.status })}
                </span>
              </div>

              {/* Session count */}
              <div>
                <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                  {t('processes.journeys.detail.linkedSessions')}
                </div>
                <div className="text-2xl font-bold text-dark">{detail.session_count ?? 1}</div>
              </div>

              {/* Timeline */}
              <div>
                <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                  {t('processes.journeys.detail.timeline')}
                </div>
                <div className="space-y-1.5">
                  {[
                    { dot: '#22c55e', label: t('processes.journeys.detail.started'),   ts: detail.created_at },
                    detail.last_event_at
                      ? { dot: '#3b82f6', label: t('processes.journeys.detail.lastEvent'), ts: detail.last_event_at }
                      : null,
                  ].filter(Boolean).map((entry, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: entry!.dot }} />
                      <span className="text-muted w-24 flex-shrink-0">{entry!.label}</span>
                      <span className="text-muted-light">{fmtDt(entry!.ts, i18n.language)}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Origin session */}
              {detail.origin_session_id && (
                <div>
                  <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                    {t('processes.journeys.detail.originSession')}
                  </div>
                  <Link
                    to={`/contacts/sessions?sessionId=${detail.origin_session_id}`}
                    className="text-xs text-secondary font-mono hover:underline">
                    {detail.origin_session_id}
                  </Link>
                </div>
              )}

              {/* Linked workflow instance */}
              {detail.workflow_instance_id && (
                <div>
                  <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                    {t('processes.journeys.detail.workflowInstance')}
                  </div>
                  <code className="text-xs text-muted font-mono">{detail.workflow_instance_id}</code>
                </div>
              )}

              {/* Customer */}
              {detail.customer_id && (
                <div>
                  <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                    {t('processes.journeys.detail.customer')}
                  </div>
                  <span className="text-xs text-dark">{detail.customer_id}</span>
                </div>
              )}

            </div>

            {/* Action buttons — only for active/suspended journeys */}
            {(detail.status === 'active' || detail.status === 'suspended') && (
              <div className="px-4 py-3 bg-white border-t border-border flex-shrink-0 space-y-2">
                <MergeButton
                  primary={detail}
                  candidates={journeys}
                  tenantId={tenantId}
                  onMerged={() => { setSelectedId(null); refresh() }}
                />
                <button
                  onClick={() => setSplitOpen(true)}
                  className="w-full py-1.5 rounded border border-orange-300 bg-orange-50 text-orange-700 text-xs font-medium hover:bg-orange-100 transition-colors"
                >
                  {t('processes.journeys.detail.splitButton')}
                </button>
              </div>
            )}

            {/* Split drawer */}
            {splitOpen && detail && (
              <SplitDrawer
                journey={detail}
                tenantId={tenantId}
                onClose={() => setSplitOpen(false)}
                onSplit={(newId) => {
                  setSplitOpen(false)
                  refresh()
                  setSelectedId(newId)
                }}
              />
            )}
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-light">
            <Map className="w-10 h-10 mb-3" aria-hidden="true" />
            <div className="text-sm">{t('processes.journeys.selectPrompt')}</div>
          </div>
        )}
      </div>

      {/* Refresh button bottom-right */}
      <div className="absolute bottom-4 right-4">
        <button onClick={refresh}
          className="text-xs px-3 py-1.5 rounded border border-border bg-white text-muted hover:text-dark hover:border-border-strong transition-colors shadow-sm">
          {t('processes.refresh')}
        </button>
      </div>
    </div>
  )
}

// ── Instances tab (existing view) ─────────────────────────────────────────────

function InstancesTab({ tenantId }: { tenantId: string }) {
  const { t, i18n } = useTranslation('contacts')
  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  async function handleCancel() {
    if (!selectedId || !tenantId) return
    if (!confirm(t('processes.instances.confirmCancel'))) return
    try {
      await cancelWorkflow(selectedId, tenantId)
      setSelectedId(null)
      refresh()
    } catch (e) { alert(String(e)) }
  }

  return (
    <div className="flex flex-1 overflow-hidden">

      {/* Left: list */}
      <div className="w-80 flex-shrink-0 border-r border-border bg-white flex flex-col overflow-hidden">

        {/* Status filter */}
        <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-border flex-shrink-0">
          {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(s => {
            const active = filterStatus === s
            const color  = s === 'all' ? '#3b82f6' : WF_STATUS_COLORS[s as WorkflowStatus]
            return (
              <button key={s} onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                className="text-xs px-2.5 py-1 rounded-md font-medium transition-all"
                style={{
                  border:     `1px solid ${active ? color : '#e2e8f0'}`,
                  background: active ? color + '22' : 'transparent',
                  color:      active ? color : '#94a3b8',
                }}>
                {t(`processes.wfStatus.${s}`)}
              </button>
            )
          })}
        </div>

        {/* Instance list */}
        <div className="flex-1 overflow-y-auto">
          {loading && instances.length === 0 && (
            <div className="flex items-center justify-center py-12 text-muted-light text-sm animate-pulse">
              {t('processes.instances.loading')}
            </div>
          )}
          {!loading && sorted.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-light text-sm gap-2">
              <ClipboardList className="w-8 h-8" aria-hidden="true" />
              <span>{t('processes.instances.empty')}</span>
            </div>
          )}
          {sorted.map(inst => {
            const color      = WF_STATUS_COLORS[inst.status]
            const isSelected = inst.id === selectedId
            return (
              <div key={inst.id}
                onClick={() => setSelectedId(inst.id === selectedId ? null : inst.id)}
                className="px-4 py-3 cursor-pointer transition-colors hover:bg-primary/5"
                style={{
                  borderBottom: '1px solid #e2e8f0',
                  background:   isSelected ? '#EBF2FA' : 'transparent',
                  borderLeft:   isSelected ? `3px solid ${color}` : '3px solid transparent',
                }}>
                <div className="flex justify-between items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <code className="text-xs font-semibold text-secondary">{inst.id.slice(0, 8)}…</code>
                    <div className="text-xs text-muted mt-0.5 truncate">{inst.flow_id}</div>
                    {inst.origin_session_id && (
                      <div className="text-xs text-muted-light mt-0.5 truncate font-mono">
                        {t('processes.instances.sessionLabel')}: …{inst.origin_session_id.slice(-10)}
                      </div>
                    )}
                  </div>
                  <span className="text-xs font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                    style={{ background: color + '33', color }}>
                    {t(`processes.wfStatus.${inst.status}`, { defaultValue: inst.status })}
                  </span>
                </div>
                <div className="text-xs text-muted-light mt-1.5">{fmtDt(inst.created_at, i18n.language)}</div>
                {inst.suspend_reason && (
                  <div className="text-xs text-warning mt-1">
                    {t(`processes.wfSuspend.${inst.suspend_reason}`, { defaultValue: inst.suspend_reason })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: detail */}
      {detail ? (
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex justify-between items-start px-5 py-3.5 bg-white border-b border-border flex-shrink-0">
            <div>
              <code className="text-xs text-secondary">{detail.id}</code>
              <div className="text-xs text-muted mt-0.5">{detail.flow_id}</div>
            </div>
            <div className="flex items-center gap-3">
              <button onClick={refresh}
                className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:text-dark hover:border-border-strong transition-colors">
                ↻
              </button>
              <button onClick={() => setSelectedId(null)}
                className="text-muted hover:text-dark" aria-label="Close"><X className="w-4 h-4" /></button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-5">

            {/* Status */}
            <div>
              <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                {t('processes.instances.detail.status')}
              </div>
              <span className="text-xs font-bold px-2.5 py-1 rounded"
                style={{ background: WF_STATUS_COLORS[detail.status] + '33', color: WF_STATUS_COLORS[detail.status] }}>
                {t(`processes.wfStatus.${detail.status}`, { defaultValue: detail.status })}
              </span>
              {detail.current_step && (
                <div className="mt-2 text-xs text-muted">
                  {t('processes.instances.detail.currentStep')}: <code className="text-dark">{detail.current_step}</code>
                </div>
              )}
              {detail.outcome && (
                <div className="mt-1 text-xs text-muted">
                  {t('processes.instances.detail.outcome')}: <code className="text-dark">{detail.outcome}</code>
                </div>
              )}
            </div>

            {/* Timeline */}
            <div>
              <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                {t('processes.instances.detail.timeline')}
              </div>
              <div className="space-y-1.5">
                {[
                  { dot: '#22c55e', label: t('processes.instances.detail.created'),   ts: detail.created_at },
                  detail.suspended_at ? { dot: '#eab308', label: t('processes.instances.detail.suspended'), ts: detail.suspended_at } : null,
                  detail.resumed_at   ? { dot: '#3b82f6', label: t('processes.instances.detail.resumed'),   ts: detail.resumed_at   } : null,
                  detail.completed_at ? { dot: '#22c55e', label: t('processes.instances.detail.completed'), ts: detail.completed_at } : null,
                ].filter(Boolean).map((entry, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: entry!.dot }} />
                    <span className="text-muted w-20 flex-shrink-0">{entry!.label}</span>
                    <span className="text-muted-light">{fmtDt(entry!.ts, i18n.language)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Suspend reason */}
            {detail.suspend_reason && (
              <div>
                <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                  {t('processes.instances.detail.suspendReason')}
                </div>
                <span className="text-xs px-2.5 py-1 rounded border border-warning/30 bg-warning-light text-warning-text">
                  {t(`processes.wfSuspend.${detail.suspend_reason}`, { defaultValue: detail.suspend_reason })}
                </span>
              </div>
            )}

            {/* Resume token */}
            {detail.resume_token && (
              <div>
                <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                  {t('processes.instances.detail.resumeToken')}
                </div>
                <div
                  className="bg-surface-muted border border-border rounded px-3 py-2 text-xs font-mono text-muted break-all cursor-pointer hover:border-border-strong"
                  onClick={() => void navigator.clipboard.writeText(detail.resume_token!)}
                  title={t('processes.instances.detail.tokenClickHint')}>
                  {detail.resume_token}
                </div>
                {detail.resume_expires_at && (
                  <div className="mt-1 text-xs text-muted-light">
                    {t('processes.instances.detail.expires')}: {fmtDt(detail.resume_expires_at, i18n.language)}
                  </div>
                )}
              </div>
            )}

            {/* Origin session link */}
            {detail.origin_session_id && (
              <div>
                <div className="text-xs font-bold text-muted uppercase tracking-wider mb-2">
                  {t('processes.instances.detail.originSession')}
                </div>
                <Link
                  to={`/contacts/sessions?sessionId=${detail.origin_session_id}`}
                  className="text-xs text-secondary font-mono hover:underline">
                  {detail.origin_session_id}
                </Link>
              </div>
            )}
          </div>

          {/* Cancel button */}
          {['active', 'suspended'].includes(detail.status) && (
            <div className="px-4 py-3 bg-white border-t border-border flex-shrink-0">
              <button onClick={handleCancel}
                className="w-full py-2 rounded border border-red/30 bg-red-light text-red-text text-sm font-semibold hover:bg-red/10 transition-colors">
                {t('processes.instances.detail.cancelButton')}
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center text-muted-light">
          <Settings className="w-10 h-10 mb-3" aria-hidden="true" />
          <div className="text-sm">{t('processes.instances.selectPrompt')}</div>
        </div>
      )}
    </div>
  )
}

// ── ProcessosPage ─────────────────────────────────────────────────────────────

type PageTab = 'journeys' | 'instances'

export default function ProcessosPage() {
  const { tenantId } = useAuth()
  const { t } = useTranslation('contacts')
  const [tab, setTab] = useState<PageTab>('journeys')

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('processes.noTenant')}
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Tab bar */}
      <div className="flex items-center justify-end px-5 py-3 bg-white border-b border-border flex-shrink-0">

        {/* Tab switcher */}
        <div className="flex items-center gap-1 bg-surface-muted border border-border rounded-lg p-1">
          {([
            { key: 'journeys'  as PageTab, labelKey: 'processes.tabs.journeys',  Icon: Map      },
            { key: 'instances' as PageTab, labelKey: 'processes.tabs.instances', Icon: Settings },
          ]).map(({ key, labelKey, Icon: TabIcon }) => (
            <button key={key} onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md font-medium transition-all ${
                tab === key
                  ? 'bg-primary text-white shadow-sm'
                  : 'text-muted hover:text-dark'
              }`}>
              <TabIcon className="w-3.5 h-3.5" aria-hidden="true" />
              {t(labelKey)}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden relative">
        {tab === 'journeys'  && <JourneysTab  tenantId={tenantId} />}
        {tab === 'instances' && <InstancesTab tenantId={tenantId} />}
      </div>
    </div>
  )
}
