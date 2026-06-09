/**
 * AgentsBenchPage — /analise/agents  (F4 — bancada de comparação 360°)
 *
 * Reescreve a aba Analytics/Agents como uma BANCADA DE COMPARAÇÃO
 * (docs/arcos/analytics-agents-workbench.md): lista pools→agentes + seletor de
 * lente + gráfico de comparação com a "média dos agentes" de referência.
 *
 * Fontes:
 *   GET /reports/agents/performance     → lista (pools→agentes em escopo) — C1b
 *   GET /reports/agents/compare         → séries por lente + média (F3)
 *
 * F4.1 (este passo): shell — filtro período+pool, seletor de lente (com domínio),
 * lista flat de agentes, fetch das duas fontes, gráfico mínimo da média.
 * F4.2 enriquece o gráfico; F4.3 a lista interativa; F4.4 o detalhe; F4.5 polish.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  ScatterChart, Scatter, ZAxis, ReferenceLine,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'

// ── Lentes ──────────────────────────────────────────────────────────────────
// domain: 'universal' (humano + IA) | 'human' (IA desabilitada na lista).
// primaryKey: métrica plotada no gráfico mínimo da F4.1 (F4.2 enriquece a viz).

type LensId = 'resolution' | 'sessions_aht' | 'availability' | 'pause_reason' | 'quality' | 'quality_criteria' | 'nps' | 'wrapup'
type Domain = 'universal' | 'human'

interface LensDef { id: LensId; domain: Domain; primaryKey: string | null; pct: boolean }

const LENSES: LensDef[] = [
  { id: 'resolution',      domain: 'universal', primaryKey: 'resolution_rate', pct: true  },
  { id: 'sessions_aht',    domain: 'universal', primaryKey: 'sessions',        pct: false },
  { id: 'quality',         domain: 'universal', primaryKey: 'avg_score',       pct: false },
  { id: 'quality_criteria', domain: 'universal', primaryKey: null,             pct: false },
  { id: 'nps',             domain: 'universal', primaryKey: 'nps',             pct: false },
  { id: 'wrapup',          domain: 'universal', primaryKey: null,              pct: false },
  { id: 'availability',    domain: 'human',     primaryKey: 'occupancy_pct',   pct: true  },
  { id: 'pause_reason',    domain: 'human',     primaryKey: null,              pct: false },
]

// Cor da célula por nota 0–10 (vermelho → âmbar → verde). Reusada no heatmap (F8.3)
// e no radar do detalhe (F8.4).
const SCORE_STOPS: [number, number, number][] = [
  [252, 235, 235], [250, 238, 218], [234, 243, 222], [192, 221, 151], [99, 153, 34],
]
function scoreColor(v: number | null | undefined): string {
  if (v == null) return 'transparent'
  const t = Math.max(0, Math.min(1, v / 10)) * (SCORE_STOPS.length - 1)
  const i = Math.floor(t), f = t - i
  const a = SCORE_STOPS[i], b = SCORE_STOPS[Math.min(i + 1, SCORE_STOPS.length - 1)]
  const c = a.map((x, k) => Math.round(x + (b[k] - x) * f))
  return `rgb(${c.join(',')})`
}

// ── Tipos das respostas ─────────────────────────────────────────────────────

interface PerfRow {
  agent_type_id: string
  agent_type?:   string
  user_login?:   string
  flow_id?:      string
  user_id?:      string
  pool_id:       string
  total_sessions:  number
  avg_duration_ms: number
  resolved_count:  number
  escalation_rate: number
}

interface SeriesPoint { date: string; n?: number; [k: string]: number | string | undefined }
interface CompareEntity {
  agent_key: string; label: string; agent_type: string | null
  series: SeriesPoint[]; summary: Record<string, number | null>; missing?: boolean
}
interface CompareResp {
  data: { average: { label: string; n: number; series: SeriesPoint[] } | null; entities: CompareEntity[] }
  meta: { lens: string; bucket: string; agents_in_scope?: number }
  error?: string
}

// agent_key + label derivados de uma PerfRow (mesma regra do backend F2/F3).
function agentKeyOf(r: PerfRow): string {
  return r.agent_type === 'human'
    ? (r.user_id || r.agent_type_id)
    : (r.flow_id || r.agent_type_id)
}
function labelOf(r: PerfRow): string {
  return r.agent_type === 'human'
    ? (r.user_login || r.user_id || r.agent_type_id)
    : (r.flow_id || r.agent_type_id.replace(/_v\d+$/, '').replace(/_/g, ' '))
}

// Paleta — cor estável por entidade (por agent_key) torna-se cross-lente na F4.5.
const PALETTE = ['#1B4F8A', '#059669', '#D97706', '#7C3AED', '#0891B2', '#DC2626', '#DB2777', '#65A30D']
function colorFor(key: string): string {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length] as string
}

const inp = 'text-xs border border-border-strong rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white'

// ── Hooks de dados ──────────────────────────────────────────────────────────

// F4.5: lista SEM filtro de pool no servidor — sempre todos os pools do período,
// para popular o combo e a árvore completos. O pool é aplicado no cliente (qual
// grupo exibir) e no compare (escopo da média/gráfico).
function usePerformanceList(tenantId: string, fromDt: string, toDt: string) {
  const [rows, setRows] = useState<PerfRow[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt })
    fetch(`/reports/agents/performance?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: PerfRow[] }) => setRows(d.data ?? []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt])
  return { rows, loading }
}

function useCompare(
  tenantId: string, fromDt: string, toDt: string, poolId: string,
  lens: LensId, entities: string[],
) {
  const [resp, setResp] = useState<CompareResp | null>(null)
  const [loading, setLoading] = useState(false)
  const entityCsv = entities.join(',')
  const fetch_ = useCallback(() => {
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, lens })
    if (poolId) p.set('pool_id', poolId)
    if (entityCsv) p.set('entities', entityCsv)
    fetch(`/reports/agents/compare?${p}`)
      .then(r => r.json())
      .then((d: CompareResp) => setResp(d))
      .catch(() => setResp(null))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt, poolId, lens, entityCsv])
  useEffect(() => { fetch_() }, [fetch_])
  return { resp, loading }
}

// ── Cruzamento (F6) — 3 vantagens lado a lado por agente ──────────────────────

interface CrossRow {
  agent_key: string; agent_type: string; label: string
  sessions: number
  resolution_rate: number | null
  escalation_rate: number | null
  quality_score: number | null   // 0–1
  quality_n: number
  nps: number | null              // -100..100
  avg_nps: number | null
  nps_n: number
}
interface CrossResp { data: CrossRow[]; meta: Record<string, unknown>; error?: string }

function useCross(tenantId: string, fromDt: string, toDt: string, poolId: string, enabled: boolean) {
  const [resp, setResp] = useState<CrossResp | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!enabled) return
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt })
    if (poolId) p.set('pool_id', poolId)
    fetch(`/reports/agents/cross?${p}`)
      .then(r => r.json())
      .then((d: CrossResp) => setResp(d))
      .catch(() => setResp(null))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt, poolId, enabled])
  return { resp, loading }
}

// Sinais de divergência (só flag — F6.1). Limiares fixos, conservadores.
type CrossFlag = 'star' | 'perception' | 'disposition'
function crossFlags(r: CrossRow): CrossFlag[] {
  const res = r.resolution_rate, q = r.quality_score, nps = r.nps
  const highRes = res != null && res >= 0.7
  const highQ   = q != null && q >= 0.7
  const lowQ    = q != null && q < 0.5
  const badNps  = nps != null && nps < 0
  const goodNps = nps != null && nps >= 50
  const flags: CrossFlag[] = []
  if (highRes && (q == null || highQ) && (nps == null || goodNps)) flags.push('star')
  if ((highRes || highQ) && badNps) flags.push('perception')
  if (highRes && lowQ) flags.push('disposition')
  return flags
}

// ── Gráfico por lente (F4.2) ──────────────────────────────────────────────────

type Fmt = 'pct' | 'time' | 'count' | 'score'

function fmtMsShort(ms: number): string {
  if (ms == null) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ''}`
}
function fmtVal(v: number | null | undefined, fmt: Fmt): string {
  if (v == null) return '—'
  if (fmt === 'pct')   return `${(v * 100).toFixed(1)}%`
  if (fmt === 'time')  return fmtMsShort(v)
  if (fmt === 'score') return v.toFixed(2)
  return `${Math.round(v)}`
}

// key → rótulo legível (entidades trazem label; média = "média dos agentes").
function labelMapOf(resp: CompareResp | null, t: (k: string) => string): Record<string, string> {
  const m: Record<string, string> = { __avg__: t('bench.average') }
  for (const e of resp?.data.entities ?? []) m[e.agent_key] = e.label || e.agent_key
  return m
}

// Linhas temporais (avg + entidades) para uma métrica. Gap (null) = quebra.
function MetricLine({
  resp, metricKey, fmt, selected, title, labelMap,
}: {
  resp: CompareResp; metricKey: string; fmt: Fmt; selected: string[]
  title: string; labelMap: Record<string, string>
}) {
  const scale = fmt === 'pct' ? 100 : 1
  const rows = useMemo(() => {
    const byDate = new Map<string, Record<string, number | string | null>>()
    const put = (series: SeriesPoint[], col: string) => {
      for (const pt of series) {
        const d = String(pt.date)
        if (!byDate.has(d)) byDate.set(d, { date: d })
        const v = pt[metricKey]
        byDate.get(d)![col] = (typeof v === 'number') ? v * scale : null
      }
    }
    if (resp.data.average) put(resp.data.average.series, '__avg__')
    for (const e of resp.data.entities) put(e.series, e.agent_key)
    return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))
  }, [resp, metricKey, scale])

  const yFmt = (v: number) => fmt === 'pct' ? `${v}%` : fmt === 'time' ? fmtMsShort(v) : `${v}`
  return (
    <div>
      <p className="text-2xs font-semibold text-muted uppercase tracking-wide mb-1">{title}</p>
      <ResponsiveContainer width="100%" height={190}>
        <LineChart data={rows} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(d: string) => d.slice(5)} />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={yFmt}
            domain={fmt === 'pct' ? [0, 100] : ['auto', 'auto']} />
          <Tooltip formatter={(v: number) => fmt === 'pct' ? `${v?.toFixed?.(1)}%` : fmt === 'time' ? fmtMsShort(v) : v} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="__avg__" name={labelMap.__avg__}
            stroke="#111827" strokeWidth={2.5} strokeDasharray="6 4" dot={false} connectNulls={false} />
          {selected.map(k => (
            <Line key={k} type="monotone" dataKey={k} name={labelMap[k] ?? k}
              stroke={colorFor(k)} strokeWidth={2} dot={false} connectNulls={false}
              strokeDasharray={k.startsWith('pool:') ? '5 3' : undefined} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// Barras agrupadas por entidade (availability) — usa o summary do período.
// Valor da média = média (sobre buckets) da série de referência.
function GroupedBars({
  resp, selected, labelMap, metrics, t,
}: {
  resp: CompareResp; selected: string[]; labelMap: Record<string, string>
  metrics: { key: string; name: string }[]
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  const avgFromSeries = (key: string): number | null => {
    const s = resp.data.average?.series ?? []
    const vals = s.map(p => p[key]).filter((v): v is number => typeof v === 'number')
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null
  }
  const rows: Record<string, number | string | null>[] = []
  if (resp.data.average) {
    const row: Record<string, number | string | null> = { name: labelMap.__avg__ }
    for (const m of metrics) { const v = avgFromSeries(m.key); row[m.key] = v == null ? null : v * 100 }
    rows.push(row)
  }
  for (const k of selected) {
    const e = resp.data.entities.find(x => x.agent_key === k)
    if (!e) continue
    const row: Record<string, number | string | null> = { name: labelMap[k] ?? k }
    for (const m of metrics) { const v = e.summary[m.key]; row[m.key] = v == null ? null : v * 100 }
    rows.push(row)
  }
  if (rows.length === 0) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light">{t('bench.chart.noData')}</div>
  )
  const barColors = ['#2D9CDB', '#D97706', '#059669']
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={rows} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v}%`} domain={[0, 100]} />
        <Tooltip formatter={(v: number) => `${v?.toFixed?.(1)}%`} />
        <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
        {metrics.map((m, i) => (
          <Bar key={m.key} dataKey={m.key} name={m.name} fill={barColors[i % barColors.length]} radius={[2, 2, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

// Barra empilhada por entidade, segmentada por motivo de pausa (pause_reason).
function StackedReasonBars({
  resp, selected, labelMap, t,
}: {
  resp: CompareResp; selected: string[]; labelMap: Record<string, string>
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  const ents = selected
    .map(k => resp.data.entities.find(e => e.agent_key === k))
    .filter((e): e is CompareEntity => !!e)
  if (ents.length === 0) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light text-center px-6">
      {t('bench.chart.selectForPause')}
    </div>
  )
  // Conjunto de motivos presente nas entidades selecionadas.
  const reasons = new Map<string, string>()
  for (const e of ents) {
    for (const r of ((e.summary.reasons as unknown as { reason_id: string; reason_label: string }[]) ?? [])) {
      reasons.set(r.reason_id, r.reason_label || r.reason_id)
    }
  }
  const reasonIds = [...reasons.keys()]
  const rows = ents.map(e => {
    const row: Record<string, number | string> = { name: labelMap[e.agent_key] ?? e.agent_key }
    const rs = (e.summary.reasons as unknown as { reason_id: string; total_ms: number }[]) ?? []
    for (const rid of reasonIds) {
      const found = rs.find(x => x.reason_id === rid)
      row[rid] = found ? Math.round(found.total_ms / 60000) : 0   // minutos
    }
    return row
  })
  const palette = ['#1B4F8A', '#D97706', '#059669', '#7C3AED', '#DC2626', '#0891B2']
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={rows} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v}m`} />
        <Tooltip formatter={(v: number) => `${v} min`} />
        <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
        {reasonIds.map((rid, i) => (
          <Bar key={rid} dataKey={rid} name={reasons.get(rid)} stackId="pause"
            fill={palette[i % palette.length]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

// Barras empilhadas por entidade, segmentadas por DISPOSIÇÃO do wrap-up.
// Lê summary.dispositions[] (como StackedReasonBars lê summary.reasons).
function StackedDispositionBars({
  resp, selected, labelMap, t,
}: {
  resp: CompareResp; selected: string[]; labelMap: Record<string, string>
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  const ents = selected
    .map(k => resp.data.entities.find(e => e.agent_key === k))
    .filter((e): e is CompareEntity => !!e)
  if (ents.length === 0) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light text-center px-6">
      {t('bench.chart.selectForWrapup')}
    </div>
  )
  // Disposições normalizadas presentes (chave = outcome; rótulo = issue_status cru).
  const dispKeys = new Map<string, string>()
  for (const e of ents) {
    for (const d of ((e.summary.dispositions as unknown as { outcome: string; issue_status: string }[]) ?? [])) {
      dispKeys.set(d.outcome || d.issue_status, d.issue_status || d.outcome)
    }
  }
  const keys = [...dispKeys.keys()]
  const rows = ents.map(e => {
    const row: Record<string, number | string> = { name: labelMap[e.agent_key] ?? e.agent_key }
    const ds = (e.summary.dispositions as unknown as { outcome: string; issue_status: string; count: number }[]) ?? []
    for (const k of keys) {
      const found = ds.find(x => (x.outcome || x.issue_status) === k)
      row[k] = found ? found.count : 0
    }
    return row
  })
  // Cores semânticas por outcome normalizado (verde resolved, laranja escalated, …).
  const OUTCOME_COLOR: Record<string, string> = {
    resolved: '#059669', escalated: '#D97706', suspended: '#7C3AED',
    abandoned: '#DC2626', failed: '#6B7280', transferred: '#0891B2',
  }
  const palette = ['#1B4F8A', '#D97706', '#059669', '#7C3AED', '#DC2626', '#0891B2']
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={rows} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip />
        <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} name={dispKeys.get(k)} stackId="disp"
            fill={OUTCOME_COLOR[k] ?? palette[i % palette.length]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

// Heatmap agente × dimensão (quality_criteria, F8.3). Lê summary.dimensions[].
// Comparável só dentro do mesmo formulário → avisa quando os selecionados misturam forms.
function QualityCriteriaHeatmap({
  resp, selected, labelMap, t,
}: {
  resp: CompareResp; selected: string[]; labelMap: Record<string, string>
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  type DimEntry = { dimension_id: string; dimension_label: string; avg_score: number | null; n: number }
  const ents = selected
    .map(k => resp.data.entities.find(e => e.agent_key === k))
    .filter((e): e is CompareEntity => !!e && !e.missing)
  if (ents.length === 0) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light text-center px-6">
      {t('bench.chart.selectForQuality')}
    </div>
  )
  // Guard de comparabilidade: form único entre os selecionados (com dado).
  const forms = [...new Set(ents
    .map(e => (e.summary.form_id as unknown as string) || '')
    .filter(Boolean))]
  if (forms.length > 1) return (
    <div className="h-52 flex flex-col items-center justify-center text-sm text-muted-light text-center px-6 gap-1">
      <span className="text-warning font-medium">{t('bench.criteria.multiForm')}</span>
      <span className="text-2xs">{t('bench.criteria.multiFormHint', { forms: forms.join(', ') })}</span>
    </div>
  )
  // União das dimensões presentes (id → label).
  const dimMap = new Map<string, string>()
  for (const e of ents)
    for (const d of ((e.summary.dimensions as unknown as DimEntry[]) ?? []))
      dimMap.set(d.dimension_id, d.dimension_label || d.dimension_id)
  const dimIds = [...dimMap.keys()]
  const rows = ents.map(e => {
    const scores: Record<string, number | null> = {}
    let n = 0
    for (const d of ((e.summary.dimensions as unknown as DimEntry[]) ?? [])) {
      scores[d.dimension_id] = d.avg_score
      n = Math.max(n, d.n)
    }
    return { key: e.agent_key, label: labelMap[e.agent_key] ?? e.label ?? e.agent_key, scores, n }
  })

  return (
    <div className="space-y-2">
      {forms[0] && <p className="text-2xs text-muted-light">{t('bench.criteria.form', { form: forms[0] })}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs border-separate" style={{ borderSpacing: '3px' }}>
          <thead>
            <tr>
              <th className="text-left font-semibold text-muted px-2 py-1"></th>
              {dimIds.map(id => (
                <th key={id} className="font-semibold text-muted px-2 py-1 text-center">{dimMap.get(id)}</th>
              ))}
              <th className="font-semibold text-muted-light px-2 py-1 text-center text-2xs">n</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.key}>
                <td className="pr-2 py-1 max-w-[10rem]">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: colorFor(r.key) }} />
                    <span className="font-medium text-dark truncate">{r.label}</span>
                  </span>
                </td>
                {dimIds.map(id => {
                  const v = r.scores[id]
                  return (
                    <td key={id} className="text-center px-0.5 py-0.5">
                      <span className="block rounded py-1.5 font-medium tabular-nums"
                        style={{ background: scoreColor(v), color: v == null ? 'var(--color-muted-light)' : '#173404' }}>
                        {v == null ? '—' : v.toFixed(1)}
                      </span>
                    </td>
                  )
                })}
                <td className="text-center text-2xs text-muted-light tabular-nums">{r.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2 text-2xs text-muted-light pt-1">
        <span>0</span>
        <span className="inline-block h-2 w-24 rounded"
          style={{ background: 'linear-gradient(90deg,#FCEBEB,#FAEEDA,#EAF3DE,#C0DD97,#639922)' }} />
        <span>10</span>
      </div>
    </div>
  )
}

function LensChart({
  lens, resp, selected, t,
}: {
  lens: LensId; resp: CompareResp | null; selected: string[]
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  if (!resp || resp.error) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light">{t('bench.chart.noData')}</div>
  )
  const labelMap = labelMapOf(resp, t as (k: string) => string)
  const hasData = (resp.data.average && resp.data.average.series.length > 0) ||
    resp.data.entities.some(e => e.series.length > 0 ||
      ((e.summary.reasons as unknown as unknown[] | undefined)?.length ?? 0) > 0)
  if (!hasData && lens !== 'pause_reason' && lens !== 'wrapup' && lens !== 'quality_criteria') return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light">{t('bench.chart.noData')}</div>
  )

  if (lens === 'resolution') return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <MetricLine resp={resp} metricKey="resolution_rate" fmt="pct" selected={selected}
        title={t('bench.metric.resolution')} labelMap={labelMap} />
      <MetricLine resp={resp} metricKey="escalation_rate" fmt="pct" selected={selected}
        title={t('bench.metric.escalation')} labelMap={labelMap} />
    </div>
  )
  if (lens === 'sessions_aht') return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <MetricLine resp={resp} metricKey="sessions" fmt="count" selected={selected}
        title={t('bench.metric.sessions')} labelMap={labelMap} />
      <MetricLine resp={resp} metricKey="aht_ms" fmt="time" selected={selected}
        title={t('bench.metric.aht')} labelMap={labelMap} />
    </div>
  )
  if (lens === 'quality') return (
    <MetricLine resp={resp} metricKey="avg_score" fmt="score" selected={selected}
      title={t('bench.metric.quality')} labelMap={labelMap} />
  )
  if (lens === 'nps') return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <MetricLine resp={resp} metricKey="nps" fmt="count" selected={selected}
        title={t('bench.metric.npsIndex')} labelMap={labelMap} />
      <MetricLine resp={resp} metricKey="avg_nps" fmt="score" selected={selected}
        title={t('bench.metric.npsAvg')} labelMap={labelMap} />
    </div>
  )
  if (lens === 'wrapup') return (
    <StackedDispositionBars resp={resp} selected={selected} labelMap={labelMap} t={t} />
  )
  if (lens === 'quality_criteria') return (
    <QualityCriteriaHeatmap resp={resp} selected={selected} labelMap={labelMap} t={t} />
  )
  if (lens === 'availability') return (
    <GroupedBars resp={resp} selected={selected} labelMap={labelMap} t={t}
      metrics={[
        { key: 'occupancy_pct', name: t('bench.metric.occupancy') },
        { key: 'pause_pct',     name: t('bench.metric.pause') },
      ]} />
  )
  // pause_reason
  return <StackedReasonBars resp={resp} selected={selected} labelMap={labelMap} t={t} />
}

// ── Detalhe type-aware (F4.4) — consolidado das lentes por agente ─────────────

function KpiTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-muted rounded-lg px-3 py-2 flex flex-col gap-0.5">
      <span className="text-2xs font-semibold text-muted uppercase tracking-wide">{label}</span>
      <span className="text-base font-bold text-dark tabular-nums">{value}</span>
    </div>
  )
}

function AgentDetail({
  tenantId, fromDt, toDt, agentKey, isHuman, t,
}: {
  tenantId: string; fromDt: string; toDt: string; agentKey: string; isHuman: boolean
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  const [byLens, setByLens] = useState<Partial<Record<LensId, CompareEntity | null>>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    const lenses: LensId[] = isHuman
      ? ['resolution', 'quality', 'quality_criteria', 'availability']
      : ['resolution', 'quality', 'quality_criteria']
    Promise.all(lenses.map(l => {
      const p = new URLSearchParams({
        tenant_id: tenantId, from_dt: fromDt, to_dt: toDt,
        lens: l, entities: agentKey, include_average: 'false',
      })
      return fetch(`/reports/agents/compare?${p}`)
        .then(r => r.json())
        .then((d: CompareResp) => [l, d.data?.entities?.[0] ?? null] as [LensId, CompareEntity | null])
        .catch(() => [l, null] as [LensId, CompareEntity | null])
    })).then(entries => setByLens(Object.fromEntries(entries)))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt, agentKey, isHuman])

  if (loading) return <p className="text-sm text-muted-light py-6 text-center animate-pulse">{t('bench.chart.loading')}</p>

  const res  = byLens.resolution?.summary ?? {}
  const qual = byLens.quality?.summary ?? {}
  const av   = byLens.availability?.summary ?? {}
  const num = (v: number | null | undefined) => (typeof v === 'number' ? v : null)

  // F8.4 — radar das dimensões (perfil de qualidade do agente).
  type DimEntry = { dimension_id: string; dimension_label: string; avg_score: number | null }
  const dims = (byLens.quality_criteria?.summary.dimensions as unknown as DimEntry[]) ?? []
  const radar = dims
    .filter(d => d.avg_score != null)
    .map(d => ({ dimension: d.dimension_label || d.dimension_id, score: d.avg_score as number }))

  const tiles: { label: string; value: string }[] = [
    { label: t('bench.metric.sessions'),   value: num(res.sessions) != null ? `${res.sessions}` : '—' },
    { label: t('bench.metric.resolution'), value: fmtVal(num(res.resolution_rate), 'pct') },
    { label: t('bench.metric.escalation'), value: fmtVal(num(res.escalation_rate), 'pct') },
    { label: t('bench.metric.aht'),        value: fmtVal(num(res.aht_ms), 'time') },
    { label: t('bench.metric.quality'),    value: num(qual.avg_score) != null ? `${fmtVal(num(qual.avg_score), 'score')} (${t('bench.detail.evals', { n: qual.n_evaluations ?? 0 })})` : '—' },
  ]
  if (isHuman) tiles.push({ label: t('bench.metric.occupancy'), value: fmtVal(num(av.occupancy_pct), 'pct') })

  const availMs = num(av.available_ms) ?? 0
  const pausedMs = num(av.paused_ms) ?? 0
  const donut = [
    { name: t('bench.detail.available'), value: availMs, fill: '#059669' },
    { name: t('bench.metric.pause'),     value: pausedMs, fill: '#D97706' },
  ].filter(d => d.value > 0)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        {tiles.map(t_ => <KpiTile key={t_.label} label={t_.label} value={t_.value} />)}
      </div>
      {radar.length >= 3 && (
        <div>
          <p className="text-2xs font-semibold text-muted uppercase tracking-wide mb-1">{t('bench.detail.qualityProfile')}</p>
          <ResponsiveContainer width="100%" height={220}>
            <RadarChart data={radar} outerRadius="70%">
              <PolarGrid stroke="#E5E7EB" />
              <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10 }} angle={90} />
              <Radar dataKey="score" stroke={colorFor(agentKey)} fill={colorFor(agentKey)} fillOpacity={0.3} />
              <Tooltip formatter={(v: number) => v?.toFixed?.(1)} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      )}
      {isHuman && donut.length > 0 && (
        <div>
          <p className="text-2xs font-semibold text-muted uppercase tracking-wide mb-1">{t('bench.detail.timeShare')}</p>
          <ResponsiveContainer width="100%" height={160}>
            <PieChart>
              <Pie data={donut} dataKey="value" nameKey="name" innerRadius={40} outerRadius={64} paddingAngle={2}>
                {donut.map((d, i) => <Cell key={i} fill={d.fill} />)}
              </Pie>
              <Tooltip formatter={(v: number) => fmtMsShort(v)} />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

// ── View Cruzamento (F6.3) — tabela de concordância + quadrante ───────────────

const FLAG_META: Record<CrossFlag, { icon: string; color: string }> = {
  star:        { icon: '★', color: '#059669' },
  perception:  { icon: '⚠', color: '#D97706' },
  disposition: { icon: '◑', color: '#DC2626' },
}

function CrossView({
  resp, loading, onPick, t,
}: {
  resp: CrossResp | null; loading: boolean
  onPick: (r: CrossRow) => void
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  if (loading) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('bench.chart.loading')}</div>
  )
  if (!resp || resp.error || resp.data.length === 0) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light">{t('bench.chart.noData')}</div>
  )
  const rows = [...resp.data].sort((a, b) => b.sessions - a.sessions)

  // Quadrante: só agentes com qualidade avaliada (eixo Y = qualidade).
  const scatter = rows
    .filter(r => r.quality_score != null && r.resolution_rate != null)
    .map(r => ({
      x: (r.resolution_rate as number) * 100,
      y: (r.quality_score as number) * 100,
      z: Math.max(r.sessions, 1),
      label: r.label,
      nps: r.nps,
      fill: r.nps == null ? '#94A3B8' : r.nps >= 50 ? '#059669' : r.nps >= 0 ? '#2D9CDB' : '#DC2626',
    }))

  const pctOrDash = (v: number | null) => v == null ? '—' : `${(v * 100).toFixed(1)}%`

  return (
    <div className="space-y-4">
      {/* Tabela de concordância */}
      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('bench.cross.tableTitle')}</p>
          <div className="flex items-center gap-3 text-2xs text-muted-light">
            {(Object.keys(FLAG_META) as CrossFlag[]).map(f => (
              <span key={f} className="flex items-center gap-1">
                <span style={{ color: FLAG_META[f].color }}>{FLAG_META[f].icon}</span>
                {t(`bench.cross.flag.${f}`)}
              </span>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-2xs text-muted uppercase tracking-wide bg-surface-muted/50">
                <th className="text-left font-semibold px-4 py-2">{t('bench.cross.agent')}</th>
                <th className="text-right font-semibold px-3 py-2">{t('bench.metric.sessions')}</th>
                <th className="text-right font-semibold px-3 py-2">{t('bench.cross.resolution')}</th>
                <th className="text-right font-semibold px-3 py-2">{t('bench.cross.escalation')}</th>
                <th className="text-right font-semibold px-3 py-2">{t('bench.cross.quality')}</th>
                <th className="text-right font-semibold px-3 py-2">{t('bench.cross.nps')}</th>
                <th className="text-center font-semibold px-3 py-2">{t('bench.cross.signals')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(r => {
                const flags = crossFlags(r)
                return (
                  <tr key={r.agent_key} className="hover:bg-surface-muted cursor-pointer"
                    onClick={() => onPick(r)}>
                    <td className="px-4 py-2">
                      <span className="flex items-center gap-1.5 min-w-0">
                        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: colorFor(r.agent_key) }} />
                        <span className="font-medium text-dark truncate">{r.label}</span>
                        <span className="text-muted-light flex-shrink-0">{r.agent_type === 'human' ? '👤' : '🤖'}</span>
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-dark">{r.sessions}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{pctOrDash(r.resolution_rate)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{pctOrDash(r.escalation_rate)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {r.quality_score == null
                        ? <span className="text-muted-light">{t('bench.cross.naQuality')}</span>
                        : <span>{(r.quality_score * 100).toFixed(0)}<span className="text-muted-light text-2xs"> ({r.quality_n})</span></span>}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {r.nps == null
                        ? <span className="text-muted-light">{t('bench.cross.naNps')}</span>
                        : <span>{Math.round(r.nps)}<span className="text-muted-light text-2xs"> ({r.nps_n})</span></span>}
                    </td>
                    <td className="px-3 py-2 text-center whitespace-nowrap">
                      {flags.length === 0
                        ? <span className="text-muted-light">·</span>
                        : flags.map(f => (
                            <span key={f} title={t(`bench.cross.flag.${f}`)}
                              style={{ color: FLAG_META[f].color }} className="text-sm mx-0.5">{FLAG_META[f].icon}</span>
                          ))}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Quadrante resolução × qualidade */}
      <div className="bg-white rounded-lg border border-border px-4 pt-3 pb-2">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">{t('bench.cross.quadrant')}</p>
        <p className="text-2xs text-muted-light mb-2">{t('bench.cross.quadrantHint')}</p>
        {scatter.length === 0 ? (
          <div className="h-52 flex items-center justify-center text-sm text-muted-light text-center px-6">
            {t('bench.cross.quadrantEmpty')}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <ScatterChart margin={{ top: 8, right: 24, left: 0, bottom: 16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis type="number" dataKey="x" name={t('bench.cross.resolution')} domain={[0, 100]}
                tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v}%`}
                label={{ value: t('bench.cross.resolution'), position: 'insideBottom', offset: -8, fontSize: 11 }} />
              <YAxis type="number" dataKey="y" name={t('bench.cross.quality')} domain={[0, 100]}
                tick={{ fontSize: 11 }}
                label={{ value: t('bench.cross.quality'), angle: -90, position: 'insideLeft', fontSize: 11 }} />
              <ZAxis type="number" dataKey="z" range={[60, 600]} name={t('bench.metric.sessions')} />
              <ReferenceLine x={70} stroke="#CBD5E1" strokeDasharray="4 4" />
              <ReferenceLine y={70} stroke="#CBD5E1" strokeDasharray="4 4" />
              <Tooltip cursor={{ strokeDasharray: '3 3' }}
                content={({ payload }) => {
                  const p = payload?.[0]?.payload as { label: string; x: number; y: number; z: number; nps: number | null } | undefined
                  if (!p) return null
                  return (
                    <div className="bg-white border border-border rounded-lg shadow-sm px-3 py-2 text-xs">
                      <p className="font-semibold text-dark mb-0.5">{p.label}</p>
                      <p className="text-muted">{t('bench.cross.resolution')}: {p.x.toFixed(0)}%</p>
                      <p className="text-muted">{t('bench.cross.quality')}: {p.y.toFixed(0)}</p>
                      <p className="text-muted">{t('bench.metric.sessions')}: {p.z}</p>
                      <p className="text-muted">{t('bench.cross.nps')}: {p.nps == null ? '—' : Math.round(p.nps)}</p>
                    </div>
                  )
                }} />
              <Scatter data={scatter}>
                {scatter.map((d, i) => <Cell key={i} fill={d.fill} fillOpacity={0.75} />)}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}

// ── Página ──────────────────────────────────────────────────────────────────

export default function AgentsBenchPage() {
  const { t } = useTranslation('agentReports')
  const { tenantId } = useAuth()

  // F4.5 — estado inicial vem da URL (lente/pool/período/seleção sobrevivem a
  // reload e navegação; link compartilhável).
  const [sp, setSp] = useSearchParams()
  const [fromDt, setFromDt] = useState(sp.get('from') || DEFAULT_FILTERS.fromDt)
  const [toDt,   setToDt]   = useState(sp.get('to')   || DEFAULT_FILTERS.toDt)
  const [poolId, setPoolId] = useState(sp.get('pool') || '')
  const [lens,   setLens]   = useState<LensId>(
    (LENSES.some(l => l.id === sp.get('lens')) ? sp.get('lens') : 'resolution') as LensId)
  const [selected, setSelected] = useState<string[]>(
    (sp.get('sel') || '').split(',').filter(Boolean))
  const [view, setView] = useState<'lenses' | 'cross'>(sp.get('view') === 'cross' ? 'cross' : 'lenses')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [detail, setDetail] = useState<{ key: string; label: string; type: string } | null>(null)

  // Sincroniza estado → URL (replace, sem empilhar histórico).
  useEffect(() => {
    const next = new URLSearchParams()
    if (fromDt) next.set('from', fromDt)
    if (toDt)   next.set('to', toDt)
    if (poolId) next.set('pool', poolId)
    if (lens !== 'resolution') next.set('lens', lens)
    if (selected.length) next.set('sel', selected.join(','))
    if (view === 'cross') next.set('view', view)
    setSp(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDt, toDt, poolId, lens, selected, view])

  const lensDef = LENSES.find(l => l.id === lens)!
  const { rows: perfRows, loading: listLoading } = usePerformanceList(tenantId ?? '', fromDt, toDt)
  const { resp, loading: chartLoading } = useCompare(tenantId ?? '', fromDt, toDt, poolId, lens, selected)
  const { resp: crossResp, loading: crossLoading } = useCross(tenantId ?? '', fromDt, toDt, poolId, view === 'cross')

  // F4.3 — árvore pools → agentes. Um agente pode aparecer em mais de um pool;
  // a seleção é por agent_key (global, como no compare). sessions/resolved
  // somados por (pool, agent_key) para as colunas essenciais.
  interface AgentItem { key: string; label: string; type: string; sessions: number; resolved: number }
  const poolGroups = useMemo(() => {
    const pools = new Map<string, Map<string, AgentItem>>()
    for (const r of perfRows) {
      const key = agentKeyOf(r)
      const pm = pools.get(r.pool_id) ?? new Map<string, AgentItem>()
      pools.set(r.pool_id, pm)
      const ex = pm.get(key)
      if (ex) { ex.sessions += r.total_sessions; ex.resolved += r.resolved_count }
      else pm.set(key, {
        key, label: labelOf(r), type: r.agent_type ?? 'native',
        sessions: r.total_sessions, resolved: r.resolved_count,
      })
    }
    return [...pools.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([pool, m]) => ({ pool, agents: [...m.values()].sort((a, b) => a.label.localeCompare(b.label)) }))
  }, [perfRows])

  // F4.5 — combo de pool populado pela árvore completa; árvore exibida filtrada
  // no cliente (o servidor já escopa a média/gráfico via poolId no compare).
  const poolOptions = useMemo(() => poolGroups.map(g => g.pool), [poolGroups])
  const shownGroups = poolId ? poolGroups.filter(g => g.pool === poolId) : poolGroups

  const isDisabled = (type: string) => lensDef.domain === 'human' && type !== 'human'

  const toggle = (key: string, disabled: boolean) => {
    if (disabled) return
    setSelected(s => s.includes(key) ? s.filter(k => k !== key) : [...s, key])
  }
  // Checkbox do pool = bulk dos agentes elegíveis do pool (média do pool como
  // série agregada única é refinamento futuro — exigiria pseudo-entidade no
  // endpoint compare; ver F4.5/§2 do spec).
  const poolToggle = (agents: AgentItem[]) => {
    const keys = agents.filter(a => !isDisabled(a.type)).map(a => a.key)
    const allOn = keys.length > 0 && keys.every(k => selected.includes(k))
    setSelected(s => allOn ? s.filter(k => !keys.includes(k)) : [...new Set([...s, ...keys])])
  }
  const toggleExpand = (pool: string) =>
    setExpanded(s => { const n = new Set(s); n.has(pool) ? n.delete(pool) : n.add(pool); return n })

  // F9 — fixar a média do pool como série selecionável (pseudo-entidade `pool:<id>`).
  const poolPinKey = (pool: string) => `pool:${pool}`
  const togglePoolPin = (pool: string) => {
    const k = poolPinKey(pool)
    setSelected(s => s.includes(k) ? s.filter(x => x !== k) : [...s, k])
  }

  const esc = (v: unknown) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const downloadCsv = (lines: string[], suffix: string) => {
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `bancada_${suffix}_${fromDt}_${toDt}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  // F6.3 — no Cross-cut, exporta a tabela de cruzamento (uma linha por agente).
  const exportCrossCsv = () => {
    if (!crossResp?.data.length) return
    const header = ['agent_key', 'label', 'agent_type', 'sessions', 'resolution_rate',
      'escalation_rate', 'quality_score', 'quality_n', 'nps', 'avg_nps', 'nps_n', 'signals']
    const lines = [header.join(',')]
    for (const r of crossResp.data) {
      lines.push([
        esc(r.agent_key), esc(r.label), esc(r.agent_type), r.sessions,
        esc(r.resolution_rate ?? ''), esc(r.escalation_rate ?? ''),
        esc(r.quality_score ?? ''), r.quality_n,
        esc(r.nps ?? ''), esc(r.avg_nps ?? ''), r.nps_n,
        esc(crossFlags(r).join('|')),
      ].join(','))
    }
    downloadCsv(lines, 'cruzamento')
  }

  // F4.5 — export CSV do conjunto comparado (média + entidades) da lente atual.
  // Formato longo: entity,date,<métricas numéricas presentes na série>.
  const exportLensCsv = () => {
    if (!resp) return
    const lm = labelMapOf(resp, t)
    const series: { label: string; points: SeriesPoint[] }[] = []
    if (resp.data.average) series.push({ label: lm.__avg__ ?? 'average', points: resp.data.average.series })
    for (const e of resp.data.entities) series.push({ label: e.label || e.agent_key, points: e.series })
    const keys = [...new Set(series.flatMap(s => s.points.flatMap(p =>
      Object.keys(p).filter(k => k !== 'date' && typeof p[k] === 'number'))))]
    const header = ['entity', 'date', ...keys]
    const lines = [header.join(',')]
    for (const s of series)
      for (const p of s.points)
        lines.push([esc(s.label), esc(p.date), ...keys.map(k => esc(p[k] ?? ''))].join(','))
    downloadCsv(lines, lens)
  }

  // Roteia o export conforme a view ativa.
  const exportCsv = () => view === 'cross' ? exportCrossCsv() : exportLensCsv()
  const exportDisabled = view === 'cross' ? !crossResp?.data.length : !resp

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('error.loading')}
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Filtro */}
      <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0 flex items-center gap-3 flex-wrap">
        <span className="text-xs text-muted">{t('bench.filters.from')}</span>
        <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)} className={inp} />
        <span className="text-xs text-muted">{t('bench.filters.to')}</span>
        <input type="date" value={toDt} onChange={e => setToDt(e.target.value)} className={inp} />
        <select value={poolId} onChange={e => setPoolId(e.target.value)} className={`${inp} w-48`}>
          <option value="">{t('bench.filters.allPools')}</option>
          {poolOptions.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <div className="flex-1" />
        <button onClick={exportCsv} disabled={exportDisabled}
          className={`${inp} flex items-center gap-1 disabled:opacity-40 hover:border-border-strong`}>
          ⬇ {t('bench.filters.exportCsv')}
        </button>
      </div>

      <div className="flex-1 overflow-hidden flex">

        {/* Coluna esquerda: lista */}
        <div className="w-80 flex-shrink-0 border-r border-border bg-white overflow-y-auto">
          <div className="px-3 py-2 border-b border-border bg-surface-muted sticky top-0">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('bench.list.title')}</p>
          </div>
          {listLoading ? (
            <div className="p-4 text-sm text-muted-light animate-pulse">{t('bench.list.loading')}</div>
          ) : poolGroups.length === 0 ? (
            <div className="p-4 text-sm text-muted-light">{t('bench.list.noData')}</div>
          ) : (
            <ul className="divide-y divide-border">
              {shownGroups.map(({ pool, agents }) => {
                const open = expanded.has(pool)
                const eligible = agents.filter(a => !isDisabled(a.type)).map(a => a.key)
                const allOn = eligible.length > 0 && eligible.every(k => selected.includes(k))
                const someOn = eligible.some(k => selected.includes(k))
                return (
                  <li key={pool}>
                    {/* Linha do pool */}
                    <div className="px-2 py-2 flex items-center gap-1.5 bg-surface-muted/40">
                      <button onClick={() => toggleExpand(pool)}
                        className="w-4 h-4 flex items-center justify-center text-muted hover:text-dark"
                        aria-label={t(open ? 'bench.list.collapse' : 'bench.list.expand')}>
                        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
                      </button>
                      <input type="checkbox" checked={allOn}
                        ref={el => { if (el) el.indeterminate = !allOn && someOn }}
                        disabled={eligible.length === 0}
                        onChange={() => poolToggle(agents)} className="accent-primary" />
                      <span className="flex-1 min-w-0 text-xs font-semibold text-dark font-mono truncate">{pool}</span>
                      <button onClick={() => togglePoolPin(pool)}
                        title={t('bench.list.pinPoolAvg')} aria-label={t('bench.list.pinPoolAvg')}
                        className={`text-2xs font-bold px-1.5 py-0.5 rounded border transition-colors ${
                          selected.includes(poolPinKey(pool))
                            ? 'border-transparent text-white'
                            : 'border-border text-muted-light hover:text-dark hover:border-border-strong'
                        }`}
                        style={{ background: selected.includes(poolPinKey(pool)) ? colorFor(poolPinKey(pool)) : undefined }}>
                        μ
                      </button>
                      <span className="text-2xs text-muted-light">{agents.length}</span>
                    </div>
                    {/* Agentes do pool */}
                    {open && (
                      <ul>
                        {agents.map(a => {
                          const disabled = isDisabled(a.type)
                          const checked = selected.includes(a.key)
                          const resPct = a.sessions ? a.resolved / a.sessions : null
                          return (
                            <li key={`${pool}:${a.key}`}
                              className={`pl-8 pr-3 py-1.5 flex items-center gap-2 ${disabled ? 'opacity-40' : 'hover:bg-surface-muted'}`}>
                              <input type="checkbox" checked={checked} disabled={disabled}
                                onChange={() => toggle(a.key, disabled)}
                                className="accent-primary" style={{ accentColor: checked ? colorFor(a.key) : undefined }} />
                              <button onClick={() => setDetail({ key: a.key, label: a.label, type: a.type })}
                                className="flex-1 min-w-0 text-left">
                                <span className="block text-xs font-medium text-dark truncate hover:text-primary">{a.label}</span>
                                <span className="block text-2xs text-muted-light truncate">
                                  {a.type === 'human' ? '👤' : '🤖'} {a.sessions} · {resPct == null ? '—' : `${(resPct * 100).toFixed(0)}%`}
                                </span>
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        {/* Coluna direita: toggle de view + conteúdo */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">

          {/* Toggle Lentes ↔ Cruzamento */}
          <div className="inline-flex rounded-lg border border-border overflow-hidden bg-white">
            {(['lenses', 'cross'] as const).map(v => (
              <button key={v} onClick={() => setView(v)}
                className={`px-4 py-1.5 text-xs font-medium transition-colors ${
                  view === v ? 'bg-primary text-white' : 'text-muted hover:text-dark hover:bg-surface-muted'
                }`}>
                {t(`bench.view.${v}`)}
              </button>
            ))}
          </div>

          {view === 'lenses' ? (
            <>
              {/* Seletor de lente */}
              <div className="bg-white rounded-lg border border-border p-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-muted mr-1">{t('bench.lens.label')}</span>
                  {LENSES.map(l => (
                    <button key={l.id} onClick={() => setLens(l.id)}
                      className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                        lens === l.id
                          ? 'border-primary bg-primary text-white'
                          : 'border-border text-muted hover:text-dark hover:border-border-strong'
                      }`}>
                      {t(`bench.lens.${l.id}`)}
                    </button>
                  ))}
                </div>
                <p className="text-2xs text-muted-light mt-2">{t(`bench.domain.${lensDef.domain}`)}</p>
              </div>

              {/* Gráfico de comparação */}
              <div className="bg-white rounded-lg border border-border px-4 pt-3 pb-2">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-semibold text-muted uppercase tracking-wide">
                    {t(`bench.lens.${lens}`)}
                  </p>
                  {resp?.data.average && (
                    <span className="text-2xs text-muted-light">{t('bench.n', { n: resp.data.average.n })}</span>
                  )}
                </div>
                {chartLoading
                  ? <div className="h-52 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('bench.chart.loading')}</div>
                  : <LensChart lens={lens} resp={resp} selected={selected} t={t} />}
              </div>
            </>
          ) : (
            <CrossView resp={crossResp} loading={crossLoading} t={t}
              onPick={r => setDetail({ key: r.agent_key, label: r.label, type: r.agent_type })} />
          )}

        </div>
      </div>

      {/* Detalhe (stub F4.3 → type-aware na F4.4) */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          onClick={() => setDetail(null)}>
          <div className="bg-white rounded-xl shadow-xl border border-border w-[34rem] max-w-[92vw] p-5"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-3">
              <div>
                <p className="text-sm font-bold text-dark">{detail.label}</p>
                <p className="text-2xs text-muted-light">
                  {detail.type === 'human' ? '👤' : '🤖'} {t(`bench.detail.type.${detail.type === 'human' ? 'human' : 'ai'}`)}
                </p>
              </div>
              <button onClick={() => setDetail(null)}
                className="text-muted hover:text-dark text-lg leading-none">×</button>
            </div>
            <AgentDetail tenantId={tenantId} fromDt={fromDt} toDt={toDt}
              agentKey={detail.key} isHuman={detail.type === 'human'} t={t} />
          </div>
        </div>
      )}
    </div>
  )
}
