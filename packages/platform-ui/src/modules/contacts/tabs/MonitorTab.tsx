/**
 * MonitorTab — visão em tempo real de todos os pools.
 *
 * - SSE via usePoolViews: todos os pools sempre visíveis
 * - Pools que batem com filters.poolId ficam em destaque; demais ficam em segundo plano
 * - Seletor de formato de visualização: heatmap | bars | donut | tiles | table
 * - Clique em pool → painel lateral com sessões ativas → transcript inline
 */
import React, { useState, useMemo } from 'react'
import type { ContactFilters, VizFormat } from '../types'
import { usePoolViews } from '@/modules/atendimento/api/hooks'
import { SessionList }      from '@/modules/atendimento/components/SessionList'
import { SessionTranscript } from '@/modules/atendimento/components/SessionTranscript'
import type { PoolView } from '@/modules/atendimento/types'
import { scoreToColor, scoreToAccent, formatMs } from '@/modules/atendimento/utils/sentiment'

interface Props {
  tenantId: string
  filters:  ContactFilters
}

// ── Viz format selector ────────────────────────────────────────────────────

const VIZ_OPTIONS: { id: VizFormat; label: string; icon: string }[] = [
  { id: 'heatmap', label: 'Heatmap',  icon: '🔥' },
  { id: 'bars',    label: 'Barras',   icon: '📊' },
  { id: 'donut',   label: 'Disco',    icon: '🍩' },
  { id: 'tiles',   label: 'Tiles %',  icon: '⬛' },
  { id: 'table',   label: 'Tabela',   icon: '📋' },
]

type DrillLevel = 'pools' | 'sessions' | 'transcript'

// ── Connection pill ────────────────────────────────────────────────────────

function ConnectionPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    connecting: { bg: '#fef3c7', text: '#92400e', label: 'conectando' },
    connected:  { bg: '#d1fae5', text: '#065f46', label: 'ao vivo' },
    error:      { bg: '#fee2e2', text: '#991b1b', label: 'erro SSE' },
    closed:     { bg: '#f1f5f9', text: '#475569', label: 'fechado'   },
  }
  const c = map[status] ?? map.closed
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full"
      style={{ backgroundColor: c.bg, color: c.text }}>
      {status === 'connected' && <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse inline-block" />}
      {c.label}
    </span>
  )
}

// ── Pool highlight logic ───────────────────────────────────────────────────

function isHighlighted(pool: PoolView, filters: ContactFilters): boolean {
  if (!filters.poolId && !filters.channel) return true               // no filter → all highlighted
  if (filters.poolId && pool.pool_id !== filters.poolId) return false
  if (filters.channel && !pool.channel_types?.includes(filters.channel)) return false
  return true
}

// ── Individual pool visualizations ────────────────────────────────────────

function PoolHeatmapCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const bg     = scoreToColor(pool.avg_score)
  const accent = scoreToAccent(pool.avg_score)
  const hl     = highlighted ? 1 : 0.3

  return (
    <div onClick={onClick}
      className="rounded-xl p-4 cursor-pointer transition-all select-none"
      style={{
        background: bg,
        border: selected ? '2px solid #60a5fa' : '2px solid transparent',
        boxShadow: selected ? '0 0 0 3px rgba(96,165,250,0.4)' : '0 2px 8px rgba(0,0,0,0.2)',
        opacity: hl,
        minWidth: 160,
        minHeight: 120,
      }}>
      <div className="font-semibold text-sm truncate mb-2" style={{ color: accent }}>
        {pool.pool_id.replace(/_/g, ' ')}
      </div>
      <div className="flex gap-3 text-xs mt-auto" style={{ color: accent + 'cc' }}>
        <span>✅ {pool.available}</span>
        <span>⏳ {pool.queue_length}</span>
      </div>
      {pool.avg_score !== null && (
        <div className="text-xs mt-1 opacity-70" style={{ color: accent }}>
          sentimento {pool.avg_score > 0 ? '+' : ''}{pool.avg_score.toFixed(2)}
        </div>
      )}
    </div>
  )
}

function PoolBarsCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const total  = pool.available + pool.queue_length
  const avPct  = total > 0 ? (pool.available / total) * 100 : 0
  const qPct   = total > 0 ? (pool.queue_length / total) * 100 : 0
  const accent = scoreToAccent(pool.avg_score)

  return (
    <div onClick={onClick}
      className="bg-white rounded-xl border-2 p-4 cursor-pointer transition-all select-none hover:shadow-md"
      style={{ borderColor: selected ? '#3b82f6' : '#e5e7eb', opacity: highlighted ? 1 : 0.35 }}>
      <div className="font-semibold text-sm text-gray-700 truncate mb-3">
        {pool.pool_id.replace(/_/g, ' ')}
      </div>

      {/* Available bar */}
      <div className="mb-2">
        <div className="flex justify-between text-xs text-gray-500 mb-0.5">
          <span>Disponíveis</span><span>{pool.available}</span>
        </div>
        <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${avPct}%`, backgroundColor: '#059669' }} />
        </div>
      </div>

      {/* Queue bar */}
      <div className="mb-2">
        <div className="flex justify-between text-xs text-gray-500 mb-0.5">
          <span>Na fila</span><span>{pool.queue_length}</span>
        </div>
        <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${qPct}%`, backgroundColor: '#f59e0b' }} />
        </div>
      </div>

      {pool.avg_score !== null && (
        <div className="text-xs text-gray-400 mt-1">
          sentimento <span style={{ color: accent }}>{pool.avg_score > 0 ? '+' : ''}{pool.avg_score.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

function PoolDonutCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const total   = pool.available + pool.queue_length
  const avPct   = total > 0 ? pool.available / total : 0
  const r       = 28
  const circ    = 2 * Math.PI * r
  const avDash  = avPct * circ
  const accent  = scoreToAccent(pool.avg_score)

  return (
    <div onClick={onClick}
      className="bg-white rounded-xl border-2 p-4 cursor-pointer transition-all select-none hover:shadow-md flex flex-col items-center"
      style={{ borderColor: selected ? '#3b82f6' : '#e5e7eb', opacity: highlighted ? 1 : 0.35, minWidth: 140 }}>
      <div className="font-semibold text-xs text-gray-600 truncate mb-3 w-full text-center">
        {pool.pool_id.replace(/_/g, ' ')}
      </div>

      {/* SVG donut */}
      <svg width="72" height="72" viewBox="0 0 72 72">
        <circle cx="36" cy="36" r={r} fill="none" stroke="#f3f4f6" strokeWidth="10" />
        <circle cx="36" cy="36" r={r} fill="none"
          stroke="#059669" strokeWidth="10"
          strokeDasharray={`${avDash} ${circ}`}
          strokeDashoffset={circ * 0.25}
          strokeLinecap="round" />
        <text x="36" y="36" textAnchor="middle" dominantBaseline="central"
          fontSize="13" fontWeight="700" fill="#111827">
          {Math.round(avPct * 100)}%
        </text>
      </svg>

      <div className="flex gap-2 mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-0.5">
          <span className="w-2 h-2 rounded-full bg-green-500 inline-block" /> {pool.available}
        </span>
        <span className="flex items-center gap-0.5">
          <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> {pool.queue_length}
        </span>
      </div>
    </div>
  )
}

function PoolTileCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const total  = pool.available + pool.queue_length
  const avPct  = total > 0 ? Math.round((pool.available / total) * 100) : 0
  const color  = avPct >= 50 ? '#059669' : avPct >= 25 ? '#d97706' : '#dc2626'

  return (
    <div onClick={onClick}
      className="rounded-xl border-2 p-5 cursor-pointer transition-all select-none hover:shadow-md flex flex-col items-center justify-center gap-1"
      style={{ borderColor: selected ? '#3b82f6' : '#e5e7eb', opacity: highlighted ? 1 : 0.35,
        backgroundColor: color + '10', minWidth: 130, minHeight: 120 }}>
      <div className="text-4xl font-black tabular-nums" style={{ color }}>
        {avPct}%
      </div>
      <div className="text-xs text-gray-500 text-center truncate w-full">
        {pool.pool_id.replace(/_/g, ' ')}
      </div>
      <div className="text-xs text-gray-400">
        {pool.available} disp · {pool.queue_length} fila
      </div>
    </div>
  )
}

function PoolTableRow({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const accent = scoreToAccent(pool.avg_score)
  const bg     = scoreToColor(pool.avg_score)

  return (
    <tr onClick={onClick}
      className="cursor-pointer transition-colors hover:bg-primary/5"
      style={{ opacity: highlighted ? 1 : 0.35, outline: selected ? '2px solid #3b82f6' : 'none' }}>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-sm inline-block flex-shrink-0" style={{ background: bg }} />
          <span className="text-sm font-medium text-gray-700">{pool.pool_id.replace(/_/g, ' ')}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-center">
        <span className="font-semibold text-green-700 tabular-nums">{pool.available}</span>
      </td>
      <td className="px-4 py-3 text-center">
        <span className="font-semibold text-amber-600 tabular-nums">{pool.queue_length}</span>
      </td>
      <td className="px-4 py-3 text-center text-xs text-gray-500">
        {pool.sla_target_ms ? formatMs(pool.sla_target_ms) : '—'}
      </td>
      <td className="px-4 py-3 text-center">
        {pool.avg_score !== null
          ? <span className="text-xs font-semibold" style={{ color: accent }}>
              {pool.avg_score > 0 ? '+' : ''}{pool.avg_score.toFixed(2)}
            </span>
          : <span className="text-gray-300 text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-xs text-gray-400 max-w-[140px] truncate">
        {pool.channel_types?.join(', ') ?? '—'}
      </td>
      <td className="px-4 py-3 text-gray-400 text-right">›</td>
    </tr>
  )
}

// ── Main MonitorTab ────────────────────────────────────────────────────────

export function MonitorTab({ tenantId, filters }: Props) {
  const { pools, status } = usePoolViews(tenantId)
  const [vizFormat, setVizFormat] = useState<VizFormat>('bars')
  const [drillLevel, setDrillLevel] = useState<DrillLevel>('pools')
  const [selectedPool, setSelectedPool] = useState<string | null>(null)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)

  // Sort: highlighted first, then by pool_id alphabetically
  const sortedPools = useMemo(() => {
    return [...pools].sort((a, b) => {
      const ha = isHighlighted(a, filters)
      const hb = isHighlighted(b, filters)
      if (ha && !hb) return -1
      if (!ha && hb) return 1
      return a.pool_id.localeCompare(b.pool_id)
    })
  }, [pools, filters])

  const highlightedCount = useMemo(() => sortedPools.filter(p => isHighlighted(p, filters)).length, [sortedPools, filters])

  function handlePoolClick(poolId: string) {
    setSelectedPool(poolId)
    setSelectedSession(null)
    setDrillLevel('sessions')
  }

  function handleSessionSelect(sid: string) {
    setSelectedSession(sid)
    setDrillLevel('transcript')
  }

  function goBackToPools() {
    setDrillLevel('pools')
    setSelectedPool(null)
    setSelectedSession(null)
  }

  function goBackToSessions() {
    setDrillLevel('sessions')
    setSelectedSession(null)
  }

  const isDark = vizFormat === 'heatmap'

  return (
    <div className="flex h-full overflow-hidden" style={{ backgroundColor: isDark ? '#0f172a' : '#f8fafc' }}>

      {/* ── Left: pool grid ─────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* Toolbar */}
        <div className="flex items-center gap-3 px-4 py-2.5 flex-shrink-0 border-b"
          style={{ backgroundColor: isDark ? '#0a1628' : '#ffffff', borderColor: isDark ? '#1e293b' : '#e5e7eb' }}>

          {/* Viz format selector */}
          <div className="flex items-center gap-0.5 bg-gray-100 rounded-lg p-0.5">
            {VIZ_OPTIONS.map(opt => (
              <button key={opt.id} onClick={() => setVizFormat(opt.id)} title={opt.label}
                className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all"
                style={{
                  backgroundColor: vizFormat === opt.id ? '#fff' : 'transparent',
                  color:           vizFormat === opt.id ? '#1B4F8A' : '#6b7280',
                  boxShadow:       vizFormat === opt.id ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
                }}>
                <span>{opt.icon}</span>
                <span className="hidden sm:inline">{opt.label}</span>
              </button>
            ))}
          </div>

          <ConnectionPill status={status} />

          {/* Filter context label */}
          {(filters.poolId || filters.channel) && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
              {highlightedCount} de {sortedPools.length} em destaque
            </span>
          )}

          {/* Breadcrumb for drill-down */}
          {drillLevel !== 'pools' && (
            <div className="flex items-center gap-1 text-xs ml-auto" style={{ color: isDark ? '#94a3b8' : '#6b7280' }}>
              <button onClick={goBackToPools} className="hover:underline">Pools</button>
              <><span className="mx-1">/</span>
                  {drillLevel === 'sessions'
                    ? <span className="font-semibold" style={{ color: isDark ? '#e2e8f0' : '#111827' }}>
                        {selectedPool?.replace(/_/g, ' ')}
                      </span>
                    : <button onClick={goBackToSessions} className="hover:underline">
                        {selectedPool?.replace(/_/g, ' ')}
                      </button>
                  }
                </>
              {drillLevel === 'transcript' && selectedSession && (
                <><span className="mx-1">/</span>
                  <code className="font-mono text-[11px]">…{selectedSession.slice(-10)}</code>
                </>
              )}
            </div>
          )}
        </div>

        {/* Pool grid or drill-down */}
        <div className="flex-1 overflow-auto p-4">

          {drillLevel === 'pools' && (
            <>
              {sortedPools.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-3"
                  style={{ color: isDark ? '#64748b' : '#9ca3af' }}>
                  <span className="text-4xl">📡</span>
                  <span className="text-sm">Aguardando dados dos pools…</span>
                  <span className="text-xs opacity-60">Snapshots chegam a cada 5s. Verifique o Routing Engine.</span>
                </div>
              ) : vizFormat === 'table' ? (
                <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        {['Pool','Disponíveis','Na fila','SLA alvo','Sentimento','Canais',''].map(col => (
                          <th key={col} className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {sortedPools.map(pool => (
                        <PoolTableRow key={pool.pool_id} pool={pool}
                          highlighted={isHighlighted(pool, filters)}
                          selected={selectedPool === pool.pool_id}
                          onClick={() => handlePoolClick(pool.pool_id)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="flex flex-wrap gap-4">
                  {sortedPools.map(pool => {
                    const hl  = isHighlighted(pool, filters)
                    const sel = selectedPool === pool.pool_id
                    const commonProps = { pool, highlighted: hl, selected: sel, onClick: () => handlePoolClick(pool.pool_id) }
                    return (
                      <React.Fragment key={pool.pool_id}>
                        {vizFormat === 'heatmap' && <PoolHeatmapCard {...commonProps} />}
                        {vizFormat === 'bars'    && <PoolBarsCard    {...commonProps} />}
                        {vizFormat === 'donut'   && <PoolDonutCard   {...commonProps} />}
                        {vizFormat === 'tiles'   && <PoolTileCard    {...commonProps} />}
                      </React.Fragment>
                    )
                  })}
                </div>
              )}
            </>
          )}

          {drillLevel === 'sessions' && selectedPool && (
            <div style={{ height: '100%' }}>
              <SessionList
                tenantId={tenantId}
                poolId={selectedPool}
                onSelect={handleSessionSelect}
                onBack={goBackToPools}
              />
            </div>
          )}

          {drillLevel === 'transcript' && selectedSession && (
            <div style={{ height: '100%', backgroundColor: '#0f172a', borderRadius: 12, overflow: 'hidden' }}>
              <SessionTranscript
                tenantId={tenantId}
                sessionId={selectedSession}
                onBack={goBackToSessions}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
