/**
 * LineChartTool.tsx
 * Time-series line chart using recharts.
 *
 * tool_id: "line_chart"
 * data shape: LineChartData
 */
import React from 'react'
import {
  CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { DisplayToolProps, LineChartData } from './types'

const COLORS = [
  '#1B4F8A', '#2D9CDB', '#00B4D8',
  '#059669', '#D97706', '#DC2626', '#7C3AED', '#0891B2',
]

function Skeleton() {
  return (
    <div className="h-full flex items-center justify-center animate-pulse">
      <svg viewBox="0 0 200 80" className="w-3/4 h-1/2 text-gray-200">
        <polyline
          points="0,60 30,40 60,55 90,25 120,45 150,30 200,50"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
      </svg>
    </div>
  )
}

export const LineChartTool: React.FC<DisplayToolProps<LineChartData>> = ({
  data,
  config,
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

  const chartData = data.x_labels.map((label, i) => {
    const row: Record<string, unknown> = { _label: label }
    data.series.forEach(s => { row[s.name] = s.data[i] ?? 0 })
    return row
  })

  const compact = config.compact !== false

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart
        data={chartData}
        margin={compact
          ? { top: 4, right: 4, left: 0, bottom: 0 }
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
          <Line
            key={s.name}
            type="monotone"
            dataKey={s.name}
            stroke={s.color ?? COLORS[i % COLORS.length]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

export default LineChartTool
