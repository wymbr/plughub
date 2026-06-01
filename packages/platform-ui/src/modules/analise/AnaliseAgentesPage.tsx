/**
 * AnaliseAgentesPage — /analise/agentes
 *
 * Two top-level tabs:
 *   Humanos — performance KPIs + daily trend + availability/pause section (AgentsTab)
 *   IA      — performance KPIs + daily trend + performance table by agent_type
 *
 * Data sources:
 *   GET /reports/agents/performance      → aggregate KPIs  (Arc 5 segments)
 *   GET /reports/agent-performance/daily → daily trend     (mv_agent_performance_daily)
 *   GET /reports/agent-availability      → pauses          (Arc 8, via AgentsTab)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { AgentsTab } from '@/modules/contacts/tabs/AgentsTab'
import type { ContactFilters } from '@/modules/contacts/types'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PerformanceRow {
  agent_type_id:     string
  agent_type?:       string   // "human" | "native" | ...
  user_login?:       string   // human: login/email for display
  flow_id?:          string   // AI: deployed skill
  user_id?:          string   // human: stable login id
  pool_id:           string
  role:              string
  total_sessions:    number
  avg_duration_ms:   number
  resolved_count:    number
  escalated_count:   number
  transferred_count: number
  abandoned_count:   number
  escalation_rate:   number
  handoff_rate:      number
}

interface PerformanceDailyRow {
  agent_type_id:   string
  pool_id:         string
  period_date:     string
  total_sessions:  number
  avg_duration_ms: number
  resolution_rate: number
  escalation_rate: number
  transfer_rate:   number
  human_rate:      number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtMs(ms: number): string {
  if (!ms || ms < 1000) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

function shortId(id: string): string {
  return id.replace(/_v\d+$/, '').replace(/_/g, ' ')
}

function isoDate(dt: string): string {
  return dt.split('T')[0] ?? dt
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useAgentPerformance(
  tenantId: string,
  filters: ContactFilters,
) {
  const [rows,    setRows]    = useState<PerformanceRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const fetch_ = useCallback(() => {
    setLoading(true)
    setError(null)
    const p = new URLSearchParams({
      tenant_id: tenantId,
      from_dt:   filters.fromDt,
      to_dt:     filters.toDt,
    })
    if (filters.poolId)  p.set('pool_id',       filters.poolId)
    if (filters.agentId) p.set('agent_type_id', filters.agentId)
    fetch(`/reports/agents/performance?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: PerformanceRow[] }) => setRows(d.data ?? []))
      .catch(() => setError('error'))
      .finally(() => setLoading(false))
  }, [tenantId, filters.fromDt, filters.toDt, filters.poolId, filters.agentId])

  useEffect(() => { fetch_() }, [fetch_])
  return { rows, loading, error, refetch: fetch_ }
}

function useAgentPerformanceDaily(
  tenantId: string,
  filters: ContactFilters,
) {
  const [rows,    setRows]    = useState<PerformanceDailyRow[]>([])
  const [loading, setLoading] = useState(false)

  const fetch_ = useCallback(() => {
    setLoading(true)
    const p = new URLSearchParams({
      tenant_id: tenantId,
      from_dt:   filters.fromDt,
      to_dt:     filters.toDt,
    })
    if (filters.poolId)  p.set('pool_id',       filters.poolId)
    if (filters.agentId) p.set('agent_type_id', filters.agentId)
    fetch(`/reports/agent-performance/daily?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: PerformanceDailyRow[] }) => setRows(d.data ?? []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [tenantId, filters.fromDt, filters.toDt, filters.poolId, filters.agentId])

  useEffect(() => { fetch_() }, [fetch_])
  return { rows, loading }
}

// ── KPI strip ─────────────────────────────────────────────────────────────────

interface KpiCard { label: string; value: string; sub?: string }

function KpiStrip({ cards }: { cards: KpiCard[] }) {
  return (
    <div className="grid grid-cols-4 gap-3">
      {cards.map(c => (
        <div key={c.label}
          className="bg-white rounded-lg border border-border px-4 py-3 flex flex-col gap-0.5">
          <span className="text-2xs font-semibold text-muted uppercase tracking-wide">
            {c.label}
          </span>
          <span className="text-xl font-bold text-dark">{c.value}</span>
          {c.sub && <span className="text-2xs text-muted-light">{c.sub}</span>}
        </div>
      ))}
    </div>
  )
}

// ── Daily trend chart ─────────────────────────────────────────────────────────

function TrendChart({
  rows, loading, t,
}: {
  rows:    PerformanceDailyRow[]
  loading: boolean
  t:       (k: string) => string
}) {
  // Aggregate by date across all agent_types in the filter
  const byDate = React.useMemo(() => {
    const map = new Map<string, { resolution: number[]; escalation: number[] }>()
    for (const r of rows) {
      const d = isoDate(r.period_date)
      if (!map.has(d)) map.set(d, { resolution: [], escalation: [] })
      map.get(d)!.resolution.push(r.resolution_rate)
      map.get(d)!.escalation.push(r.escalation_rate)
    }
    return [...map.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, { resolution, escalation }]) => ({
        date,
        resolution: parseFloat(
          (resolution.reduce((s, v) => s + v, 0) / resolution.length * 100).toFixed(1)
        ),
        escalation: parseFloat(
          (escalation.reduce((s, v) => s + v, 0) / escalation.length * 100).toFixed(1)
        ),
      }))
  }, [rows])

  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">
      {t('trend.loading')}
    </div>
  )
  if (byDate.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">
      {t('trend.noData')}
    </div>
  )

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={byDate} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={d => d.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${v}%`} domain={[0, 100]} />
        <Tooltip formatter={(v: number) => `${v}%`} />
        <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
        <Line
          type="monotone" dataKey="resolution"
          name={t('trend.resolution')}
          stroke="var(--color-green)" strokeWidth={2} dot={false}
        />
        <Line
          type="monotone" dataKey="escalation"
          name={t('trend.escalation')}
          stroke="var(--color-warning)" strokeWidth={2} dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// ── Performance table (IA tab) ────────────────────────────────────────────────

function PerformanceTable({
  rows, loading, t,
}: {
  rows:    PerformanceRow[]
  loading: boolean
  t:       (k: string) => string
}) {
  if (loading) return (
    <div className="py-6 text-center text-sm text-muted-light animate-pulse">
      {t('table.loading')}
    </div>
  )
  if (rows.length === 0) return (
    <div className="py-6 text-center text-sm text-muted-light">
      {t('table.noData')}
    </div>
  )

  const th = 'px-3 py-2 text-left text-2xs font-semibold text-muted uppercase tracking-wide'
  const td = 'px-3 py-2 text-xs text-dark'

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-white">
      <table className="min-w-full divide-y divide-border">
        <thead className="bg-surface-muted">
          <tr>
            <th className={th}>{t('table.agentType')}</th>
            <th className={th}>{t('table.pool')}</th>
            <th className={`${th} text-right`}>{t('table.sessions')}</th>
            <th className={`${th} text-right`}>{t('table.aht')}</th>
            <th className={`${th} text-right`}>{t('table.resolution')}</th>
            <th className={`${th} text-right`}>{t('table.escalation')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((r, i) => {
            const total = r.total_sessions || 1
            const resRate = r.resolved_count / total
            const escRate = r.escalation_rate
            return (
              <tr key={i} className="hover:bg-surface-muted transition-colors">
                <td className={td}>
                  {/* C1b — human rows show the login (email); AI rows the skill label. */}
                  <span className="font-medium">
                    {r.agent_type === 'human' && r.user_login ? r.user_login : shortId(r.agent_type_id)}
                  </span>
                  <span className="text-2xs text-muted-light ml-1 font-mono">
                    {r.agent_type === 'human' ? '' : (r.agent_type_id.match(/_v\d+$/)?.[0] ?? '')}
                  </span>
                </td>
                <td className={`${td} text-muted font-mono text-2xs`}>{r.pool_id}</td>
                <td className={`${td} text-right tabular-nums`}>{r.total_sessions.toLocaleString()}</td>
                <td className={`${td} text-right tabular-nums`}>{fmtMs(r.avg_duration_ms)}</td>
                <td className={`${td} text-right`}>
                  <span className={`inline-flex px-1.5 py-0.5 rounded text-2xs font-medium
                    ${resRate >= 0.7 ? 'bg-green-light text-green-text' :
                      resRate >= 0.5 ? 'bg-warning-light text-warning-text' :
                                       'bg-red-light text-red-text'}`}>
                    {fmtPct(resRate)}
                  </span>
                </td>
                <td className={`${td} text-right`}>
                  <span className={`inline-flex px-1.5 py-0.5 rounded text-2xs font-medium
                    ${escRate <= 0.1 ? 'bg-green-light text-green-text' :
                      escRate <= 0.25 ? 'bg-warning-light text-warning-text' :
                                        'bg-red-light text-red-text'}`}>
                    {fmtPct(escRate)}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Performance KPI aggregate ─────────────────────────────────────────────────

