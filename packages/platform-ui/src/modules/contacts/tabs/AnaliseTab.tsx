/**
 * AnaliseTab — métricas agregadas do conjunto filtrado de contatos.
 *
 * Seções:
 *   1. KPIs (total de contatos, taxa de resolução, tempo médio, canais ativos)
 *   2. Distribuição por outcome — de ATENDIMENTOS (segments), não de contatos
 *   3. Distribuição por canal — de CONTATOS (sessions)
 *   4. Timeseries de volume — TimeseriesChart compact
 *   5. Timeseries de handle time — TimeseriesChart compact
 *
 * Duas chamadas paralelas:
 *   /reports/sessions  → total de contatos, distribuição por canal, handle_time
 *   /reports/segments  → distribuição de outcome por atendimento (outcome real)
 *
 * Rationale: "contato" ≠ "atendimento". O outcome pertence ao atendimento
 * (segment), não ao contato (session). Sessions.outcome é sempre NULL porque
 * nenhum evento escreve lá — o outcome é registrado em cada segment via
 * participant_left.
 */
import React, { useCallback, useEffect, useState } from 'react'
import type { ContactFilters, ContactRow, ContactsApiResponse } from '../types'
import { formatMs, OUTCOME_COLORS, CHANNEL_ICONS } from '../types'
import { TimeseriesChart } from '@/components/TimeseriesChart'
import { apiFetch } from '@/api/apiFetch'

const FETCH_LIMIT = 1000

interface Props {
  tenantId: string
  filters:  ContactFilters
}

// ── Segment row (subset of fields we need) ────────────────────────────────────

interface SegRow {
  segment_id:  string
  session_id:  string
  pool_id:     string
  role:        string
  agent_type:  string
  outcome:     string | null
  duration_ms: number | null
}

interface SegApiResponse {
  data: SegRow[]
  meta?: { total?: number }
  error?: string
}

// ── Aggregated metrics — sessions (contacts) ──────────────────────────────────

interface SessionMetrics {
  total:       number
  avgHandleMs: number | null
  channelMap:  Record<string, number>
}

function aggregateSessions(rows: ContactRow[]): SessionMetrics {
  const channelMap: Record<string, number> = {}
  let totalHandleMs = 0
  let handledCount  = 0
  // Exclude hook/internal sessions (wrapup, NPS, etc.) — they have channel='' or null.
  // Hook sessions are synthetic conferences created by orchestrator-bridge; they are not
  // real customer contacts and must not inflate the total or skew channel distribution.
  // Active real sessions also pass through with their recovered channel (COALESCE in backend).
  const realRows = rows.filter(r => r.channel && r.channel !== '')

  for (const row of realRows) {
    channelMap[row.channel] = (channelMap[row.channel] ?? 0) + 1
    if (row.handle_time_ms) { totalHandleMs += row.handle_time_ms; handledCount++ }
  }

  return {
    total:       realRows.length,
    avgHandleMs: handledCount > 0 ? totalHandleMs / handledCount : null,
    channelMap,
  }
}

// ── Aggregated metrics — segments (attendances / atendimentos) ────────────────

interface SegmentMetrics {
  outcomeMap:        Record<string, number>
  resolved:          number
  totalWithOutcome:  number   // segments where outcome is known (not null/empty/active)
}

const ACTIVE_OUTCOMES = new Set(['active', ''])

function aggregateSegments(segs: SegRow[]): SegmentMetrics {
  const outcomeMap: Record<string, number> = {}
  let resolved = 0
  let totalWithOutcome = 0

  for (const seg of segs) {
    const out = seg.outcome
    if (!out || ACTIVE_OUTCOMES.has(out)) continue   // skip active/empty
    outcomeMap[out] = (outcomeMap[out] ?? 0) + 1
    totalWithOutcome++
    if (out === 'resolved') resolved++
  }

  return { outcomeMap, resolved, totalWithOutcome }
}

// ── Horizontal bar chart ───────────────────────────────────────────────────────

