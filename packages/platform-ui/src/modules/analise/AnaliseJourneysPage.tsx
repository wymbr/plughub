/**
 * AnaliseJourneysPage — /analise/journeys
 *
 * Analytics view of journeys with 3-level URL-param drill-down:
 *   Level 1: /analise/journeys                               — filtered journeys table
 *   Level 2: /analise/journeys?journey=:id                   — instances of that journey
 *   Level 3: /analise/journeys?journey=:id&instance=:iid     — sessions of that instance
 *   Level 4: /analise/journeys?journey=:id&instance=:iid&session=:sid — transcript
 *
 * Data sources:
 *   - GET /reports/journeys   (analytics-api — journey list + KPIs)
 *   - GET /v1/journeys/:id/instances  (workflow-api — instances for a journey)
 *   - GET /v1/workflow/instances/:id/sessions  (workflow-api — sessions for an instance)
 */
import React, { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight, GitBranch } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'
import {
  useJourneys,
  useJourneyInstances,
  useWorkflowInstanceSessions,
  type Journey,
  type JourneyStatus,
  type WorkflowInstance,
  type InstanceSession,
} from '@/modules/workflows/api/hooks'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | undefined | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}
function fmtDuration(from: string | undefined | null, to?: string | null): string {
  if (!from || !to) return '—'
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000); const s = Math.round((ms % 60_000) / 1000)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}
function truncateId(id: string | undefined | null): string {
  if (!id) return '—'
  return id.length > 16 ? `…${id.slice(-12)}` : id
}
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29)
  return d.toISOString().slice(0, 10)
}
function isoToday(): string { return new Date().toISOString().slice(0, 10) }

// ── JourneyStatusBadge ────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  active:    'bg-primary-light text-primary border-primary/30',
  suspended: 'bg-warning-light text-warning border-warning/30',
  completed: 'bg-green-light text-green border-green/30',
  failed:    'bg-red-light text-red border-red/30',
  cancelled: 'bg-surface-alt text-muted border-border',
}

function JourneyStatusBadge({ status }: { status: JourneyStatus }) {
  const { t } = useTranslation('contacts')
  const cls = STATUS_COLORS[status] ?? 'bg-surface-alt text-muted border-border'
  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded border font-medium ${cls}`}>
      {t(`processes.journeyStatus.${status}`, { defaultValue: status })}
    </span>
  )
}

// ── WfStatusBadge ─────────────────────────────────────────────────────────────

const WF_COLORS: Record<string, string> = {
  active:    'bg-primary-light text-primary border-primary/30',
  suspended: 'bg-warning-light text-warning border-warning/30',
  completed: 'bg-green-light text-green border-green/30',
  failed:    'bg-red-light text-red border-red/30',
  timed_out: 'bg-warning-light text-warning border-warning/30',
  cancelled: 'bg-surface-alt text-muted border-border',
}

function WfStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('contacts')
  const cls = WF_COLORS[status] ?? 'bg-surface-alt text-muted border-border'
  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded border font-medium ${cls}`}>
      {t(`processes.wfStatus.${status}`, { defaultValue: status })}
    </span>
  )
}

// ── SessionTypeBadge ──────────────────────────────────────────────────────────

function SessionTypeBadge({ type }: { type: 'origin' | 'collect' }) {
  const { t } = useTranslation('contacts')
  const cls = type === 'origin'
    ? 'bg-primary-light text-primary border-primary/30'
    : 'bg-surface-alt text-muted border-border'
  return (
    <span className={`inline-block text-xs px-1.5 py-0.5 rounded border font-medium ${cls}`}>
      {t(`processes.instances.sessions.type.${type}`, { defaultValue: type })}
    </span>
  )
}

const JOURNEY_PAGE_SIZE = 50

// ── Level 1: Journey list ─────────────────────────────────────────────────────

interface JourneyListProps {
  tenantId:          string
  onSelectJourney:   (id: string) => void
}

const JOURNEY_STATUSES = ['all', 'active', 'suspended', 'completed', 'failed', 'cancelled']

