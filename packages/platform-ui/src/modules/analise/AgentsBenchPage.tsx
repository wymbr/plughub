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
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'

// ── Lentes ──────────────────────────────────────────────────────────────────
// domain: 'universal' (humano + IA) | 'human' (IA desabilitada na lista).
// primaryKey: métrica plotada no gráfico mínimo da F4.1 (F4.2 enriquece a viz).

type LensId = 'resolution' | 'sessions_aht' | 'availability' | 'pause_reason' | 'quality'
type Domain = 'universal' | 'human'

interface LensDef { id: LensId; domain: Domain; primaryKey: string | null; pct: boolean }

const LENSES: LensDef[] = [
  { id: 'resolution',   domain: 'universal', primaryKey: 'resolution_rate', pct: true  },
  { id: 'sessions_aht', domain: 'universal', primaryKey: 'sessions',        pct: false },
  { id: 'availability', domain: 'human',     primaryKey: 'occupancy_pct',   pct: true  },
  { id: 'pause_reason', domain: 'human',     primaryKey: null,              pct: false },
  { id: 'quality',      domain: 'universal', primaryKey: 'avg_score',       pct: false },
]

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

function usePerformanceList(tenantId: string, fromDt: string, toDt: string, poolId: string) {
  const [rows, setRows] = useState<PerfRow[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt })
    if (poolId) p.set('pool_id', poolId)
    fetch(`/reports/agents/performance?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: PerfRow[] }) => setRows(d.data ?? []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt, poolId])
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

// ── Gráfico mínimo (F4.1) — plota a métrica primária da lente ─────────────────

function MiniChart({
  resp, lensDef, selected, t,
}: {
  resp: CompareResp | null; lensDef: LensDef; selected: string[]
  t: (k: string, o?: Record<string, unknown>) => string
}) {
  const key = lensDef.primaryKey
  const rows = useMemo(() => {
    if (!resp || !key) return []
    const byDate = new Map<string, Record<string, number | string | null>>()
    const put = (series: SeriesPoint[], col: string) => {
      for (const pt of series) {
        const d = String(pt.date)
        if (!byDate.has(d)) byDate.set(d, { date: d })
        const v = pt[key]
        byDate.get(d)![col] = (typeof v === 'number') ? (lensDef.pct ? v * 100 : v) : null
      }
    }
    if (resp.data.average) put(resp.data.average.series, '__avg__')
    for (const e of resp.data.entities) put(e.series, e.agent_key)
    return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))
  }, [resp, key, lensDef.pct])

  if (!key) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light">
      {t('bench.chart.pendingViz')}
    </div>
  )
  if (rows.length === 0) return (
    <div className="h-52 flex items-center justify-center text-sm text-muted-light">
      {t('bench.chart.noData')}
    </div>
  )

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={rows} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(d: string) => d.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => lensDef.pct ? `${v}%` : `${v}`}
          domain={lensDef.pct ? [0, 100] : ['auto', 'auto']} />
        <Tooltip formatter={(v: number) => lensDef.pct ? `${v?.toFixed?.(1)}%` : v} />
        <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
        {/* Média de referência — linha grossa tracejada (estilo definitivo na F4.2) */}
        <Line type="monotone" dataKey="__avg__" name={t('bench.average')}
          stroke="#111827" strokeWidth={2.5} strokeDasharray="6 4" dot={false} connectNulls={false} />
        {selected.map(k => (
          <Line key={k} type="monotone" dataKey={k} name={k} stroke={colorFor(k)}
            strokeWidth={2} dot={false} connectNulls={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

// ── Página ──────────────────────────────────────────────────────────────────

export default function AgentsBenchPage() {
  const { t } = useTranslation('agentReports')
  const { tenantId } = useAuth()

  const [fromDt, setFromDt] = useState(DEFAULT_FILTERS.fromDt)
  const [toDt,   setToDt]   = useState(DEFAULT_FILTERS.toDt)
  const [poolId, setPoolId] = useState('')
  const [lens,   setLens]   = useState<LensId>('resolution')
  const [selected, setSelected] = useState<string[]>([])

  const lensDef = LENSES.find(l => l.id === lens)!
  const { rows: perfRows, loading: listLoading } = usePerformanceList(tenantId ?? '', fromDt, toDt, poolId)
  const { resp, loading: chartLoading } = useCompare(tenantId ?? '', fromDt, toDt, poolId, lens, selected)

  // Lista de agentes (flat na F4.1; agrupada por pool na F4.3).
  const agents = useMemo(() => {
    const seen = new Map<string, { key: string; label: string; type: string; pool: string; sessions: number }>()
    for (const r of perfRows) {
      const key = agentKeyOf(r)
      const ex = seen.get(key)
      if (ex) { ex.sessions += r.total_sessions; continue }
      seen.set(key, {
        key, label: labelOf(r), type: r.agent_type ?? 'native',
        pool: r.pool_id, sessions: r.total_sessions,
      })
    }
    return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label))
  }, [perfRows])

  const toggle = (key: string, disabled: boolean) => {
    if (disabled) return
    setSelected(s => s.includes(key) ? s.filter(k => k !== key) : [...s, key])
  }

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
        <input type="text" value={poolId} onChange={e => setPoolId(e.target.value)}
          placeholder={t('bench.filters.poolPlaceholder')} className={`${inp} w-40`} />
      </div>

      <div className="flex-1 overflow-hidden flex">

        {/* Coluna esquerda: lista */}
        <div className="w-80 flex-shrink-0 border-r border-border bg-white overflow-y-auto">
          <div className="px-3 py-2 border-b border-border bg-surface-muted sticky top-0">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('bench.list.title')}</p>
          </div>
          {listLoading ? (
            <div className="p-4 text-sm text-muted-light animate-pulse">{t('bench.list.loading')}</div>
          ) : agents.length === 0 ? (
            <div className="p-4 text-sm text-muted-light">{t('bench.list.noData')}</div>
          ) : (
            <ul className="divide-y divide-border">
              {agents.map(a => {
                const disabled = lensDef.domain === 'human' && a.type !== 'human'
                const checked = selected.includes(a.key)
                return (
                  <li key={a.key}
                    className={`px-3 py-2 flex items-center gap-2 ${disabled ? 'opacity-40' : 'hover:bg-surface-muted'}`}>
                    <input type="checkbox" checked={checked} disabled={disabled}
                      onChange={() => toggle(a.key, disabled)}
                      className="accent-primary" style={{ accentColor: checked ? colorFor(a.key) : undefined }} />
                    <span className="flex-1 min-w-0">
                      <span className="block text-xs font-medium text-dark truncate">{a.label}</span>
                      <span className="block text-2xs text-muted-light font-mono truncate">
                        {a.type === 'human' ? '👤' : '🤖'} {a.pool}
                      </span>
                    </span>
                    <span className="text-2xs text-muted tabular-nums">{a.sessions}</span>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        {/* Coluna direita: lente + gráfico */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">

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
              : <MiniChart resp={resp} lensDef={lensDef} selected={selected} t={t} />}
          </div>

        </div>
      </div>
    </div>
  )
}
