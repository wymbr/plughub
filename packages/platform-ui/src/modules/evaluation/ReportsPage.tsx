/**
 * ReportsPage.tsx
 * /evaluation/reports — Relatórios por campanha, agente e analytics ClickHouse
 */

import React, { useState } from 'react'
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import {
  useCampaigns,
  useCampaignReport,
  useAgentReport,
  useEvaluationsSummary,
} from '@/api/evaluation-hooks'
import { TimeseriesChart, type DisplayType } from '@/components/TimeseriesChart'
import { useAuth } from '@/auth/useAuth'
import type { AgentEvaluationReport } from '@/types'

function ScorePill({ score }: { score: number | null }) {
  if (score === null) return <span className="text-gray-400 text-xs">—</span>
  const bg = score >= 8 ? 'bg-green-100 text-green-800' : score >= 6 ? 'bg-yellow-100 text-yellow-800' : 'bg-red-100 text-red-800'
  return <span className={`px-2 py-0.5 rounded text-sm font-bold ${bg}`}>{score.toFixed(1)}</span>
}

function ProgressBar({ pct }: { pct: number }) {
  const clamp = Math.max(0, Math.min(100, pct))
  return (
    <div className="w-full bg-gray-200 rounded-full h-1.5">
      <div className="bg-primary h-1.5 rounded-full" style={{ width: `${clamp}%` }} />
    </div>
  )
}

// ── CampaignTab ────────────────────────────────────────────────────────────────

