/**
 * TimeseriesChart.tsx
 *
 * Generic data-display widget for analytics-api timeseries endpoints.
 *
 * Display types (configured per card):
 *   bar   — bar chart (default)
 *   line  — line chart
 *   area  — area chart
 *   pie   — pie / donut chart (great for breakdown proportions)
 *   table — sortable data table
 *   tile  — KPI tile with trend badge + mini sparkline
 *
 * Two rendering modes:
 *   compact=true  → stripped-down card for dashboard grids (no controls, no export)
 *   compact=false → full report view with interval picker, date range, export buttons
 */
import React, { useState } from 'react'
import {
  Area, AreaChart,
  Bar, BarChart,
  CartesianGrid,
  Legend,
  Line, LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis, YAxis,
} from 'recharts'
import type { TimeseriesBucket, TimeseriesBreakdown } from '@/types'
import { SERIES_COLORS } from './constants'
import { useTimeseriesData, type TimeseriesParams } from './useTimeseriesData'
import { formatBucketLabel, getFormatter, type FormatType } from './formatters'
import { PieDisplay }   from './displays/PieDisplay'
import { TableDisplay } from './displays/TableDisplay'
import { TileDisplay }  from './displays/TileDisplay'

// ─── Types ────────────────────────────────────────────────────────────────────

/** All supported display types for a timeseries card. */
export type DisplayType = 'bar' | 'line' | 'area' | 'pie' | 'table' | 'tile'

// ─── Props ────────────────────────────────────────────────────────────────────

export interface TimeseriesChartProps {
  /** analytics-api base path (no query params), e.g. "/reports/timeseries/volume" */
  baseUrl: string

  /** Required query params */
  tenantId: string

  title:        string
  valueLabel:   string
  formatType?:  FormatType     // default 'count'
  displayType?: DisplayType    // default 'bar'

  /** Initial time range (full mode shows pickers to change these) */
  defaultInterval?:    number   // minutes, default 60
  defaultFromDt?:      string
  defaultToDt?:        string
  defaultBreakdownBy?: string

  poolId?:     string
  campaignId?: string

  /** compact=true → small card, no controls */
  compact?: boolean
  height?:  number       // chart height px (default: compact=140, full=260)

  pollMs?:      number   // polling interval (default 0 = no poll)
  accessToken?: string

  /** Export callbacks — shown only in full mode */
  onExportAggregated?: (url: string) => void  // CSV of buckets (level 1)
  onExportRaw?:        () => void             // raw rows (level 2, caller handles)
}

// ─── Interval options ─────────────────────────────────────────────────────────

const INTERVAL_OPTIONS = [
  { label: '15 min', value: 15 },
  { label: '1 hora', value: 60 },
  { label: '4 horas', value: 240 },
  { label: '1 dia', value: 1440 },
]

// ─── Recharts chart body (bar / line / area) ──────────────────────────────────

