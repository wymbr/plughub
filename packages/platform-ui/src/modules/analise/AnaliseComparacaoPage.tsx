/**
 * AnaliseComparacaoPage — /analise/comparison
 *
 * Arc 6 Fase 2-D — Painel de grupos de comparação
 *
 * Up to 4 configurable slices. Each slice defines:
 *   - label (display name)
 *   - from_dt / to_dt (time window)
 *   - agent_type_id (optional)
 *
 * Global filters: pool_id, campaign_id
 *
 * On "Calcular", issues N parallel GET /reports/quality-metrics calls and
 * renders a Recharts BarChart with grouped bars — one group per KPI, one
 * bar per slice.
 */
import React, { useState } from 'react'
import { apiFetch } from '@/api/apiFetch'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import {
  MetricDef,
  MetricSelector,
  BASE_METRIC_KEYS,
  buildMetricDefs,
} from './MetricSelector'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SliceDefinition {
  id: string
  label: string
  from_dt: string
  to_dt: string
  agent_type_id: string
}

interface SliceMetrics {
  n_sessions: number
  n_evaluations: number
  /** Base metrics + dynamic agent_event:* keys */
  metrics: Record<string, number | null>
}

interface SliceResult extends SliceDefinition {
  data: SliceMetrics | null
  error: string | null
  loading: boolean
}

// ── Constants ─────────────────────────────────────────────────────────────────

const SLICE_COLORS = ['#1B4F8A', '#059669', '#D97706', '#DC2626']

const MAX_SLICES = 4


// ── Helpers ───────────────────────────────────────────────────────────────────

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29); return d.toISOString().slice(0, 10)
}

function makeDefaultSlice(n: number): SliceDefinition {
  return {
    id: `slice-${Date.now()}-${n}`,
    label: `Grupo ${n + 1}`,
    from_dt: iso30DaysAgo(),
    to_dt: isoToday(),
    agent_type_id: '',
  }
}

function metricToChartValue(key: string, data: SliceMetrics | null): number | null {
  if (!data) return null
  const v = data.metrics[key] ?? null
  if (v === null) return null
  // Scale percentages to 0–100 for bar chart readability; aht remains as-is in minutes
  if (key === 'aht_ms') return parseFloat((v / 60000).toFixed(2))
  // agent_event metrics are already numeric (not 0-1 ratios) — display raw
  if (key.startsWith('agent_event:')) return parseFloat(v.toFixed(2))
  return parseFloat((v * 100).toFixed(2))
}

function hasLowN(data: SliceMetrics | null): boolean {
  if (!data) return false
  return data.n_sessions < 30 || data.n_evaluations < 30
}

function deltaColor(v: number | null, higherIsBetter = true): string {
  if (v === null) return '#9CA3AF'
  if (v === 0) return '#6B7280'
  const good = higherIsBetter ? v > 0 : v < 0
  return good ? '#059669' : '#DC2626'
}

