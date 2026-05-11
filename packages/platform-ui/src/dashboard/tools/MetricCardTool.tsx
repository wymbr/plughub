/**
 * MetricCardTool.tsx
 * Large KPI number with trend arrow and formatted value.
 *
 * tool_id: "metric_card"
 * data shape: MetricCardData
 */
import React from 'react'
import type { DisplayToolProps, MetricCardData } from './types'

// ─── Formatters ───────────────────────────────────────────────────────────────

function formatValue(value: number, format: MetricCardData['format']): string {
  switch (format) {
    case 'duration_ms': {
      if (value < 1_000) return `${Math.round(value)}ms`
      if (value < 60_000) return `${(value / 1_000).toFixed(1)}s`
      const m = Math.floor(value / 60_000)
      const s = Math.round((value % 60_000) / 1_000)
      return `${m}m${s > 0 ? ` ${s}s` : ''}`
    }
    case 'score':
      return value.toFixed(2)
    case 'percent':
      return `${(value * 100).toFixed(1)}%`
    default:
      return value >= 1_000
        ? value.toLocaleString('pt-BR')
        : String(value)
  }
}

// ─── Skeleton ─────────────────────────────────────────────────────────────────

function Skeleton() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-2 animate-pulse">
      <div className="h-8 w-24 rounded bg-gray-200" />
      <div className="h-3 w-16 rounded bg-gray-100" />
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

export const MetricCardTool: React.FC<DisplayToolProps<MetricCardData>> = ({
  data,
  loading,
  error,
}) => {
  if (loading) return <Skeleton />

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-red-400">
        Indisponível
      </div>
    )
  }

  const formatted = formatValue(data.value, data.format)
  const hasTrend  = data.trend !== undefined && data.trend !== null
  const positive  = (data.trend ?? 0) >= 0

  return (
    <div className="h-full flex flex-col items-center justify-center gap-1 px-2">
      <span className="text-3xl font-bold text-gray-800 leading-none">
        {formatted}
      </span>

      <span className="text-xs text-gray-500 text-center truncate max-w-full">
        {data.label}
      </span>

      {hasTrend && (
        <span
          className={`text-xs font-medium flex items-center gap-0.5 ${
            positive ? 'text-green-600' : 'text-red-500'
          }`}
        >
          {positive ? '↑' : '↓'}
          {Math.abs(data.trend!).toFixed(1)}%
        </span>
      )}
    </div>
  )
}

export default MetricCardTool
