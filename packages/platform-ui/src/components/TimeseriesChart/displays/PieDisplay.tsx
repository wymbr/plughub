/**
 * PieDisplay.tsx
 *
 * Pie / donut chart for timeseries data.
 * Aggregates breakdown labels across all buckets to produce slices.
 * Falls back to a single "total" slice when no breakdown is present.
 */
import React from 'react'
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import type { TimeseriesBucket } from '@/types'
import { SERIES_COLORS } from '../constants'
import { getFormatter, type FormatType } from '../formatters'

export interface PieDisplayProps {
  buckets:    TimeseriesBucket[]
  formatType: FormatType
  valueLabel: string
  height:     number
  compact:    boolean
}

export function PieDisplay({ buckets, formatType, valueLabel, height, compact }: PieDisplayProps) {
  const fmt = getFormatter(formatType)

  // Aggregate across all time buckets → one value per label
  const totals: Record<string, number> = {}
  for (const bucket of buckets) {
    if (bucket.breakdown && bucket.breakdown.length > 0) {
      for (const bd of bucket.breakdown) {
        totals[bd.label] = (totals[bd.label] ?? 0) + bd.value
      }
    } else {
      totals[valueLabel] = (totals[valueLabel] ?? 0) + bucket.value
    }
  }

  const data = Object.entries(totals)
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({ name, value }))

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center text-xs text-gray-400" style={{ height }}>
        Sem dados no período
      </div>
    )
  }

  const outerR = compact ? Math.min(height * 0.38, 55) : Math.min(height * 0.38, 90)
  const innerR = compact ? 0 : outerR * 0.55   // donut in full mode

  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy={compact ? '50%' : '45%'}
          innerRadius={innerR}
          outerRadius={outerR}
          paddingAngle={data.length > 1 ? 2 : 0}
          dataKey="value"
          label={compact
            ? undefined
            : ({ name, percent }) => `${name}: ${(percent * 100).toFixed(1)}%`
          }
          labelLine={!compact}
        >
          {data.map((_entry, i) => (
            <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
          ))}
        </Pie>
        <Tooltip
          formatter={(v: number) => [fmt(v), '']}
          contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E5E7EB' }}
        />
        {!compact && (
          <Legend
            wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
            formatter={(value) => (
              <span style={{ color: '#374151' }}>{value}</span>
            )}
          />
        )}
      </PieChart>
    </ResponsiveContainer>
  )
}
