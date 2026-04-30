/**
 * AnaliseTab — métricas agregadas do conjunto filtrado de contatos.
 *
 * Seções:
 *   1. KPIs (total, taxa de resolução, tempo médio, SLA breach)
 *   2. Distribuição por outcome — barras horizontais coloridas
 *   3. Distribuição por canal — barras horizontais
 *   4. Timeseries de volume — TimeseriesChart compact
 *   5. Timeseries de handle time — TimeseriesChart compact
 */
import React, { useCallback, useEffect, useState } from 'react'
import type { ContactFilters, ContactRow, ContactsApiResponse } from '../types'
import { formatMs, OUTCOME_COLORS, CHANNEL_ICONS } from '../types'
import { TimeseriesChart } from '@/components/TimeseriesChart'

const FETCH_LIMIT = 1000

interface Props {
  tenantId: string
  filters:  ContactFilters
}

// ── Aggregated metrics ─────────────────────────────────────────────────────

interface AggMetrics {
  total:          number
  resolved:       number
  avgHandleMs:    number | null
  outcomeMap:     Record<string, number>
  channelMap:     Record<string, number>
}

function aggregate(rows: ContactRow[]): AggMetrics {
  const outcomeMap: Record<string, number> = {}
  const channelMap: Record<string, number> = {}
  let totalHandleMs = 0
  let handledCount  = 0
  let resolved      = 0

  for (const row of rows) {
    const out = row.outcome ?? 'unknown'
    outcomeMap[out] = (outcomeMap[out] ?? 0) + 1
    channelMap[row.channel] = (channelMap[row.channel] ?? 0) + 1
    if (row.handle_time_ms) { totalHandleMs += row.handle_time_ms; handledCount++ }
    if (row.outcome === 'resolved') resolved++
  }

  return {
    total:       rows.length,
    resolved,
    avgHandleMs: handledCount > 0 ? totalHandleMs / handledCount : null,
    outcomeMap,
    channelMap,
  }
}

// ── Horizontal bar chart ───────────────────────────────────────────────────

function HBar({ label, value, total, color, icon }: {
  label: string; value: number; total: number; color: string; icon?: string
}) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-36 text-right text-xs text-gray-600 truncate shrink-0">
        {icon ? `${icon} ` : ''}{label}
      </span>
      <div className="flex-1 h-5 bg-gray-100 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="w-10 text-right text-xs font-semibold tabular-nums text-gray-700">{value}</span>
      <span className="w-9 text-right text-xs text-gray-400 tabular-nums">{pct}%</span>
    </div>
  )
}

// ── KPI card ───────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-5 py-4 flex flex-col gap-1">
      <span className="text-xs text-gray-500">{label}</span>
      <span className="text-2xl font-bold tabular-nums" style={{ color: color ?? '#111827' }}>
        {value}
      </span>
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </div>
  )
}

// ── Outcome colors (extended for unknown/active) ───────────────────────────

const OUTCOME_COLOR_EXT: Record<string, string> = {
  ...OUTCOME_COLORS,
  unknown: '#9ca3af',
  active:  '#2563eb',
}

function outcomeColor(key: string): string {
  return OUTCOME_COLOR_EXT[key] ?? '#9ca3af'
}

const CHANNEL_COLORS: Record<string, string> = {
  webchat:   '#2D9CDB',
  whatsapp:  '#25D366',
  voice:     '#7c3aed',
  email:     '#ea580c',
  sms:       '#0891b2',
  instagram: '#e1306c',
  telegram:  '#229ED9',
  webrtc:    '#475569',
}

function channelColor(ch: string): string {
  return CHANNEL_COLORS[ch] ?? '#6b7280'
}

// ── AnaliseTab ─────────────────────────────────────────────────────────────

