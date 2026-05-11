export type UserRole = 'operator' | 'supervisor' | 'admin' | 'developer' | 'business'

// ── ABAC module-config types ──────────────────────────────────────────────────

/** Valores possíveis de acesso por campo de permissão. */
export type PermissionAccess = 'none' | 'read_only' | 'write_only' | 'read_write'

/** Configuração de um campo de permissão de um módulo. */
export interface ModuleFieldConfig {
  access: PermissionAccess
  /** Pool IDs ou Campaign IDs com escopo restrito. [] = acesso global. */
  scope: string[]
}

/**
 * module_config completo do usuário.
 * Chave externa = module_id (ex: "evaluation").
 * Chave interna = campo de permissão (ex: "contestar", "revisar").
 */
export type ModuleConfig = Record<string, Record<string, ModuleFieldConfig>>

export interface Session {
  userId: string
  name: string
  email: string
  role: UserRole
  roles: string[]                  // all roles from JWT (user may have multiple)
  tenantId: string
  accessiblePools: string[]        // [] = all pools (admin); non-empty = restricted
  installationId: string
  locale: 'pt-BR' | 'en'
  /** ABAC config por módulo, carregada do JWT. */
  moduleConfig: ModuleConfig
  // JWT tokens — stored in memory; refresh_token persisted in localStorage
  accessToken: string
  refreshToken: string
  expiresAt: number                // epoch ms — when the access token expires
}

export interface RoutingExpression {
  weight_sla:      number  // default 1.0 — SLA urgency
  weight_wait:     number  // default 0.8 — raw wait time
  weight_tier:     number  // default 0.6 — customer tier
  weight_churn:    number  // default 0.9 — churn risk
  weight_business: number  // default 0.4 — business value score
}

export const ROUTING_EXPRESSION_DEFAULTS: RoutingExpression = {
  weight_sla:      1.0,
  weight_wait:     0.8,
  weight_tier:     0.6,
  weight_churn:    0.9,
  weight_business: 0.4,
}

/**
 * RoutingWeights — integer 0-9 priority weights for a pool.
 *  fixos:     per-competency-skill weight (0 = disable, 1-9 = strength)
 *  dinamicos: dynamic queue scoring factors (0 = ignore, 9 = max influence)
 */
export interface RoutingWeightsDinamicos {
  sla:      number   // 0-9, default 9 — SLA urgency
  wait:     number   // 0-9, default 7 — raw wait time
  tier:     number   // 0-9, default 5 — customer tier
  churn:    number   // 0-9, default 8 — churn risk
  business: number   // 0-9, default 3 — business value score
}

export interface RoutingWeights {
  fixos:     Record<string, number>    // competency skill key → 0-9
  dinamicos: RoutingWeightsDinamicos
}

export const ROUTING_WEIGHTS_DEFAULTS: RoutingWeights = {
  fixos: {},
  dinamicos: { sla: 9, wait: 7, tier: 5, churn: 8, business: 3 },
}

export interface Pool {
  pool_id: string
  tenant_id: string
  description?: string
  channel_types: string[]
  sla_target_ms: number
  calendar_id?: string
  routing_skills?: string[]
  routing_expression?: RoutingExpression
  routing_weights?: RoutingWeights
  status: string
  created_at: string
  updated_at: string
}

export interface CreatePoolInput {
  pool_id: string
  description?: string
  channel_types: string[]
  sla_target_ms: number
  calendar_id?: string
  routing_skills?: string[]
  routing_expression?: RoutingExpression
  routing_weights?: RoutingWeights
}

export interface UpdatePoolInput {
  description?: string
  channel_types?: string[]
  sla_target_ms?: number
  calendar_id?: string
  routing_skills?: string[]
  routing_expression?: RoutingExpression
  routing_weights?: RoutingWeights
}

export interface AgentType {
  agent_type_id: string
  tenant_id: string
  framework: string
  execution_model: string
  role: string
  pools: Array<{ pool_id: string }>
  skills: Array<{ skill_id: string; version_policy?: string }>
  permissions: string[]
  max_concurrent_sessions: number
  traffic_weight: number
  status: string
  created_at: string
  updated_at?: string
}

export interface CreateAgentTypeInput {
  agent_type_id: string
  framework: string
  execution_model: 'stateless' | 'stateful'
  role?: string
  pools: string[]
  skills?: Array<{ skill_id: string; version_policy: string }>
  max_concurrent_sessions?: number
  permissions?: string[]
  prompt_id?: string
}