function ChartBody({
  buckets, intervalMinutes, displayType, formatType, valueLabel, height, compact,
}: {
  buckets:         TimeseriesBucket[]
  intervalMinutes: number
  displayType:     'bar' | 'line' | 'area'
  formatType:      FormatType
  valueLabel:      string
  height:          number
  compact:         boolean
}) {
  const fmt = getFormatter(formatType)

  const hasBreakdown = buckets.some(b => b.breakdown && b.breakdown.length > 0)

  const chartData = buckets.map(b => {
    const row: Record<string, unknown> = {
      _bucket: b.bucket,
      _label:  formatBucketLabel(b.bucket, intervalMinutes),
    }
    if (hasBreakdown && b.breakdown.length > 0) {
      b.breakdown.forEach((bd: TimeseriesBreakdown) => { row[bd.label] = bd.value })
    } else {
      row[valueLabel] = b.value
    }
    return row
  })

  const seriesKeys: string[] = hasBreakdown
    ? Array.from(new Set(buckets.flatMap(b => b.breakdown.map((bd: TimeseriesBreakdown) => bd.label))))
    : [valueLabel]

  const tooltipFmt = (value: number) => [fmt(value), '']

  const commonProps = {
    data: chartData,
    margin: compact
      ? { top: 4, right: 4, left: 0, bottom: 0 }
      : { top: 8, right: 16, left: 8, bottom: 4 },
  }

  const xAxis = (
    <XAxis
      dataKey="_label"
      tick={{ fontSize: compact ? 9 : 11, fill: '#6B7280' }}
      tickLine={false}
      axisLine={false}
      interval="preserveStartEnd"
    />
  )

  const yAxis = compact ? null : (
    <YAxis
      tickFormatter={fmt}
      tick={{ fontSize: 11, fill: '#6B7280' }}
      tickLine={false}
      axisLine={false}
      width={56}
    />
  )

  const grid = compact ? null : (
    <CartesianGrid strokeDasharray="3 3" stroke="#F3F4F6" vertical={false} />
  )

  const tooltip = (
    <Tooltip
      formatter={tooltipFmt}
      labelStyle={{ fontSize: 12, fontWeight: 600 }}
      contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #E5E7EB' }}
    />
  )

  if (displayType === 'line') {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart {...commonProps}>
          {grid}{xAxis}{yAxis}{tooltip}
          {!compact && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {seriesKeys.map((key, i) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    )
  }

  if (displayType === 'area') {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart {...commonProps}>
          {grid}{xAxis}{yAxis}{tooltip}
          {!compact && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {seriesKeys.map((key, i) => (
            <Area
              key={key}
              type="monotone"
              dataKey={key}
              stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
              fill={SERIES_COLORS[i % SERIES_COLORS.length] + '22'}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    )
  }

  // default: bar
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart {...commonProps}>
        {grid}{xAxis}{yAxis}{tooltip}
        {!compact && <Legend wrapperStyle={{ fontSize: 11 }} />}
        {seriesKeys.map((key, i) => (
          <Bar
            key={key}
            dataKey={key}
            fill={SERIES_COLORS[i % SERIES_COLORS.length]}
            radius={[2, 2, 0, 0]}
            maxBarSize={compact ? 12 : 32}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

// ─── Display dispatcher ───────────────────────────────────────────────────────

function DataDisplay({
  buckets, intervalMinutes, total, displayType, formatType, valueLabel, title, height, compact,
}: {
  buckets:         TimeseriesBucket[]
  intervalMinutes: number
  total:           number
  displayType:     DisplayType
  formatType:      FormatType
  valueLabel:      string
  title:           string
  height:          number
  compact:         boolean
}) {
  if (displayType === 'pie') {
    return (
      <PieDisplay
        buckets={buckets}
        formatType={formatType}
        valueLabel={valueLabel}
        height={height}
        compact={compact}
      />
    )
  }

  if (displayType === 'table') {
    return (
      <TableDisplay
        buckets={buckets}
        intervalMinutes={intervalMinutes}
        formatType={formatType}
        valueLabel={valueLabel}
        height={height}
        compact={compact}
      />
    )
  }

  if (displayType === 'tile') {
    return (
      <TileDisplay
        buckets={buckets}
        total={total}
        formatType={formatType}
        valueLabel={valueLabel}
        title={title}
        height={height}
        compact={compact}
      />
    )
  }

  // bar | line | area
  return (
    <ChartBody
      buckets={buckets}
      intervalMinutes={intervalMinutes}
      displayType={displayType as 'bar' | 'line' | 'area'}
      formatType={formatType}
      valueLabel={valueLabel}
      height={height}
      compact={compact}
    />
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function TimeseriesChart({
  baseUrl,
  tenantId,
  title,
  valueLabel,
  formatType    = 'count',
  displayType   = 'bar',
  defaultInterval    = 60,
  defaultFromDt,
  defaultToDt,
  defaultBreakdownBy,
  poolId,
  campaignId,
  compact  = false,
  height,
  pollMs   = 0,
  accessToken,
  onExportAggregated,
  onExportRaw,
}: TimeseriesChartProps) {
  const [interval, setInterval] = useState(defaultInterval)
  const [fromDt, setFromDt]     = useState(defaultFromDt ?? '')
  const [toDt, setToDt]         = useState(defaultToDt ?? '')

  const params: TimeseriesParams = {
    tenantId,
    interval,
    fromDt:      fromDt  || undefined,
    toDt:        toDt    || undefined,
    breakdownBy: defaultBreakdownBy,
    poolId,
    campaignId,
  }

  const { data, loading, error } = useTimeseriesData({ baseUrl, params, pollMs, accessToken })

  const fmt     = getFormatter(formatType)
  const total   = data?.meta?.total ?? 0
  const chartH  = height ?? (compact ? 140 : 260)
  const buckets = data?.buckets ?? []
  const ivMin   = data?.meta?.interval_minutes ?? interval

  // Tile display skips the KPI header row (it renders its own big number)
  const isTile = displayType === 'tile'

  // ── Compact card ─────────────────────────────────────────────────────────────

  if (compact) {
    return (
      <div className="h-full flex flex-col">
        {/* Header row — hidden for tile (tile renders its own number) */}
        {!isTile && (
          <div className="flex items-baseline justify-between px-1 pb-1 flex-shrink-0">
            <span className="text-xs text-gray-500 truncate">{title}</span>
            <span className="text-lg font-bold text-gray-800 ml-2">{fmt(total)}</span>
          </div>
        )}

        {loading && (
          <div className="flex-1 flex items-center justify-center">
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          </div>
        )}
        {!loading && error && (
          <div className="flex-1 flex items-center justify-center text-xs text-red-400">indisponível</div>
        )}
        {!loading && !error && (
          <div className="flex-1 min-h-0">
            <DataDisplay
              buckets={buckets}
              intervalMinutes={ivMin}
              total={total}
              displayType={displayType}
              formatType={formatType}
              valueLabel={valueLabel}
              title={title}
              height={chartH}
              compact
            />
          </div>
        )}
      </div>
    )
  }

  // ── Full mode ─────────────────────────────────────────────────────────────────

  function handleExportCsv() {
    const qs = new URLSearchParams()
    qs.set('tenant_id', tenantId)
    qs.set('interval', String(interval))
    qs.set('format', 'csv')
    if (fromDt) qs.set('from_dt', fromDt)
    if (toDt)   qs.set('to_dt', toDt)
    if (defaultBreakdownBy) qs.set('breakdown_by', defaultBreakdownBy)
    if (poolId)     qs.set('pool_id', poolId)
    if (campaignId) qs.set('campaign_id', campaignId)
    const url = `${baseUrl}?${qs.toString()}`
    if (onExportAggregated) {
      onExportAggregated(url)
    } else {
      window.open(url, '_blank')
    }
  }

  // Table and tile don't benefit from the interval/date controls in full mode
  const showControls = displayType !== 'tile'

  return (
    <div className="bg-white rounded-lg border border-gray-200 flex flex-col gap-3 p-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
          {!isTile && (
            <p className="text-2xl font-bold text-primary mt-0.5">{fmt(total)}</p>
          )}
        </div>

        {showControls && (
          <div className="flex items-center gap-2 flex-wrap">
            {/* Interval picker — not useful for table/tile */}
            {displayType !== 'table' && (
              <select
                value={interval}
                onChange={e => setInterval(Number(e.target.value))}
                className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-600 focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {INTERVAL_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            )}

            {/* Date range */}
            <input
              type="datetime-local"
              value={fromDt.slice(0, 16)}
              onChange={e => setFromDt(e.target.value)}
              className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-600 focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <span className="text-gray-400 text-xs">→</span>
            <input
              type="datetime-local"
              value={toDt.slice(0, 16)}
              onChange={e => setToDt(e.target.value)}
              className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-600 focus:outline-none focus:ring-1 focus:ring-primary"
            />

            {/* Export */}
            <button
              onClick={handleExportCsv}
              className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-600 hover:bg-gray-50 transition-colors"
              title="Exportar CSV agregado"
            >
              ↓ CSV
            </button>
            {onExportRaw && (
              <button
                onClick={onExportRaw}
                className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-600 hover:bg-gray-50 transition-colors"
                title="Exportar dados brutos"
              >
                ↓ Bruto
              </button>
            )}
          </div>
        )}
      </div>

      {/* Content area */}
      {loading && (
        <div className="flex items-center justify-center" style={{ height: chartH }}>
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      )}
      {!loading && error && (
        <div className="flex items-center justify-center text-sm text-red-400" style={{ height: chartH }}>
          Dados indisponíveis
        </div>
      )}
      {!loading && !error && buckets.length === 0 && !isTile && (
        <div className="flex items-center justify-center text-sm text-gray-400" style={{ height: chartH }}>
          Nenhum dado no período
        </div>
      )}
      {!loading && !error && (buckets.length > 0 || isTile) && (
        <DataDisplay
          buckets={buckets}
          intervalMinutes={ivMin}
          total={total}
          displayType={displayType}
          formatType={formatType}
          valueLabel={valueLabel}
          title={title}
          height={chartH}
          compact={false}
        />
      )}
    </div>
  )
}

export default TimeseriesChart
