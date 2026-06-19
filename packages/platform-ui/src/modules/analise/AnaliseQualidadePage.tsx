/**
 * AnaliseQualidadePage — /analise/qualidade
 *
 * Three tabs:
 *   Resumo      — evaluation summary grouped by campaign/form/evaluator/date
 *   Tendência   — score timeseries (Recharts LineChart) with deploy markers (ReferenceLine)
 *   Comparação  — dual-slice comparison (before/after, human vs AI, deploy epochs)
 *
 * Data sources (all proxied via Vite → analytics-api):
 *   GET /reports/evaluations/summary
 *   GET /reports/quality-timeseries   (Arc 6 Fase 2-C)
 *   GET /reports/quality-comparison   (Arc 6 Fase 2-B)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CartesianGrid, Legend, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { useAuth } from '@/auth/useAuth'
import { useQualityReport } from '@/api/evaluation-hooks'
import Spinner from '@/components/ui/Spinner'
import {
  MetricDef,
  MetricSelector,
  BASE_METRIC_KEYS,
  buildMetricDefs,
} from './MetricSelector'

// ── Shared helpers ────────────────────────────────────────────────────────────

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29); return d.toISOString().slice(0, 10)
}
function iso7DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 6); return d.toISOString().slice(0, 10)
}
function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}
function scoreColor(avg: number): string {
  if (avg >= 0.9) return '#059669'
  if (avg >= 0.7) return '#1B4F8A'
  if (avg >= 0.5) return '#D97706'
  return '#DC2626'
}
function msToMin(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  return `${(ms / 60000).toFixed(1)} min`
}
function deltaSign(v: number | null): string {
  if (v === null) return '—'
  const s = v > 0 ? '+' : ''
  return `${s}${v.toFixed(3)}`
}
function deltaColor(v: number | null, higherIsBetter = true): string {
  if (v === null) return '#9CA3AF'
  if (v === 0) return '#6B7280'
  const good = higherIsBetter ? v > 0 : v < 0
  return good ? '#059669' : '#DC2626'
}

// ── Shared KPI card ───────────────────────────────────────────────────────────

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-border rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[140px]">
      <span className="text-xs text-muted-light uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold text-dark leading-none">{value}</span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

type Tab = 'summary' | 'timeseries' | 'comparison'
// Consolidação (2026-06-16): Trend/Comparison aposentados — a comparação de
// qualidade (por agente/dimensão/tempo) vive no bench (Analytics → Agents).
// Mantém só o Summary (tabela agregada por campanha).
const TAB_IDS: Tab[] = ['summary']

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: RESUMO
// ═══════════════════════════════════════════════════════════════════════════════

type QGroupBy = 'campaign_id' | 'finalize_reason' | 'segment_id' | 'form_version' | 'evaluated_agent_type' | 'date'
const Q_GROUP_BY_VALUES: QGroupBy[] = ['campaign_id', 'finalize_reason', 'segment_id', 'form_version', 'evaluated_agent_type', 'date']

function QDistBar({ high, mid, low }: { high: number; mid: number; low: number }) {
  const total = high + mid + low || 1
  return (
    <div className="flex h-4 rounded overflow-hidden w-24 gap-px" title={`Alta:${high} · Média:${mid} · Baixa:${low}`}>
      {high > 0 && <div style={{ width: `${(high / total) * 100}%`, background: '#059669' }} />}
      {mid > 0  && <div style={{ width: `${(mid / total) * 100}%`, background: '#D97706' }} />}
      {low > 0  && <div style={{ width: `${(low / total) * 100}%`, background: '#DC2626' }} />}
    </div>
  )
}

/**
 * SummaryView — T11: relatório de qualidade Oficial × Operacional (§17.3).
 * Oficial (default) = só finalizadas (invariante); Operacional = inclui provisório, rotulado.
 * Fonte: GET /reports/evaluations/quality (via useQualityReport).
 */
