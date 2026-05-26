/**
 * AnaliseProcessosPage — /analise/processos
 *
 * Workflow analytics: completion rates, failure analysis, avg duration.
 * Data source: GET /reports/workflow-summary (analytics-api, proxied via Vite)
 *
 * Groups workflow_events by flow_id or campaign_id and shows:
 *   - Total triggered / completed / failed / timeout / cancelled / suspended
 *   - Completion rate and failure rate
 *   - Average duration
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Settings } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ─────────────────────────────────────────────────────────────────────

interface WorkflowSummaryRow {
  group_key:        string
  total_triggered:  number
  total_completed:  number
  total_failed:     number
  total_timeout:    number
  total_cancelled:  number
  total_suspended:  number
  completion_rate:  number   // 0.0–1.0
  failure_rate:     number   // 0.0–1.0
  avg_duration_ms:  number | null
}

interface SummaryResponse {
  data:     WorkflowSummaryRow[]
  group_by: string
  meta:     { total: number; from_dt: string; to_dt: string }
  error?:   string
}

type GroupBy = 'flow_id' | 'campaign_id'

const GROUP_BY_VALUES: GroupBy[] = ['flow_id', 'campaign_id']

// ── Helpers ───────────────────────────────────────────────────────────────────

function isoToday(): string { return new Date().toISOString().slice(0, 10) }
function iso7DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 6)
  return d.toISOString().slice(0, 10)
}
function pct(v: number): string { return `${(v * 100).toFixed(1)}%` }
function fmtDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}

// ── Rate bar ──────────────────────────────────────────────────────────────────

function RateBar({ value, color }: { value: number; color: string }) {
  const w = Math.min(100, Math.max(0, value * 100)).toFixed(0)
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-20 h-1.5 rounded bg-surface-alt overflow-hidden">
        <div className="h-full rounded" style={{ width: `${w}%`, background: color }} />
      </div>
      <span className="text-xs tabular-nums" style={{ color }}>{pct(value)}</span>
    </div>
  )
}

// ── Outcome distribution bar ──────────────────────────────────────────────────

function OutcomeBar({ row }: { row: WorkflowSummaryRow }) {
  const { t } = useTranslation('workflows')
  const total = row.total_triggered || 1
  const segments = [
    { count: row.total_completed, color: '#059669', statusKey: 'completed' },
    { count: row.total_suspended, color: '#2D9CDB', statusKey: 'suspended' },
    { count: row.total_failed,    color: '#DC2626', statusKey: 'failed' },
    { count: row.total_timeout,   color: '#D97706', statusKey: 'timed_out' },
    { count: row.total_cancelled, color: '#6b7280', statusKey: 'cancelled' },
  ]
  return (
    <div className="flex h-3 rounded overflow-hidden w-28 gap-px" title={
      segments.map(s => `${t(`statuses.${s.statusKey}`, { defaultValue: s.statusKey })}: ${s.count}`).join(' · ')
    }>
      {segments.map(s => s.count > 0
        ? <div key={s.statusKey} style={{ width: `${(s.count / total) * 100}%`, background: s.color }} />
        : null
      )}
    </div>
  )
}

// ── KPI card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-white border border-border rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[140px]">
      <span className="text-xs text-muted-light uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold leading-none" style={{ color: color ?? '#1e293b' }}>{value}</span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AnaliseProcessosPage() {
  const { tenantId } = useAuth()
  const { t, i18n } = useTranslation('workflows')

  const [fromDt,    setFromDt]    = useState(iso7DaysAgo)
  const [toDt,      setToDt]      = useState(isoToday)
  const [groupBy,   setGroupBy]   = useState<GroupBy>('flow_id')
  const [data,      setData]      = useState<WorkflowSummaryRow[]>([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)
  const [sortKey,   setSortKey]   = useState<keyof WorkflowSummaryRow>('total_triggered')
  const [sortAsc,   setSortAsc]   = useState(false)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams({
        tenant_id: tenantId,
        from_dt:   fromDt,
        to_dt:     toDt,
        group_by:  groupBy,
      })
      const res  = await fetch(`/reports/workflow-summary?${qs}`)
      const body = await res.json() as SummaryResponse
      if (body.error) throw new Error(body.error)
      setData(body.data ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setData([])
    } finally { setLoading(false) }
  }, [tenantId, fromDt, toDt, groupBy])

  useEffect(() => { load() }, [load])

  // ── Derived KPIs ─────────────────────────────────────────────────────────────

  const totalTriggered = data.reduce((s, r) => s + r.total_triggered, 0)
  const totalCompleted = data.reduce((s, r) => s + r.total_completed, 0)
  const totalFailed    = data.reduce((s, r) => s + r.total_failed + r.total_timeout, 0)

  const wCompletion    = totalTriggered > 0
    ? data.reduce((s, r) => s + r.completion_rate * r.total_triggered, 0) / totalTriggered
    : null
  const wAvgDuration   = totalCompleted > 0
    ? data.reduce((s, r) => r.avg_duration_ms !== null ? s + r.avg_duration_ms * r.total_completed : s, 0) / totalCompleted
    : null

  // ── Sort ──────────────────────────────────────────────────────────────────────

  function handleSort(key: keyof WorkflowSummaryRow) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sorted = [...data].sort((a, b) => {
    const va = a[sortKey] ?? 0
    const vb = b[sortKey] ?? 0
    const cmp = typeof va === 'number' && typeof vb === 'number'
      ? va - vb : String(va).localeCompare(String(vb))
    return sortAsc ? cmp : -cmp
  })

  // ── CSV export ────────────────────────────────────────────────────────────────

  function exportCsv() {
    const cols: (keyof WorkflowSummaryRow)[] = [
      'group_key', 'total_triggered', 'total_completed', 'total_failed',
      'total_timeout', 'total_cancelled', 'total_suspended',
      'completion_rate', 'failure_rate', 'avg_duration_ms',
    ]
    const header = cols.join(',')
    const rows   = data.map(r => cols.map(c => r[c] ?? '').join(',')).join('\n')
    const blob   = new Blob([header + '\n' + rows], { type: 'text/csv' })
    const url    = URL.createObjectURL(blob)
    const a      = document.createElement('a')
    a.href = url; a.download = `processos_${fromDt}_${toDt}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  // ── Th helper ─────────────────────────────────────────────────────────────────

  function Th({ label, k, align = 'left' }: { label: string; k: keyof WorkflowSummaryRow; align?: string }) {
    const active = sortKey === k
    return (
      <th onClick={() => handleSort(k)}
        className={`px-3 py-2.5 font-medium text-${align} cursor-pointer select-none whitespace-nowrap hover:text-dark transition-colors ${active ? 'text-primary' : 'text-muted'}`}>
        {label}{active ? (sortAsc ? ' ↑' : ' ↓') : ''}
      </th>
    )
  }

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('analise.noTenant')}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">De</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">–</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('analise.groupBy')}</label>
          <select value={groupBy} onChange={e => setGroupBy(e.target.value as GroupBy)}
            className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            {GROUP_BY_VALUES.map(v => (
              <option key={v} value={v}>{t(`analise.groupByOptions.${v}`)}</option>
            ))}
          </select>
        </div>

        <div className="flex-1" />

        {loading
          ? <Spinner />
          : <button onClick={load} className="text-xs text-muted-light hover:text-muted transition-colors px-2 py-1">
              {t('analise.refresh')}
            </button>
        }
        <button onClick={exportCsv} disabled={data.length === 0}
          className="text-xs border border-border rounded px-2.5 py-1 text-muted hover:bg-surface-muted disabled:opacity-40 transition-colors">
          {t('analise.exportCsv')}
        </button>
      </div>

      {/* KPI strip */}
      <div className="flex gap-3 px-5 py-3 flex-shrink-0 flex-wrap">
        <KpiCard label={t('analise.kpi.triggered')} value={totalTriggered.toLocaleString(i18n.language)} />
        <KpiCard label={t('analise.kpi.completed')} value={totalCompleted.toLocaleString(i18n.language)}
          sub={totalTriggered > 0 ? t('analise.kpi.ofTotal', { pct: pct(totalCompleted / totalTriggered) }) : undefined}
          color="#059669" />
        <KpiCard label={t('analise.kpi.failures')} value={totalFailed.toLocaleString(i18n.language)}
          sub={totalTriggered > 0 ? t('analise.kpi.ofTotal', { pct: pct(totalFailed / totalTriggered) }) : undefined}
          color={totalFailed > 0 ? '#DC2626' : '#6b7280'} />
        <KpiCard
          label={t('analise.kpi.avgCompletion')}
          value={wCompletion !== null ? pct(wCompletion) : '—'}
          color={wCompletion !== null && wCompletion >= 0.8 ? '#059669'
               : wCompletion !== null && wCompletion >= 0.6 ? '#1B4F8A'
               : '#D97706'}
        />
        <KpiCard label={t('analise.kpi.avgDuration')} value={fmtDuration(wAvgDuration)} />
      </div>

      {/* Error */}
      {error && (
        <div className="mx-5 mb-2 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">
          {t('analise.loadError')} {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 pb-5">
        {sorted.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <Settings className="w-10 h-10" aria-hidden="true" />
            <span className="text-sm">{t('analise.empty')}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <Th label={t(`analise.groupByOptions.${groupBy}`)} k="group_key" />
                <Th label={t('analise.table.triggered')}  k="total_triggered"  align="right" />
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.table.distribution')}
                </th>
                <Th label={t('analise.table.completed')}  k="total_completed"  align="right" />
                <Th label={t('analise.table.failed')}     k="total_failed"     align="right" />
                <Th label={t('analise.table.timeout')}    k="total_timeout"    align="right" />
                <Th label={t('analise.table.cancelled')}  k="total_cancelled"  align="right" />
                <Th label={t('analise.table.suspended')}  k="total_suspended"  align="right" />
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.table.completion')}
                </th>
                <th className="px-3 py-2.5 text-left text-muted font-medium whitespace-nowrap">
                  {t('analise.table.failure')}
                </th>
                <Th label={t('analise.table.avgDuration')} k="avg_duration_ms" align="right" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr key={i} className="border-t border-border hover:bg-surface-muted transition-colors">
                  <td className="px-3 py-2.5 font-mono text-dark max-w-[220px] truncate" title={row.group_key}>
                    {row.group_key || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-dark font-medium">
                    {row.total_triggered.toLocaleString(i18n.language)}
                  </td>
                  <td className="px-3 py-2.5">
                    <OutcomeBar row={row} />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span className="text-green-text font-medium">{row.total_completed}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span className={row.total_failed > 0 ? 'text-red font-medium' : 'text-border-strong'}>
                      {row.total_failed}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span className={row.total_timeout > 0 ? 'text-warning font-medium' : 'text-border-strong'}>
                      {row.total_timeout}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right text-muted-light">{row.total_cancelled || <span className="text-border">0</span>}</td>
                  <td className="px-3 py-2.5 text-right text-muted-light">{row.total_suspended || <span className="text-border">0</span>}</td>
                  <td className="px-3 py-2.5">
                    <RateBar value={row.completion_rate}
                      color={row.completion_rate >= 0.8 ? '#059669' : row.completion_rate >= 0.6 ? '#1B4F8A' : '#D97706'} />
                  </td>
                  <td className="px-3 py-2.5">
                    <RateBar value={row.failure_rate}
                      color={row.failure_rate > 0.15 ? '#DC2626' : '#6b7280'} />
                  </td>
                  <td className="px-3 py-2.5 text-right text-muted">
                    {fmtDuration(row.avg_duration_ms)}
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
