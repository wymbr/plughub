/**
 * AgentsTab.tsx — Arc 8
 * Agent availability & pause report, embedded as a tab in ContactsPage.
 *
 * Receives tenantId + filters (fromDt, toDt, poolId, agentId) from the
 * shared ContactsPage filter bar — no local filter duplication needed.
 *
 * Two inner sub-tabs:
 *   availability — pivot table agent × date, total pause time heatmap
 *   pauses       — flat rows per (agent, pool, date, reason) with CSV export
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ContactFilters } from '../types'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReasonBreakdown {
  reason_id:    string
  reason_label: string
  count:        number
  total_ms:     number
}

interface AvailabilityRow {
  agent_type_id:    string
  pool_id:          string
  period_date:      string
  total_pauses:     number
  total_pause_ms:   number
  reason_breakdown: ReasonBreakdown[]
}

interface AvailabilityMeta {
  page:      number
  page_size: number
  total:     number
  from_dt:   string
  to_dt:     string
}

interface AvailabilityResponse {
  data: AvailabilityRow[]
  meta: AvailabilityMeta
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDuration(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m`
  return `${m}m`
}

function shortAgent(id: string): string {
  return id.replace(/_v\d+$/, '').replace(/_/g, ' ')
}

function shortPool(id: string): string {
  return id.replace(/_/g, ' ').replace(/\s*(humano|ia|v\d+)$/i, '').trim() || id
}

// ── Data hook ─────────────────────────────────────────────────────────────────

function useAvailability(params: {
  fromDt:   string
  toDt:     string
  poolId:   string
  agentId:  string
  page:     number
  pageSize: number
  tenantId: string
}) {
  const [data,    setData]    = useState<AvailabilityRow[]>([])
  const [meta,    setMeta]    = useState<AvailabilityMeta | null>(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const { fromDt, toDt, poolId, agentId, page, pageSize, tenantId } = params

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)

    const qs = new URLSearchParams({
      tenant_id: tenantId,
      from_dt:   fromDt,
      to_dt:     toDt,
      page:      String(page),
      page_size: String(pageSize),
    })
    if (poolId)  qs.set('pool_id',       poolId)
    if (agentId) qs.set('agent_type_id', agentId)

    fetch(`/reports/agent-availability?${qs}`)
      .then(r => r.ok ? (r.json() as Promise<AvailabilityResponse>) : Promise.reject(r.status))
      .then(resp => {
        if (cancelled) return
        setData(resp.data ?? [])
        setMeta(resp.meta ?? null)
      })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [fromDt, toDt, poolId, agentId, page, pageSize, tenantId])

  return { data, meta, loading, error }
}

// ── Availability sub-tab (pivot table) ───────────────────────────────────────

const AvailabilitySubTab: React.FC<{ rows: AvailabilityRow[] }> = ({ rows }) => {
  const { t } = useTranslation('contacts')

  if (rows.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-light text-sm">
        {t('agents.availability.noData')}
      </div>
    )
  }

  const dateSet = new Set(rows.map(r => r.period_date))
  const dates   = [...dateSet].sort()

  type Key = string
  const groups = new Map<Key, { agent: string; pool: string; byDate: Map<string, AvailabilityRow> }>()
  for (const row of rows) {
    const key = `${row.agent_type_id}|${row.pool_id}`
    if (!groups.has(key)) {
      groups.set(key, { agent: row.agent_type_id, pool: row.pool_id, byDate: new Map() })
    }
    groups.get(key)!.byDate.set(row.period_date, row)
  }

  return (
    <div className="flex-1 overflow-auto">
      <table className="min-w-full text-xs border-collapse">
        <thead className="sticky top-0 bg-white z-10">
          <tr className="border-b border-border">
            <th className="text-left px-3 py-2 font-semibold text-muted whitespace-nowrap min-w-40">{t('agents.availability.columns.agent')}</th>
            <th className="text-left px-3 py-2 font-semibold text-muted whitespace-nowrap min-w-[120px]">{t('agents.availability.columns.pool')}</th>
            {dates.map(d => (
              <th key={d} className="px-2 py-2 font-semibold text-muted text-center whitespace-nowrap min-w-20">
                {d.slice(5)}
              </th>
            ))}
            <th className="px-3 py-2 font-semibold text-muted text-right whitespace-nowrap">{t('agents.availability.columns.total')}</th>
          </tr>
        </thead>
        <tbody>
          {[...groups.entries()].map(([key, { agent, pool, byDate }]) => {
            const totalMs = [...byDate.values()].reduce((s, r) => s + r.total_pause_ms, 0)
            return (
              <tr key={key} className="border-b border-border hover:bg-surface-muted transition-colors">
                <td className="px-3 py-2 text-dark font-medium truncate max-w-[200px]" title={agent}>
                  {shortAgent(agent)}
                </td>
                <td className="px-3 py-2 text-muted truncate max-w-[140px]" title={pool}>
                  {shortPool(pool)}
                </td>
                {dates.map(d => {
                  const row = byDate.get(d)
                  if (!row) return <td key={d} className="px-2 py-2 text-center text-border">—</td>
                  const pauseMs   = row.total_pause_ms
                  const intensity = Math.min(pauseMs / (4 * 3_600_000), 1)
                  const bg = pauseMs === 0
                    ? 'bg-surface-muted text-border-strong'
                    : intensity < 0.25
                      ? 'bg-warning-light text-warning-text'
                      : intensity < 0.5
                        ? 'bg-warning-light text-warning-text'
                        : 'bg-warning text-white'
                  return (
                    <td key={d} className={`px-2 py-2 text-center font-mono tabular-nums ${bg}`}
                        title={t('agents.availability.pauseTooltip', { count: row.total_pauses, duration: fmtDuration(pauseMs) })}>
                      {pauseMs > 0 ? fmtDuration(pauseMs) : '—'}
                    </td>
                  )
                })}
                <td className="px-3 py-2 text-right font-semibold text-dark tabular-nums">
                  {totalMs > 0 ? fmtDuration(totalMs) : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Pauses sub-tab (flat rows + CSV) ─────────────────────────────────────────

const PausesSubTab: React.FC<{
  rows:   AvailabilityRow[]
  meta:   AvailabilityMeta | null
  page:   number
  onPage: (p: number) => void
  csvUrl: string
}> = ({ rows, meta, page, onPage, csvUrl }) => {
  const { t } = useTranslation('contacts')

  const flat: Array<{
    date: string; agent: string; pool: string
    reason: string; count: number; ms: number
  }> = []

  for (const r of rows) {
    if (r.reason_breakdown.length === 0) {
      flat.push({ date: r.period_date, agent: r.agent_type_id, pool: r.pool_id,
                  reason: '—', count: r.total_pauses, ms: r.total_pause_ms })
    } else {
      for (const rb of r.reason_breakdown) {
        flat.push({ date: r.period_date, agent: r.agent_type_id, pool: r.pool_id,
                    reason: rb.reason_label || rb.reason_id, count: rb.count, ms: rb.total_ms })
      }
    }
  }

  if (flat.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 text-muted-light text-sm">
        <span className="text-3xl">📋</span>
        <p>{t('agents.availability.noPauses')}</p>
      </div>
    )
  }

  const totalPages = meta ? Math.ceil(meta.total / (meta.page_size || 50)) : 1

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-end px-4 py-2 border-b border-border bg-surface-muted flex-shrink-0">
        <a href={csvUrl} download
          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium
            border border-border-strong text-muted hover:bg-white hover:border-border transition-colors">
          {t('agents.availability.exportCsv')}
        </a>
      </div>

      <div className="flex-1 overflow-auto">
        <table className="min-w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-border">
              <th className="text-left px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.date')}</th>
              <th className="text-left px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.agent')}</th>
              <th className="text-left px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.pool')}</th>
              <th className="text-left px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.reason')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.count')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.duration')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted">{t('agents.availability.columns.avgPause')}</th>
            </tr>
          </thead>
          <tbody>
            {flat.map((r, i) => (
              <tr key={i} className="border-b border-border hover:bg-surface-muted transition-colors">
                <td className="px-3 py-2 text-muted whitespace-nowrap font-mono">{r.date}</td>
                <td className="px-3 py-2 text-dark font-medium truncate max-w-[180px]" title={r.agent}>
                  {shortAgent(r.agent)}
                </td>
                <td className="px-3 py-2 text-muted truncate max-w-[130px]" title={r.pool}>
                  {shortPool(r.pool)}
                </td>
                <td className="px-3 py-2">
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full
                    bg-warning-light border border-warning/30 text-warning-text font-medium whitespace-nowrap">
                    {r.reason}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{r.count}</td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-dark">
                  {fmtDuration(r.ms)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">
                  {r.count > 0 ? fmtDuration(Math.round(r.ms / r.count)) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {meta && totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-border bg-white flex-shrink-0">
          <span className="text-xs text-muted">{t('agents.availability.results', { count: meta.total })}</span>
          <div className="flex items-center gap-1">
            <button onClick={() => onPage(page - 1)} disabled={page <= 1}
              className="px-2 py-1 text-xs rounded border border-border disabled:opacity-40 hover:bg-surface-muted">
              {t('agents.availability.prevPage')}
            </button>
            <span className="text-xs text-muted px-2">{t('agents.availability.page', { page, total: totalPages })}</span>
            <button onClick={() => onPage(page + 1)} disabled={page >= totalPages}
              className="px-2 py-1 text-xs rounded border border-border disabled:opacity-40 hover:bg-surface-muted">
              {t('agents.availability.nextPage')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── AgentsTab ─────────────────────────────────────────────────────────────────

type SubTab = 'availability' | 'pauses'

interface Props {
  tenantId: string
  filters:  ContactFilters
}

export function AgentsTab({ tenantId, filters }: Props) {
  const { t } = useTranslation('contacts')
  const [subTab, setSubTab] = useState<SubTab>('availability')
  const [page,   setPage]   = useState(1)

  // Reset page when filters change
  const resetPage = useCallback(() => setPage(1), [])
  useEffect(resetPage, [filters.fromDt, filters.toDt, filters.poolId, filters.agentId, resetPage])

  const { data, meta, loading, error } = useAvailability({
    fromDt:   filters.fromDt,
    toDt:     filters.toDt,
    poolId:   filters.poolId,
    agentId:  filters.agentId,
    page,
    pageSize: 50,
    tenantId,
  })

  const csvUrl = (() => {
    const qs = new URLSearchParams({
      tenant_id: tenantId,
      from_dt:   filters.fromDt,
      to_dt:     filters.toDt,
      format:    'csv',
    })
    if (filters.poolId)  qs.set('pool_id',       filters.poolId)
    if (filters.agentId) qs.set('agent_type_id', filters.agentId)
    return `/reports/agent-availability?${qs}`
  })()

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white">
      {/* Sub-tab bar */}
      <div className="border-b border-border px-4 flex items-end gap-0 flex-shrink-0">
        {([
          { id: 'availability' as SubTab, label: t('agents.availability.tabLabel') },
          { id: 'pauses'       as SubTab, label: t('agents.availability.pausesLabel') },
        ]).map(s => (
          <button key={s.id} onClick={() => setSubTab(s.id)}
            className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              subTab === s.id
                ? 'border-primary text-primary'
                : 'border-transparent text-muted hover:text-dark'
            }`}>
            {s.label}
          </button>
        ))}
        {loading && (
          <span className="ml-auto self-center text-xs text-muted-light animate-pulse pr-4">{t('agents.availability.loading')}</span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {error ? (
          <div className="flex-1 flex items-center justify-center text-sm text-red">
            {t('agents.availability.loadError', { error })}
          </div>
        ) : subTab === 'availability' ? (
          <AvailabilitySubTab rows={data} />
        ) : (
          <PausesSubTab rows={data} meta={meta} page={page} onPage={setPage} csvUrl={csvUrl} />
        )}
      </div>
    </div>
  )
}