export interface Skill {
  skill_id: string
  tenant_id: string
  name: string
  version: string
  description?: string
  classification?: {
    type?: string
    vertical?: string
    domain?: string
  }
  status: string
  created_at: string
}

export interface CreateSkillInput {
  skill_id: string
  name: string
  version: string
  description?: string
  classification?: {
    type?: string
    vertical?: string
    domain?: string
  }
}

export interface Instance {
  instance_id: string
  agent_type_id: string
  pool_id: string
  tenant_id: string
  status: string
  channel_types: string[]
  updated_at: string
}

// ── Channel / GatewayConfig ───────────────────────────────────────────────────

export type ChannelType =
  | 'whatsapp'
  | 'webchat'
  | 'voice'
  | 'email'
  | 'sms'
  | 'instagram'
  | 'telegram'
  | 'webrtc'

export interface GatewayConfig {
  id: string
  tenant_id: string
  channel: ChannelType
  display_name: string
  active: boolean
  credentials: Record<string, string>   // values are masked (e.g. "••••••") on read
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
  created_by: string
}

export interface CreateGatewayConfigInput {
  channel: ChannelType
  display_name: string
  active?: boolean
  credentials?: Record<string, string>
  settings?: Record<string, unknown>
}

export interface UpdateGatewayConfigInput {
  display_name?: string
  active?: boolean
  credentials?: Record<string, string>
  settings?: Record<string, unknown>
}

// ── ChannelEndpoint — external address → pool mapping ────────────────────────

export type ChannelEndpointChannel = 'webchat' | 'whatsapp' | 'voice' | 'sms' | 'email' | 'webhook'

export interface ChannelEndpoint {
  id:                string
  tenant_id:         string
  channel:           ChannelEndpointChannel
  identifier:        string
  pool_id:           string
  display_name:      string
  settings:          Record<string, unknown>
  active:            boolean
  gateway_config_id: string | null   // FK to GatewayConfig; null = standalone
  created_at:        string
  updated_at:        string
}

export interface CreateChannelEndpointInput {
  channel:            ChannelEndpointChannel
  identifier:         string
  pool_id:            string
  display_name:       string
  settings?:          Record<string, unknown>
  active?:            boolean
  gateway_config_id?: string | null
}

export interface UpdateChannelEndpointInput {
  pool_id?:            string
  display_name?:       string
  settings?:           Record<string, unknown>
  active?:             boolean
  gateway_config_id?:  string | null
}

// ── Human Agent (AgentType framework=human) ───────────────────────────────────

// HumanAgentType — AgentType with framework=human (all base fields are already required)
// eslint-disable-next-line @typescript-eslint/no-empty-interface
export interface HumanAgentType extends AgentType {}

export interface CreateHumanAgentInput {
  agent_type_id: string
  role: string
  max_concurrent_sessions: number
  pools: string[]
  permissions?: string[]
}

export interface UpdateHumanAgentInput {
  max_concurrent_sessions?: number
  permissions?: string[]
}

export interface AgentInstance extends Instance {
  framework?: string
  role?: string
  agent_type?: {
    agent_type_id: string
    framework: string
    role?: string
  }
}

// ── Pricing / Billing ──────────────────────────────────────────────────────────

export interface InvoiceLineItem {
  resource_type: string
  label:         string
  quantity:      number
  unit_price:    number
  days_active:   number | null
  billing_days:  number
  subtotal:      number
}

export interface ReserveGroup {
  pool_id:      string
  label:        string
  active:       boolean
  days_active:  number
  billing_days: number
  items:        InvoiceLineItem[]
  subtotal:     number
}

export interface Invoice {
  tenant_id:       string
  installation_id: string
  cycle_start:     string
  cycle_end:       string
  billing_days:    number
  currency:        string
  base_items:      InvoiceLineItem[]
  reserve_groups:  ReserveGroup[]
  base_total:      number
  reserve_total:   number
  grand_total:     number
  generated_at:    string
}

export interface InstallationResource {
  id:              string
  tenant_id:       string
  installation_id: string
  resource_type:   string
  quantity:        number
  pool_type:       'base' | 'reserve'
  reserve_pool_id: string | null
  active:          boolean
  billing_unit:    string
  label:           string
  updated_at:      string
}

// ── Campaigns / Collect ────────────────────────────────────────────────────────

