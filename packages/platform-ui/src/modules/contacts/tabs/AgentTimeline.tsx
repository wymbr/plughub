/**
 * AgentTimeline.tsx — timeline swimlanes for a single agent (Fase 1b / timeline).
 *
 * Top lane = total logged-in time (login_intervals) with pause blocks overlaid.
 * Below = one lane per pool (pool_intervals) on the SAME time axis, also with the
 * agent-level pause blocks overlaid (a pause removes the agent from every pool).
 *
 * Reads GET /reports/agent-timeline?tenant_id&instance_id&from_dt&to_dt.
 * Rendered as a modal opened by drill-down from the availability table.
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LoginInterval { interval_id: string; logged_in_at: string; logged_out_at: string | null; duration_ms: number | null }
interface PauseInterval { interval_id: string; pool_id: string; reason_id: string; reason_label: string; paused_at: string; resumed_at: string | null; duration_ms: number | null }
interface PoolInterval  { interval_id: string; pool_id: string; entered_at: string; left_at: string | null; duration_ms: number | null }

interface TimelineData {
  instance_id:     string
  user_login:      string
  login_intervals: LoginInterval[]
  pause_intervals: PauseInterval[]
  pool_intervals:  PoolInterval[]
}

interface Props {
  tenantId:   string
  instanceId: string
  label:      string
  fromDt:     string
  toDt:       string
  onClose:    () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const COLOR_LOGGED = '#059669'   // green
const COLOR_POOL   = '#2D9CDB'   // secondary blue
const COLOR_PAUSE  = '#D97706'   // warning orange

function toMs(s: string | null): number | null {
  if (!s) return null
  const m = Date.parse(s)
  return Number.isNaN(m) ? null : m
}

function fmtClock(ms: number): string {
  const d = new Date(ms)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function fmtDuration(ms: number): string {
  if (!ms || ms < 1000) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000)
  const h = Math.floor(m / 60)
  return h > 0 ? `${h}h ${m % 60}m` : `${m}m`
}

function shortPool(id: string): string {
  return id.replace(/_/g, ' ')
}

// ── Bar primitive ─────────────────────────────────────────────────────────────

const Bar: React.FC<{
  startMs: number; endMs: number; t0: number; span: number
  color: string; opacity?: number; title?: string; z?: number
}> = ({ startMs, endMs, t0, span, color, opacity = 1, title, z = 1 }) => {
  const left  = ((startMs - t0) / span) * 100
  const width = Math.max(((endMs - startMs) / span) * 100, 0.4)
  return (
    <div
      className="absolute top-1 bottom-1 rounded-sm"
      style={{ left: `${left}%`, width: `${width}%`, backgroundColor: color, opacity, zIndex: z }}
      title={title}
    />
  )
}

// ── Lane ──────────────────────────────────────────────────────────────────────

const Lane: React.FC<{
  name: string
  bars:   Array<{ start: number; end: number; color: string; title: string }>
  pauses: Array<{ start: number; end: number; title: string }>
  t0: number; span: number
}> = ({ name, bars, pauses, t0, span }) => (
  <div className="flex items-stretch border-b border-border last:border-b-0">
    <div className="w-40 flex-shrink-0 px-3 py-2 text-xs text-dark font-medium truncate flex items-center" title={name}>
      {name}
    </div>
    <div className="relative flex-1 h-9 bg-surface-muted">
      {bars.map((b, i) => (
        <Bar key={`b${i}`} startMs={b.start} endMs={b.end} t0={t0} span={span} color={b.color} title={b.title} z={1} />
      ))}
      {pauses.map((p, i) => (
        <Bar key={`p${i}`} startMs={p.start} endMs={p.end} t0={t0} span={span} color={COLOR_PAUSE} opacity={0.85} title={p.title} z={2} />
      ))}
    </div>
  </div>
)

// ── Timeline modal ────────────────────────────────────────────────────────────

export const AgentTimeline: React.FC<Props> = ({ tenantId, instanceId, label, fromDt, toDt, onClose }) => {
  const { t } = useTranslation('contacts')
  const [data,    setData]    = useState<TimelineData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    const qs = new URLSearchParams({ tenant_id: tenantId, instance_id: instanceId, from_dt: fromDt, to_dt: toDt })
    apiFetch(`/reports/agent-timeline?${qs}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(resp => { if (!cancelled) setData(resp.data ?? null) })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, instanceId, fromDt, toDt])

  // ── Build the time domain + lanes ──
  const now = Date.now()
  const login  = data?.login_intervals ?? []
  const pauses = data?.pause_intervals ?? []
  const pools  = data?.pool_intervals  ?? []

  const starts: number[] = []
  const ends:   number[] = []
  for (const i of login) { const s = toMs(i.logged_in_at); if (s !== null) { starts.push(s); ends.push(toMs(i.logged_out_at) ?? now) } }
  for (const p of pauses) { const s = toMs(p.paused_at);   if (s !== null) { starts.push(s); ends.push(toMs(p.resumed_at) ?? now) } }
  for (const p of pools)  { const s = toMs(p.entered_at);  if (s !== null) { starts.push(s); ends.push(toMs(p.left_at) ?? now) } }

  const hasData = starts.length > 0
  const t0   = hasData ? Math.min(...starts) : now
  const t1   = hasData ? Math.max(...ends)   : now + 1
  const span = Math.max(t1 - t0, 1)

  const pauseBars = pauses
    .map(p => ({ start: toMs(p.paused_at), end: toMs(p.resumed_at) ?? now, label: p.reason_label || p.reason_id || '—' }))
    .filter((p): p is { start: number; end: number; label: string } => p.start !== null)
    .map(p => ({ start: p.start, end: p.end, title: `${t('agents.availability.timeline.pause')}: ${p.label} · ${fmtDuration(p.end - p.start)}` }))

  const totalBars = login
    .map(i => ({ start: toMs(i.logged_in_at), end: toMs(i.logged_out_at) ?? now }))
    .filter((i): i is { start: number; end: number } => i.start !== null)
    .map(i => ({ start: i.start, end: i.end, color: COLOR_LOGGED,
                 title: `${t('agents.availability.timeline.logged')}: ${fmtClock(i.start)}–${i.end === now ? '•' : fmtClock(i.end)} · ${fmtDuration(i.end - i.start)}` }))

  const poolNames = [...new Set(pools.map(p => p.pool_id))].sort()
  const poolLanes = poolNames.map(pid => ({
    pid,
    bars: pools.filter(p => p.pool_id === pid)
      .map(p => ({ start: toMs(p.entered_at), end: toMs(p.left_at) ?? now }))
      .filter((p): p is { start: number; end: number } => p.start !== null)
      .map(p => ({ start: p.start, end: p.end, color: COLOR_POOL,
                   title: `${shortPool(pid)}: ${fmtClock(p.start)}–${p.end === now ? '•' : fmtClock(p.end)} · ${fmtDuration(p.end - p.start)}` })),
  }))

  // ~6 axis ticks
  const ticks = Array.from({ length: 6 }, (_, i) => t0 + (span * i) / 5)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden"
           onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-surface-muted">
          <div>
            <p className="text-sm font-semibold text-dark">{t('agents.availability.timeline.title')}</p>
            <p className="text-xs text-muted">{label}</p>
          </div>
          <button onClick={onClose}
            className="px-2 py-1 text-xs rounded border border-border text-muted hover:bg-white">
            {t('agents.availability.timeline.close')}
          </button>
        </div>

        {/* Legend */}
        <div className="flex items-center gap-4 px-4 py-2 border-b border-border text-xs text-muted">
          <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm" style={{ backgroundColor: COLOR_LOGGED }} />{t('agents.availability.timeline.logged')}</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm" style={{ backgroundColor: COLOR_POOL }} />{t('agents.availability.timeline.pool')}</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm" style={{ backgroundColor: COLOR_PAUSE }} />{t('agents.availability.timeline.pause')}</span>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-4">
          {loading ? (
            <div className="h-40 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('agents.availability.loading')}</div>
          ) : error ? (
            <div className="h-40 flex items-center justify-center text-sm text-red">{t('agents.availability.loadError', { error })}</div>
          ) : !hasData ? (
            <div className="h-40 flex items-center justify-center text-sm text-muted-light">{t('agents.availability.timeline.noData')}</div>
          ) : (
            <div className="border border-border rounded-lg overflow-hidden">
              {/* Axis */}
              <div className="flex items-stretch border-b border-border bg-surface-muted">
                <div className="w-40 flex-shrink-0 px-3 py-1.5 text-2xs font-semibold text-muted uppercase tracking-wide">
                  {t('agents.availability.timeline.lane')}
                </div>
                <div className="relative flex-1 h-6">
                  {ticks.map((tk, i) => (
                    <span key={i} className="absolute top-1 text-2xs text-muted-light -translate-x-1/2"
                          style={{ left: `${(i / 5) * 100}%` }}>
                      {fmtClock(tk)}
                    </span>
                  ))}
                </div>
              </div>

              {/* Total lane */}
              <Lane name={t('agents.availability.timeline.total')} bars={totalBars} pauses={pauseBars} t0={t0} span={span} />

              {/* Pool lanes */}
              {poolLanes.map(pl => (
                <Lane key={pl.pid} name={shortPool(pl.pid)} bars={pl.bars} pauses={pauseBars} t0={t0} span={span} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
