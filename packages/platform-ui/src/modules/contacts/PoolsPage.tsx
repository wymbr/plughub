/**
 * PoolsPage — /contacts/pools
 *
 * Real-time operational view of all pools:
 *   - Capacity: available / busy / paused agents
 *   - Queue: depth + estimated wait time
 *   - SLA target
 *   - Drill-down: contacts currently waiting in the pool's queue
 *
 * Data sources:
 *   GET /v1/operational/pools            → Redis snapshots + Pool config from DB
 *   GET /v1/operational/pools/:id/queue  → ZSET queue entries
 *
 * Auto-refresh every 15 s. Snapshot age shown per row.
 *
 * Also exports PoolStatusCard for reuse in dashboard widgets (Task #35).
 */
import React, { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Server } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface PoolOp {
  pool_id:             string
  description:         string | null
  sla_target_ms:       number
  channel_types:       string[]
  pool_status:         string
  op_status:           'available' | 'queued' | 'empty' | 'unknown'
  available:           number
  queue_length:        number
  estimated_wait_ms:   number | null
  snapshot_age_ms:     number | null
  snapshot_updated_at: string | null
  has_snapshot:        boolean
}

interface QueueEntry {
  position:          number
  session_id:        string
  queued_at:         string
  wait_ms:           number
  estimated_wait_ms: number
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms === null || ms < 0) return '—'
  if (ms < 1000)  return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60)    return `${s}s`
  const m = Math.floor(s / 60), rem = s % 60
  return `${m}m${rem > 0 ? `${rem}s` : ''}`
}

type TFn = (key: string, opts?: Record<string, unknown>) => string

function fmtAge(ms: number | null, t: TFn): string {
  if (ms === null) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 5)  return t('pools.age.now')
  if (s < 60) return t('pools.age.seconds', { s })
  const m = Math.floor(s / 60)
  return t('pools.age.minutes', { m })
}

function statusConfig(op: PoolOp['op_status']) {
  switch (op) {
    case 'available': return { dot: '#22c55e', labelKey: 'pools.status.available', bg: '#22c55e18' }
    case 'queued':    return { dot: '#eab308', labelKey: 'pools.status.queued',    bg: '#eab30818' }
    case 'empty':     return { dot: '#ef4444', labelKey: 'pools.status.empty',     bg: '#ef444418' }
    default:          return { dot: '#6b7280', labelKey: 'pools.status.unknown',   bg: '#6b728018' }
  }
}

// ── API ────────────────────────────────────────────────────────────────────────