function HBar({ label, value, total, color, icon }: {
  label: string; value: number; total: number; color: string; icon?: string
}) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-36 text-right text-xs text-muted truncate shrink-0">
        {icon ? `${icon} ` : ''}{label}
      </span>
      <div className="flex-1 h-5 bg-surface-alt rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="w-10 text-right text-xs font-semibold tabular-nums text-dark">{value}</span>
      <span className="w-9 text-right text-xs text-muted-light tabular-nums">{pct}%</span>
    </div>
  )
}

// ── KPI card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="bg-white rounded-xl border border-border px-5 py-4 flex flex-col gap-1">
      <span className="text-xs text-muted">{label}</span>
      <span className="text-2xl font-bold tabular-nums" style={{ color: color ?? '#111827' }}>
        {value}
      </span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── Outcome colors (extended for unknown/active) ──────────────────────────────

const OUTCOME_COLOR_EXT: Record<string, string> = {
  ...OUTCOME_COLORS,
  unknown:     '#9ca3af',
  active:      '#2563eb',
  escalated_human: '#d97706',
  humano:      '#1B4F8A',
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
  internal:  '#9ca3af',
}

function channelColor(ch: string): string {
  return CHANNEL_COLORS[ch] ?? '#6b7280'
}

// ── AnaliseTab ────────────────────────────────────────────────────────────────