function buildKpis(rows: PerformanceRow[], t: (k: string) => string): KpiCard[] {
  if (rows.length === 0) return [
    { label: t('kpi.sessions'),   value: '—' },
    { label: t('kpi.aht'),        value: '—' },
    { label: t('kpi.resolution'), value: '—' },
    { label: t('kpi.escalation'), value: '—' },
  ]
  const total   = rows.reduce((s, r) => s + r.total_sessions, 0)
  const avgMs   = rows.reduce((s, r) => s + r.avg_duration_ms * r.total_sessions, 0) / (total || 1)
  const resolved = rows.reduce((s, r) => s + r.resolved_count, 0)
  const escalated = rows.reduce((s, r) => s + r.escalated_count, 0)
  return [
    { label: t('kpi.sessions'),   value: total.toLocaleString() },
    { label: t('kpi.aht'),        value: fmtMs(avgMs) },
    { label: t('kpi.resolution'), value: fmtPct(resolved / (total || 1)) },
    { label: t('kpi.escalation'), value: fmtPct(escalated / (total || 1)) },
  ]
}

// ── Filter bar input class ─────────────────────────────────────────────────────

const inp = 'text-xs border border-border-strong rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white'

// ── Main page ─────────────────────────────────────────────────────────────────

type PageTab = 'humans' | 'ai'

