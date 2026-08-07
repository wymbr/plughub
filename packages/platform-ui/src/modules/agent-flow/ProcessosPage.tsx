/**
 * ProcessosPage — /flow/processos  (Monitor)
 *
 * Two-tab view:
 * Tab "summary"   — Workflow execution KPIs + grouped table (arc18 A1)
 * Tab "instances" — Live workflow instance list + detail panel
 *
 * Journey operational view has moved to MonitorJourneysPage (/monitor/journeys).
 */
import React, { useCallback, useEffect, useState } from 'react'
import { BarChart2, ClipboardList, Settings, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import {
  useWorkflowInstances, useWorkflowInstance,
} from '@/modules/workflows/api/hooks'
import type { WorkflowStatus } from '@/modules/workflows/api/hooks'
import { Link } from 'react-router-dom'
import { listPools } from '@/api/registry'

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

// ── Instance status colours ───────────────────────────────────────────────────

const WF_STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

// ── Summary tab types & helpers ───────────────────────────────────────────────

interface WorkflowSummaryRow {
  group_key:        string
  total_triggered:  number
  total_completed:  number
  total_failed:     number
  total_timeout:    number
  total_cancelled:  number
  total_suspended:  number
  completion_rate:  number
  failure_rate:     number
  avg_duration_ms:  number | null
}

interface SummaryResponse {
  data:     WorkflowSummaryRow[]
  group_by: string
  meta:     { total: number; from_dt: string; to_dt: string }
  error?:   string
}

type GroupBy = 'pool_id' | 'flow_id' | 'campaign_id'
const GROUP_BY_VALUES: GroupBy[] = ['pool_id', 'flow_id', 'campaign_id']

type WindowPreset = 'today' | '1d' | '7d'
function presetToRange(preset: WindowPreset): { fromDt: string; toDt: string } {
  const today = new Date()
  const toDt  = today.toISOString().slice(0, 10)
  if (preset === 'today') return { fromDt: toDt, toDt }
  const from  = new Date(today)
  from.setDate(from.getDate() - (preset === '1d' ? 1 : 6))
  return { fromDt: from.toISOString().slice(0, 10), toDt }
}

function pct(v: number): string { return `${(v * 100).toFixed(1)}%` }

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

function OutcomeBar({ row }: { row: WorkflowSummaryRow }) {
  const { t } = useTranslation('workflows')
  const total = row.total_triggered || 1
  const segments = [
    { count: row.total_completed, color: '#059669', statusKey: 'completed' },
    { count: row.total_suspended, color: '#2D9CDB', statusKey: 'suspended' },
    { count: row.total_failed,    color: '#DC2626', statusKey: 'failed'    },
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

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-white border border-border rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[140px]">
      <span className="text-xs text-muted-light uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold leading-none" style={{ color: color ?? '#1e293b' }}>{value}</span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── Pool options hook ─────────────────────────────────────────────────────────
//
// When groupBy === 'pool_id':
//   • restricted user  (accessiblePools.length > 0) → use those pool IDs directly
//   • admin / operator (accessiblePools.length === 0) → fetch all pools from registry
// For other groupBy dimensions the options are derived from the returned data.

function usePoolOptions(tenantId: string, accessiblePools: string[]) {
  const [allPools, setAllPools] = useState<string[]>([])

  useEffect(() => {
    if (accessiblePools.length > 0) {
      // restricted: JWT already has the allowed list
      setAllPools([...accessiblePools].sort())
      return
    }
    // unrestricted (admin): load every pool registered for this tenant
    listPools(tenantId)
      .then(res => {
        const ids = (res.items ?? []).map((p) => p.pool_id).sort()
        setAllPools(ids)
      })
      .catch(() => setAllPools([]))
  }, [tenantId, accessiblePools])

  return allPools
}

// ── Summary tab ───────────────────────────────────────────────────────────────

function SummaryTab({ tenantId }: { tenantId: string }) {
  const { t, i18n } = useTranslation('workflows')
  const { session }  = useAuth()
  const accessiblePools = session?.accessiblePools ?? []

  const [window_,     setWindow_]     = useState<WindowPreset>('7d')
  const { fromDt, toDt } = presetToRange(window_)
  const [groupBy,     setGroupBy]     = useState<GroupBy>('pool_id')
  const [filterGroup, setFilterGroup] = useState<string>('')
  const [data,        setData]        = useState<WorkflowSummaryRow[]>([])
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const [sortKey,     setSortKey]     = useState<keyof WorkflowSummaryRow>('total_triggered')
  const [sortAsc,     setSortAsc]     = useState(false)

  const poolOptions = usePoolOptions(tenantId, accessiblePools)

  // reset group filter when the groupBy dimension changes
  useEffect(() => { setFilterGroup('') }, [groupBy])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, group_by: groupBy })
      const res  = await apiFetch(`/reports/workflow-summary?${qs}`)
      const body = await res.json() as SummaryResponse
      if (body.error) throw new Error(body.error)
      setData(body.data ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setData([])
    } finally { setLoading(false) }
  }, [tenantId, fromDt, toDt, groupBy])

  useEffect(() => { load() }, [load])

  const totalTriggered = data.reduce((s, r) => s + r.total_triggered, 0)
  const totalCompleted = data.reduce((s, r) => s + r.total_completed, 0)
  const totalFailed    = data.reduce((s, r) => s + r.total_failed + r.total_timeout, 0)
  const wCompletion    = totalTriggered > 0
    ? data.reduce((s, r) => s + r.completion_rate * r.total_triggered, 0) / totalTriggered : null
  const wAvgDuration   = totalCompleted > 0
    ? data.reduce((s, r) => r.avg_duration_ms !== null ? s + r.avg_duration_ms * r.total_completed : s, 0) / totalCompleted : null

  function handleSort(key: keyof WorkflowSummaryRow) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sorted = [...data].sort((a, b) => {
    const va = a[sortKey] ?? 0; const vb = b[sortKey] ?? 0
    const cmp = typeof va === 'number' && typeof vb === 'number'
      ? va - vb : String(va).localeCompare(String(vb))
    return sortAsc ? cmp : -cmp
  })

  // group-level filter options:
  //   pool_id → from JWT/registry (so pools without recent activity still appear)
  //   others  → derived from returned data
  const groupOptions = groupBy === 'pool_id'
    ? poolOptions
    : [...new Set(data.map(r => r.group_key).filter(Boolean))].sort()
  const displayRows  = filterGroup ? sorted.filter(r => r.group_key === filterGroup) : sorted

  function exportCsv() {
    const cols: (keyof WorkflowSummaryRow)[] = [
      'group_key', 'total_triggered', 'total_completed', 'total_failed',
      'total_timeout', 'total_cancelled', 'total_suspended',
      'completion_rate', 'failure_rate', 'avg_duration_ms',
    ]
    const blob = new Blob(
      [cols.join(',') + '\n' + data.map(r => cols.map(c => r[c] ?? '').join(',')).join('\n')],
      { type: 'text/csv' }
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `processos_${fromDt}_${toDt}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  function Th({ label, k, align = 'left' }: { label: string; k: keyof WorkflowSummaryRow; align?: string }) {
    const active = sortKey === k
    return (
      <th onClick={() => handleSort(k)}
        className={`px-3 py-2.5 font-medium text-${align} cursor-pointer select-none whitespace-nowrap hover:text-dark transition-colors ${active ? 'text-primary' : 'text-muted'}`}>
        {label}{active ? (sortAsc ? ' ↑' : ' ↓') : ''}
      </th>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('analise.period')}</label>
          <select value={window_} onChange={e => setWindow_(e.target.value as WindowPreset)}
            className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            <option value="today">{t('analise.periodOptions.today')}</option>
            <option value="1d">{t('analise.periodOptions.1d')}</option>
            <option value="7d">{t('analise.periodOptions.7d')}</option>
          </select>
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
        {groupOptions.length > 0 && (
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-muted">{t(`analise.groupByOptions.${groupBy}`)}</label>
            <select value={filterGroup} onChange={e => setFilterGroup(e.target.value)}
              className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
              <option value="">{t('analise.filterAll')}</option>
              {groupOptions.map(g => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </div>
        )}
        <div className="flex-1" />
        {loading ? <Spinner /> : (
          <button onClick={load} className="text-xs text-muted-light hover:text-muted transition-colors px-2 py-1">
            {t('analise.refresh')}
          </button>
        )}
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
        <KpiCard label={t('analise.kpi.avgCompletion')}
          value={wCompletion !== null ? pct(wCompletion) : '—'}
          color={wCompletion !== null && wCompletion >= 0.8 ? '#059669'
               : wCompletion !== null && wCompletion >= 0.6 ? '#1B4F8A' : '#D97706'} />
        <KpiCard label={t('analise.kpi.avgDuration')} value={fmtDuration(wAvgDuration)} />
      </div>

      {error && (
        <div className="mx-5 mb-2 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">
          {t('analise.loadError')} {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 pb-5">
        {displayRows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <BarChart2 className="w-10 h-10" aria-hidden="true" />
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
              {displayRows.map((row, i) => (
                <tr key={i} className="border-t border-border hover:bg-surface-muted transition-colors">
                  <td className="px-3 py-2.5 font-mono text-dark max-w-[220px] truncate" title={row.group_key}>
                    {row.group_key || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-dark font-medium">
                    {row.total_triggered.toLocaleString(i18n.language)}
                  </td>
                  <td className="px-3 py-2.5"><OutcomeBar row={row} /></td>
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

// ── Instances tab ─────────────────────────────────────────────────────────────

function InstancesTab({ tenantId }: { tenantId: string }) {
  const { t, i18n } = useTranslation('contacts')
  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)
  const [flowFilter,   setFlowFilter]   = useState('')

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const flowFilterTrimmed = flowFilter.trim().toLowerCase()
  const sorted = [...instances]
    .filter(inst => !flowFilterTrimmed || inst.flow_id.toLowerCase().includes(flowFilterTrimmed))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  return (
    <div className="flex flex-1 overflow-hidden">

      {/* Left: list */}
      <div className="w-80 flex-shrink-0 border-r border-border bg-white flex flex-col overflow-hidden">

        {/* Flow filter */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border flex-shrink-0">
          <input
            type="text"
            value={flowFilter}
            onChange={e => setFlowFilter(e.target.value)}
            placeholder={t('processes.instances.filters.flowPlaceholder')}
            className="flex-1 text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
          />
          {flowFilter && (
            <button onClick={() => setFlowFilter('')}
              className="text-muted-light hover:text-muted transition-colors">
              <X className="w-3 h-3" aria-hidden="true" />
            </button>
          )}
        </div>

        {/* Status filter */}
        <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-border flex-shrink-0">
          {(['all', 'active', 'suspended', 'completed', 'failed', 'timed_out', 'cancelled'] as const).map(s => {
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
                {t(`processes.wfStatus.${s}`, { defaultValue: s })}
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

          {/* Botão "Cancelar" REMOVIDO em 2026-08-07 (I5, lacuna 4b): o endpoint
              que ele chamava é 410 hard e não há substituto endereçável —
              `workflow.instances` nunca tem `session_id`. Ver `api/hooks.ts`. */}
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

type PageTab = 'summary' | 'instances'

export default function ProcessosPage() {
  const { tenantId } = useAuth()
  const { t } = useTranslation('contacts')
  const [tab, setTab] = useState<PageTab>('summary')

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('processes.noTenant')}
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Tab bar */}
      <div className="flex items-center justify-end px-5 py-3 bg-white border-b border-border flex-shrink-0">
        <div className="flex items-center gap-1 bg-surface-muted border border-border rounded-lg p-1">
          {([
            { key: 'summary'   as PageTab, labelKey: 'processes.tabs.summary',   Icon: BarChart2     },
            { key: 'instances' as PageTab, labelKey: 'processes.tabs.instances', Icon: ClipboardList },
          ]).map(({ key, labelKey, Icon: TabIcon }) => (
            <button key={key} onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md font-medium transition-all ${
                tab === key ? 'bg-primary text-white shadow-sm' : 'text-muted hover:text-dark'
              }`}>
              <TabIcon className="w-3.5 h-3.5" aria-hidden="true" />
              {t(labelKey)}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden relative">
        {tab === 'summary'   && <SummaryTab   tenantId={tenantId} />}
        {tab === 'instances' && <InstancesTab tenantId={tenantId} />}
      </div>
    </div>
  )
}
