/**
 * registry.ts
 * Display Tool Registry — maps tool_id → DisplayTool descriptor.
 *
 * Also exports normalizeCard() which converts legacy (type+config) cards to the
 * new (tool_id+query+tool_config) format so both schemas coexist without
 * forcing a data migration.
 */
import { MetricCardTool } from './MetricCardTool'
import { TableTool }      from './TableTool'
import { BarChartTool }   from './BarChartTool'
import { LineChartTool }  from './LineChartTool'
import { DonutTool }      from './DonutTool'
import type { DisplayTool, NewDashboardCard, QueryParam } from './types'

// ─── Registry ─────────────────────────────────────────────────────────────────

const tools: DisplayTool[] = [
  {
    id:          'metric_card',
    label:       'KPI / Métrica',
    icon:        '◈',
    description: 'Número grande com variação percentual',
    defaultW:    3,
    defaultH:    2,
    component:   MetricCardTool as DisplayTool['component'],
  },
  {
    id:          'table',
    label:       'Tabela',
    icon:        '☰',
    description: 'Tabela de dados ordenável',
    defaultW:    12,
    defaultH:    5,
    component:   TableTool as DisplayTool['component'],
  },
  {
    id:          'bar_chart',
    label:       'Barras',
    icon:        '▐',
    description: 'Gráfico de barras verticais',
    defaultW:    6,
    defaultH:    4,
    component:   BarChartTool as DisplayTool['component'],
  },
  {
    id:          'line_chart',
    label:       'Linha',
    icon:        '╱',
    description: 'Série temporal com linhas',
    defaultW:    6,
    defaultH:    4,
    component:   LineChartTool as DisplayTool['component'],
  },
  {
    id:          'donut',
    label:       'Donut',
    icon:        '◔',
    description: 'Distribuição proporcional',
    defaultW:    4,
    defaultH:    3,
    component:   DonutTool as DisplayTool['component'],
  },
]

/** Lookup a tool by ID. Returns undefined if not registered. */
export function getDisplayTool(toolId: string): DisplayTool | undefined {
  return tools.find(t => t.id === toolId)
}

/** The full ordered list (useful for AddCardModal). */
export function listDisplayTools(): DisplayTool[] {
  return tools
}

// ─── normalizeCard() — backward-compat adapter ────────────────────────────────

/**
 * Migration map: old DashboardCardType → new tool_id + display endpoint.
 * Endpoints under /reports/display/ are created in Part 4.
 */
const MIGRATION_MAP: Record<
  string,
  { tool_id: string; endpoint: string }
> = {
  timeseries_volume:      { tool_id: 'bar_chart',   endpoint: '/reports/display/session-volume' },
  timeseries_handle_time: { tool_id: 'line_chart',  endpoint: '/reports/display/handle-time' },
  timeseries_score:       { tool_id: 'line_chart',  endpoint: '/reports/display/evaluation-score' },
  kpi_sessions:           { tool_id: 'metric_card', endpoint: '/reports/display/kpi-sessions' },
  kpi_score:              { tool_id: 'metric_card', endpoint: '/reports/display/kpi-score' },
  pool_status:            { tool_id: 'table',       endpoint: '/reports/display/pool-status' },
}

/**
 * Converts a raw card (old or new format) to NewDashboardCard.
 * Cards already in new format (have tool_id) are returned unchanged.
 * Old-format cards (have type+config) are migrated using MIGRATION_MAP.
 */
export function normalizeCard(raw: Record<string, unknown>): NewDashboardCard | null {
  // Already new format
  if (typeof raw.tool_id === 'string') {
    return raw as unknown as NewDashboardCard
  }

  // Legacy format — must have type
  const type = raw.type as string | undefined
  if (!type) return null

  const mapping = MIGRATION_MAP[type]
  if (!mapping) return null

  // Extract tenant_id from config if present (timeseries cards had it)
  const cfg = (raw.config ?? {}) as Record<string, unknown>
  const tenantParam: Record<string, QueryParam> = cfg.tenantId
    ? { tenant_id: { type: 'runtime' as const, filter_key: 'tenant_id', default: cfg.tenantId } }
    : {}

  const title = (cfg.title as string | undefined) ?? type

  const normalized: NewDashboardCard = {
    id:      raw.id as string,
    x:       raw.x as number,
    y:       raw.y as number,
    w:       raw.w as number,
    h:       raw.h as number,
    tool_id: mapping.tool_id,
    title,
    query: {
      endpoint: mapping.endpoint,
      params:   {
        ...tenantParam,
        // interval / poolId as fixed params if present in old config
        ...(cfg.interval ? { interval: { type: 'fixed', value: cfg.interval } } : {}),
        ...(cfg.poolId   ? { pool_id:  { type: 'fixed', value: cfg.poolId } }   : {}),
      },
    },
    tool_config: cfg,
    refresh_ms:  30_000,
  }

  return normalized
}
