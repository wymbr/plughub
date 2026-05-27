/**
 * AnaliseProcessosPage — /analise/processos
 *
 * Analytics view of workflow instances with 3-level URL-param drill-down:
 *   Level 1: /analise/processos                     — filtered instances table
 *   Level 2: /analise/processos?instance=:id        — sessions of that instance
 *   Level 3: /analise/processos?instance=:id&session=:sid — SessionTranscript
 *
 * Data source: GET /v1/workflow/instances (workflow-api, proxied via Vite)
 */
import React, { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight, FileText } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'
import * as registryApi from '@/api/registry'
import type { Pool } from '@/types'
import {
  useWorkflowInstancesFiltered,
  useWorkflowInstanceSessions,
  type WorkflowInstance,
  type WorkflowStatus,
  type InstanceSession,
} from '@/modules/workflows/api/hooks'

// ── Helpers ───────────────────────────────────────────────────────────────────

function isoToday(): string { return new Date().toISOString().slice(0, 10) }
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29)
  return d.toISOString().slice(0, 10)
}
function fmtDate(iso: string): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}
function fmtDuration(from: string, to?: string | null): string {
  if (!from || !to) return '—'
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000); const s = Math.round((ms % 60_000) / 1000)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}
function truncateId(id: string | undefined): string {
  if (!id) return '—'
  return id.length > 16 ? `…${id.slice(-12)}` : id
}

// ── StatusBadge ───────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  active:    'bg-primary-light text-primary border-primary/30',
  suspended: 'bg-warning-light text-warning border-warning/30',
  completed: 'bg-green-light text-green border-green/30',
  failed:    'bg-red-light text-red border-red/30',
  timed_out: 'bg-warning-light text-warning border-warning/30',
  cancelled: 'bg-surface-alt text-muted border-border',
}