export function AnaliseTab({ tenantId, filters }: Props) {
  const [rows,        setRows]        = useState<ContactRow[]>([])
  const [segRows,     setSegRows]     = useState<SegRow[]>([])
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState('')
  const [segError,    setSegError]    = useState('')
  const [degraded,    setDegraded]    = useState(false)

  const fetchAll = useCallback(async () => {
    setLoading(true); setError(''); setSegError(''); setDegraded(false)

    // ── Build session params ─────────────────────────────────────────────────
    const sParams = new URLSearchParams({
      tenant_id: tenantId,
      page:      '1',
      page_size: String(FETCH_LIMIT),
    })
    const { fromDt, toDt, sessionIdSearch, channel, outcome, poolId,
            agentId, ani, dnis, insightCategory, insightTags } = filters
    if (fromDt)          sParams.set('from_dt',          fromDt + 'T00:00:00')
    if (toDt)            sParams.set('to_dt',            toDt   + 'T23:59:59')
    if (sessionIdSearch) sParams.set('session_id',       sessionIdSearch)
    if (channel)         sParams.set('channel',          channel)
    if (outcome)         sParams.set('outcome',          outcome)
    if (poolId)          sParams.set('pool_id',          poolId)
    if (agentId)         sParams.set('agent_id',         agentId)
    if (ani)             sParams.set('ani',              ani)
    if (dnis)            sParams.set('dnis',             dnis)
    if (insightCategory) sParams.set('insight_category', insightCategory)
    if (insightTags)     sParams.set('insight_tags',     insightTags)

    // ── Build segment params (date + pool only — outcome is what we measure) ─
    const gParams = new URLSearchParams({
      tenant_id: tenantId,
      page:      '1',
      page_size: String(FETCH_LIMIT),
    })
    if (fromDt) gParams.set('from_dt', fromDt + 'T00:00:00')
    if (toDt)   gParams.set('to_dt',   toDt   + 'T23:59:59')
    if (poolId) gParams.set('pool_id', poolId)

    // ── Parallel fetch ───────────────────────────────────────────────────────
    const [sessRes, segRes] = await Promise.allSettled([
      apiFetch(`/reports/sessions?${sParams}`),
      apiFetch(`/reports/segments?${gParams}`),
    ])

    // Sessions
    if (sessRes.status === 'fulfilled') {
      const res = sessRes.value
      try {
        const data: ContactsApiResponse = await res.json()
        if (!res.ok || (data as any).error) {
          setDegraded(true)
          setError(`HTTP ${res.status}`)
          setRows(prev => prev.length > 0 ? prev : [])
        } else {
          const items = Array.isArray(data) ? (data as unknown as ContactRow[]) : (data.data ?? [])
          setRows(items)
        }
      } catch {
        setDegraded(true)
        setError('parse error')
        setRows(prev => prev.length > 0 ? prev : [])
      }
    } else {
      setDegraded(true)
      setError(String(sessRes.reason))
      setRows(prev => prev.length > 0 ? prev : [])
    }

    // Segments
    if (segRes.status === 'fulfilled') {
      const res = segRes.value
      try {
        const data: SegApiResponse = await res.json()
        if (!res.ok || data.error) {
          setSegError(`HTTP ${res.status}`)
          setSegRows(prev => prev.length > 0 ? prev : [])
        } else {
          setSegRows(Array.isArray(data) ? (data as unknown as SegRow[]) : (data.data ?? []))
        }
      } catch {
        setSegError('parse error')
        setSegRows(prev => prev.length > 0 ? prev : [])
      }
    } else {
      setSegError(String(segRes.reason))
      setSegRows(prev => prev.length > 0 ? prev : [])
    }

    setLoading(false)
  }, [tenantId, filters])

  useEffect(() => { fetchAll() }, [fetchAll])

  const sMet = aggregateSessions(rows)
  const gMet = aggregateSegments(segRows)

  const resRate = gMet.totalWithOutcome > 0
    ? Math.round((gMet.resolved / gMet.totalWithOutcome) * 100)
    : 0

  const outcomeEntries = Object.entries(gMet.outcomeMap).sort((a, b) => b[1] - a[1])
  const channelEntries = Object.entries(sMet.channelMap).sort((a, b) => b[1] - a[1])

  // TimeseriesChart params derived from filters
  const tsFromDt = filters.fromDt || undefined
  const tsToDt   = filters.toDt   || undefined

  return (
    <div className="flex flex-col h-full overflow-auto bg-surface-muted">

      {/* Header bar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-border flex-shrink-0 text-xs text-muted-light">
        {loading
          ? <><span className="animate-spin">⟳</span> Calculando métricas…</>
          : <><strong className="text-dark">{sMet.total.toLocaleString('pt-BR')}</strong>
              &nbsp;contato{sMet.total !== 1 ? 's' : ''} analisados
              {sMet.total >= FETCH_LIMIT && (
                <span className="ml-1 text-warning">(mostrando primeiros {FETCH_LIMIT})</span>
              )}
              {segRows.length > 0 && (
                <span className="ml-2 text-border-strong">·</span>
              )}
              {segRows.length > 0 && (
                <span className="ml-1">
                  <strong className="text-dark">{segRows.length.toLocaleString('pt-BR')}</strong>
                  &nbsp;atendimento{segRows.length !== 1 ? 's' : ''}
                </span>
              )}
            </>
        }
      </div>

      {/* Degraded banner — shown when sessions query fails but tab still renders */}
      {degraded && !loading && (
        <div className="flex items-center gap-3 px-4 py-2 bg-warning-light border-b border-warning/30 flex-shrink-0 text-xs text-warning-text">
          <span>⚠️</span>
          <span>
            Dados de contatos temporariamente indisponíveis ({error}).
            Os gráficos de série histórica ainda são exibidos.
          </span>
          <button
            onClick={fetchAll}
            className="ml-auto px-2 py-1 rounded border border-warning/40 hover:bg-warning/10 transition-colors font-medium"
          >
            ⟳ Tentar novamente
          </button>
        </div>
      )}
      {segError && !loading && (
        <div className="flex items-center gap-3 px-4 py-2 bg-warning-light border-b border-warning/30 flex-shrink-0 text-xs text-warning-text">
          <span>⚠️</span>
          <span>Dados de atendimentos temporariamente indisponíveis ({segError}). Outcome não disponível.</span>
        </div>
      )}

      <div className="flex-1 overflow-auto p-5 space-y-6">

        {/* ── KPIs ────────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <KpiCard label="Total de contatos"   value={sMet.total.toLocaleString('pt-BR')} />
          <KpiCard label="Taxa de resolução"
            value={gMet.totalWithOutcome > 0 ? `${resRate}%` : '—'}
            sub={gMet.totalWithOutcome > 0
              ? `${gMet.resolved} de ${gMet.totalWithOutcome} atend.`
              : segError ? 'indisponível' : 'sem atendimentos'}
            color={gMet.totalWithOutcome > 0
              ? (resRate >= 70 ? '#059669' : resRate >= 40 ? '#d97706' : '#dc2626')
              : undefined} />
          <KpiCard label="Tempo médio (HT)"
            value={formatMs(sMet.avgHandleMs)}
            color="#1B4F8A" />
          <KpiCard label="Canais ativos"
            value={String(channelEntries.length)}
            sub={channelEntries.map(([ch]) => CHANNEL_ICONS[ch] ?? ch).join(' ')} />
        </div>

        {/* ── Distribution row ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

          {/* Outcome distribution — from SEGMENTS (atendimentos) */}
          <div className="bg-white rounded-xl border border-border p-5">
            <h3 className="text-sm font-semibold text-dark mb-1">Distribuição por Outcome</h3>
            <p className="text-xs text-muted-light mb-4">por atendimento (segment)</p>
            {loading ? (
              <div className="space-y-2">
                {[1,2,3].map(i => (
                  <div key={i} className="h-5 bg-surface-alt rounded animate-pulse" style={{ width: `${70 - i * 15}%` }} />
                ))}
              </div>
            ) : outcomeEntries.length === 0 ? (
              <div className="text-center text-muted-light text-sm py-6">
                {segError ? 'Dados indisponíveis' : 'Sem atendimentos concluídos'}
              </div>
            ) : (
              <div className="space-y-2.5">
                {outcomeEntries.map(([key, count]) => (
                  <HBar key={key}
                    label={key}
                    value={count}
                    total={gMet.totalWithOutcome}
                    color={outcomeColor(key)} />
                ))}
              </div>
            )}
          </div>

          {/* Channel distribution — from SESSIONS (contatos) */}
          <div className="bg-white rounded-xl border border-border p-5">
            <h3 className="text-sm font-semibold text-dark mb-1">Distribuição por Canal</h3>
            <p className="text-xs text-muted-light mb-4">por contato (session)</p>
            {loading ? (
              <div className="space-y-2">
                {[1,2,3].map(i => (
                  <div key={i} className="h-5 bg-surface-alt rounded animate-pulse" style={{ width: `${70 - i * 15}%` }} />
                ))}
              </div>
            ) : channelEntries.length === 0 ? (
              <div className="text-center text-muted-light text-sm py-6">Sem dados</div>
            ) : (
              <div className="space-y-2.5">
                {channelEntries.map(([ch, count]) => (
                  <HBar key={ch}
                    label={ch}
                    value={count}
                    total={sMet.total}
                    color={channelColor(ch)}
                    icon={CHANNEL_ICONS[ch]} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── Timeseries charts ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

          <div className="bg-white rounded-xl border border-border p-5">
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

          <div className="bg-white rounded-xl border border-border p-5">
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

        {/* ── Pool breakdown table (if no pool filter) ────────────────────── */}
        {!filters.poolId && sMet.total > 0 && (() => {
          const poolMap: Record<string, number> = {}
          for (const row of rows) {
            if (row.pool_id) poolMap[row.pool_id] = (poolMap[row.pool_id] ?? 0) + 1
          }
          const poolEntries = Object.entries(poolMap).sort((a, b) => b[1] - a[1])
          if (poolEntries.length < 2) return null
          return (
            <div className="bg-white rounded-xl border border-border p-5">
              <h3 className="text-sm font-semibold text-dark mb-4">Volume por Pool</h3>
              <div className="space-y-2.5">
                {poolEntries.map(([pid, count]) => (
                  <HBar key={pid}
                    label={pid.replace(/_/g, ' ')}
                    value={count}
                    total={sMet.total}
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
