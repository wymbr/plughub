/**
 * AnalisePoolsPage — /analise/pools (Fase 2 — saúde operacional por pool/canal)
 *
 * Sub-abas: Volume (implementado) · Fila · Capacidade · SLA (em breve).
 * Volume lê GET /reports/pools/volume → área de contatos no tempo (empilhada por
 * canal) + donut por canal + tabela por endpoint (DNIS).
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts'
import { useAuth } from '@/auth/useAuth'

type SubTab = 'volume' | 'queue' | 'capacity' | 'sla'

interface VolumeRow   { bucket: string; pool_id: string; channel: string; endpoint: string; contacts: number }
interface ChannelRow  { channel: string; contacts: number }
interface EndpointRow { channel: string; endpoint: string; contacts: number }
interface VolumeData  { series: VolumeRow[]; by_channel: ChannelRow[]; by_endpoint: EndpointRow[]; totals: { contacts: number } }

interface OccPoolRow  { pool_id: string; peak_concurrency: number; capacity: number; headroom: number; utilization: number | null }
interface OccTotal    { peak_concurrency: number; capacity: number; headroom: number; utilization: number | null }
interface OccData     { series: unknown[]; by_pool: OccPoolRow[]; total: OccTotal | null }

interface QSeriesRow  { bucket: string; pool_id: string; avg_wait_ms: number; contacts: number; queued: number; abandoned: number; max_queue_len: number; available_agents: number }
interface QPoolRow    { pool_id: string; contacts: number; queued: number; abandoned: number; abandon_rate: number; avg_wait_ms: number; p95_wait_ms: number; sla_target_ms: number; within_sla: number; sla_eligible: number; sla_attainment: number | null }
interface QueueData   { series: QSeriesRow[]; by_pool: QPoolRow[] }

const CHANNEL_COLORS = ['#1B4F8A', '#2D9CDB', '#00B4D8', '#059669', '#D97706', '#7C3AED', '#DC2626', '#0891B2']

function fmtBucket(b: string): string {
  return b.slice(5, 16).replace('T', ' ')
}

function fmtMs(ms: number): string {
  if (!ms || ms < 1000) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

function colorFor(i: number): string {
  return CHANNEL_COLORS[i % CHANNEL_COLORS.length]
}

// ── Volume sub-tab ─────────────────────────────────────────────────────────────

const VolumeSubTab: React.FC<{ data: VolumeData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')

  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">
      {t('pools.volume.loading')}
    </div>
  )
  const series = data?.series ?? []
  if (series.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">
      {t('pools.volume.noData')}
    </div>
  )

  const chLabel = (ch: string) => ch || '—'
  const channels = [...new Set(series.map(r => chLabel(r.channel)))].sort()
  const byBucket = new Map<string, Record<string, number | string>>()
  for (const r of series) {
    const row = byBucket.get(r.bucket) ?? { bucket: r.bucket }
    const k = chLabel(r.channel)
    row[k] = ((row[k] as number) ?? 0) + r.contacts
    byBucket.set(r.bucket, row)
  }
  const chartData = [...byBucket.values()].sort((a, b) => String(a.bucket).localeCompare(String(b.bucket)))
  const donut = (data?.by_channel ?? []).map(c => ({ name: chLabel(c.channel), value: c.contacts }))

  return (
    <div className="flex flex-col gap-4">
      {/* Área no tempo */}
      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.title')}</p>
        </div>
        <div className="p-3">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip labelFormatter={fmtBucket} />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
              {channels.map((ch, i) => (
                <Area key={ch} type="monotone" dataKey={ch} stackId="1"
                      stroke={colorFor(i)} fill={colorFor(i)} fillOpacity={0.5} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Donut por canal */}
        <div className="bg-white rounded-lg border border-border overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.byChannel')}</p>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={donut} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={2}>
                {donut.map((_, i) => <Cell key={i} fill={colorFor(i)} />)}
              </Pie>
              <Tooltip />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Tabela por endpoint */}
        <div className="bg-white rounded-lg border border-border overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.byEndpoint')}</p>
          </div>
          <div className="overflow-auto max-h-[220px]">
            <table className="min-w-full text-xs border-collapse">
              <thead className="bg-surface-muted sticky top-0">
                <tr className="border-b border-border text-2xs text-muted uppercase">
                  <th className="text-left px-3 py-2">{t('pools.volume.cols.channel')}</th>
                  <th className="text-left px-3 py-2">{t('pools.volume.cols.endpoint')}</th>
                  <th className="text-right px-3 py-2">{t('pools.volume.cols.contacts')}</th>
                </tr>
              </thead>
              <tbody>
                {(data?.by_endpoint ?? []).map((r, i) => (
                  <tr key={i} className="border-b border-border hover:bg-surface-muted">
                    <td className="px-3 py-2 text-dark">{r.channel || '—'}</td>
                    <td className="px-3 py-2 text-muted">{r.endpoint || '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-dark">{r.contacts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Capacity sub-tab ───────────────────────────────────────────────────────────

function utilColor(u: number | null): string {
  if (u === null) return 'var(--color-border-strong, #D1D5DB)'
  if (u >= 0.9) return '#DC2626'
  if (u >= 0.7) return '#D97706'
  return '#059669'
}

const CapacitySubTab: React.FC<{ data: OccData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')
  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )
  const rows = data?.by_pool ?? []
  if (rows.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">{t('pools.capacity.noData')}</div>
  )
  const total = data?.total
  const pct = (u: number | null) => u === null ? '—' : `${Math.round(u * 100)}%`

  return (
    <div className="flex flex-col gap-4">
      {total && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.totalPeak')}</div><div className="text-xl font-semibold">{total.peak_concurrency}</div></div>
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.totalCap')}</div><div className="text-xl font-semibold">{total.capacity}</div></div>
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.headroom')}</div><div className="text-xl font-semibold">{total.headroom}</div></div>
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.utilization')}</div><div className="text-xl font-semibold" style={{ color: utilColor(total.utilization) }}>{pct(total.utilization)}</div></div>
        </div>
      )}

      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.capacity.title')}</p>
          <p className="text-2xs text-muted-light mt-0.5">{t('pools.capacity.hint')}</p>
        </div>
        <table className="min-w-full text-xs border-collapse">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-2xs text-muted uppercase">
              <th className="text-left px-3 py-2">{t('pools.capacity.cols.pool')}</th>
              <th className="text-right px-3 py-2">{t('pools.capacity.cols.peak')}</th>
              <th className="text-right px-3 py-2">{t('pools.capacity.cols.capacity')}</th>
              <th className="text-right px-3 py-2">{t('pools.capacity.cols.headroom')}</th>
              <th className="px-3 py-2 w-[34%]">{t('pools.capacity.cols.util')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const u = r.utilization ?? (r.capacity > 0 ? r.peak_concurrency / r.capacity : 0)
              return (
                <tr key={i} className="border-b border-border hover:bg-surface-muted">
                  <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-dark">{r.peak_concurrency}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted">{r.capacity}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-dark">{r.headroom}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-2.5 rounded bg-surface-muted overflow-hidden">
                        <div className="h-full rounded" style={{ width: `${Math.min(Math.round(u * 100), 100)}%`, backgroundColor: utilColor(r.utilization) }} />
                      </div>
                      <span className="text-2xs tabular-nums text-muted w-9 text-right">{pct(r.utilization)}</span>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Queue (Fila) sub-tab ─────────────────────────────────────────────────────

const FilaSubTab: React.FC<{ data: QueueData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')
  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )
  const rows = data?.by_pool ?? []
  if (rows.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">{t('pools.queue.noData')}</div>
  )

  const byBucket = new Map<string, { bucket: string; _w: number; _n: number; available: number }>()
  for (const r of (data?.series ?? [])) {
    const b = byBucket.get(r.bucket) ?? { bucket: r.bucket, _w: 0, _n: 0, available: 0 }
    if (r.avg_wait_ms > 0) { b._w += r.avg_wait_ms; b._n += 1 }
    b.available += r.available_agents
    byBucket.set(r.bucket, b)
  }
  const chartData = [...byBucket.values()]
    .sort((a, b) => a.bucket.localeCompare(b.bucket))
    .map(b => ({ bucket: b.bucket, wait_s: b._n ? Math.round(b._w / b._n / 1000) : 0, available: b.available }))

  return (
    <div className="flex flex-col gap-4">
      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.queue.title')}</p>
        </div>
        <div className="p-3">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip labelFormatter={fmtBucket} />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="wait_s" name={t('pools.queue.waitAvg')} stroke="#D97706" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="available" name={t('pools.queue.available')} stroke="#2D9CDB" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.queue.tableTitle')}</p>
        </div>
        <table className="min-w-full text-xs border-collapse">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-2xs text-muted uppercase">
              <th className="text-left px-3 py-2">{t('pools.queue.cols.pool')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.contacts')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.queued')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.abandoned')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.abandonRate')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.avgWait')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.p95Wait')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-border hover:bg-surface-muted">
                <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{r.contacts}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{r.queued}</td>
                <td className="px-3 py-2 text-right tabular-nums text-warning-text">{r.abandoned}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{Math.round((r.abandon_rate ?? 0) * 100)}%</td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{fmtMs(r.avg_wait_ms)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtMs(r.p95_wait_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SLA sub-tab ──────────────────────────────────────────────────────────────

const SlaSubTab: React.FC<{ data: QueueData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')
  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )
  const rows = (data?.by_pool ?? []).filter(r => r.sla_eligible > 0)
  if (rows.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">{t('pools.sla.noData')}</div>
  )

  return (
    <div className="bg-white rounded-lg border border-border overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.sla.title')}</p>
      </div>
      <table className="min-w-full text-xs border-collapse">
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-2xs text-muted uppercase">
            <th className="text-left px-3 py-2">{t('pools.sla.cols.pool')}</th>
            <th className="text-right px-3 py-2">{t('pools.sla.cols.target')}</th>
            <th className="text-right px-3 py-2">{t('pools.sla.cols.within')}</th>
            <th className="px-3 py-2 w-[40%]">{t('pools.sla.cols.attainment')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const a = r.sla_attainment ?? 0
            const c = a >= 0.9 ? '#059669' : a >= 0.75 ? '#D97706' : '#DC2626'
            return (
              <tr key={i} className="border-b border-border hover:bg-surface-muted">
                <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtMs(r.sla_target_ms)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{r.within_sla}/{r.sla_eligible}</td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2.5 rounded bg-surface-muted overflow-hidden">
                      <div className="h-full rounded" style={{ width: `${Math.round(a * 100)}%`, backgroundColor: c }} />
                    </div>
                    <span className="text-2xs tabular-nums w-9 text-right" style={{ color: c }}>{Math.round(a * 100)}%</span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function AnalisePoolsPage() {
  const { t }        = useTranslation('agentReports')
  const { tenantId } = useAuth()

  const today   = new Date()
  const weekAgo = new Date(today.getTime() - 7 * 86400000)
  const iso     = (d: Date) => d.toISOString().slice(0, 10)

  const [subTab,  setSubTab]  = useState<SubTab>('volume')
  const [fromDt,  setFromDt]  = useState(iso(weekAgo))
  const [toDt,    setToDt]    = useState(iso(today))
  const [poolId,  setPoolId]  = useState('')
  const [channel, setChannel] = useState('')

  const [volume,  setVolume]  = useState<VolumeData | null>(null)
  const [occ,     setOcc]     = useState<OccData | null>(null)
  const [queue,   setQueue]   = useState<QueueData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!tenantId || subTab !== 'volume') return
    let cancelled = false
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, bucket: 'day' })
    if (poolId)  p.set('pool_id', poolId)
    if (channel) p.set('channel', channel)
    fetch(`/reports/pools/volume?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: VolumeData }) => { if (!cancelled) setVolume(d.data ?? null) })
      .catch(() => { if (!cancelled) setVolume(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, subTab, fromDt, toDt, poolId, channel])

  useEffect(() => {
    if (!tenantId || subTab !== 'capacity') return
    let cancelled = false
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, bucket: 'day' })
    if (poolId) p.set('pool_id', poolId)
    fetch(`/reports/pools/occupancy?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: OccData }) => { if (!cancelled) setOcc(d.data ?? null) })
      .catch(() => { if (!cancelled) setOcc(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, subTab, fromDt, toDt, poolId])

  useEffect(() => {
    if (!tenantId || (subTab !== 'queue' && subTab !== 'sla')) return
    let cancelled = false
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, bucket: 'day' })
    if (poolId) p.set('pool_id', poolId)
    fetch(`/reports/pools/queue?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: QueueData }) => { if (!cancelled) setQueue(d.data ?? null) })
      .catch(() => { if (!cancelled) setQueue(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, subTab, fromDt, toDt, poolId])

  if (!tenantId) return null

  const subtabs: Array<{ id: SubTab; soon?: boolean }> = [
    { id: 'volume' }, { id: 'queue' }, { id: 'capacity' }, { id: 'sla' },
  ]

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Filtros */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 bg-white border-b border-border">
        <span className="text-xs text-muted">{t('pools.filter.from')}</span>
        <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
          className="text-sm border border-border rounded px-2 py-1" />
        <span className="text-xs text-muted">{t('pools.filter.to')}</span>
        <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
          className="text-sm border border-border rounded px-2 py-1" />
        <input type="text" value={poolId} onChange={e => setPoolId(e.target.value)}
          placeholder={t('pools.filter.allPools')}
          className="text-sm border border-border rounded px-2 py-1 w-44" />
        <input type="text" value={channel} onChange={e => setChannel(e.target.value)}
          placeholder={t('pools.filter.allChannels')}
          className="text-sm border border-border rounded px-2 py-1 w-36" />
        {volume && (
          <span className="ml-auto text-sm text-muted">
            {t('pools.kpi.contacts')}: <strong className="text-dark">{volume.totals.contacts}</strong>
          </span>
        )}
      </div>

      {/* Sub-abas */}
      <div className="flex gap-1 px-4 border-b border-border bg-white">
        {subtabs.map(s => (
          <button key={s.id} onClick={() => !s.soon && setSubTab(s.id)} disabled={s.soon}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              subTab === s.id ? 'border-primary text-primary'
              : s.soon ? 'border-transparent text-muted-light cursor-not-allowed'
              : 'border-transparent text-muted hover:text-dark'}`}>
            {t(`pools.subtabs.${s.id}`)}{s.soon && <span className="ml-1 text-2xs">· {t('pools.soon')}</span>}
          </button>
        ))}
      </div>

      {/* Conteúdo */}
      <div className="flex-1 overflow-auto p-4">
        {subTab === 'volume'   && <VolumeSubTab data={volume} loading={loading} />}
        {subTab === 'queue'    && <FilaSubTab data={queue} loading={loading} />}
        {subTab === 'capacity' && <CapacitySubTab data={occ} loading={loading} />}
        {subTab === 'sla'      && <SlaSubTab data={queue} loading={loading} />}
      </div>
    </div>
  )
}