function StatusBadge({ status }: { status: WorkflowStatus }) {
  const { t } = useTranslation('contacts')
  const cls = STATUS_COLORS[status] ?? 'bg-surface-alt text-muted border-border'
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

// ── Level 1: Instances list ───────────────────────────────────────────────────

interface InstancesListProps {
  tenantId: string
  onSelectInstance: (id: string) => void
}

const PROC_PAGE_SIZE = 50

function InstancesList({ tenantId, onSelectInstance }: InstancesListProps) {
  const { t } = useTranslation('contacts')
  const [fromDt,  setFromDt]  = React.useState(iso30DaysAgo)
  const [toDt,    setToDt]    = React.useState(isoToday)
  const [status,  setStatus]  = React.useState<string>('all')
  const [poolId,  setPoolId]  = React.useState('')
  const [flowId,  setFlowId]  = React.useState('')
  const [page,    setPage]    = React.useState(1)
  const [pools,   setPools]   = React.useState<Pool[]>([])

  React.useEffect(() => {
    if (!tenantId) return
    registryApi.listPools(tenantId).then(r => setPools(r.items)).catch(() => {})
  }, [tenantId])

  const filters = useMemo(() => ({
    status:  status || undefined,
    poolId:  poolId || undefined,
    flowId:  flowId || undefined,
    fromDt,
    toDt,
  }), [status, poolId, flowId, fromDt, toDt])

  const { instances, loading, error, refresh } = useWorkflowInstancesFiltered(tenantId, filters)

  // Reset page when filters change
  React.useEffect(() => { setPage(1) }, [filters])

  const totalPages    = Math.max(1, Math.ceil(instances.length / PROC_PAGE_SIZE))
  const pagedInstances = instances.slice((page - 1) * PROC_PAGE_SIZE, page * PROC_PAGE_SIZE)

  const WF_STATUSES: string[] = ['all', 'active', 'suspended', 'completed', 'failed', 'timed_out', 'cancelled']

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('processes.instances.filters.from')}</label>
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
          {WF_STATUSES.map(s => (
            <option key={s} value={s}>{t(`processes.wfStatus.${s}`, { defaultValue: s })}</option>
          ))}
        </select>

        <select value={poolId} onChange={e => setPoolId(e.target.value)}
          className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
          <option value="">{t('processes.instances.filters.poolPlaceholder')}</option>
          {pools.map(p => (
            <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
          ))}
        </select>

        <input type="text" value={flowId} onChange={e => setFlowId(e.target.value)}
          placeholder={t('processes.instances.filters.flowPlaceholder')}
          className="text-xs border border-border-strong rounded px-2 py-1 w-40 focus:outline-none focus:ring-1 focus:ring-primary/40" />

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
            : <><strong className="text-dark">{t('processes.instances.totalCount', { count: instances.length })}</strong>
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
        {error ? (
          <div className="flex flex-col items-center justify-center py-20 text-red gap-2">
            <FileText className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm font-medium">Erro ao carregar processos</span>
            <span className="text-xs text-muted font-mono max-w-lg text-center break-all">{error}</span>
          </div>
        ) : instances.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <FileText className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm">{t('processes.instances.empty')}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.flowId')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.status')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.pool')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.originSession')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.journey')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.created')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.duration')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('processes.instances.columns.outcome')}
                </th>
              </tr>
            </thead>
            <tbody>
              {pagedInstances.map(inst => (
                <tr key={inst.id}
                  onClick={() => onSelectInstance(inst.id)}
                  className="border-t border-border hover:bg-surface-muted transition-colors cursor-pointer">
                  <td className="px-3 py-2.5 font-mono text-dark max-w-[180px] truncate" title={inst.flow_id}>
                    {inst.flow_id || '—'}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={inst.status} />
                    {inst.suspend_reason && (
                      <span className="ml-1.5 text-xs text-muted-light italic">
                        {t(`processes.wfSuspend.${inst.suspend_reason}`, { defaultValue: inst.suspend_reason })}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-muted-light text-xs max-w-[120px] truncate"
                    title={(inst as WorkflowInstance & { pool_id?: string }).pool_id ?? ''}>
                    {(inst as WorkflowInstance & { pool_id?: string }).pool_id || '—'}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    {inst.origin_session_id
                      ? <span className="text-primary cursor-pointer hover:underline" title={inst.origin_session_id}>
                          {truncateId(inst.origin_session_id)}
                        </span>
                      : <span className="text-border-strong">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    {inst.session_id
                      ? <span className="text-muted-light" title={inst.session_id}>
                          {truncateId(inst.session_id)}
                        </span>
                      : <span className="text-border-strong">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">
                    {fmtDate(inst.created_at)}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">
                    {fmtDuration(inst.created_at, inst.completed_at)}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light">
                    {inst.outcome || <span className="text-border-strong">—</span>}
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

// ── Level 2: Session list for an instance ─────────────────────────────────────

interface SessionsListProps {
  tenantId:   string
  instance:   WorkflowInstance
  onBack:     () => void
  onSelectSession: (sessionId: string) => void
}

function SessionsList({ tenantId, instance, onBack, onSelectSession }: SessionsListProps) {
  const { t } = useTranslation('contacts')
  const { sessions, loading } = useWorkflowInstanceSessions(instance.id)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Sub-breadcrumb */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
        <button onClick={onBack}
          className="text-muted-light hover:text-dark transition-colors font-medium">
          {t('processes.instances.breadcrumb')}
        </button>
        <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
        <span className="text-dark font-medium font-mono" title={instance.id}>
          {truncateId(instance.id)}
        </span>
        <span className="ml-1 text-muted-light">·</span>
        <StatusBadge status={instance.status} />
      </div>

      {/* Instance metadata */}
      <div className="bg-surface-muted border-b border-border px-5 py-3 flex items-start gap-6 flex-shrink-0 flex-wrap text-xs">
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">
            {t('processes.instances.detail.flowId')}
          </span>
          <span className="font-mono text-dark">{instance.flow_id || '—'}</span>
        </div>
        {instance.current_step && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">
              {t('processes.instances.detail.currentStep')}
            </span>
            <span className="font-mono text-dark">{instance.current_step}</span>
          </div>
        )}
        {instance.origin_session_id && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">
              {t('processes.instances.detail.originSession')}
            </span>
            <button
              onClick={() => onSelectSession(instance.origin_session_id!)}
              className="font-mono text-primary hover:underline">
              {truncateId(instance.origin_session_id)}
            </button>
          </div>
        )}
        <div>
          <span className="text-muted uppercase tracking-wide block mb-0.5">
            {t('processes.instances.detail.created')}
          </span>
          <span className="text-dark">{fmtDate(instance.created_at)}</span>
        </div>
        {instance.completed_at && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">
              {t('processes.instances.detail.completed')}
            </span>
            <span className="text-dark">{fmtDate(instance.completed_at)}</span>
          </div>
        )}
        {instance.outcome && (
          <div>
            <span className="text-muted uppercase tracking-wide block mb-0.5">
              {t('processes.instances.detail.outcome')}
            </span>
            <span className="text-dark">{instance.outcome}</span>
          </div>
        )}
      </div>

      {/* Sessions list */}
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
                  {s.channel && (
                    <span className="text-xs text-muted-light">{s.channel}</span>
                  )}
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

// ── InstanceDetail loader — fetches single instance for Level 2 header ────────

function useInstanceById(
  tenantId: string,
  instanceId: string | null,
): { instance: WorkflowInstance | null; loading: boolean } {
  const [instance, setInstance] = React.useState<WorkflowInstance | null>(null)
  const [loading, setLoading]   = React.useState(false)

  React.useEffect(() => {
    if (!instanceId || !tenantId) { setInstance(null); return }
    let cancelled = false
    setLoading(true)
    fetch(`/v1/workflow/instances/${encodeURIComponent(instanceId)}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { if (!cancelled) setInstance(d as WorkflowInstance) })
      .catch(() => { if (!cancelled) setInstance(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [instanceId, tenantId])

  return { instance, loading }
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AnaliseProcessosPage() {
  const { tenantId } = useAuth()
  const { t } = useTranslation('contacts')
  const [searchParams, setSearchParams] = useSearchParams()

  const instanceId = searchParams.get('instance')
  const sessionId  = searchParams.get('session')

  const { instance, loading: instanceLoading } = useInstanceById(tenantId ?? '', instanceId)

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('processes.noTenant')}
      </div>
    )
  }

  // ── Level 3: SessionTranscript ────────────────────────────────────────────

  if (instanceId && sessionId) {
    return (
      <div className="h-full overflow-hidden">
        <SessionTranscript
          tenantId={tenantId}
          sessionId={sessionId}
          canJoin={false}
          onBack={() => setSearchParams({ instance: instanceId })}
        />
      </div>
    )
  }

  // ── Level 2: Sessions of instance ────────────────────────────────────────

  if (instanceId) {
    if (instanceLoading || !instance) {
      return (
        <div className="flex items-center justify-center h-full gap-2 text-muted-light text-sm">
          <Spinner />
        </div>
      )
    }
    return (
      <SessionsList
        tenantId={tenantId}
        instance={instance}
        onBack={() => setSearchParams({})}
        onSelectSession={sid => setSearchParams({ instance: instanceId, session: sid })}
      />
    )
  }

  // ── Level 1: Instances list ───────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <InstancesList
        tenantId={tenantId}
        onSelectInstance={id => setSearchParams({ instance: id })}
      />
    </div>
  )
}
