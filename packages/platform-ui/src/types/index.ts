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
