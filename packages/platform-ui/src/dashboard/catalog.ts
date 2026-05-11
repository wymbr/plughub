/**
 * catalog.ts
 * Static catalog of display endpoints available for new-format dashboard cards.
 *
 * Each entry declares which Display Tools can render its response, the suggested
 * default tool, and which params the user can optionally pin to a fixed value
 * at card creation time.
 *
 * All endpoints also accept `tenant_id` (injected automatically as a fixed param)
 * and `from` / `to` (runtime params wired to the FilterBar in Part 3).
 */

export interface EndpointDescriptor {
  /** Unique ID — endpoint path suffix, e.g. "session-volume" */
  id:               string
  /** Full path, e.g. "/reports/display/session-volume" */
  endpoint:         string
  label:            string
  icon:             string
  description:      string
  /** Tool IDs that are valid visualizations for this endpoint's data shape. */
  compatible_tools: string[]
  /** Suggested default tool. */
  default_tool:     string
  /** Default card width (grid columns). */
  defaultW:         number
  /** Default card height (row units). */
  defaultH:         number
  /**
   * Optional param keys the user can pin to a fixed value at card creation.
   * Params not listed here are always runtime (wired to FilterBar filters).
   */
  configurable_params?: ConfigurableParam[]
}

export interface ConfigurableParam {
  key:         string
  label:       string
  placeholder: string
  optional:    boolean
}

// ─── The 10 display endpoints ─────────────────────────────────────────────────

export const ENDPOINT_CATALOG: EndpointDescriptor[] = [
  {
    id:               'session-volume',
    endpoint:         '/reports/display/session-volume',
    label:            'Volume de Sessões',
    icon:             '📊',
    description:      'Número de sessões por período de tempo',
    compatible_tools: ['bar_chart', 'line_chart'],
    default_tool:     'bar_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'handle-time',
    endpoint:         '/reports/display/handle-time',
    label:            'Tempo Médio de Atendimento',
    icon:             '⏱️',
    description:      'Tempo médio de atendimento por período',
    compatible_tools: ['line_chart', 'bar_chart'],
    default_tool:     'line_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'evaluation-score',
    endpoint:         '/reports/display/evaluation-score',
    label:            'Nota Média de Avaliação',
    icon:             '⭐',
    description:      'Nota média de avaliação por período',
    compatible_tools: ['line_chart', 'bar_chart'],
    default_tool:     'line_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'sessions-by-pool',
    endpoint:         '/reports/display/sessions-by-pool',
    label:            'Sessões por Pool',
    icon:             '🏊',
    description:      'Volume de sessões agrupado por pool',
    compatible_tools: ['bar_chart'],
    default_tool:     'bar_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [],
  },
  {
    id:               'outcome-distribution',
    endpoint:         '/reports/display/outcome-distribution',
    label:            'Distribuição de Outcomes',
    icon:             '🎯',
    description:      'Proporção de resolvidos, escalados e abandonados',
    compatible_tools: ['donut'],
    default_tool:     'donut',
    defaultW:         4,
    defaultH:         3,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'pool-status',
    endpoint:         '/reports/display/pool-status',
    label:            'Status dos Pools',
    icon:             '🟢',
    description:      'Disponibilidade, fila e SLA dos pools operacionais',
    compatible_tools: ['table'],
    default_tool:     'table',
    defaultW:         12,
    defaultH:         5,
    configurable_params: [],
  },
  {
    id:               'agent-performance',
    endpoint:         '/reports/display/agent-performance',
    label:            'Performance de Agents',
    icon:             '🤖',
    description:      'Taxa de resolução e escalação por tipo de agent',
    compatible_tools: ['table'],
    default_tool:     'table',
    defaultW:         12,
    defaultH:         5,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'kpi-sessions',
    endpoint:         '/reports/display/kpi-sessions',
    label:            'KPI: Total de Sessões',
    icon:             '◈',
    description:      'Total de sessões no período com tendência',
    compatible_tools: ['metric_card'],
    default_tool:     'metric_card',
    defaultW:         3,
    defaultH:         2,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'kpi-resolution',
    endpoint:         '/reports/display/kpi-resolution',
    label:            'KPI: Taxa de Resolução',
    icon:             '✅',
    description:      'Taxa de resolução com variação vs período anterior',
    compatible_tools: ['metric_card'],
    default_tool:     'metric_card',
    defaultW:         3,
    defaultH:         2,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'kpi-score',
    endpoint:         '/reports/display/kpi-score',
    label:            'KPI: Nota Média',
    icon:             '⭐',
    description:      'Nota média de avaliação com variação percentual',
    compatible_tools: ['metric_card'],
    default_tool:     'metric_card',
    defaultW:         3,
    defaultH:         2,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },

  // ─── Journey cards (Arc 10 — Phase E) ────────────────────────────────────
  {
    id:               'journey-active-count',
    endpoint:         '/reports/display/journey-active-count',
    label:            'KPI: Jornadas Ativas',
    icon:             '🗺️',
    description:      'Total de jornadas com status ativo e tendência vs período anterior',
    compatible_tools: ['metric_card'],
    default_tool:     'metric_card',
    defaultW:         3,
    defaultH:         2,
    configurable_params: [
      { key: 'skill_id', label: 'Skill (fixo)', placeholder: 'Ex: skill_portabilidade_v1', optional: true },
    ],
  },
  {
    id:               'journey-resolution-rate',
    endpoint:         '/reports/display/journey-resolution-rate',
    label:            'Taxa de Resolução por Jornada',
    icon:             '✅',
    description:      'Percentual de jornadas concluídas por skill (apenas jornadas finalizadas)',
    compatible_tools: ['bar_chart'],
    default_tool:     'bar_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      { key: 'skill_id', label: 'Skill (fixo)', placeholder: 'Ex: skill_portabilidade_v1', optional: true },
    ],
  },
  {
    id:               'journey-funnel',
    endpoint:         '/reports/display/journey-funnel',
    label:            'Funil de Jornadas',
    icon:             '🔽',
    description:      'Distribuição de jornadas por status (ativa, suspensa, concluída, falha)',
    compatible_tools: ['donut'],
    default_tool:     'donut',
    defaultW:         4,
    defaultH:         3,
    configurable_params: [
      { key: 'skill_id', label: 'Skill (fixo)', placeholder: 'Ex: skill_portabilidade_v1', optional: true },
    ],
  },
  {
    id:               'journey-median-duration',
    endpoint:         '/reports/display/journey-median-duration',
    label:            'Duração Mediana de Jornadas',
    icon:             '⏳',
    description:      'Duração mediana (p50) em minutos por skill para jornadas finalizadas',
    compatible_tools: ['bar_chart'],
    default_tool:     'bar_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      { key: 'skill_id', label: 'Skill (fixo)', placeholder: 'Ex: skill_portabilidade_v1', optional: true },
    ],
  },
]

/** Lookup an endpoint by ID. */
export function getEndpoint(id: string): EndpointDescriptor | undefined {
  return ENDPOINT_CATALOG.find(e => e.id === id)
}