export interface CampaignSummary {
  campaign_id:       string
  total:             number
  responded:         number
  timed_out:         number
  sent:              number
  requested:         number
  response_rate_pct: number
  avg_elapsed_ms:    number
}

export interface CollectEvent {
  collect_token: string
  tenant_id:     string
  instance_id:   string | null
  flow_id:       string
  campaign_id:   string | null
  step_id:       string
  target_type:   string
  channel:       string
  interaction:   string
  status:        string
  send_at:       string | null
  responded_at:  string | null
  elapsed_ms:    number | null
  timestamp:     string
}

// ── Evaluation (Arc 6) ─────────────────────────────────────────────────────────

export interface EvaluationCriterion {
  criterion_id:  string
  label:         string
  description:   string
  weight:        number
  allows_na:     boolean
  applies_when?: string | null
  max_score:     number
  instructions?: string | null
}

export interface EvaluationDimension {
  dimension_id: string
  label:        string
  weight:       number
  criteria:     EvaluationCriterion[]
}

export interface EvaluationForm {
  form_id:         string
  tenant_id:       string
  name:            string
  description:     string
  status:          'active' | 'archived'
  dimensions:      EvaluationDimension[]
  knowledge_namespace?: string | null
  created_at:      string
  updated_at:      string
}

export interface SamplingRules {
  mode?:           'all' | 'percentage' | 'fixed'
  rate?:           number
  every_n?:        number
  min_duration_s?: number
  agent_type_ids?: string[]
  pool_ids?:       string[]
  channels?:       string[]
  outcome_filter?: string[]
}

export interface ReviewerRules {
  auto_review?:      boolean
  score_threshold?:  number
  random_rate?:      number
  human_review?:     boolean
}

export interface ContestationRound {
  round_number:          number
  contestation_roles:    string[]
  review_roles:          string[]
  authority_level:       'supervisor' | 'manager' | 'director'
  review_deadline_hours: number
}

export interface ContestationPolicy {
  contestation_roles:    string[]
  max_rounds:            number
  review_deadline_hours: number
  auto_lock_on_timeout:  boolean
  rounds?:               ContestationRound[]
}

export interface EvaluationCampaign {
  campaign_id:              string
  tenant_id:                string
  form_id:                  string
  name:                     string
  description:              string
  status:                   'draft' | 'active' | 'paused' | 'closed'
  sampling_rules:           SamplingRules
  reviewer_rules:           ReviewerRules
  contestation_policy?:     ContestationPolicy
  review_workflow_skill_id?: string
  total_instances:          number
  completed:                number
  pending:                  number
  in_review:                number
  avg_score:                number | null
  created_at:               string
  updated_at:               string
}

export interface EvaluationInstance {
  instance_id:   string
  campaign_id:   string
  session_id:    string
  tenant_id:     string
  status:        'pending' | 'in_progress' | 'completed' | 'expired' | 'error'
  priority:      number
  session_meta:  Record<string, unknown>
  expires_at:    string | null
  claimed_by:    string | null
  created_at:    string
  updated_at:    string
}

export interface EvaluationCriterionResponse {
  criterion_id:   string
  value:          number | null
  na:             boolean
  na_reason:      string | null
  justification:  string
  evidence_refs?: number[]
}

export interface EvaluationResult {
  result_id:          string
  instance_id:        string
  session_id:         string
  tenant_id:          string
  evaluator_id:       string
  form_id:            string | null
  campaign_id:        string | null
  overall_score:      number
  overall_observation:string
  highlights:         string[]
  improvement_points: string[]
  compliance_flags:   string[]
  criterion_responses:EvaluationCriterionResponse[]
  eval_status:        'submitted' | 'approved' | 'adjusted_approved' | 'rejected' | 'contested'
  locked:             boolean
  created_at:         string
  updated_at:         string
}

export interface EvaluationContestation {
  contestation_id:  string
  result_id:        string
  tenant_id:        string
  contested_by:     string
  reason:           string
  status:           'open' | 'upheld' | 'dismissed'
  adjudicator:      string | null
  adjudication_note:string | null
  created_at:       string
  updated_at:       string
}

// Arc 6 v2 — Result with computed available_actions
export interface EvaluationResultWithActions extends EvaluationResult {
  workflow_instance_id?: string | null
  resume_token?:         string | null
  action_required?:      'review' | 'contestation' | null
  current_round:         number
  deadline_at?:          string | null
  lock_reason?:          string | null
  available_actions:     ('review' | 'contest')[]
  action_context?: {
    deadline_at:     string
    round:           number
    authority_level: string
  } | null
}