function SummaryView({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('contacts')
  const [mode,    setMode]    = useState<'oficial' | 'operacional'>('oficial')
  const [fromDt,  setFromDt]  = useState(iso7DaysAgo)
  const [toDt,    setToDt]    = useState(isoToday)
  const [groupBy, setGroupBy] = useState<QGroupBy>('campaign_id')

  const { rows, finalize_reasons, meta, loading, error } = useQualityReport(
    tenantId, { mode, group_by: groupBy, from_dt: fromDt, to_dt: toDt }, 0,
  )

  const totalN        = rows.reduce((s, r) => s + r.n, 0)
  const weightedScore = rows.reduce((s, r) => s + (r.avg_score ?? 0) * r.n, 0)
  const avgScore      = totalN > 0 ? weightedScore / totalN : null
  const reasonEntries = Object.entries(finalize_reasons || {}).sort((a, b) => b[1] - a[1])
  const sorted        = [...rows].sort((a, b) => b.n - a.n)

  function exportCsv() {
    const cols = ['group_key', 'n', 'finalized_n', 'provisional_n', 'avg_score', 'score_high', 'score_mid', 'score_low'] as const
    const header = cols.join(',')
    const body   = rows.map(r => cols.map(c => (r as unknown as Record<string, unknown>)[c]).join(',')).join('\n')
    const blob   = new Blob([header + '\n' + body], { type: 'text/csv' })
    const url    = URL.createObjectURL(blob)
    const a      = document.createElement('a')
    a.href = url; a.download = `qualidade_${mode}_${fromDt}_${toDt}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  const groupLabel = (g: string) =>
    t(`quality.groupByOptions.${g}`, { defaultValue: ({
      campaign_id: 'Campanha', finalize_reason: 'Motivo de finalização', segment_id: 'Segmento',
      form_version: 'Versão do form', evaluated_agent_type: 'Tipo de agente', date: 'Data',
    } as Record<string, string>)[g] ?? g })

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        {/* Mode toggle — Oficial × Operacional (nunca blendados, §17.3) */}
        <div className="flex items-center gap-1">
          {([
            { v: 'oficial',     l: t('quality.modes.oficial',     { defaultValue: 'Oficial' }) },
            { v: 'operacional', l: t('quality.modes.operacional', { defaultValue: 'Operacional' }) },
          ] as const).map(o => (
            <button key={o.v} onClick={() => setMode(o.v)}
              className={`text-xs px-3 py-1 rounded border font-medium transition-colors ${
                mode === o.v ? 'bg-primary text-white border-primary'
                             : 'bg-white text-muted border-border-strong hover:bg-surface-muted'}`}>
              {o.l}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.from')}</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.to')}</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.groupBy')}</label>
          <select value={groupBy} onChange={e => setGroupBy(e.target.value as QGroupBy)}
            className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            {Q_GROUP_BY_VALUES.map(v => <option key={v} value={v}>{groupLabel(v)}</option>)}
          </select>
        </div>
        <div className="flex-1" />
        {loading && <Spinner />}
        <button onClick={exportCsv} disabled={rows.length === 0}
          className="text-xs border border-border rounded px-2.5 py-1 text-muted hover:bg-surface-muted disabled:opacity-40">↓ CSV</button>
      </div>

      {/* Mode banner */}
      <div className={`mx-5 mt-3 text-xs rounded px-3 py-2 border ${mode === 'oficial'
        ? 'bg-green-light text-green-text border-green/30'
        : 'bg-warning/10 text-warning-text border-warning/30'}`}>
        {mode === 'oficial'
          ? t('quality.modeBanner.oficial', { defaultValue: '✓ Oficial — somente avaliações finalizadas (invariante de qualidade).' })
          : t('quality.modeBanner.operacional', { defaultValue: '◷ Operacional — inclui avaliações em andamento (provisório), rotuladas. Não é a nota oficial.' })}
      </div>

      {/* KPI strip */}
      <div className="flex gap-3 px-5 py-3 flex-shrink-0 flex-wrap">
        <KpiCard label={t('quality.kpi.finalized', { defaultValue: 'Finalizadas' })} value={String(meta.total_finalized)} />
        {mode === 'operacional' && (
          <KpiCard label={t('quality.kpi.provisional', { defaultValue: 'Provisórias' })} value={String(meta.total_provisional)} />
        )}
        <KpiCard label={t('quality.kpi.avgScore')} value={avgScore !== null ? pct(avgScore) : '—'}
          sub={avgScore !== null ? (avgScore >= 0.9 ? t('quality.scoreLabels.excellent') : avgScore >= 0.7 ? t('quality.scoreLabels.good') : avgScore >= 0.5 ? t('quality.scoreLabels.fair') : t('quality.scoreLabels.poor')) : undefined} />
      </div>

      {/* Finalize-reason chips */}
      {reasonEntries.length > 0 && (
        <div className="px-5 pb-2 flex items-center gap-2 flex-wrap flex-shrink-0">
          <span className="text-xs text-muted-light">{t('quality.finalizeReasons', { defaultValue: 'Motivos de finalização' })}:</span>
          {reasonEntries.map(([reason, n]) => (
            <span key={reason} className="text-xs bg-surface-muted text-muted rounded px-2 py-0.5 border border-border">
              {reason}: <strong className="text-dark">{n}</strong>
            </span>
          ))}
        </div>
      )}

      {error && <div className="mx-5 mb-2 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">{error}</div>}

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 pb-5">
        {sorted.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <span className="text-3xl">✓</span><span className="text-sm">{t('quality.noData')}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{groupLabel(groupBy)}</th>
                <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.table.total', { defaultValue: 'Total' })}</th>
                {mode === 'operacional' && (
                  <>
                    <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.kpi.finalized', { defaultValue: 'Finalizadas' })}</th>
                    <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.kpi.provisional', { defaultValue: 'Provisórias' })}</th>
                  </>
                )}
                <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.table.avgScore')}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('quality.table.distribution')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr key={i} className="border-t border-border hover:bg-surface-muted">
                  <td className="px-3 py-2.5 font-mono text-dark max-w-[200px] truncate" title={row.group_key}>{row.group_key || '—'}</td>
                  <td className="px-3 py-2.5 text-right font-medium">{row.n.toLocaleString('pt-BR')}</td>
                  {mode === 'operacional' && (
                    <>
                      <td className="px-3 py-2.5 text-right text-green-text">{row.finalized_n}</td>
                      <td className="px-3 py-2.5 text-right text-warning-text">{row.provisional_n}</td>
                    </>
                  )}
                  <td className="px-3 py-2.5 text-right font-bold" style={{ color: scoreColor(row.avg_score) }}>{pct(row.avg_score)}</td>
                  <td className="px-3 py-2.5"><QDistBar high={row.score_high} mid={row.score_mid} low={row.score_low} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: TENDÊNCIA — score timeseries + deploy markers
// ═══════════════════════════════════════════════════════════════════════════════

interface SeriesPoint {
  period:         string
  avg_score:      number | null
  n_evaluations:  number
  [key: string]:  number | string | null   // agent_event:* dynamic keys
}
interface DeployMarker { deploy_id: string; skill_id: string; version_label: string; deployed_at: string; deployed_by: string }

function TimeseriesView({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('contacts')
  const [fromDt,          setFromDt]          = useState(iso30DaysAgo)
  const [toDt,            setToDt]            = useState(isoToday)
  const [campaignId,      setCampaignId]      = useState('')
  const [skillId,         setSkillId]         = useState('')
  const [granularity,     setGranularity]     = useState<'day' | 'week'>('day')
  const [selectedMetrics, setSelectedMetrics] = useState<string[]>(BASE_METRIC_KEYS.slice(0, 1)) // only score by default
  const [series,          setSeries]          = useState<SeriesPoint[]>([])
  const [markers,         setMarkers]         = useState<DeployMarker[]>([])
  const [loading,         setLoading]         = useState(false)
  const [error,           setError]           = useState<string | null>(null)

  const metricDefs = buildMetricDefs(selectedMetrics)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, granularity })
      if (campaignId) qs.set('campaign_id', campaignId)
      if (skillId)    qs.set('skill_id', skillId)
      // Append metrics[] for agent_event overlay
      selectedMetrics
        .filter(k => k.startsWith('agent_event:'))
        .forEach(k => qs.append('metrics[]', k))
      const res  = await fetch(`/reports/quality-timeseries?${qs}`)
      const body = await res.json()
      if (body.error) throw new Error(body.error)
      setSeries(body.series ?? [])
      setMarkers(body.deploy_markers ?? [])
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setSeries([]); setMarkers([]) }
    finally { setLoading(false) }
  }, [tenantId, fromDt, toDt, campaignId, skillId, granularity, selectedMetrics])

  useEffect(() => { load() }, [load])

  // Build chart data: score → 0-100; agent_event values pass through as-is
  const chartData = series.map(p => {
    const point: Record<string, number | string | null> = { period: p.period.slice(0, 10) }
    if (selectedMetrics.includes('evaluation_score')) {
      point['score'] = p.avg_score !== null ? +(p.avg_score * 100).toFixed(1) : null
    }
    // agent_event keys are already present in each series point from the backend
    selectedMetrics.filter(k => k.startsWith('agent_event:')).forEach(k => {
      point[k] = p[k] != null ? +(p[k] as number).toFixed(2) : null
    })
    return point
  })

  const uniqueDeployDays = [...new Set(markers.map(m => m.deployed_at.slice(0, 10)))]

  // Build a lookup from metric key → MetricDef for quick access
  const defByKey = new Map(metricDefs.map(d => [d.key, d]))

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.from')}</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.to')}</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.timeseries.campaign')}</label>
          <input value={campaignId} onChange={e => setCampaignId(e.target.value)} placeholder="ID (opt.)"
            className="text-xs border border-border-strong rounded px-2 py-1 w-28 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.timeseries.skill')}</label>
          <input value={skillId} onChange={e => setSkillId(e.target.value)} placeholder="ID (opt.)"
            className="text-xs border border-border-strong rounded px-2 py-1 w-40 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('quality.timeseries.granularity')}</label>
          <select value={granularity} onChange={e => setGranularity(e.target.value as 'day' | 'week')}
            className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            <option value="day">{t('quality.timeseries.daily')}</option>
            <option value="week">{t('quality.timeseries.weekly')}</option>
          </select>
        </div>
        <div className="flex-1" />
        {loading ? <Spinner /> : <button onClick={load} className="text-xs text-muted-light hover:text-muted px-2 py-1">↻</button>}
      </div>

      {/* Metric selector */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex-shrink-0">
        <MetricSelector
          selected={selectedMetrics}
          onChange={setSelectedMetrics}
          tenantId={tenantId}
        />
      </div>

      {error && <div className="mx-5 mt-2 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">{error}</div>}

      {/* Legend for deploy markers */}
      {markers.length > 0 && (
        <div className="flex items-center gap-2 px-5 py-2 flex-shrink-0 flex-wrap">
          <span className="text-xs text-muted-light">{t('quality.timeseries.deploys')}</span>
          {markers.map(m => (
            <span key={m.deploy_id}
              className="text-xs bg-primary/10 text-primary rounded px-1.5 py-0.5 font-mono"
              title={`${m.deployed_at.slice(0, 10)} por ${m.deployed_by}`}>
              {m.skill_id} {m.version_label}
            </span>
          ))}
        </div>
      )}

      {/* Chart */}
      <div className="flex-1 px-5 pb-5 pt-2 min-h-0">
        {series.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-light gap-2">
            <span className="text-3xl">📈</span><span className="text-sm">{t('quality.timeseries.noData')}</span>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F3F4F6" vertical={false} />
              <XAxis
                dataKey="period"
                tick={{ fontSize: 10, fill: '#6B7280' }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#6B7280' }}
                tickLine={false}
                axisLine={false}
                width={40}
              />
              <Tooltip
                formatter={(value: number, key: string) => {
                  const def = key === 'score' ? defByKey.get('evaluation_score') : defByKey.get(key)
                  const label = def?.label ?? key
                  const unit  = key === 'score' ? '%' : ''
                  return [`${value}${unit}`, label]
                }}
                labelFormatter={label => t('quality.timeseries.periodLabel', { period: label })}
                contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E5E7EB' }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {/* Deploy vertical markers */}
              {uniqueDeployDays.map(day => (
                <ReferenceLine
                  key={day}
                  x={day}
                  stroke="#D97706"
                  strokeDasharray="4 2"
                  label={{ value: '▼', position: 'top', fontSize: 10, fill: '#D97706' }}
                />
              ))}
              {/* Base evaluation_score line */}
              {selectedMetrics.includes('evaluation_score') && (
                <Line
                  type="monotone"
                  dataKey="score"
                  name={t('quality.timeseries.scoreLineName')}
                  stroke="#1B4F8A"
                  strokeWidth={2}
                  dot={{ r: 2, fill: '#1B4F8A' }}
                  activeDot={{ r: 5 }}
                  connectNulls={false}
                />
              )}
              {/* Dynamic agent_event lines */}
              {metricDefs
                .filter(d => d.key.startsWith('agent_event:'))
                .map(d => (
                  <Line
                    key={d.key}
                    type="monotone"
                    dataKey={d.key}
                    name={d.label}
                    stroke={d.color ?? '#7C3AED'}
                    strokeWidth={2}
                    strokeDasharray="5 3"
                    dot={{ r: 2, fill: d.color ?? '#7C3AED' }}
                    activeDot={{ r: 5 }}
                    connectNulls={false}
                  />
                ))
              }
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: COMPARAÇÃO — dual-slice
// ═══════════════════════════════════════════════════════════════════════════════

interface SliceMetrics {
  evaluation_score:  number | null
  resolution_rate:   number | null
  escalation_rate:   number | null
  aht_ms:            number | null
  /** Dynamic agent_event:* keys added by MetricSelector */
  [key: string]:     number | null
}
interface SliceResult {
  label:         string
  from_dt:       string
  to_dt:         string
  agent_type:    string | null
  n_sessions:    number
  n_evaluations: number
  metrics:       SliceMetrics
}
interface ComparisonResult {
  slice_a: SliceResult
  slice_b: SliceResult
  delta:   SliceMetrics
  statistical_significance: { sufficient: boolean; n_a: number; n_b: number; warning: string | null }
  error?:  string
}

function MetricComparisonRow({
  label, a, b, delta, formatter, higherIsBetter = true,
}: {
  label: string
  a: number | null
  b: number | null
  delta: number | null
  formatter: (v: number | null) => string
  higherIsBetter?: boolean
}) {
  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2.5 text-xs text-muted w-32">{label}</td>
      <td className="px-3 py-2.5 text-sm font-bold text-right" style={{ color: a !== null ? scoreColor(a > 1 ? 1 : a) : '#9CA3AF' }}>
        {formatter(a)}
      </td>
      <td className="px-3 py-2.5 text-sm font-bold text-right" style={{ color: b !== null ? scoreColor(b > 1 ? 1 : b) : '#9CA3AF' }}>
        {formatter(b)}
      </td>
      <td className="px-3 py-2.5 text-sm text-right font-mono" style={{ color: deltaColor(delta, higherIsBetter) }}>
        {deltaSign(delta)}
      </td>
    </tr>
  )
}

function SliceForm({
  prefix, label, setLabel, fromDt, setFromDt, toDt, setToDt, agentType, setAgentType,
}: {
  prefix: string
  label: string; setLabel: (v: string) => void
  fromDt: string; setFromDt: (v: string) => void
  toDt: string; setToDt: (v: string) => void
  agentType: string; setAgentType: (v: string) => void
}) {
  const { t } = useTranslation('contacts')
  const inputCls = "text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40 w-full"
  return (
    <div className="flex flex-col gap-2 flex-1 min-w-[180px]">
      <div className="text-xs font-semibold text-muted uppercase tracking-wide">{t('quality.comparison.sliceTitle', { prefix })}</div>
      <div><label className="text-xs text-muted-light">{t('quality.comparison.sliceLabel')}</label>
        <input value={label} onChange={e => setLabel(e.target.value)} className={inputCls} /></div>
      <div><label className="text-xs text-muted-light">{t('quality.from')}</label>
        <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)} className={inputCls} /></div>
      <div><label className="text-xs text-muted-light">{t('quality.to')}</label>
        <input type="date" value={toDt} onChange={e => setToDt(e.target.value)} className={inputCls} /></div>
      <div><label className="text-xs text-muted-light">{t('quality.comparison.sliceAgentType')}</label>
        <input value={agentType} onChange={e => setAgentType(e.target.value)} placeholder="ex: agente_retencao_v1"
          className={inputCls} /></div>
    </div>
  )
}

function ComparisonView({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('contacts')
  const [aLabel,          setALabel]          = useState(() => t('quality.comparison.sliceADefault'))
  const [aFrom,           setAFrom]           = useState(iso30DaysAgo)
  const [aTo,             setATo]             = useState(iso7DaysAgo)
  const [aAgentType,      setAAgentType]      = useState('')
  const [bLabel,          setBLabel]          = useState(() => t('quality.comparison.sliceBDefault'))
  const [bFrom,           setBFrom]           = useState(iso7DaysAgo)
  const [bTo,             setBTo]             = useState(isoToday)
  const [bAgentType,      setBAgentType]      = useState('')
  const [poolId,          setPoolId]          = useState('')
  const [campaignId,      setCampaignId]      = useState('')
  const [selectedMetrics, setSelectedMetrics] = useState<string[]>(BASE_METRIC_KEYS)
  const [result,          setResult]          = useState<ComparisonResult | null>(null)
  const [loading,         setLoading]         = useState(false)
  const [error,           setError]           = useState<string | null>(null)

  const metricDefs = buildMetricDefs(selectedMetrics)

  const run = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams({
        tenant_id: tenantId,
        a_label: aLabel, a_from: aFrom, a_to: aTo,
        b_label: bLabel, b_from: bFrom, b_to: bTo,
      })
      if (aAgentType) qs.set('a_agent_type', aAgentType)
      if (bAgentType) qs.set('b_agent_type', bAgentType)
      if (poolId)     qs.set('pool_id', poolId)
      if (campaignId) qs.set('campaign_id', campaignId)
      // Append metrics[] for agent_event overlay
      selectedMetrics
        .filter(k => k.startsWith('agent_event:'))
        .forEach(k => qs.append('metrics[]', k))
      const res  = await fetch(`/reports/quality-comparison?${qs}`)
      const body = await res.json() as ComparisonResult
      if (body.error) throw new Error(body.error)
      setResult(body)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setResult(null) }
    finally { setLoading(false) }
  }, [tenantId, aLabel, aFrom, aTo, aAgentType, bLabel, bFrom, bTo, bAgentType, poolId, campaignId, selectedMetrics])

  const sig = result?.statistical_significance

  return (
    <div className="flex flex-col h-full overflow-auto">
      {/* Config panel */}
      <div className="bg-white border-b border-border px-5 py-4 flex-shrink-0">
        <div className="flex gap-6 flex-wrap mb-4">
          <SliceForm prefix="A"
            label={aLabel} setLabel={setALabel}
            fromDt={aFrom} setFromDt={setAFrom}
            toDt={aTo} setToDt={setATo}
            agentType={aAgentType} setAgentType={setAAgentType}
          />
          <div className="self-stretch w-px bg-border hidden sm:block" />
          <SliceForm prefix="B"
            label={bLabel} setLabel={setBLabel}
            fromDt={bFrom} setFromDt={setBFrom}
            toDt={bTo} setToDt={setBTo}
            agentType={bAgentType} setAgentType={setBAgentType}
          />
          <div className="self-stretch w-px bg-border hidden sm:block" />
          <div className="flex flex-col gap-2 min-w-40">
            <div className="text-xs font-semibold text-muted uppercase tracking-wide">{t('quality.comparison.globalFilters')}</div>
            <div><label className="text-xs text-muted-light">{t('quality.comparison.pool')}</label>
              <input value={poolId} onChange={e => setPoolId(e.target.value)} placeholder="pool_id"
                className="text-xs border border-border-strong rounded px-2 py-1 w-full focus:outline-none focus:ring-1 focus:ring-primary/40" /></div>
            <div><label className="text-xs text-muted-light">{t('quality.comparison.campaign')}</label>
              <input value={campaignId} onChange={e => setCampaignId(e.target.value)} placeholder="campaign_id"
                className="text-xs border border-border-strong rounded px-2 py-1 w-full focus:outline-none focus:ring-1 focus:ring-primary/40" /></div>
          </div>
        </div>
        <div className="mt-3 pt-3 border-t border-border">
          <MetricSelector
            selected={selectedMetrics}
            onChange={setSelectedMetrics}
            tenantId={tenantId}
          />
        </div>
        <button onClick={run} disabled={loading}
          className="mt-3 flex items-center gap-2 px-4 py-2 bg-primary text-white rounded text-xs font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors">
          {loading ? <Spinner /> : null}
          {loading ? t('quality.comparison.comparing') : t('quality.comparison.compare')}
        </button>
      </div>

      {error && <div className="mx-5 mt-3 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text">{error}</div>}

      {/* Results — guarda contra resposta sem slices (evita tela branca) */}
      {result && (!result.slice_a || !result.slice_b) && (
        <div className="mx-5 mt-3 px-3 py-2 bg-warning/10 border border-warning/30 rounded text-xs text-warning-text">
          {t('quality.comparison.noData', { defaultValue: 'Sem dados para comparar nas fatias selecionadas.' })}
        </div>
      )}
      {result && result.slice_a && result.slice_b && (
        <div className="px-5 py-4 flex flex-col gap-4">
          {/* Significance badge */}
          {sig && (
            <div className={`flex items-center gap-2 px-3 py-2 rounded text-xs font-medium ${sig.sufficient ? 'bg-green-light text-green-text border border-green/30' : 'bg-warning/10 text-warning border border-warning/30'}`}>
              {sig.sufficient
                ? t('quality.comparison.sufficiency', { nA: sig.n_a, nB: sig.n_b })
                : t('quality.comparison.insufficiency', { warning: sig.warning })}
            </div>
          )}

          {/* Slice headers + metrics table */}
          <div className="bg-white border border-border rounded-lg overflow-hidden">
            <table className="w-full text-sm border-separate border-spacing-0">
              <thead>
                <tr className="bg-surface-muted">
                  <th className="px-3 py-3 text-left text-xs font-semibold text-muted w-32">{t('quality.comparison.metric')}</th>
                  <th className="px-3 py-3 text-right text-xs font-semibold text-primary">{result.slice_a.label}</th>
                  <th className="px-3 py-3 text-right text-xs font-semibold text-accent">{result.slice_b.label}</th>
                  <th className="px-3 py-3 text-right text-xs font-semibold text-muted">{t('quality.comparison.delta')}</th>
                </tr>
              </thead>
              <tbody>
                {/* Sample sizes */}
                <tr className="border-t border-border bg-surface-muted/50">
                  <td className="px-3 py-2 text-xs text-muted-light">{t('quality.comparison.sessions')}</td>
                  <td className="px-3 py-2 text-xs text-right text-muted">{result.slice_a.n_sessions}</td>
                  <td className="px-3 py-2 text-xs text-right text-muted">{result.slice_b.n_sessions}</td>
                  <td className="px-3 py-2 text-xs text-right text-muted-light">—</td>
                </tr>
                <tr className="border-t border-border bg-surface-muted/50">
                  <td className="px-3 py-2 text-xs text-muted-light">{t('quality.comparison.evaluations')}</td>
                  <td className="px-3 py-2 text-xs text-right text-muted">{result.slice_a.n_evaluations}</td>
                  <td className="px-3 py-2 text-xs text-right text-muted">{result.slice_b.n_evaluations}</td>
                  <td className="px-3 py-2 text-xs text-right text-muted-light">—</td>
                </tr>
                {/* Dynamic KPI rows (base + agent_event overlays) */}
                {metricDefs.map(def => {
                  const rawA = result.slice_a.metrics[def.key] ?? null
                  const rawB = result.slice_b.metrics[def.key] ?? null
                  const rawD = result.delta[def.key] ?? null
                  // aht_ms: convert ms→min; others pass through
                  const toDisplay = (v: number | null) => def.key === 'aht_ms' && v !== null ? v / 60000 : v
                  return (
                    <MetricComparisonRow
                      key={def.key}
                      label={def.label}
                      higherIsBetter={def.higherIsBetter}
                      a={toDisplay(rawA)}
                      b={toDisplay(rawB)}
                      delta={toDisplay(rawD)}
                      formatter={v => def.format(
                        // Re-expand to raw for format() which expects raw values
                        def.key === 'aht_ms' && v !== null ? v * 60000 : v
                      )}
                    />
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!result && !loading && !error && (
        <div className="flex flex-col items-center justify-center flex-1 text-muted-light gap-2 py-16">
          <span className="text-3xl">⇄</span>
          <span className="text-sm">{t('quality.comparison.emptyState')}</span>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════

export default function AnaliseQualidadePage() {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()
  const [activeTab, setActiveTab] = useState<Tab>('summary')

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('quality.noTenant')}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Tab bar */}
      <div className="bg-white border-b border-border px-5 pt-3 flex-shrink-0">
        <div className="flex gap-0">
          {TAB_IDS.map(id => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                activeTab === id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted hover:text-dark'
              }`}
            >
              {t(`quality.tabs.${id}`)}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden min-h-0">
        {activeTab === 'summary' && <SummaryView tenantId={tenantId} />}
        {/* Trend/Comparison removidos — consolidados no bench (Analytics → Agents) */}
      </div>
    </div>
  )
}