function JourneyList({ tenantId, onSelectJourney }: JourneyListProps) {
  const { t } = useTranslation('contacts')
  const [fromDt,       setFromDt]       = React.useState(iso30DaysAgo)
  const [toDt,         setToDt]         = React.useState(isoToday)
  const [status,       setStatus]       = React.useState<string>('all')
  const [poolId,       setPoolId]       = React.useState('')
  const [journeyType,  setJourneyType]  = React.useState('')
  const [page,         setPage]         = React.useState(1)

  // useJourneys: (tenantId, skillId?, status?, intervalMs?, journeyTypeId?, poolId?)
  const statusParam = status === 'all' ? undefined : (status as JourneyStatus)
  const { journeys, loading, refresh } = useJourneys(
    tenantId,
    undefined,
    statusParam,
    0,               // no polling on analytics page
    journeyType || undefined,
    poolId || undefined,
  )

  // Client-side date filter (useJourneys doesn't yet pass from_dt/to_dt to /reports/journeys)
  const filtered = useMemo(() => {
    if (!fromDt && !toDt) return journeys
    const from = fromDt ? new Date(fromDt).getTime() : 0
    const to   = toDt   ? new Date(toDt + 'T23:59:59').getTime() : Infinity
    return journeys.filter(j => {
      const t = new Date(j.created_at).getTime()
      return t >= from && t <= to
    })
  }, [journeys, fromDt, toDt])

  // Reset page whenever filtered results change
  React.useEffect(() => { setPage(1) }, [filtered])

  const totalPages = Math.max(1, Math.ceil(filtered.length / JOURNEY_PAGE_SIZE))
  const paged      = filtered.slice((page - 1) * JOURNEY_PAGE_SIZE, page * JOURNEY_PAGE_SIZE)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('processes.journeys.filters.from')}</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">–</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>

        <select value={status} onChange={e => setStatus(e.target.value)}
          className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
          {JOURNEY_STATUSES.map(s => (
            <option key={s} value={s}>{t(`processes.journeyStatus.${s}`, { defaultValue: s })}</option>
          ))}
        </select>

        <input type="text" value={journeyType} onChange={e => setJourneyType(e.target.value)}
          placeholder={t('processes.journeys.filters.journeyType')}
          className="text-xs border border-border-strong rounded px-2 py-1 w-32 focus:outline-none focus:ring-1 focus:ring-primary/40" />

        <input type="text" value={poolId} onChange={e => setPoolId(e.target.value)}
          placeholder={t('processes.journeys.filters.pool')}
          className="text-xs border border-border-strong rounded px-2 py-1 w-32 focus:outline-none focus:ring-1 focus:ring-primary/40" />

        <div className="flex-1" />
        {loading
          ? <Spinner />
          : <button onClick={refresh}
              className="text-xs text-muted-light hover:text-muted transition-colors px-2 py-1">
              {t('processes.refresh')}
            </button>
        }
      </div>

      {/* Count + pagination bar */}
      <div className="flex items-center justify-between px-5 py-2 bg-white border-b border-border flex-shrink-0 text-xs">
        <span className="text-muted-light">
          {loading
            ? <span className="animate-spin inline-block">⟳</span>
            : <><strong className="text-dark">{t('processes.journeys.totalCount', { count: filtered.length })}</strong>
                {totalPages > 1 && <span className="ml-2 text-muted">· {t('lista.page', { page, total: totalPages })}</span>}
              </>
          }
        </span>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
              className="px-2 py-0.5 rounded border border-border text-muted disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              {t('lista.prev')}
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              return start + i
            }).map(p => (
              <button key={p} onClick={() => setPage(p)}
                className={`px-2 py-0.5 rounded border transition-colors ${
                  p === page ? 'bg-primary text-white border-primary' : 'border-border text-muted hover:border-primary hover:text-primary'
                }`}>
                {p}
              </button>
            ))}
            <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}
              className="px-2 py-0.5 rounded border border-border text-muted disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              {t('lista.next')}
            </button>
          </div>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 py-4">
        {filtered.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <GitBranch className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm">{t('processes.journeys.empty')}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.journeyId')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.status')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.pool')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.journeyType')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.sessions')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.created')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.journeys.columns.lastEvent')}
                </th>
              </tr>
            </thead>
            <tbody>
              {paged.map(j => (
                <tr key={j.journey_id}
                  onClick={() => onSelectJourney(j.journey_id)}
                  className="border-t border-border hover:bg-surface-muted transition-colors cursor-pointer">
                  <td className="px-3 py-2.5 font-mono text-xs" title={j.journey_id}>
                    <span className="text-primary hover:underline">{truncateId(j.journey_id)}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <JourneyStatusBadge status={j.status} />
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-dark max-w-[140px] truncate"
                    title={j.pool_id ?? ''}>
                    {j.pool_id || '—'}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    {j.journey_type_id
                      ? <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 border border-purple-200">
                          {j.journey_type_id}
                        </span>
                      : <span className="text-border-strong">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5 text-right text-dark font-medium">
                    {j.session_count}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">
                    {fmtDate(j.created_at)}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">
                    {fmtDate(j.last_event_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Level 2: Instances for a journey ─────────────────────────────────────────

interface JourneyInstancesProps {
  tenantId:         string
  journey:          Journey
  onBack:           () => void
  onSelectInstance: (iid: string) => void
}

function JourneyInstances({ tenantId, journey, onBack, onSelectInstance }: JourneyInstancesProps) {
  const { t } = useTranslation('contacts')
  const { instances, loading, refresh } = useJourneyInstances(journey.journey_id, tenantId)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Sub-breadcrumb */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
        <button onClick={onBack}
          className="text-muted-light hover:text-dark transition-colors font-medium">
          {t('analise.journeys.breadcrumbs.journeys')}
        </button>
        <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
        <span className="text-dark font-medium font-mono" title={journey.journey_id}>
          {truncateId(journey.journey_id)}
        </span>
        <span className="ml-1 text-muted-light">·</span>
        <JourneyStatusBadge status={journey.status} />
        {journey.journey_type_id && (
          <>
            <span className="text-muted-light">·</span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 border border-purple-200">
              {journey.journey_type_id}
            </span>
          </>
        )}
      </div>

      {/* Journey metadata */}
      <div className="bg-surface-muted border-b border-border px-5 py-3 flex items-start gap-6 flex-shrink-0 flex-wrap text-xs">
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">{t('analise.journeys.detail.skill')}</span>
          <span className="font-mono text-dark">{journey.skill_id || '—'}</span>
        </div>
        {journey.pool_id && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.journeys.detail.pool')}</span>
            <span className="font-mono text-dark">{journey.pool_id}</span>
          </div>
        )}
        {journey.customer_id && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.journeys.detail.customer')}</span>
            <span className="font-mono text-dark">{journey.customer_id}</span>
          </div>
        )}
        {journey.origin_session_id && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.journeys.detail.originSession')}</span>
            <span className="font-mono text-primary" title={journey.origin_session_id}>
              {truncateId(journey.origin_session_id)}
            </span>
          </div>
        )}
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.journeys.detail.started')}</span>
          <span className="text-dark">{fmtDate(journey.created_at)}</span>
        </div>
        {journey.last_event_at && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.journeys.detail.lastEvent')}</span>
            <span className="text-dark">{fmtDate(journey.last_event_at)}</span>
          </div>
        )}
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">{t('analise.journeys.detail.sessions')}</span>
          <span className="font-medium text-dark">{journey.session_count}</span>
        </div>
      </div>

      {/* Instances list */}
      <div className="flex-1 overflow-auto px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-muted uppercase tracking-wide">
            {t('analise.journeys.detail.instances')}
          </h3>
          {!loading && (
            <button onClick={refresh}
              className="text-xs text-muted-light hover:text-muted transition-colors">
              {t('processes.refresh')}
            </button>
          )}
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-muted-light text-xs py-6">
            <Spinner />
            <span>{t('processes.instances.loading')}</span>
          </div>
        ) : instances.length === 0 ? (
          <p className="text-xs text-muted-light py-6">
            {t('analise.journeys.detail.instancesEmpty')}
          </p>
        ) : (
          <div className="space-y-2">
            {instances.map((inst: WorkflowInstance) => (
              <button key={inst.id}
                onClick={() => onSelectInstance(inst.id)}
                className="w-full text-left bg-white border border-border rounded-lg px-4 py-3 hover:border-primary/40 hover:bg-surface-muted transition-colors group">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-dark flex-1 truncate" title={inst.id}>
                    {truncateId(inst.id)}
                  </span>
                  <span className="text-xs font-mono text-muted-light">{inst.flow_id}</span>
                  <WfStatusBadge status={inst.status} />
                  <span className="text-xs text-muted-light">{fmtDate(inst.created_at)}</span>
                  <ChevronRight className="w-3.5 h-3.5 text-border-strong group-hover:text-primary transition-colors" aria-hidden="true" />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Level 3: Sessions of an instance (inside a journey context) ───────────────

interface InstanceSessionsProps {
  tenantId:        string
  instance:        WorkflowInstance
  journeyId:       string
  onBackToJourney: () => void
  onSelectSession: (sid: string) => void
}

function InstanceSessions({
  instance, journeyId, onBackToJourney, onSelectSession,
}: InstanceSessionsProps) {
  const { t } = useTranslation('contacts')
  const { sessions, loading } = useWorkflowInstanceSessions(instance.id)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Sub-breadcrumb */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 flex-wrap sticky top-0 z-10">
        <button onClick={() => onBackToJourney()}
          className="text-muted-light hover:text-dark transition-colors font-medium">
          {t('analise.journeys.breadcrumbs.journeys')}
        </button>
        <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
        <span className="font-mono text-muted-light" title={journeyId}>{truncateId(journeyId)}</span>
        <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
        <span className="text-dark font-medium font-mono" title={instance.id}>{truncateId(instance.id)}</span>
        <span className="text-muted-light">·</span>
        <WfStatusBadge status={instance.status} />
      </div>

      {/* Instance summary */}
      <div className="bg-surface-muted border-b border-border px-5 py-3 flex items-start gap-6 flex-shrink-0 flex-wrap text-xs">
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.instances.detail.flowId')}</span>
          <span className="font-mono text-dark">{instance.flow_id || '—'}</span>
        </div>
        {instance.current_step && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.instances.detail.currentStep')}</span>
            <span className="font-mono text-dark">{instance.current_step}</span>
          </div>
        )}
        {instance.outcome && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.instances.detail.outcome')}</span>
            <span className="text-dark">{instance.outcome}</span>
          </div>
        )}
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.instances.detail.created')}</span>
          <span className="text-dark">{fmtDate(instance.created_at)}</span>
        </div>
        {instance.completed_at && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">{t('processes.instances.detail.completed')}</span>
            <span className="text-dark">{fmtDate(instance.completed_at)}</span>
          </div>
        )}
      </div>

      {/* Sessions */}
      <div className="flex-1 overflow-auto px-5 py-4">
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-3">
          {t('processes.instances.sessions.title')}
        </h3>

        {loading ? (
          <div className="flex items-center gap-2 text-muted-light text-xs py-6">
            <Spinner />
            <span>{t('processes.instances.sessions.loading')}</span>
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-xs text-muted-light py-6">
            {t('processes.instances.sessions.empty')}
          </p>
        ) : (
          <div className="space-y-2">
            {sessions.map((s: InstanceSession) => (
              <button key={s.session_id}
                onClick={() => onSelectSession(s.session_id)}
                className="w-full text-left bg-white border border-border rounded-lg px-4 py-3 hover:border-primary/40 hover:bg-surface-muted transition-colors group">
                <div className="flex items-center gap-3">
                  <SessionTypeBadge type={s.type} />
                  <span className="font-mono text-xs text-dark flex-1 truncate" title={s.session_id}>
                    {s.session_id}
                  </span>
                  {s.channel && <span className="text-xs text-muted-light">{s.channel}</span>}
                  {s.responded_at && (
                    <span className="text-xs text-muted-light">
                      {t('processes.instances.sessions.responded')}: {fmtDate(s.responded_at)}
                    </span>
                  )}
                  {s.step_id && (
                    <span className="text-xs text-muted-light font-mono">{s.step_id}</span>
                  )}
                  <ChevronRight className="w-3.5 h-3.5 text-border-strong group-hover:text-primary transition-colors" aria-hidden="true" />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Single-journey loader ─────────────────────────────────────────────────────

function useJourneyById(
  journeyId: string | null,
  tenantId:  string,
): { journey: Journey | null; loading: boolean; notFound: boolean } {
  const [journey,  setJourney]  = React.useState<Journey | null>(null)
  const [loading,  setLoading]  = React.useState(false)
  const [notFound, setNotFound] = React.useState(false)

  React.useEffect(() => {
    if (!journeyId || !tenantId) { setJourney(null); setNotFound(false); return }
    let cancelled = false
    setLoading(true)
    setNotFound(false)
    fetch(`/v1/journeys/${encodeURIComponent(journeyId)}`, {
      headers: { 'x-tenant-id': tenantId },
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { if (!cancelled) setJourney(d as Journey) })
      .catch(() => { if (!cancelled) { setJourney(null); setNotFound(true) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [journeyId, tenantId])

  return { journey, loading, notFound }
}

function useInstanceById(
  instanceId: string | null,
  tenantId:   string,
): { instance: WorkflowInstance | null; loading: boolean; notFound: boolean } {
  const [instance, setInstance] = React.useState<WorkflowInstance | null>(null)
  const [loading,  setLoading]  = React.useState(false)
  const [notFound, setNotFound] = React.useState(false)

  React.useEffect(() => {
    if (!instanceId || !tenantId) { setInstance(null); setNotFound(false); return }
    let cancelled = false
    setLoading(true)
    setNotFound(false)
    fetch(`/v1/workflow/instances/${encodeURIComponent(instanceId)}`, {
      headers: { 'x-tenant-id': tenantId },
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { if (!cancelled) setInstance(d as WorkflowInstance) })
      .catch(() => { if (!cancelled) { setInstance(null); setNotFound(true) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [instanceId, tenantId])

  return { instance, loading, notFound }
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AnaliseJourneysPage() {
  const { tenantId } = useAuth()
  const { t } = useTranslation('contacts')
  const [searchParams, setSearchParams] = useSearchParams()

  const journeyId    = searchParams.get('journey')
  const instanceId   = searchParams.get('instance')
  const sessionId    = searchParams.get('session')

  const { journey,  loading: journeyLoading,  notFound: journeyNotFound  } = useJourneyById(journeyId, tenantId ?? '')
  const { instance, loading: instanceLoading, notFound: instanceNotFound } = useInstanceById(instanceId, tenantId ?? '')

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('processes.noTenant')}
      </div>
    )
  }

  // ── Level 4: SessionTranscript ────────────────────────────────────────────

  if (journeyId && instanceId && sessionId) {
    return (
      <div className="h-full overflow-hidden">
        <SessionTranscript
          tenantId={tenantId}
          sessionId={sessionId}
          canJoin={false}
          onBack={() => setSearchParams({ journey: journeyId, instance: instanceId })}
        />
      </div>
    )
  }

  // ── Level 3: Sessions of instance (within a journey) ─────────────────────

  if (journeyId && instanceId) {
    if (instanceLoading) {
      return (
        <div className="flex items-center justify-center h-full gap-2 text-muted-light text-sm">
          <Spinner />
        </div>
      )
    }
    if (instanceNotFound || !instance) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-light text-sm">
          <span>{t('analise.journeys.notFound')}</span>
          <button onClick={() => setSearchParams({ journey: journeyId })}
            className="text-primary text-xs hover:underline">
            {t('analise.journeys.breadcrumbs.journeys')}
          </button>
        </div>
      )
    }
    return (
      <InstanceSessions
        tenantId={tenantId}
        instance={instance}
        journeyId={journeyId}
        onBackToJourney={() => setSearchParams({ journey: journeyId })}
        onSelectSession={sid => setSearchParams({ journey: journeyId, instance: instanceId, session: sid })}
      />
    )
  }

  // ── Level 2: Instances of journey ────────────────────────────────────────

  if (journeyId) {
    if (journeyLoading) {
      return (
        <div className="flex items-center justify-center h-full gap-2 text-muted-light text-sm">
          <Spinner />
        </div>
      )
    }
    if (journeyNotFound || !journey) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-light text-sm">
          <span>{t('analise.journeys.notFound')}</span>
          <button onClick={() => setSearchParams({})}
            className="text-primary text-xs hover:underline">
            {t('analise.journeys.breadcrumbs.journeys')}
          </button>
        </div>
      )
    }
    return (
      <JourneyInstances
        tenantId={tenantId}
        journey={journey}
        onBack={() => setSearchParams({})}
        onSelectInstance={iid => setSearchParams({ journey: journeyId, instance: iid })}
      />
    )
  }

  // ── Level 1: Journey list ─────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <JourneyList
        tenantId={tenantId}
        onSelectJourney={id => setSearchParams({ journey: id })}
      />
    </div>
  )
}
