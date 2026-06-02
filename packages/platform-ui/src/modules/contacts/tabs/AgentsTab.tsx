/**
 * AgentsTab.tsx — Arc 8 + Fase 1b
 * Agent availability & pause report, embedded as a tab in ContactsPage and in
 * Analytics/Agents (Human tab).
 *
 * Receives tenantId + filters (fromDt, toDt, poolId, agentId) from the shared
 * filter bar — no local filter duplication needed.
 *
 * Two inner sub-tabs:
 *   availability — per-identity summary (logged / paused / available) + a
 *                  pause-reason donut. Fase 1b adds logged time from
 *                  agent_login_intervals; rows are keyed by instance_id, so
 *                  humans are shown per person (user_login), not collapsed.
 *   pauses       — flat rows per (identity, pool, date, reason) with CSV export.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts'
import type { ContactFilters } from '../types'
import { AgentTimeline } from './AgentTimeline'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReasonBreakdown {
  reason_id:    string
  reason_label: string
  count:        number
  total_ms:     number
}

interface AvailabilityRow {
  instance_id:      string
  user_login:       string
  user_id:          string
  agent_type_id:    string
  pool_id:          string
  period_date:      string
  logged_ms:        number
  total_logins:     number
  total_pauses:     number
  total_pause_ms:   number
  available_ms:     number
  busy_ms:          number
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
  if (!ms || ms < 1000) return '—'
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

/** Display label for an agent: human → login (email), AI/native → agent_type_id. */
function identityLabel(row: { user_login: string; agent_type_id: string }): string {
  return row.user_login || shortAgent(row.agent_type_id)
}

// Donut palette (design tokens — Recharts needs literal colours in SVG attrs)
const REASON_COLORS = [
  '#1B4F8A', '#2D9CDB', '#00B4D8', '#D97706', '#059669',
  '#DC2626', '#7C3AED', '#0891B2', '#CA8A04', '#BE185D',
]

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

// ── Reason donut ──────────────────────────────────────────────────────────────