export default function AnaliseAgentesPage() {
  const { t }      = useTranslation('agentReports')
  const { tenantId } = useAuth()

  const [pageTab,  setPageTab]  = useState<PageTab>('humans')
  const [fromDt,   setFromDt]   = useState(DEFAULT_FILTERS.fromDt)
  const [toDt,     setToDt]     = useState(DEFAULT_FILTERS.toDt)
  const [poolId,   setPoolId]   = useState('')
  const [agentId,  setAgentId]  = useState('')

  const filters: ContactFilters = { ...DEFAULT_FILTERS, fromDt, toDt, poolId, agentId }

  const { rows: perfRows,  loading: perfLoading  } = useAgentPerformance(tenantId ?? '', filters)
  const { rows: dailyRows, loading: dailyLoading } = useAgentPerformanceDaily(tenantId ?? '', filters)

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('error.loading')}
    </div>
  )

  // C1b — split performance rows by identity so each tab shows its own agents
  // and KPIs: humans (by user_id, displayed via user_login) vs AI (by skill).
  const humanRows = perfRows.filter(r => r.agent_type === 'human')
  const aiRows    = perfRows.filter(r => r.agent_type !== 'human')
  const tabRows   = pageTab === 'humans' ? humanRows : aiRows
  const kpiCards  = buildKpis(perfLoading ? [] : tabRows, t)

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* ── Filter bar ── */}
      <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs text-muted">{t('filters.from')}</span>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)} className={inp} />
          <span className="text-xs text-muted">{t('filters.to')}</span>
          <input type="date" value={toDt}   onChange={e => setToDt(e.target.value)}   className={inp} />
          <input type="text" value={poolId}  onChange={e => setPoolId(e.target.value)}
            placeholder={t('filters.poolPlaceholder')}      className={`${inp} w-36`} />
          <input type="text" value={agentId} onChange={e => setAgentId(e.target.value)}
            placeholder={t('filters.agentTypePlaceholder')} className={`${inp} w-48`} />
        </div>
      </div>

      {/* ── Top-level tab bar ── */}
      <div className="bg-white border-b border-border flex-shrink-0 flex">
        {(['humans', 'ai'] as PageTab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setPageTab(tab)}
            className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              pageTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted hover:text-dark'
            }`}
          >
            {t(`tabs.${tab}`)}
          </button>
        ))}
      </div>

      {/* ── Content ── */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">

        {/* KPI strip — shared across tabs */}
        {perfLoading
          ? <div className="grid grid-cols-4 gap-3">
              {[0,1,2,3].map(i => (
                <div key={i} className="bg-white rounded-lg border border-border h-20 animate-pulse" />
              ))}
            </div>
          : <KpiStrip cards={kpiCards} />
        }

        {/* Daily trend chart — shared across tabs */}
        <div className="bg-white rounded-lg border border-border px-4 pt-3 pb-2">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-3">
            {t('trend.title')}
          </p>
          <TrendChart rows={dailyRows} loading={dailyLoading} t={t} />
        </div>

        {/* Humanos: performance por agente (por user_login) + availability & pause */}
        {pageTab === 'humans' && (
          <>
            <div className="bg-white rounded-lg border border-border overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
                <p className="text-xs font-semibold text-muted uppercase tracking-wide">
                  {t('table.title')}
                </p>
              </div>
              <div className="p-3">
                <PerformanceTable rows={humanRows} loading={perfLoading} t={t} />
              </div>
            </div>
            <div className="bg-white rounded-lg border border-border overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
                <p className="text-xs font-semibold text-muted uppercase tracking-wide">
                  {t('section.availability')}
                </p>
              </div>
              <AgentsTab tenantId={tenantId} filters={filters} />
            </div>
          </>
        )}

        {/* IA: performance table */}
        {pageTab === 'ai' && (
          <div className="bg-white rounded-lg border border-border overflow-hidden">
            <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide">
                {t('table.title')}
              </p>
            </div>
            <div className="p-3">
              <PerformanceTable rows={aiRows} loading={perfLoading} t={t} />
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
