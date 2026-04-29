/**
 * TileDisplay.tsx
 *
 * KPI tile: large metric value + trend badge + mini sparkline.
 *
 * Trend is computed by comparing the first half vs the second half
 * of the current period's buckets — a lightweight proxy that needs
 * no extra API call.
 *
 * Compact mode: number + trend on top, sparkline fills remaining space.
 * Full mode:    centred large number, trend sentence, wider sparkline.
 */
import React, { useMemo } from 'react'
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import type { TimeseriesBucket } from '@/types'
import { getFormatter, type FormatType } from '../formatters'

export interface TileDisplayProps {
  buckets:    TimeseriesBucket[]
  total:      number
  formatType: FormatType
  valueLabel: string
  title:      string
  height:     number
  compact:    boolean
}

type TrendDir = 'up' | 'down' | 'flat'

export function TileDisplay({
  buckets,
  total,
  formatType,
  valueLabel,
  title,
  height,
  compact,
}: TileDisplayProps) {
  const fmt = getFormatter(formatType)

  // Trend: first-half sum vs second-half sum
  const { trendPct, trendDir } = useMemo<{ trendPct: number | null; trendDir: TrendDir }>(() => {
    if (buckets.length < 4) return { trendPct: null, trendDir: 'flat' }
    const half       = Math.floor(buckets.length / 2)
    const firstHalf  = buckets.slice(0, half).reduce((s, b) => s + b.value, 0)
    const secondHalf = buckets.slice(half).reduce((s, b) => s + b.value, 0)
    if (firstHalf === 0) return { trendPct: null, trendDir: 'flat' }
    const pct = ((secondHalf - firstHalf) / firstHalf) * 100
    return {
      trendPct: pct,
      trendDir: pct > 1 ? 'up' : pct < -1 ? 'down' : 'flat',
    }
  }, [buckets])

  const sparkData = buckets.map(b => ({ v: b.value }))

  const trendColor =
    trendDir === 'up'   ? 'text-green-600' :
    trendDir === 'down' ? 'text-red-500'   :
    'text-gray-400'

  const trendBg =
    trendDir === 'up'   ? 'bg-green-50 border-green-200' :
    trendDir === 'down' ? 'bg-red-50 border-red-200'     :
    'bg-gray-50 border-gray-200'

  const trendIcon  = trendDir === 'up' ? '↑' : trendDir === 'down' ? '↓' : '→'
  const trendLabel =
    trendDir === 'up'   ? 'crescimento' :
    trendDir === 'down' ? 'queda'       :
    'estável'

  // ── Compact ─────────────────────────────────────────────────────────────────
  if (compact) {
    return (
      <div className="h-full flex flex-col px-1 pt-0.5">
        {/* Top row: KPI number + trend badge */}
        <div className="flex items-center justify-between gap-2 flex-shrink-0">
          <span className="text-2xl font-bold text-gray-800 leading-none tabular-nums">
            {fmt(total)}
          </span>
          {trendPct !== null && (
            <span className={`inline-flex items-center gap-0.5 text-xs font-semibold px-1.5 py-0.5
              rounded border ${trendColor} ${trendBg}`}>
              {trendIcon} {Math.abs(trendPct).toFixed(1)}%
            </span>
          )}
        </div>

        {/* Mini sparkline */}
        <div className="flex-1 mt-1 min-h-0">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparkData} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
              <Line
                type="monotone"
                dataKey="v"
                stroke="#1B4F8A"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 3, fill: '#1B4F8A' }}
              />
              <Tooltip
                formatter={(v: number) => [fmt(v), valueLabel]}
                labelStyle={{ display: 'none' }}
                contentStyle={{ fontSize: 11, borderRadius: 4, border: '1px solid #E5E7EB' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    )
  }

  // ── Full mode ────────────────────────────────────────────────────────────────
  const sparkH = Math.min(Math.round(height * 0.3), 100)

  return (
    <div
      className="flex flex-col items-center justify-center gap-3"
      style={{ height }}
    >
      {/* Title */}
      <p className="text-sm text-gray-500">{title}</p>

      {/* Big number */}
      <p className="text-6xl font-bold text-primary tabular-nums leading-none">
        {fmt(total)}
      </p>

      {/* Trend badge */}
      {trendPct !== null ? (
        <span className={`inline-flex items-center gap-1 text-sm font-semibold px-3 py-1
          rounded-full border ${trendColor} ${trendBg}`}>
          {trendIcon} {Math.abs(trendPct).toFixed(1)}% {trendLabel} no período
        </span>
      ) : (
        <span className="text-xs text-gray-400">dados insuficientes para tendência</span>
      )}

      {/* Subtitle */}
      <p className="text-xs text-gray-400">{valueLabel}</p>

      {/* Sparkline */}
      {sparkData.length > 1 && (
        <div style={{ width: '70%', height: sparkH }}>
          <ResponsiveContainer width="100%" height={sparkH}>
            <LineChart data={sparkData} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
              <Line
                type="monotone"
                dataKey="v"
                stroke="#1B4F8A"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: '#1B4F8A' }}
              />
              <Tooltip
                formatter={(v: number) => [fmt(v), valueLabel]}
                contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E5E7EB' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
