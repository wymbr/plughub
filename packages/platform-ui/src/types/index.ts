export type UserRole = 'operator' | 'supervisor' | 'admin' | 'developer' | 'business'

export interface Session {
  userId: string
  name: string
  role: UserRole
  tenantId: string
  installationId: string
  locale: 'pt-BR' | 'en'
}

export interface Pool {
  pool_id: string
  tenant_id: string
  description?: string
  channel_types: string[]
  sla_target_ms: number
  status: string
  created_at: string
  updated_at: string
}

export interface CreatePoolInput {
  pool_id: string
  description?: string
  channel_types: string[]
  sla_target_ms: number
}

export interface UpdatePoolInput {
  description?: string
  channel_types?: string[]
  sla_target_ms?: number
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

export interface EvaluationCampaign {
  campaign_id:      string
  tenant_id:        string
  form_id:          string
  name:             string
  description:      string
  status:           'draft' | 'active' | 'paused' | 'closed'
  sampling_rules:   SamplingRules
  reviewer_rules:   ReviewerRules
  total_instances:  number
  completed:        number
  pending:          number
  in_review:        number
  avg_score:        number | null
  created_at:       string
  updated_at:       string
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
