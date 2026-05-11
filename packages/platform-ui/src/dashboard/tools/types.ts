/**
 * types.ts
 * TypeScript contracts for the Display Tool Registry.
 *
 * A DisplayTool is a pure React component registered by ID.  The backend
 * returns data already shaped for the tool — the frontend never transforms it.
 */
import type React from 'react'

// ─── Data shapes (one per tool) ───────────────────────────────────────────────

export interface MetricCardData {
  value:   number
  label:   string
  trend?:  number   // % change vs previous period — positive = growth
  format:  'number' | 'duration_ms' | 'score' | 'percent'
}

export interface TableColumn {
  key:       string
  label:     string
  sortable?: boolean
  align?:    'left' | 'right' | 'center'
}

export interface TableData {
  columns: TableColumn[]
  rows:    Record<string, string | number | null>[]
  total?:  number   // total row count for future pagination
}

export interface BarChartData {
  x_labels: string[]
  series:   { name: string; data: number[]; color?: string }[]
  stacked?: boolean
  y_label?: string
}

export interface LineChartData {
  x_labels: string[]
  series:   { name: string; data: number[]; color?: string }[]
  y_label?: string
}

export interface DonutData {
  labels: string[]
  values: number[]
  total?: number   // if omitted, computed as sum(values)
}

export type DisplayToolDataShape =
  | MetricCardData
  | TableData
  | BarChartData
  | LineChartData
  | DonutData

// ─── Tool component props ──────────────────────────────────────────────────────

export interface DisplayToolProps<TData extends DisplayToolDataShape = DisplayToolDataShape> {
  data:    TData
  config:  Record<string, unknown>   // tool_config from the card
  loading: boolean
  error:   string | null
}

// ─── Tool descriptor ──────────────────────────────────────────────────────────

export interface DisplayTool<TData extends DisplayToolDataShape = DisplayToolDataShape> {
  id:          string
  label:       string
  icon:        string
  description: string
  defaultW:    number   // default width in grid columns (1-12)
  defaultH:    number   // default height in row units
  component:   React.FC<DisplayToolProps<TData>>
}

// ─── New card schema (tool_id + query + tool_config) ──────────────────────────

export interface FixedQueryParam {
  type:  'fixed'
  value: unknown
}

export interface RuntimeQueryParam {
  type:       'runtime'
  filter_key: string   // matched against GlobalFilter.filter_key
  default:    unknown
}

export type QueryParam = FixedQueryParam | RuntimeQueryParam

export interface CardQuery {
  endpoint: string                         // e.g. "/reports/display/sessions-by-pool"
  params:   Record<string, QueryParam>
}

/** New-format card (tool_id + query + tool_config). */
export interface NewDashboardCard {
  id:          string
  x:           number
  y:           number
  w:           number
  h:           number
  tool_id:     string
  title:       string
  query:       CardQuery
  tool_config: Record<string, unknown>
  refresh_ms?: number   // polling interval, default 30_000
}

// ─── Runtime filter (Part 3) ──────────────────────────────────────────────────

export interface GlobalFilter {
  filter_key: string
  label:      string
  type:       'date' | 'select' | 'multi_select'
  options?:   { value: string; label: string }[]
  default:    unknown
}

// ─── Query URL builder ────────────────────────────────────────────────────────

export function buildQueryUrl(
  card: NewDashboardCard,
  runtimeFilters: Record<string, unknown> = {},
): string {
  const params = new URLSearchParams()
  for (const [key, param] of Object.entries(card.query.params)) {
    if (param.type === 'fixed') {
      if (param.value !== null && param.value !== undefined) {
        params.set(key, String(param.value))
      }
    } else {
      const value = runtimeFilters[param.filter_key] ?? param.default
      if (value !== null && value !== undefined) {
        params.set(key, String(value))
      }
    }
  }
  return `${card.query.endpoint}?${params.toString()}`
}
