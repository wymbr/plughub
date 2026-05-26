/**
 * BarChartTool.tsx
 * Vertical bar chart using recharts.
 *
 * tool_id: "bar_chart"
 * data shape: BarChartData
 */
import React from 'react'
import {
  Bar, BarChart, CartesianGrid,
  Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { DisplayToolProps, BarChartData } from './types'

const COLORS = [
  '#1B4F8A', '#2D9CDB', '#00B4D8',
  '#059669', '#D97706', '#DC2626', '#7C3AED', '#0891B2',
]

function Skeleton() {
  return (
    <div className="h-full flex items-end gap-1 px-4 pb-4 animate-pulse">
      {[60, 80, 45, 90, 70, 55, 85].map((h, i) => (
        <div
          key={i}
          className="flex-1 rounded-t bg-border"
          style={{ height: `${h}%` }}
        />
      ))}
    </div>
  )
}

export const BarChartTool: React.FC<DisplayToolProps<BarChartData>> = ({
  data,
  config,
  loading,
  error,
}) => {
  if (loading) return <Skeleton />

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-red">
        Indisponível
      </div>
    )
  }

  // Build recharts data rows: { _label: string, [seriesName]: number }
  const chartData = data.x_labels.map((label, i) => {
    const row: Record<string, unknown> = { _label: label }
    data.series.forEach(s => { row[s.name] = s.data[i] ?? 0 })
    return row
  })

  const compact = config.compact !== false

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={chartData}
        margin={compact
          ? { top: 4, right: 4, left: -8, bottom: 0 }
          : { top: 8, right: 16, left: 8, bottom: 4 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#F3F4F6" vertical={false} />
        <XAxis
          dataKey="_label"
          tick={{ fontSize: compact ? 9 : 11, fill: '#6B7280' }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          label={!compact && data.y_label
            ? { value: data.y_label, angle: -90, position: 'insideLeft', fontSize: 10, fill: '#9CA3AF' }
            : undefined}
          tick={{ fontSize: compact ? 9 : 11, fill: '#6B7280' }}
          tickLine={false}
          axisLine={false}
          width={compact ? 28 : 56}
        />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E5E7EB' }}
          labelStyle={{ fontSize: 12, fontWeight: 600 }}
        />
        {!compact && <Legend wrapperStyle={{ fontSize: 11 }} />}
        {data.series.map((s, i) => (
          <Bar
            key={s.name}
            dataKey={s.name}
            fill={s.color ?? COLORS[i % COLORS.length]}
            stackId={data.stacked ? 'stack' : undefined}
            radius={data.stacked ? undefined : [2, 2, 0, 0]}
            maxBarSize={compact ? 16 : 32}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

export default BarChartTool