function CampaignTab() {
  const { tenantId: TENANT } = useAuth()
  const { campaigns } = useCampaigns(TENANT)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { report, loading } = useCampaignReport(selectedId)

  return (
    <div className="flex gap-6">
      {/* Campaign selector */}
      <div className="w-64 shrink-0">
        <label className="block text-xs text-gray-500 mb-1">Campanha</label>
        <select
          className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
          value={selectedId ?? ''}
          onChange={e => setSelectedId(e.target.value || null)}
        >
          <option value="">Selecione…</option>
          {campaigns.map(c => (
            <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
          ))}
        </select>
      </div>

      {/* Report */}
      <div className="flex-1">
        {!selectedId && (
          <div className="text-gray-400 text-sm text-center py-12">
            Selecione uma campanha para ver o relatório
          </div>
        )}
        {selectedId && loading && <div className="text-gray-400 text-sm py-4">Carregando…</div>}
        {report && (
          <div className="space-y-6">
            <div className="grid grid-cols-5 gap-3">
              {[
                { label: 'Total', value: report.total, color: 'text-gray-700' },
                { label: 'Concluídas', value: report.completed, color: 'text-green-700' },
                { label: 'Pendentes', value: report.pending, color: 'text-yellow-700' },
                { label: 'Em revisão', value: report.in_review, color: 'text-blue-700' },
                { label: 'Expiradas', value: report.expired, color: 'text-red-600' },
              ].map(k => (
                <div key={k.label} className="bg-gray-50 rounded p-3 text-center border">
                  <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
                  <div className="text-xs text-gray-500 mt-1">{k.label}</div>
                </div>
              ))}
            </div>

            <div className="bg-white border rounded p-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-700">Conclusão</span>
                <span className="text-sm font-bold text-primary">{report.completion_pct.toFixed(1)}%</span>
              </div>
              <ProgressBar pct={report.completion_pct} />
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="bg-white border rounded p-4 text-center">
                <div className="text-xs text-gray-500 mb-1">Nota média</div>
                <ScorePill score={report.avg_score} />
              </div>
              <div className="bg-white border rounded p-4 text-center">
                <div className="text-xs text-gray-500 mb-1">P25</div>
                <ScorePill score={report.score_p25} />
              </div>
              <div className="bg-white border rounded p-4 text-center">
                <div className="text-xs text-gray-500 mb-1">P75</div>
                <ScorePill score={report.score_p75} />
              </div>
            </div>

            {report.top_flags?.length > 0 && (
              <div className="bg-white border rounded p-4">
                <div className="text-sm font-semibold text-gray-700 mb-2">Flags mais frequentes</div>
                <div className="flex flex-wrap gap-2">
                  {report.top_flags.map(f => (
                    <span key={f} className="bg-red-50 text-red-700 text-xs px-2 py-1 rounded border border-red-100">{f}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── AgentTab ──────────────────────────────────────────────────────────────────

function AgentTab() {
  const { tenantId: TENANT } = useAuth()
  const [poolFilter, setPoolFilter] = useState('')
  const { rows, loading } = useAgentReport(TENANT, poolFilter || undefined)

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs text-gray-500 mb-1">Filtrar por pool</label>
        <input
          className="border border-gray-300 rounded px-2 py-1.5 text-sm w-48"
          placeholder="Pool ID (opcional)"
          value={poolFilter}
          onChange={e => setPoolFilter(e.target.value)}
        />
      </div>

      {loading && <div className="text-gray-400 text-sm">Carregando…</div>}

      {!loading && rows.length === 0 && (
        <div className="text-center text-gray-400 py-8">Sem dados de avaliação por agente</div>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50">
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">Agente</th>
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">Pool</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Sessões</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Avaliadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Nota média</th>
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">Principais melhorias</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: AgentEvaluationReport) => (
                <tr key={`${r.agent_type_id}-${r.pool_id}`} className="border-b hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono text-xs text-gray-600">{r.agent_type_id}</td>
                  <td className="px-3 py-2 text-gray-600">{r.pool_id}</td>
                  <td className="px-3 py-2 text-center text-gray-700">{r.total_sessions}</td>
                  <td className="px-3 py-2 text-center text-gray-700">{r.evaluated}</td>
                  <td className="px-3 py-2 text-center"><ScorePill score={r.avg_score} /></td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {r.top_improvement?.slice(0, 2).map((t, i) => (
                        <span key={i} className="bg-orange-50 text-orange-700 text-xs px-1.5 py-0.5 rounded max-w-32 truncate">{t}</span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── AnalyticsTab ──────────────────────────────────────────────────────────────

const GROUP_BY_OPTIONS = [
  { value: 'campaign_id',  label: 'Campanha' },
  { value: 'evaluator_id', label: 'Avaliador' },
  { value: 'form_id',      label: 'Formulário' },
  { value: 'date',         label: 'Data' },
]

function ScoreDistBar({ excellent, good, fair, poor }: { excellent: number; good: number; fair: number; poor: number }) {
  const total = excellent + good + fair + poor
  if (total === 0) return <span className="text-gray-300 text-xs">—</span>
  const pct = (n: number) => ((n / total) * 100).toFixed(0)
  return (
    <div className="flex rounded overflow-hidden h-3 w-24 gap-px">
      {excellent > 0 && <div className="bg-green-500" style={{ width: `${pct(excellent)}%` }} title={`Excelente: ${excellent}`} />}
      {good > 0      && <div className="bg-emerald-400" style={{ width: `${pct(good)}%` }} title={`Bom: ${good}`} />}
      {fair > 0      && <div className="bg-yellow-400" style={{ width: `${pct(fair)}%` }} title={`Regular: ${fair}`} />}
      {poor > 0      && <div className="bg-red-400" style={{ width: `${pct(poor)}%` }} title={`Ruim: ${poor}`} />}
    </div>
  )
}

function RateBadge({ numerator, denominator, warnBelow }: { numerator: number; denominator: number; warnBelow?: number }) {
  if (denominator === 0) return <span className="text-gray-300 text-xs">—</span>
  const pct = (numerator / denominator) * 100
  const color = warnBelow != null && pct < warnBelow ? 'text-red-600' : pct >= 80 ? 'text-green-700' : 'text-yellow-700'
  return <span className={`text-sm font-semibold ${color}`}>{pct.toFixed(1)}%</span>
}

function AnalyticsTab() {
  const { tenantId: TENANT } = useAuth()
  const [groupBy, setGroupBy] = useState<string>('campaign_id')
  const [campaignFilter, setCampaignFilter] = useState('')

  const { rows, meta, loading, error } = useEvaluationsSummary(
    TENANT,
    {
      group_by: groupBy,
      campaign_id: campaignFilter || undefined,
    },
    60_000, // refresh every 60s
  )

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-end gap-4 flex-wrap">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Agrupar por</label>
          <select
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"
            value={groupBy}
            onChange={e => setGroupBy(e.target.value)}
          >
            {GROUP_BY_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Campanha (opcional)</label>
          <input
            className="border border-gray-300 rounded px-2 py-1.5 text-sm w-48"
            placeholder="campaign_id"
            value={campaignFilter}
            onChange={e => setCampaignFilter(e.target.value)}
          />
        </div>
        {meta.from_dt && (
          <span className="text-xs text-gray-400 self-end pb-2">
            {meta.from_dt.slice(0, 10)} → {meta.to_dt.slice(0, 10)} · {meta.total} grupos
          </span>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-gray-500">
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded bg-green-500" /> Excelente ≥0.9</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded bg-emerald-400" /> Bom 0.7–0.9</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded bg-yellow-400" /> Regular 0.5–0.7</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded bg-red-400" /> Ruim &lt;0.5</span>
      </div>

      {loading && <div className="text-gray-400 text-sm py-4">Carregando…</div>}
      {error && <div className="text-red-500 text-sm bg-red-50 rounded p-3">{error}</div>}

      {!loading && rows.length === 0 && !error && (
        <div className="text-center text-gray-400 py-10">
          Sem dados de avaliação no período
        </div>
      )}

      {/* Aggregate distribution pie */}
      {rows.length > 0 && (() => {
        const totals = rows.reduce(
          (acc, r) => ({
            excellent: acc.excellent + r.score_excellent,
            good:      acc.good      + r.score_good,
            fair:      acc.fair      + r.score_fair,
            poor:      acc.poor      + r.score_poor,
          }),
          { excellent: 0, good: 0, fair: 0, poor: 0 },
        )
        const pieData = [
          { name: 'Excelente ≥0.9', value: totals.excellent, color: '#22c55e' },
          { name: 'Bom 0.7–0.9',    value: totals.good,      color: '#34d399' },
          { name: 'Regular 0.5–0.7', value: totals.fair,     color: '#facc15' },
          { name: 'Ruim <0.5',       value: totals.poor,     color: '#f87171' },
        ].filter(d => d.value > 0)
        const grandTotal = pieData.reduce((s, d) => s + d.value, 0)
        if (grandTotal === 0) return null
        return (
          <div className="bg-white border rounded p-4 flex items-center gap-6">
            <div className="shrink-0">
              <div className="text-xs font-semibold text-gray-600 mb-2 text-center">Distribuição agregada</div>
              <ResponsiveContainer width={200} height={160}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={72}
                    dataKey="value"
                    paddingAngle={2}
                  >
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(value: number, name: string) => [
                      `${value} (${((value / grandTotal) * 100).toFixed(1)}%)`,
                      name,
                    ]}
                    contentStyle={{ fontSize: 11, borderRadius: 6 }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex-1 space-y-2">
              {pieData.map(d => (
                <div key={d.name} className="flex items-center gap-2">
                  <span className="w-3 h-3 rounded-full shrink-0" style={{ background: d.color }} />
                  <span className="text-xs text-gray-600 flex-1">{d.name}</span>
                  <span className="text-xs font-semibold text-gray-800 tabular-nums">{d.value}</span>
                  <span className="text-xs text-gray-400 w-12 text-right tabular-nums">
                    {((d.value / grandTotal) * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
              <div className="border-t pt-1 flex items-center gap-2 mt-1">
                <span className="text-xs text-gray-500 flex-1">Total avaliações</span>
                <span className="text-sm font-bold text-gray-800">{grandTotal}</span>
              </div>
            </div>
          </div>
        )
      })()}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50">
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">
                  {GROUP_BY_OPTIONS.find(o => o.value === groupBy)?.label ?? groupBy}
                </th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Avaliadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Nota média</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Distribuição</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Aprovadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Rejeitadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Contestadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Bloqueadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">⚑ Flags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.group_key} className="border-b hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono text-xs text-gray-700 max-w-48 truncate" title={r.group_key}>
                    {r.group_key}
                  </td>
                  <td className="px-3 py-2 text-center text-gray-700">{r.total_evaluated}</td>
                  <td className="px-3 py-2 text-center">
                    <ScorePill score={r.avg_score != null ? r.avg_score * 10 : null} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <ScoreDistBar
                      excellent={r.score_excellent}
                      good={r.score_good}
                      fair={r.score_fair}
                      poor={r.score_poor}
                    />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <RateBadge numerator={r.count_approved} denominator={r.total_evaluated} warnBelow={60} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <RateBadge numerator={r.count_rejected} denominator={r.total_evaluated} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <RateBadge numerator={r.count_contested} denominator={r.total_evaluated} />
                  </td>
                  <td className="px-3 py-2 text-center text-gray-600 text-sm">{r.count_locked}</td>
                  <td className="px-3 py-2 text-center">
                    {r.with_compliance_flags > 0
                      ? <span className="text-red-600 font-semibold text-sm">{r.with_compliance_flags}</span>
                      : <span className="text-gray-300 text-xs">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
            {rows.length > 1 && (
              <tfoot>
                <tr className="bg-gray-50 border-t-2 font-semibold">
                  <td className="px-3 py-2 text-xs text-gray-500">Total</td>
                  <td className="px-3 py-2 text-center text-gray-700">
                    {rows.reduce((s, r) => s + r.total_evaluated, 0)}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {(() => {
                      const total = rows.reduce((s, r) => s + r.total_evaluated, 0)
                      const wsum  = rows.reduce((s, r) => s + r.avg_score * r.total_evaluated, 0)
                      return total > 0
                        ? <ScorePill score={(wsum / total) * 10} />
                        : <span className="text-gray-300 text-xs">—</span>
                    })()}
                  </td>
                  <td />
                  <td className="px-3 py-2 text-center">
                    <RateBadge
                      numerator={rows.reduce((s, r) => s + r.count_approved, 0)}
                      denominator={rows.reduce((s, r) => s + r.total_evaluated, 0)}
                      warnBelow={60}
                    />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <RateBadge
                      numerator={rows.reduce((s, r) => s + r.count_rejected, 0)}
                      denominator={rows.reduce((s, r) => s + r.total_evaluated, 0)}
                    />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <RateBadge
                      numerator={rows.reduce((s, r) => s + r.count_contested, 0)}
                      denominator={rows.reduce((s, r) => s + r.total_evaluated, 0)}
                    />
                  </td>
                  <td className="px-3 py-2 text-center text-gray-700">
                    {rows.reduce((s, r) => s + r.count_locked, 0)}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {(() => {
                      const total = rows.reduce((s, r) => s + r.with_compliance_flags, 0)
                      return total > 0
                        ? <span className="text-red-600 font-semibold text-sm">{total}</span>
                        : <span className="text-gray-300 text-xs">—</span>
                    })()}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </div>
  )
}

// ── TrendTab ──────────────────────────────────────────────────────────────────

const BREAKDOWN_OPTIONS = [
  { value: '',            label: 'Sem agrupamento' },
  { value: 'campaign_id', label: 'Por campanha' },
  { value: 'form_id',     label: 'Por formulário' },
]

const DISPLAY_OPTIONS: { value: DisplayType; label: string; icon: string }[] = [
  { value: 'line',  label: 'Linha',  icon: '📈' },
  { value: 'area',  label: 'Área',   icon: '🏔️' },
  { value: 'bar',   label: 'Barras', icon: '📊' },
  { value: 'tile',  label: 'KPI',    icon: '🔢' },
]

function TrendTab() {
  const { session, getAccessToken, tenantId: TENANT } = useAuth()
  const [accessToken, setAccessToken] = useState<string | undefined>(undefined)
  const [breakdownBy, setBreakdownBy] = useState('')
  const [displayType, setDisplayType] = useState<DisplayType>('line')
  const [campaignId, setCampaignId] = useState('')
  const { campaigns } = useCampaigns(TENANT)

  // Resolve JWT so the timeseries endpoint respects pool-scoping (Arc 7c)
  React.useEffect(() => {
    getAccessToken().then(t => setAccessToken(t ?? undefined)).catch(() => {})
  }, [getAccessToken, session])

  return (
    <div className="space-y-5">
      {/* Controls */}
      <div className="flex items-end gap-4 flex-wrap bg-gray-50 rounded p-3 border">
        {/* Display type toggle */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">Visualização</label>
          <div className="flex gap-1">
            {DISPLAY_OPTIONS.map(o => (
              <button
                key={o.value}
                onClick={() => setDisplayType(o.value)}
                className={`flex items-center gap-1 text-xs px-2.5 py-1 rounded border transition-colors ${
                  displayType === o.value
                    ? 'bg-primary text-white border-primary'
                    : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                }`}
              >
                {o.icon} {o.label}
              </button>
            ))}
          </div>
        </div>

        {/* Breakdown selector */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">Agrupar série por</label>
          <select
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"
            value={breakdownBy}
            onChange={e => setBreakdownBy(e.target.value)}
          >
            {BREAKDOWN_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        {/* Campaign filter (optional — narrows the data before breakdown) */}
        <div>
          <label className="block text-xs text-gray-500 mb-1">Filtrar campanha</label>
          <select
            className="border border-gray-300 rounded px-2 py-1.5 text-sm max-w-[180px]"
            value={campaignId}
            onChange={e => setCampaignId(e.target.value)}
          >
            <option value="">Todas</option>
            {(campaigns as { campaign_id: string; name: string }[]).map(c => (
              <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Score trend chart — full mode (shows interval/date pickers + CSV export) */}
      <TimeseriesChart
        baseUrl="/reports/timeseries/score"
        tenantId={TENANT}
        title="Tendência de nota de avaliação"
        valueLabel="Nota média"
        formatType="score"
        displayType={displayType}
        defaultInterval={1440}           /* default: 1-day buckets */
        defaultBreakdownBy={breakdownBy || undefined}
        campaignId={campaignId || undefined}
        compact={false}
        height={320}
        accessToken={accessToken}
      />

      {/* Secondary KPI tile — always shows overall period total */}
      {displayType !== 'tile' && (
        <div className="grid grid-cols-2 gap-4">
          <TimeseriesChart
            baseUrl="/reports/timeseries/score"
            tenantId={TENANT}
            title="Nota média (período)"
            valueLabel="Nota"
            formatType="score"
            displayType="tile"
            defaultInterval={1440}
            campaignId={campaignId || undefined}
            compact={false}
            height={180}
            accessToken={accessToken}
          />
          <TimeseriesChart
            baseUrl="/reports/timeseries/volume"
            tenantId={TENANT}
            title="Volume de sessões (período)"
            valueLabel="Sessões"
            formatType="count"
            displayType="tile"
            defaultInterval={1440}
            campaignId={campaignId || undefined}
            compact={false}
            height={180}
            accessToken={accessToken}
          />
        </div>
      )}
    </div>
  )
}

// ── ReportsPage ────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'campaign',  label: '📋 Por campanha' },
  { id: 'agent',     label: '👤 Por agente' },
  { id: 'analytics', label: '📊 Analytics' },
  { id: 'trend',     label: '📈 Tendência' },
]

export default function ReportsPage() {
  const [tab, setTab] = useState('campaign')

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex gap-1 border-b px-4 bg-white">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-3 text-sm transition-colors border-b-2 -mb-px ${
              tab === t.id
                ? 'border-primary text-primary font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'campaign'  && <CampaignTab />}
        {tab === 'agent'     && <AgentTab />}
        {tab === 'analytics' && <AnalyticsTab />}
        {tab === 'trend'     && <TrendTab />}
      </div>
    </div>
  )
}
