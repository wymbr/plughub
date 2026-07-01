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
  key:          string
  label:        string
  placeholder:  string
  optional:     boolean
  /** Endpoint URL; when present, AddCardModal renders a <select> populated from its response. */
  options_from?: string
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
    id:               'agent-availability',
    endpoint:         '/reports/display/agent-availability',
    label:            'Disponibilidade de Agentes',
    icon:             '🟢',
    description:      'Pausas de agentes humanos por instância / pool / dia (Arc 8)',
    compatible_tools: ['table'],
    default_tool:     'table',
    defaultW:         12,
    defaultH:         5,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'pools-queue',
    endpoint:         '/reports/display/pools-queue',
    label:            'Fila / SLA por Pool',
    icon:             '⏳',
    description:      'Contatos, fila, abandono e atingimento de SLA por pool',
    compatible_tools: ['table'],
    default_tool:     'table',
    defaultW:         12,
    defaultH:         5,
    configurable_params: [
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'volume-by-channel',
    endpoint:         '/reports/display/volume-by-channel',
    label:            'Volume por Canal',
    icon:             '📨',
    description:      'Distribuição de contatos por canal',
    compatible_tools: ['donut'],
    default_tool:     'donut',
    defaultW:         4,
    defaultH:         3,
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

  // ─── Journey cards (Arc 10 — Phase E; Arc 17 — journey_type_id + pool_id filters) ──────
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
      { key: 'skill_id',        label: 'Skill (fixo)',         placeholder: 'Ex: skill_portabilidade_v1', optional: true },
      { key: 'journey_type_id', label: 'Tipo de Jornada',      placeholder: 'Ex: portabilidade_telco',    optional: true },
      { key: 'pool_id',         label: 'Pool (fixo)',           placeholder: 'Ex: retencao_humano',        optional: true },
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
      { key: 'skill_id',        label: 'Skill (fixo)',         placeholder: 'Ex: skill_portabilidade_v1', optional: true },
      { key: 'journey_type_id', label: 'Tipo de Jornada',      placeholder: 'Ex: portabilidade_telco',    optional: true },
      { key: 'pool_id',         label: 'Pool (fixo)',           placeholder: 'Ex: retencao_humano',        optional: true },
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
      { key: 'skill_id',        label: 'Skill (fixo)',         placeholder: 'Ex: skill_portabilidade_v1', optional: true },
      { key: 'journey_type_id', label: 'Tipo de Jornada',      placeholder: 'Ex: portabilidade_telco',    optional: true },
      { key: 'pool_id',         label: 'Pool (fixo)',           placeholder: 'Ex: retencao_humano',        optional: true },
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
      { key: 'skill_id',        label: 'Skill (fixo)',         placeholder: 'Ex: skill_portabilidade_v1', optional: true },
      { key: 'journey_type_id', label: 'Tipo de Jornada',      placeholder: 'Ex: portabilidade_telco',    optional: true },
      { key: 'pool_id',         label: 'Pool (fixo)',           placeholder: 'Ex: retencao_humano',        optional: true },
    ],
  },

  // ─── Agent Business Events cards (Arc 12 — Fase D) ───────────────────────
  {
    id:               'agent-event-timeseries',
    endpoint:         '/reports/display/agent-event-timeseries',
    label:            'Eventos de Negócio (Série)',
    icon:             '📈',
    description:      'Volume diário e valor médio de eventos de negócio publicados por agentes AI/humanos',
    compatible_tools: ['line_chart', 'bar_chart'],
    default_tool:     'line_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      {
        key:          'category',
        label:        'Categoria de evento',
        placeholder:  'Ex: retencao_humano.skill_portabilidade_v1.nps',
        optional:     false,
        options_from: '/reports/agent-events/categories',
      },
      { key: 'pool_id', label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
    ],
  },
  {
    id:               'agent-event-summary',
    endpoint:         '/reports/display/agent-event-summary',
    label:            'Eventos de Negócio (Resumo)',
    icon:             '📊',
    description:      'Total de eventos por dimensão (categoria, skill, pool ou tipo de agente)',
    compatible_tools: ['bar_chart'],
    default_tool:     'bar_chart',
    defaultW:         6,
    defaultH:         4,
    configurable_params: [
      {
        key:          'category',
        label:        'Categoria de evento',
        placeholder:  'Ex: retencao_humano.skill_portabilidade_v1.nps',
        optional:     false,
        options_from: '/reports/agent-events/categories',
      },
      { key: 'pool_id',  label: 'Pool (fixo)', placeholder: 'Ex: retencao_humano', optional: true },
      { key: 'group_by', label: 'Agrupar por', placeholder: 'category | skill_id | pool_id | agent_type_id', optional: true },
    ],
  },
]

/** Lookup an endpoint by ID. */
export function getEndpoint(id: string): EndpointDescriptor | undefined {
  return ENDPOINT_CATALOG.find(e => e.id === id)
}

/** Resolve the catalog id for a card's query endpoint (new-format cards). */
export function catalogIdForEndpoint(endpoint?: string): string | undefined {
  if (!endpoint) return undefined
  return ENDPOINT_CATALOG.find(e => e.endpoint === endpoint)?.id
}

type TFunc = (key: string, opts?: Record<string, unknown>) => string

/**
 * Display title for a dashboard card, locale-aware.
 * Cards store a `title` string that was baked in whatever language was active at
 * creation time. If that stored title still matches the catalog label (in EN or
 * pt-BR — i.e. the user never renamed it), re-translate it to the current locale.
 * A genuinely custom title is preserved as-is.
 */
export function resolveCardTitle(
  card: { title?: string; query?: { endpoint?: string }; config?: { title?: string }; type?: string },
  t: TFunc,
): string {
  const stored = (card.title ?? '').trim()
  const id = catalogIdForEndpoint(card.query?.endpoint)
  if (id) {
    const key = `catalog.${id}.label`
    const cur = t(key)
    const en  = t(key, { lng: 'en' })
    const pt  = t(key, { lng: 'pt-BR' })
    const entry = getEndpoint(id)
    if (!stored || stored === en || stored === pt || (entry && stored === entry.label)) return cur
    return stored
  }
  return stored || card.config?.title || card.type || ''
}
