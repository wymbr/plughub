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
  if (rows.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
        No data for selected period.
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
          <tr className="border-b border-gray-200">
            <th className="text-left px-3 py-2 font-semibold text-gray-600 whitespace-nowrap min-w-[160px]">Agent</th>
            <th className="text-left px-3 py-2 font-semibold text-gray-600 whitespace-nowrap min-w-[120px]">Pool</th>
            {dates.map(d => (
              <th key={d} className="px-2 py-2 font-semibold text-gray-600 text-center whitespace-nowrap min-w-[80px]">
                {d.slice(5)}
              </th>
            ))}
            <th className="px-3 py-2 font-semibold text-gray-600 text-right whitespace-nowrap">Total</th>
          </tr>
        </thead>
        <tbody>
          {[...groups.entries()].map(([key, { agent, pool, byDate }]) => {
            const totalMs = [...byDate.values()].reduce((s, r) => s + r.total_pause_ms, 0)
            return (
              <tr key={key} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                <td className="px-3 py-2 text-gray-800 font-medium truncate max-w-[200px]" title={agent}>
                  {shortAgent(agent)}
                </td>
                <td className="px-3 py-2 text-gray-500 truncate max-w-[140px]" title={pool}>
                  {shortPool(pool)}
                </td>
                {dates.map(d => {
                  const row = byDate.get(d)
                  if (!row) return <td key={d} className="px-2 py-2 text-center text-gray-200">—</td>
                  const pauseMs   = row.total_pause_ms
                  const intensity = Math.min(pauseMs / (4 * 3_600_000), 1)
                  const bg = pauseMs === 0
                    ? 'bg-gray-50 text-gray-300'
                    : intensity < 0.25
                      ? 'bg-amber-50 text-amber-700'
                      : intensity < 0.5
                        ? 'bg-amber-100 text-amber-800'
                        : 'bg-amber-200 text-amber-900'
                  return (
                    <td key={d} className={`px-2 py-2 text-center font-mono tabular-nums ${bg}`}
                        title={`${row.total_pauses} pause(s) · ${fmtDuration(pauseMs)}`}>
                      {pauseMs > 0 ? fmtDuration(pauseMs) : '—'}
                    </td>
                  )
                })}
                <td className="px-3 py-2 text-right font-semibold text-gray-700 tabular-nums">
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
      <div className="flex-1 flex flex-col items-center justify-center gap-2 text-gray-400 text-sm">
        <span className="text-3xl">📋</span>
        <p>No pause records for selected period.</p>
      </div>
    )
  }

  const totalPages = meta ? Math.ceil(meta.total / (meta.page_size || 50)) : 1

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-end px-4 py-2 border-b border-gray-100 bg-gray-50 flex-shrink-0">
        <a href={csvUrl} download
          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium
            border border-gray-300 text-gray-600 hover:bg-white hover:border-gray-400 transition-colors">
          ⬇ Export CSV
        </a>
      </div>

      <div className="flex-1 overflow-auto">
        <table className="min-w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-gray-200">
              <th className="text-left px-3 py-2 font-semibold text-gray-600">Date</th>
              <th className="text-left px-3 py-2 font-semibold text-gray-600">Agent</th>
              <th className="text-left px-3 py-2 font-semibold text-gray-600">Pool</th>
              <th className="text-left px-3 py-2 font-semibold text-gray-600">Reason</th>
              <th className="text-right px-3 py-2 font-semibold text-gray-600">Count</th>
              <th className="text-right px-3 py-2 font-semibold text-gray-600">Duration</th>
              <th className="text-right px-3 py-2 font-semibold text-gray-600">Avg per pause</th>
            </tr>
          </thead>
          <tbody>
            {flat.map((r, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                <td className="px-3 py-2 text-gray-500 whitespace-nowrap font-mono">{r.date}</td>
                <td className="px-3 py-2 text-gray-800 font-medium truncate max-w-[180px]" title={r.agent}>
                  {shortAgent(r.agent)}
                </td>
                <td className="px-3 py-2 text-gray-500 truncate max-w-[130px]" title={r.pool}>
                  {shortPool(r.pool)}
                </td>
                <td className="px-3 py-2">
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full
                    bg-amber-50 border border-amber-200 text-amber-700 font-medium whitespace-nowrap">
                    {r.reason}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-700">{r.count}</td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-gray-800">
                  {fmtDuration(r.ms)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-500">
                  {r.count > 0 ? fmtDuration(Math.round(r.ms / r.count)) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {meta && totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-gray-100 bg-white flex-shrink-0">
          <span className="text-xs text-gray-500">{meta.total} results</span>
          <div className="flex items-center gap-1">
            <button onClick={() => onPage(page - 1)} disabled={page <= 1}
              className="px-2 py-1 text-xs rounded border border-gray-200 disabled:opacity-40 hover:bg-gray-50">
              ← Prev
            </button>
            <span className="text-xs text-gray-600 px-2">{page} / {totalPages}</span>
            <button onClick={() => onPage(page + 1)} disabled={page >= totalPages}
              className="px-2 py-1 text-xs rounded border border-gray-200 disabled:opacity-40 hover:bg-gray-50">
              Next →
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
      <div className="border-b border-gray-200 px-4 flex items-end gap-0 flex-shrink-0">
        {([
          { id: 'availability' as SubTab, label: 'Availability' },
          { id: 'pauses'       as SubTab, label: 'Pauses'       },
        ]).map(s => (
          <button key={s.id} onClick={() => setSubTab(s.id)}
            className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              subTab === s.id
                ? 'border-primary text-primary'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {s.label}
          </button>
        ))}
        {loading && (
          <span className="ml-auto self-center text-xs text-gray-400 animate-pulse pr-4">Loading…</span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {error ? (
          <div className="flex-1 flex items-center justify-center text-sm text-red-500">
            Error loading data: {error}
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