function deltaLabel(v: number | null): string {
  if (v === null) return '—'
  const s = v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}`
}

// ── SliceForm ─────────────────────────────────────────────────────────────────

interface SliceFormProps {
  slice: SliceDefinition
  colorIdx: number
  canRemove: boolean
  onChange: (updated: SliceDefinition) => void
  onRemove: () => void
}

function SliceForm({ slice, colorIdx, canRemove, onChange, onRemove }: SliceFormProps) {
  const color = SLICE_COLORS[colorIdx]

  function set(field: keyof SliceDefinition, value: string) {
    onChange({ ...slice, [field]: value })
  }

  return (
    <div className="border rounded-lg p-4 flex flex-col gap-3" style={{ borderColor: color }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full" style={{ background: color }} />
          <input
            className="text-sm font-semibold border-b border-transparent hover:border-border-strong focus:border-primary focus:outline-none bg-transparent px-0"
            value={slice.label}
            onChange={(e) => set('label', e.target.value)}
            maxLength={40}
          />
        </div>
        {canRemove && (
          <button
            onClick={onRemove}
            className="text-red hover:text-red-text text-xs"
          >
            Remover
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">De</label>
          <input
            type="date"
            className="border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            value={slice.from_dt}
            onChange={(e) => set('from_dt', e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted">Até</label>
          <input
            type="date"
            className="border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            value={slice.to_dt}
            onChange={(e) => set('to_dt', e.target.value)}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted">Tipo de agente (opcional)</label>
        <input
          className="border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="ex: agente_retencao_v1"
          value={slice.agent_type_id}
          onChange={(e) => set('agent_type_id', e.target.value)}
        />
      </div>
    </div>
  )
}

// ── MetricTable ───────────────────────────────────────────────────────────────

interface MetricTableProps {
  results:    SliceResult[]
  metricDefs: MetricDef[]
}

function MetricTable({ results, metricDefs }: MetricTableProps) {
  const loaded = results.filter(r => r.data)

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-surface-muted border-b">
            <th className="text-left px-4 py-2 font-semibold text-dark">Métrica</th>
            {results.map((r, i) => (
              <th key={r.id} className="text-right px-4 py-2 font-semibold" style={{ color: SLICE_COLORS[i] }}>
                {r.label}
              </th>
            ))}
            {loaded.length === 2 && (
              <th className="text-right px-4 py-2 font-semibold text-muted">
                Δ ({loaded[1].label} − {loaded[0].label})
              </th>
            )}
          </tr>
          <tr className="bg-surface-muted border-b text-xs text-muted">
            <td className="px-4 py-1">N sessões / avaliações</td>
            {results.map((r) => (
              <td key={r.id} className="text-right px-4 py-1">
                {r.loading ? <Spinner size="sm" /> : r.data
                  ? (
                    <span className={hasLowN(r.data) ? 'text-warning' : ''}>
                      {r.data.n_sessions} / {r.data.n_evaluations}
                      {hasLowN(r.data) && ' ⚠'}
                    </span>
                  )
                  : (r.error ? <span className="text-red">erro</span> : '—')}
              </td>
            ))}
            {loaded.length === 2 && <td />}
          </tr>
        </thead>
        <tbody>
          {metricDefs.map((def) => {
            const valA = loaded[0]?.data?.metrics[def.key] ?? null
            const valB = loaded[1]?.data?.metrics[def.key] ?? null
            let delta: number | null = null
            if (valA !== null && valB !== null) {
              if (def.key === 'aht_ms') {
                delta = parseFloat(((valB - valA) / 60000).toFixed(3))
              } else if (def.key.startsWith('agent_event:')) {
                delta = parseFloat((valB - valA).toFixed(3))
              } else {
                delta = parseFloat(((valB - valA) * 100).toFixed(2))
              }
            }
            const deltaUnit = def.key === 'aht_ms' ? ' min' : def.key.startsWith('agent_event:') ? '' : '%'

            return (
              <tr key={def.key} className="border-b hover:bg-surface-muted transition-colors">
                <td className="px-4 py-3 font-medium text-dark">{def.label}</td>
                {results.map((r) => (
                  <td key={r.id} className="text-right px-4 py-3 tabular-nums">
                    {r.loading
                      ? <Spinner size="sm" />
                      : r.data
                      ? def.format(r.data.metrics[def.key] ?? null)
                      : '—'}
                  </td>
                ))}
                {loaded.length === 2 && (
                  <td className="text-right px-4 py-3 tabular-nums font-semibold"
                      style={{ color: deltaColor(delta, def.higherIsBetter) }}>
                    {delta !== null ? `${deltaLabel(delta)}${deltaUnit}` : '—'}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── GroupedBarChart ───────────────────────────────────────────────────────────

interface GroupedBarChartProps {
  results:    SliceResult[]
  metricDefs: MetricDef[]
}

function GroupedBarChart({ results, metricDefs }: GroupedBarChartProps) {
  const loaded = results.filter(r => r.data)
  if (loaded.length === 0) return null

  // Build Recharts data: one object per KPI metric
  const chartData = metricDefs.map((def) => {
    const entry: Record<string, string | number | null> = { metric: def.label }
    loaded.forEach((r) => {
      entry[r.label] = metricToChartValue(def.key, r.data)
    })
    return entry
  })

  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
          <XAxis
            dataKey="metric"
            tick={{ fontSize: 11, fill: '#6B7280' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#6B7280' }}
            axisLine={false}
            tickLine={false}
            width={36}
          />
          <Tooltip
            formatter={(value: number, name: string) => [`${value}`, name]}
            contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E5E7EB' }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {loaded.map((r, i) => (
            <Bar
              key={r.id}
              dataKey={r.label}
              fill={SLICE_COLORS[i]}
              radius={[3, 3, 0, 0]}
              maxBarSize={40}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AnaliseComparacaoPage() {
  const { session } = useAuth()
  const tenantId = session?.tenantId ?? ''

  const [slices, setSlices] = useState<SliceDefinition[]>([
    makeDefaultSlice(0),
    makeDefaultSlice(1),
  ])
  const [poolId,           setPoolId]           = useState('')
  const [campaignId,       setCampaignId]       = useState('')
  const [selectedMetrics,  setSelectedMetrics]  = useState<string[]>(BASE_METRIC_KEYS)
  const [results,          setResults]          = useState<SliceResult[]>([])
  const [calculating,      setCalculating]      = useState(false)

  const metricDefs = buildMetricDefs(selectedMetrics)

  function addSlice() {
    if (slices.length >= MAX_SLICES) return
    setSlices(prev => [...prev, makeDefaultSlice(prev.length)])
  }

  function removeSlice(id: string) {
    setSlices(prev => prev.filter(s => s.id !== id))
  }

  function updateSlice(updated: SliceDefinition) {
    setSlices(prev => prev.map(s => s.id === updated.id ? updated : s))
  }

  async function fetchSlice(slice: SliceDefinition): Promise<SliceMetrics | null> {
    const params = new URLSearchParams({ tenant_id: tenantId })
    if (slice.from_dt) params.set('from_dt', slice.from_dt)
    if (slice.to_dt) params.set('to_dt', slice.to_dt)
    if (slice.agent_type_id) params.set('agent_type_id', slice.agent_type_id)
    if (poolId) params.set('pool_id', poolId)
    if (campaignId) params.set('campaign_id', campaignId)
    // Append metrics[] for agent_event overlay
    selectedMetrics
      .filter(k => k.startsWith('agent_event:'))
      .forEach(k => params.append('metrics[]', k))

    const res = await apiFetch(`/reports/quality-metrics?${params.toString()}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }

  async function handleCalculate() {
    if (!tenantId || calculating) return
    setCalculating(true)

    // Initialise result list with loading state
    const initial: SliceResult[] = slices.map(s => ({
      ...s,
      data: null,
      error: null,
      loading: true,
    }))
    setResults(initial)

    // Parallel fetch — each slice independent
    const settled = await Promise.allSettled(slices.map(s => fetchSlice(s)))

    setResults(slices.map((s, i) => {
      const outcome = settled[i]
      if (outcome.status === 'fulfilled') {
        return { ...s, data: outcome.value, error: null, loading: false }
      } else {
        return { ...s, data: null, error: String(outcome.reason), loading: false }
      }
    }))

    setCalculating(false)
  }

  const hasResults = results.length > 0

  return (
    <div className="flex flex-col gap-6 p-6 max-w-6xl mx-auto">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-dark">Comparação por Grupos</h1>
        <p className="text-sm text-muted mt-1">
          Compare até {MAX_SLICES} grupos — por período, tipo de agente ou versão de skill.
        </p>
      </div>

      {/* Global filters */}
      <div className="bg-white border rounded-lg p-4 flex flex-col gap-4">
        <h2 className="text-sm font-semibold text-dark">Filtros globais</h2>
        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">Pool</label>
            <input
              className="border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="ex: retencao_humano"
              value={poolId}
              onChange={(e) => setPoolId(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">Campanha</label>
            <input
              className="border rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="ex: campanha_q2_2026"
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
            />
          </div>
        </div>
        <div className="border-t pt-3">
          <MetricSelector
            selected={selectedMetrics}
            onChange={setSelectedMetrics}
            tenantId={tenantId}
          />
        </div>
      </div>

      {/* Slice builder */}
      <div className="bg-white border rounded-lg p-4 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-dark">
            Grupos ({slices.length}/{MAX_SLICES})
          </h2>
          {slices.length < MAX_SLICES && (
            <button
              onClick={addSlice}
              className="text-xs text-primary border border-primary rounded px-3 py-1 hover:bg-primary hover:text-white transition-colors"
            >
              + Adicionar grupo
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {slices.map((slice, i) => (
            <SliceForm
              key={slice.id}
              slice={slice}
              colorIdx={i}
              canRemove={slices.length > 1}
              onChange={updateSlice}
              onRemove={() => removeSlice(slice.id)}
            />
          ))}
        </div>

        <div className="flex justify-end">
          <button
            onClick={handleCalculate}
            disabled={calculating || !tenantId}
            className="bg-primary text-white text-sm font-semibold px-5 py-2 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {calculating && <Spinner size="sm" />}
            Calcular
          </button>
        </div>
      </div>

      {/* Results */}
      {hasResults && (
        <>
          {/* Low-N warning banner */}
          {results.some(r => hasLowN(r.data)) && (
            <div className="bg-warning-light border border-warning/30 text-warning-text text-sm rounded-lg px-4 py-3 flex items-start gap-2">
              <span>⚠</span>
              <span>
                Um ou mais grupos têm N {'<'} 30 sessões ou avaliações. Os resultados podem
                ter baixa significância estatística.
              </span>
            </div>
          )}

          {/* Error indicators */}
          {results.some(r => r.error) && (
            <div className="bg-red-light border border-red/30 text-red-text text-sm rounded-lg px-4 py-3">
              Erro ao carregar:{' '}
              {results.filter(r => r.error).map(r => r.label).join(', ')}
            </div>
          )}

          {/* Grouped bar chart */}
          <div className="bg-white border rounded-lg p-4">
            <h2 className="text-sm font-semibold text-dark mb-4">Gráfico de barras agrupadas</h2>
            {results.some(r => r.loading) ? (
              <div className="h-72 flex items-center justify-center">
                <Spinner />
              </div>
            ) : (
              <GroupedBarChart results={results} metricDefs={metricDefs} />
            )}
          </div>

          {/* Comparison table */}
          <div className="bg-white border rounded-lg overflow-hidden">
            <div className="p-4 border-b">
              <h2 className="text-sm font-semibold text-dark">Tabela comparativa</h2>
            </div>
            {results.some(r => r.loading) ? (
              <div className="p-8 flex items-center justify-center">
                <Spinner />
              </div>
            ) : (
              <MetricTable results={results} metricDefs={metricDefs} />
            )}
          </div>
        </>
      )}

      {/* Empty state */}
      {!hasResults && (
        <div className="bg-surface-muted border border-dashed border-border-strong rounded-lg p-12 text-center text-muted text-sm">
          Configure os grupos e clique em <strong>Calcular</strong> para ver os resultados.
        </div>
      )}
    </div>
  )
}