// ── Access Control / Users (Arc 7) ────────────────────────────────────────────

export interface PlatformUser {
  id:               string
  tenant_id:        string
  email:            string
  name:             string
  roles:            string[]
  accessible_pools: string[]   // [] = all pools
  module_config?:   ModuleConfig
  active:           boolean
  created_at:       string
  updated_at:       string
}

export interface CreateUserInput {
  tenant_id:        string
  email:            string
  name:             string
  password:         string
  roles:            string[]
  accessible_pools?: string[]
}

export interface UpdateUserInput {
  name?:             string
  password?:         string
  roles?:            string[]
  accessible_pools?: string[]
  active?:           boolean
}

export interface KnowledgeSnippet {
  snippet_id:  string
  tenant_id:   string
  namespace:   string
  content:     string
  source_ref?: string | null
  metadata?:   Record<string, unknown>
  score?:      number
  created_at:  string
  updated_at:  string
}

export interface CampaignReport {
  campaign_id:     string
  name:            string
  form_id:         string
  total:           number
  completed:       number
  pending:         number
  in_review:       number
  expired:         number
  completion_pct:  number
  avg_score:       number | null
  score_p25:       number | null
  score_p75:       number | null
  top_flags:       string[]
  generated_at:    string
}

export interface AgentEvaluationReport {
  agent_type_id:      string
  pool_id:            string
  total_sessions:     number
  evaluated:          number
  avg_score:          number | null
  score_trend:        { date: string; avg_score: number }[]
  top_improvement:    string[]
  compliance_flags:   string[]
}

// ─── Timeseries ───────────────────────────────────────────────────────────────

export interface TimeseriesBreakdown {
  label: string
  value: number
}

export interface TimeseriesBucket {
  bucket:    string   // ISO8601
  value:     number
  breakdown: TimeseriesBreakdown[]
}

export interface TimeseriesMeta {
  interval_minutes: number
  from_dt:          string
  to_dt:            string
  total:            number
}

export interface TimeseriesResponse {
  buckets: TimeseriesBucket[]
  meta:    TimeseriesMeta
  error?:  string
}

// ─── Dashboard templates ───────────────────────────────────────────────────────

export type DashboardCardType =
  | 'timeseries_volume'
  | 'timeseries_handle_time'
  | 'timeseries_score'
  | 'kpi_sessions'
  | 'kpi_score'
  | 'pool_status'

export interface TimeseriesCardConfig {
  url:          string    // analytics-api path, e.g. "/reports/timeseries/volume"
  title:        string
  valueLabel:   string
  /** How to visualise the data. Stored per-card in the dashboard template. */
  displayType:  'bar' | 'line' | 'area' | 'pie' | 'table' | 'tile'
  interval?:    number
  breakdownBy?: string
  poolId?:      string
}

export interface KpiCardConfig {
  title:     string
  metricKey: string    // key from /dashboard/metrics response
  format:    'number' | 'duration_ms' | 'score'
  icon?:     string
}

export interface PoolStatusCardConfig {
  title:   string
  poolId?: string    // undefined = all pools summary
}

export type DashboardCardConfig = TimeseriesCardConfig | KpiCardConfig | PoolStatusCardConfig

export interface DashboardCard {
  id:     string
  // react-grid-layout position + size (grid units)
  x:      number
  y:      number
  w:      number
  h:      number
  type:   DashboardCardType
  config: DashboardCardConfig
}

export interface DashboardTemplate {
  template_id:  string
  tenant_id:    string
  name:         string
  description?: string
  /** Cards: legacy (type+config) or new-format (tool_id+query+tool_config). */
  cards:        (DashboardCard | import('@/dashboard/tools/types').NewDashboardCard)[]
  /** Runtime filter declarations — added in Part 3 (default: []). */
  global_filters?: import('@/dashboard/tools/types').GlobalFilter[]
  created_by:   string
  created_at:   string
  updated_at?:  string
}

// ─── New card schema (tool_id + query + tool_config) ──────────────────────────
// Re-exported from dashboard/tools/types for convenience.
export type {
  NewDashboardCard,
  CardQuery,
  QueryParam,
  FixedQueryParam,
  RuntimeQueryParam,
  GlobalFilter,
  DisplayToolDataShape,
} from '@/dashboard/tools/types'