async function fetchPools(tenantId: string): Promise<PoolOp[]> {
  const res = await fetch('/v1/operational/pools', {
    headers: { 'x-tenant-id': tenantId },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return body.items ?? []
}

async function fetchQueue(tenantId: string, poolId: string): Promise<QueueEntry[]> {
  const res = await fetch(`/v1/operational/pools/${poolId}/queue`, {
    headers: { 'x-tenant-id': tenantId },
  })
  if (!res.ok) return []
  const body = await res.json()
  return body.queue ?? []
}

// ── PoolStatusCard — reusable for dashboard (Task #35) ─────────────────────────

export function PoolStatusCard({ pool, compact = false }: { pool: PoolOp; compact?: boolean }) {
  const { t } = useTranslation('contacts')
  const st = statusConfig(pool.op_status)
  return (
    <div
      className="rounded-xl bg-white border p-4 flex flex-col gap-3"
      style={{ borderColor: '#e2e8f0', borderTop: `3px solid ${st.dot}` }}>

      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-bold text-dark truncate">{pool.pool_id}</div>
          {pool.description && !compact && (
            <div className="text-xs text-muted mt-0.5 truncate">{pool.description}</div>
          )}
        </div>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap flex-shrink-0"
          style={{ background: st.bg, color: st.dot }}>
          {t(st.labelKey)}
        </span>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-3 gap-2">
        <Metric label={t('pools.card.available')} value={pool.available}      color="#22c55e" />
        <Metric label={t('pools.card.inSession')} value={pool.available === 0 && pool.queue_length === 0 && !pool.has_snapshot ? '—' : (pool.has_snapshot ? String(pool.queue_length > 0 ? '~' : pool.available) : '—')} color="#3b82f6" />
        <Metric label={t('pools.card.queue')}     value={pool.queue_length}   color={pool.queue_length > 0 ? '#eab308' : '#6b7280'} />
      </div>

      {/* Wait + SLA */}
      {!compact && (
        <div className="flex gap-4 text-xs text-muted">
          <span>{t('pools.card.estWait')}: <span className="text-dark">{fmtMs(pool.estimated_wait_ms)}</span></span>
          <span>SLA: <span className="text-dark">{fmtMs(pool.sla_target_ms)}</span></span>
        </div>
      )}

      {/* Channels */}
      {!compact && pool.channel_types.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {pool.channel_types.map(ch => (
            <span key={ch} className="text-2xs px-1.5 py-0.5 rounded bg-surface-alt text-muted">{ch}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div className="flex flex-col items-center rounded-lg py-2 bg-surface-alt">
      <span className="text-lg font-bold" style={{ color }}>{value}</span>
      <span className="text-2xs text-muted mt-0.5 text-center leading-tight">{label}</span>
    </div>
  )
}

// ── QueueDrilldown ─────────────────────────────────────────────────────────────

function QueueDrilldown({ tenantId, poolId, slaMs }: { tenantId: string; poolId: string; slaMs: number }) {
  const { t } = useTranslation('contacts')
  const [queue,   setQueue]   = useState<QueueEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchQueue(tenantId, poolId)
      .then(setQueue)
      .finally(() => setLoading(false))
    const id = setInterval(() => {
      fetchQueue(tenantId, poolId).then(setQueue)
    }, 15_000)
    return () => clearInterval(id)
  }, [tenantId, poolId])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 px-6 text-xs text-muted-light">
        <Spinner /> {t('pools.queue.loading')}
      </div>
    )
  }

  if (queue.length === 0) {
    return (
      <div className="py-4 px-6 text-xs text-muted-light italic">
        {t('pools.queue.empty')}
      </div>
    )
  }

  return (
    <div className="px-6 py-3">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted border-b border-border">
            <th className="text-left py-1.5 pr-4 font-medium w-12">{t('pools.queue.columns.position')}</th>
            <th className="text-left py-1.5 pr-4 font-medium">{t('pools.queue.columns.session')}</th>
            <th className="text-left py-1.5 pr-4 font-medium">{t('pools.queue.columns.waiting')}</th>
            <th className="text-left py-1.5 font-medium">{t('pools.queue.columns.estWait')}</th>
          </tr>
        </thead>
        <tbody>
          {queue.map(entry => {
            const overSla = entry.wait_ms > slaMs
            return (
              <tr key={entry.session_id} className="border-b border-border">
                <td className="py-2 pr-4 text-muted font-mono">{entry.position}</td>
                <td className="py-2 pr-4 font-mono text-secondary">{entry.session_id}</td>
                <td className="py-2 pr-4" style={{ color: overSla ? '#DC2626' : '#1e293b' }}>
                  {fmtMs(entry.wait_ms)}
                  {overSla && <span className="ml-1.5 text-2xs bg-red-light text-red-text px-1 rounded">{t('pools.slaExceeded')}</span>}
                </td>
                <td className="py-2 text-muted">{fmtMs(entry.estimated_wait_ms)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── PoolsPage ──────────────────────────────────────────────────────────────────

export default function PoolsPage() {
  const { tenantId } = useAuth()
  const { t, i18n } = useTranslation('contacts')

  const [pools,        setPools]        = useState<PoolOp[]>([])
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [lastRefresh,  setLastRefresh]  = useState<Date | null>(null)
  const [expandedPool, setExpandedPool] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState<string>('')
  const [filterPool,   setFilterPool]   = useState<string>('')

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const data = await fetchPools(tenantId)
      setPools(data)
      setLastRefresh(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('noTenant')}
      </div>
    )
  }

  // Filter
  const filtered = pools.filter(p => {
    if (filterStatus && p.op_status !== filterStatus) return false
    if (filterPool   && p.pool_id !== filterPool)     return false
    return true
  })

  // Summary counts
  const totalAvailable = pools.reduce((s, p) => s + p.available, 0)
  const totalQueued    = pools.reduce((s, p) => s + p.queue_length, 0)
  const poolsWithQueue = pools.filter(p => p.queue_length > 0).length

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Header bar */}
      <div className="flex-shrink-0 px-5 pt-4 pb-3 bg-white border-b border-border">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <span className="text-xs text-muted-light">
                {t('pools.updatedAt', { time: lastRefresh.toLocaleTimeString(i18n.language) })}
              </span>
            )}
            {loading
              ? <Spinner />
              : <button onClick={load} className="text-xs text-muted-light hover:text-dark transition-colors px-2 py-1">{t('pools.refresh')}</button>
            }
          </div>
        </div>

        {/* Summary pills */}
        <div className="flex gap-3 mb-3">
          <SummaryPill label={t('pools.summary.available')} value={totalAvailable} color="#22c55e" />
          <SummaryPill label={t('pools.summary.queued')}    value={totalQueued}    color={totalQueued > 0 ? '#eab308' : '#6b7280'} />
          <SummaryPill label={t('pools.summary.withQueue')} value={poolsWithQueue} color={poolsWithQueue > 0 ? '#f97316' : '#6b7280'} />
          <SummaryPill label={t('pools.summary.total')}     value={pools.length}   color="#1B4F8A" />
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <select
            value={filterPool}
            onChange={e => { setFilterPool(e.target.value); setExpandedPool(null) }}
            className="text-xs bg-white border border-border-strong rounded px-2.5 py-1.5 text-dark focus:outline-none focus:ring-2 focus:ring-primary/30 min-w-[180px]"
          >
            <option value="">{t('monitor.allPools')}</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="text-xs bg-white border border-border-strong rounded px-2.5 py-1.5 text-dark focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{t('pools.allStatuses')}</option>
            <option value="available">{t('pools.status.available')}</option>
            <option value="queued">{t('pools.status.queued')}</option>
            <option value="empty">{t('pools.status.empty')}</option>
          </select>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-5 mt-3 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">
          {t('pools.loadError')}: {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-16 text-muted-light gap-2">
            <Server className="w-10 h-10" aria-hidden="true" />
            <span className="text-sm">{t('pools.noData')}</span>
          </div>
        )}

        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 z-10 bg-white">
            <tr className="text-muted text-xs border-b border-border">
              <th className="text-left px-5 py-3 font-medium">{t('pools.columns.pool')}</th>
              <th className="text-center px-3 py-3 font-medium w-28">{t('pools.columns.status')}</th>
              <th className="text-center px-3 py-3 font-medium w-24">{t('pools.columns.available')}</th>
              <th className="text-center px-3 py-3 font-medium w-20">{t('pools.columns.queue')}</th>
              <th className="text-center px-3 py-3 font-medium w-28">{t('pools.columns.estWait')}</th>
              <th className="text-center px-3 py-3 font-medium w-20">{t('pools.columns.sla')}</th>
              <th className="text-left px-3 py-3 font-medium">{t('pools.columns.channels')}</th>
              <th className="text-right px-5 py-3 font-medium w-28">{t('pools.columns.snapshot')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(pool => {
              const st      = statusConfig(pool.op_status)
              const expanded = expandedPool === pool.pool_id
              return (
                <React.Fragment key={pool.pool_id}>
                  <tr
                    className="border-b border-border cursor-pointer transition-colors"
                    style={{ background: expanded ? '#EBF2FA' : 'transparent' }}
                    onMouseEnter={e => { if (!expanded) (e.currentTarget as HTMLElement).style.background = '#F8FAFC' }}
                    onMouseLeave={e => { if (!expanded) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                    onClick={() => setExpandedPool(expanded ? null : pool.pool_id)}
                  >
                    {/* Pool name + description */}
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {expanded
                          ? <ChevronDown  className="w-3.5 h-3.5 text-primary" aria-hidden="true" />
                          : <ChevronRight className="w-3.5 h-3.5 text-muted-light" aria-hidden="true" />
                        }
                        <div>
                          <div className="font-semibold text-dark text-xs">{pool.pool_id}</div>
                          {pool.description && (
                            <div className="text-xs text-muted truncate max-w-xs">{pool.description}</div>
                          )}
                        </div>
                      </div>
                    </td>

                    {/* Status */}
                    <td className="px-3 py-3 text-center">
                      <span className="text-xs font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: st.bg, color: st.dot }}>
                        {t(st.labelKey)}
                      </span>
                    </td>

                    {/* Available */}
                    <td className="px-3 py-3 text-center">
                      {pool.has_snapshot
                        ? <span className="text-sm font-bold" style={{ color: pool.available > 0 ? '#22c55e' : '#6b7280' }}>{pool.available}</span>
                        : <span className="text-xs text-warning" title="Snapshot Redis expirado — aguardando próximo evento de roteamento">{t('pools.noSnapshot')}</span>
                      }
                    </td>

                    {/* Queue */}
                    <td className="px-3 py-3 text-center">
                      {!pool.has_snapshot
                        ? <span className="text-xs text-muted-light">—</span>
                        : pool.queue_length > 0
                          ? <span className="text-sm font-bold text-warning">{pool.queue_length}</span>
                          : <span className="text-xs text-muted-light">0</span>
                      }
                    </td>

                    {/* Estimated wait */}
                    <td className="px-3 py-3 text-center text-xs text-muted">
                      {fmtMs(pool.estimated_wait_ms)}
                    </td>

                    {/* SLA */}
                    <td className="px-3 py-3 text-center text-xs text-muted">
                      {fmtMs(pool.sla_target_ms)}
                    </td>

                    {/* Channels */}
                    <td className="px-3 py-3">
                      <div className="flex gap-1 flex-wrap">
                        {pool.channel_types.map(ch => (
                          <span key={ch} className="text-2xs px-1.5 py-0.5 rounded bg-surface-alt text-muted">{ch}</span>
                        ))}
                      </div>
                    </td>

                    {/* Snapshot age */}
                    <td className="px-5 py-3 text-right">
                      <span className={`text-xs ${
                        pool.snapshot_age_ms === null          ? 'text-muted-light'
                        : pool.snapshot_age_ms > 90_000        ? 'text-red'
                        : pool.snapshot_age_ms > 30_000        ? 'text-warning'
                        : 'text-muted'
                      }`}>
                        {fmtAge(pool.snapshot_age_ms, t)}
                      </span>
                    </td>
                  </tr>

                  {/* Queue drill-down row */}
                  {expanded && (
                    <tr key={`${pool.pool_id}-queue`} className="border-b border-border">
                      <td colSpan={8} className="p-0 bg-surface-muted">
                        <div className="pl-8 border-l-2 border-primary/30 ml-5 my-1">
                          <div className="px-4 pt-2 pb-1 text-xs font-semibold text-muted uppercase tracking-wider">
                            {t('pools.queueOf', { pool: pool.pool_id })}
                          </div>
                          <QueueDrilldown
                            tenantId={tenantId}
                            poolId={pool.pool_id}
                            slaMs={pool.sla_target_ms}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SummaryPill ────────────────────────────────────────────────────────────────

function SummaryPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-white">
      <span className="text-base font-bold" style={{ color }}>{value}</span>
      <span className="text-xs text-muted">{label}</span>
    </div>
  )
}