const ReasonsDonut: React.FC<{ rows: AvailabilityRow[] }> = ({ rows }) => {
  const { t } = useTranslation('contacts')

  const byReason = new Map<string, number>()
  for (const r of rows) {
    for (const rb of r.reason_breakdown) {
      const label = rb.reason_label || rb.reason_id || '—'
      byReason.set(label, (byReason.get(label) ?? 0) + rb.total_ms)
    }
  }
  const data = [...byReason.entries()]
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)

  return (
    <div className="bg-white rounded-lg border border-border overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide">
          {t('agents.availability.reasonsTitle')}
        </p>
      </div>
      {data.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-sm text-muted-light">
          {t('agents.availability.noReasons')}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%"
                 innerRadius={50} outerRadius={80} paddingAngle={2}>
              {data.map((_, i) => (
                <Cell key={i} fill={REASON_COLORS[i % REASON_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip formatter={(v: number) => fmtDuration(v)} />
            <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Availability sub-tab (per-identity summary + donut) ──────────────────────

const AvailabilitySubTab: React.FC<{
  rows:     AvailabilityRow[]
  onSelect: (instanceId: string, label: string) => void
}> = ({ rows, onSelect }) => {
  const { t } = useTranslation('contacts')

  if (rows.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-light text-sm">
        {t('agents.availability.noData')}
      </div>
    )
  }

  // Aggregate across the date range per identity (instance_id) + pool.
  type Agg = {
    instance: string; label: string; pool: string
    logged: number; paused: number; available: number; pauses: number; busy: number
  }
  // Aggregate per IDENTITY (instance) — logged time is per-agent (one clock), so a
  // login row (pool may be blank) and busy/pause rows (real pool) must not split
  // into separate lines. Pool column shows a representative (non-blank) pool.
  const groups = new Map<string, Agg>()
  for (const r of rows) {
    const key = r.instance_id || r.agent_type_id
    const g = groups.get(key) ?? {
      instance: r.instance_id, label: '', pool: '',
      logged: 0, paused: 0, available: 0, pauses: 0, busy: 0,
    }
    g.logged    += r.logged_ms
    g.paused    += r.total_pause_ms
    g.pauses    += r.total_pauses
    g.busy      += r.busy_ms
    if (r.user_login) g.label = r.user_login                        // login identity wins
    else if (!g.label && r.agent_type_id) g.label = shortAgent(r.agent_type_id)
    if (!g.pool && r.pool_id) g.pool = r.pool_id
    if (!g.instance && r.instance_id) g.instance = r.instance_id
    groups.set(key, g)
  }
  const aggRows = [...groups.values()].sort((a, b) => b.logged - a.logged)

  return (
    <div className="flex-1 overflow-auto p-3 flex flex-col gap-3">
      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <table className="min-w-full text-xs border-collapse">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border">
              <th className="text-left px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.agent')}</th>
              <th className="text-left px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.pool')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.logged')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.paused')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.available')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.availPct')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.busy')}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted whitespace-nowrap">{t('agents.availability.columns.occupancy')}</th>
            </tr>
          </thead>
          <tbody>
            {aggRows.map((g, i) => {
              const available = Math.max(g.logged - g.paused, 0)
              const pct = g.logged > 0 ? Math.round((available / g.logged) * 100) : null
              const occ = available > 0 ? Math.round((g.busy / available) * 100) : null
              const clickable = !!g.instance
              return (
                <tr key={i}
                    onClick={clickable ? () => onSelect(g.instance, g.label) : undefined}
                    className={`border-b border-border transition-colors ${clickable ? 'cursor-pointer hover:bg-surface-muted' : ''}`}
                    title={clickable ? t('agents.availability.timeline.open') : undefined}>
                  <td className="px-3 py-2 text-dark font-medium truncate max-w-[220px]" title={g.label}>
                    {clickable && <span className="text-secondary mr-1">↳</span>}{g.label}
                  </td>
                  <td className="px-3 py-2 text-muted truncate max-w-[140px]" title={g.pool}>{shortPool(g.pool)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-dark">{fmtDuration(g.logged)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-warning-text">{fmtDuration(g.paused)}</td>
                  <td className="px-3 py-2 text-right tabular-nums font-semibold text-dark">{fmtDuration(available)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted">{pct === null ? '—' : `${pct}%`}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-dark">{fmtDuration(g.busy)}</td>
                  <td className="px-3 py-2 text-right tabular-nums font-semibold text-primary">{occ === null ? '—' : `${occ}%`}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <ReasonsDonut rows={rows} />
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

  // user_login lives on the agent's login rows; pause-only rows may lack it —
  // resolve the label by instance_id so the Agent column is never empty.
  const labelByInstance = new Map<string, string>()
  for (const r of rows) {
    if (r.user_login) labelByInstance.set(r.instance_id, r.user_login)
  }

  for (const r of rows) {
    const agent = labelByInstance.get(r.instance_id) || identityLabel(r)
    if (r.reason_breakdown.length === 0) {
      if (r.total_pauses === 0) continue
      flat.push({ date: r.period_date, agent, pool: r.pool_id,
                  reason: '—', count: r.total_pauses, ms: r.total_pause_ms })
    } else {
      for (const rb of r.reason_breakdown) {
        flat.push({ date: r.period_date, agent, pool: r.pool_id,
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
                  {r.agent}
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
  const [selected, setSelected] = useState<{ instanceId: string; label: string } | null>(null)

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
          <AvailabilitySubTab rows={data} onSelect={(instanceId, label) => setSelected({ instanceId, label })} />
        ) : (
          <PausesSubTab rows={data} meta={meta} page={page} onPage={setPage} csvUrl={csvUrl} />
        )}
      </div>

      {selected && (
        <AgentTimeline
          tenantId={tenantId}
          instanceId={selected.instanceId}
          label={selected.label}
          fromDt={filters.fromDt}
          toDt={filters.toDt}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}