export function AnaliseTab({ tenantId, filters }: Props) {
  const [rows,    setRows]    = useState<ContactRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  const fetchRows = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const params = new URLSearchParams({
        tenant_id: tenantId,
        page:      '1',
        page_size: String(FETCH_LIMIT),
      })
      const { fromDt, toDt, sessionIdSearch, channel, outcome, poolId,
              agentId, ani, dnis, insightCategory, insightTags } = filters
      if (fromDt)          params.set('from_dt',          fromDt + 'T00:00:00')
      if (toDt)            params.set('to_dt',            toDt   + 'T23:59:59')
      if (sessionIdSearch) params.set('session_id',       sessionIdSearch)
      if (channel)         params.set('channel',          channel)
      if (outcome)         params.set('outcome',          outcome)
      if (poolId)          params.set('pool_id',          poolId)
      if (agentId)         params.set('agent_id',         agentId)
      if (ani)             params.set('ani',              ani)
      if (dnis)            params.set('dnis',             dnis)
      if (insightCategory) params.set('insight_category', insightCategory)
      if (insightTags)     params.set('insight_tags',     insightTags)

      const res = await fetch(`/reports/sessions?${params}`)
      if (!res.ok) { setError(`Erro HTTP ${res.status}`); return }
      const data: ContactsApiResponse = await res.json()
      const items = Array.isArray(data) ? (data as unknown as ContactRow[]) : (data.data ?? [])
      setRows(items)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, filters])

  useEffect(() => { fetchRows() }, [fetchRows])

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-500 text-sm p-8">
        {error}
      </div>
    )
  }

  const metrics = aggregate(rows)
  const resRate = metrics.total > 0 ? Math.round((metrics.resolved / metrics.total) * 100) : 0

  // Sort outcome entries: by count desc
  const outcomeEntries = Object.entries(metrics.outcomeMap)
    .sort((a, b) => b[1] - a[1])

  const channelEntries = Object.entries(metrics.channelMap)
    .sort((a, b) => b[1] - a[1])

  // TimeseriesChart params derived from filters
  const tsFromDt = filters.fromDt || undefined
  const tsToDt   = filters.toDt   || undefined

  return (
    <div className="flex flex-col h-full overflow-auto bg-gray-50">

      {/* Header bar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-gray-100 flex-shrink-0 text-xs text-gray-400">
        {loading
          ? <><span className="animate-spin">⟳</span> Calculando métricas…</>
          : <><strong className="text-gray-700">{metrics.total.toLocaleString('pt-BR')}</strong>
              &nbsp;contato{metrics.total !== 1 ? 's' : ''} analisados
              {metrics.total >= FETCH_LIMIT && (
                <span className="ml-1 text-amber-500">(mostrando primeiros {FETCH_LIMIT})</span>
              )}
            </>
        }
      </div>

      <div className="flex-1 overflow-auto p-5 space-y-6">

        {/* ── KPIs ─────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <KpiCard label="Total de contatos"   value={metrics.total.toLocaleString('pt-BR')} />
          <KpiCard label="Taxa de resolução"
            value={`${resRate}%`}
            sub={`${metrics.resolved} resolvidos`}
            color={resRate >= 70 ? '#059669' : resRate >= 40 ? '#d97706' : '#dc2626'} />
          <KpiCard label="Tempo médio (HT)"
            value={formatMs(metrics.avgHandleMs)}
            color="#1B4F8A" />
          <KpiCard label="Canais ativos"
            value={String(channelEntries.length)}
            sub={channelEntries.map(([ch]) => CHANNEL_ICONS[ch] ?? ch).join(' ')} />
        </div>

        {/* ── Distribution row ─────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

          {/* Outcome distribution */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-4">Distribuição por Outcome</h3>
            {loading ? (
              <div className="space-y-2">
                {[1,2,3].map(i => (
                  <div key={i} className="h-5 bg-gray-100 rounded animate-pulse" style={{ width: `${70 - i * 15}%` }} />
                ))}
              </div>
            ) : outcomeEntries.length === 0 ? (
              <div className="text-center text-gray-400 text-sm py-6">Sem dados</div>
            ) : (
              <div className="space-y-2.5">
                {outcomeEntries.map(([key, count]) => (
                  <HBar key={key}
                    label={key}
                    value={count}
                    total={metrics.total}
                    color={outcomeColor(key)} />
                ))}
              </div>
            )}
          </div>

          {/* Channel distribution */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-4">Distribuição por Canal</h3>
            {loading ? (
              <div className="space-y-2">
                {[1,2,3].map(i => (
                  <div key={i} className="h-5 bg-gray-100 rounded animate-pulse" style={{ width: `${70 - i * 15}%` }} />
                ))}
              </div>
            ) : channelEntries.length === 0 ? (
              <div className="text-center text-gray-400 text-sm py-6">Sem dados</div>
            ) : (
              <div className="space-y-2.5">
                {channelEntries.map(([ch, count]) => (
                  <HBar key={ch}
                    label={ch}
                    value={count}
                    total={metrics.total}
                    color={channelColor(ch)}
                    icon={CHANNEL_ICONS[ch]} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── Timeseries charts ─────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <TimeseriesChart
              baseUrl="/reports/timeseries/volume"
              tenantId={tenantId}
              title="Volume de Contatos"
              valueLabel="Contatos"
              formatType="count"
              displayType="bar"
              compact
              height={180}
              defaultFromDt={tsFromDt}
              defaultToDt={tsToDt}
              defaultInterval={60}
              poolId={filters.poolId || undefined}
            />
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <TimeseriesChart
              baseUrl="/reports/timeseries/handle_time"
              tenantId={tenantId}
              title="Tempo Médio de Atendimento"
              valueLabel="Tempo"
              formatType="duration_ms"
              displayType="line"
              compact
              height={180}
              defaultFromDt={tsFromDt}
              defaultToDt={tsToDt}
              defaultInterval={60}
              poolId={filters.poolId || undefined}
            />
          </div>
        </div>

        {/* ── Pool breakdown table (if no pool filter) ──────────────────── */}
        {!filters.poolId && metrics.total > 0 && (() => {
          const poolMap: Record<string, number> = {}
          for (const row of rows) {
            if (row.pool_id) poolMap[row.pool_id] = (poolMap[row.pool_id] ?? 0) + 1
          }
          const poolEntries = Object.entries(poolMap).sort((a, b) => b[1] - a[1])
          if (poolEntries.length < 2) return null
          return (
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Volume por Pool</h3>
              <div className="space-y-2.5">
                {poolEntries.map(([pid, count]) => (
                  <HBar key={pid}
                    label={pid.replace(/_/g, ' ')}
                    value={count}
                    total={metrics.total}
                    color="#1B4F8A" />
                ))}
              </div>
            </div>
          )
        })()}

      </div>
    </div>
  )
}
