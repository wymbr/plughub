/**
 * DonutTool.tsx
 * Donut chart with legend for proportion breakdowns.
 *
 * tool_id: "donut"
 * data shape: DonutData
 */
import React from 'react'
import {
  Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip,
} from 'recharts'
import type { DisplayToolProps, DonutData } from './types'

const COLORS = [
  '#1B4F8A', '#2D9CDB', '#00B4D8',
  '#059669', '#D97706', '#DC2626', '#7C3AED', '#0891B2',
]

function Skeleton() {
  return (
    <div className="h-full flex items-center justify-center animate-pulse">
      <div className="w-20 h-20 rounded-full border-8 border-border border-t-border" />
    </div>
  )
}

interface TooltipPayload {
  name: string
  value: number
  payload: { pct: number }
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  if (!active || !payload?.length) return null
  const p = payload[0]
  return (
    <div className="bg-white border border-border rounded shadow-sm px-2 py-1.5 text-xs">
      <p className="font-medium text-dark">{p.name}</p>
      <p className="text-muted">
        {p.value.toLocaleString('pt-BR')} ({p.payload.pct.toFixed(1)}%)
      </p>
    </div>
  )
}

export const DonutTool: React.FC<DisplayToolProps<DonutData>> = ({
  data,
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

  const total = data.total ?? data.values.reduce((s, v) => s + v, 0)

  const chartData = data.labels.map((label, i) => ({
    name: label,
    value: data.values[i] ?? 0,
    pct:  total > 0 ? ((data.values[i] ?? 0) / total) * 100 : 0,
  }))

  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="50%"
          innerRadius="55%"
          outerRadius="80%"
          dataKey="value"
          paddingAngle={2}
        >
          {chartData.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{ fontSize: 11, color: '#6B7280' }}
          iconSize={10}
          iconType="circle"
        />
      </PieChart>
    </ResponsiveContainer>
  )
}

export default DonutTool
