// ─── Pool snapshot (from GET /dashboard/operational SSE) ─────────────────────

export interface PoolSnapshot {
  pool_id:       string
  tenant_id:     string
  available:     number          // agents ready
  queue_length:  number
  sla_target_ms: number
  channel_types: string[]
  updated_at:    string          // ISO8601
}

// ─── Sentiment live (from GET /dashboard/sentiment) ──────────────────────────

export interface SentimentEntry {
  pool_id:          string
  tenant_id:        string
  avg_score:        number       // -1.0 to 1.0
  count:            number       // sessions in window
  distribution: {
    satisfied:  number
    neutral:    number
    frustrated: number
    angry:      number
  }
  last_session_id:  string | null
  updated_at:       string | null
}

// ─── Merged pool view (snapshot + sentiment) ─────────────────────────────────

export interface PoolView {
  pool_id:       string
  tenant_id:     string
  available:     number
  queue_length:  number
  sla_target_ms: number
  channel_types: string[]
  updated_at:    string
  // sentiment overlay (null when not yet received)
  avg_score:     number | null
  sentiment_count: number
  distribution:  SentimentEntry['distribution'] | null
}

// ─── 24h metrics (from GET /dashboard/metrics) ───────────────────────────────

export interface Metrics24h {
  period:    string
  tenant_id: string
  sessions: {
    total:          number
    avg_handle_ms:  number | null
    by_channel:     Record<string, number>
    by_outcome:     Record<string, number>
    by_close_reason: Record<string, number>
  }
  agent_events: {
    total_routed: number
    total_done:   number
    by_outcome:   Record<string, number>
  }
  usage: {
    by_dimension: Record<string, number>
  }
  sentiment: {
    avg_score:    number | null
    sample_count: number
    by_category:  Record<string, number>
  }
}

// ─── Active session (from GET /sessions/active) ───────────────────────────────

export interface ActiveSession {
  session_id:      string
  channel:         string
  opened_at:       string        // ISO8601
  handle_time_ms:  number | null // running handle time
  wait_time_ms:    number | null
  latest_score:    number | null // from Redis sentiment overlay
  latest_category: string | null // satisfied | neutral | frustrated | angry
}

// ─── Stream entry (from GET /sessions/{id}/stream SSE) ────────────────────────

export interface StreamEntry {
  entry_id:    string
  type:        string            // session_opened | message | flow_step_completed | …
  timestamp:   string | null
  author_id:   string | null
  author_role: string | null
  visibility:  string            // all | agents_only | [list]
  content:     unknown           // parsed JSON or string
  payload:     unknown           // parsed JSON or string
}

// ─── App state ────────────────────────────────────────────────────────────────

export type ConnectionStatus = 'connecting' | 'connected' | 'error' | 'closed'

export interface AppConfig {
  tenantId:   string
  apiBaseUrl: string
}

// ─── Supervisor intervention state ────────────────────────────────────────────

export type SupervisorStatus = 'idle' | 'joining' | 'active' | 'leaving' | 'error'

export interface SupervisorState {
  status:        SupervisorStatus
  participantId: string | null    // returned by POST /supervisor/join
  joinedAt:      string | null    // ISO8601
  error:         string | null
}

// ─── Workflow instance (from workflow-api) ────────────────────────────────────

export type WorkflowStatus = 'active' | 'suspended' | 'completed' | 'failed' | 'timed_out' | 'cancelled'
export type SuspendReason = 'approval' | 'input' | 'webhook' | 'timer'

export interface WorkflowInstance {
  id: string
  installation_id: string
  organization_id: string
  tenant_id: string
  flow_id: string
  session_id?: string
  pool_id?: string
  campaign_id?: string
  status: WorkflowStatus
  current_step?: string
  pipeline_state: Record<string, unknown>
  suspend_reason?: SuspendReason
  resume_token?: string
  resume_expires_at?: string
  suspended_at?: string
  resumed_at?: string
  completed_at?: string
  outcome?: string
  created_at: string
  metadata: Record<string, unknown>
}

// ─── Campaign / Collect (from analytics-api) ─────────────────────────────────

export type CollectStatus = 'requested' | 'sent' | 'responded' | 'timed_out'

export interface CollectEvent {
  collect_token: string
  tenant_id:     string
  instance_id:   string
  flow_id:       string
  campaign_id:   string | null
  step_id:       string
  target_type:   string
  channel:       string
  interaction:   string
  status:        CollectStatus
  send_at:       string | null
  responded_at:  string | null
  elapsed_ms:    number | null
  timestamp:     string
}

export interface CampaignSummary {
  campaign_id:       string
  total:             number
  responded:         number
  timed_out:         number
  sent:              number
  requested:         number
  response_rate_pct: number
  avg_elapsed_ms:    number | null
}

// ─── Webhook (from GET /v1/workflow/webhooks) ─────────────────────────────────

export interface Webhook {
  id:                string
  tenant_id:         string
  flow_id:           string
  description:       string
  token_prefix:      string       // first 16 chars of plain token, for display only
  active:            boolean
  trigger_count:     number
  last_triggered_at: string | null
  context_override:  Record<string, unknown>
  created_at:        string
  updated_at:        string
}

export interface WebhookDelivery {
  id:           string
  webhook_id:   string
  tenant_id:    string
  triggered_at: string
  status_code:  number
  payload_hash: string
  instance_id:  string | null
  error:        string | null
  latency_ms:   number | null
}

// ─── Registry (from GET /v1/pools, /v1/agent-types, /v1/skills) ──────────────

export interface RegistryPool {
  pool_id:         string
  tenant_id:       string
  description:     string | null
  channel_types:   string[]
  sla_target_ms:   number
  status:          'active' | 'inactive'
  created_at:      string
  updated_at:      string
  mentionable_pools?: Record<string, string>
  hooks?: {
    on_human_start?: Array<{ pool: string }>
    on_human_end?:   Array<{ pool: string }>
    post_human?:     Array<{ pool: string }>
  }
}

export interface RegistryAgentType {
  agent_type_id:           string
  tenant_id:               string
  framework:               string
  execution_model:         'stateless' | 'stateful'
  role:                    'executor' | 'orchestrator' | 'evaluator'
  max_concurrent_sessions: number
  pools:                   Array<{ pool_id: string }>
  skills:                  Array<{ skill_id: string; version_policy: string }>
  permissions:             string[]
  capabilities:            Record<string, string>
  prompt_id:               string | null
  traffic_weight:          number
  status:                  'active' | 'deprecated'
  created_at:              string
  updated_at:              string
}

export interface RegistrySkill {
  skill_id:    string
  tenant_id:   string
  name:        string
  version:     string
  description: string
  classification: {
    type:      'vertical' | 'horizontal' | 'orchestrator'
    vertical?: string
    domain?:   string
  }
  tools:       Array<{ name: string; server: string }>
  knowledge_domains: string[]
  status:      'active' | 'inactive'
  created_at:  string
  updated_at:  string
}

export interface RegistryInstance {
  instance_id:    string
  tenant_id:      string
  agent_type_id:  string
  pool_id:        string
  status:         string
  channel_types:  string[]
  created_at:     string
  updated_at:     string
}

// ─── Channel Gateway Config (from GET /v1/channels) ──────────────────────────

export type ChannelType =
  | 'whatsapp' | 'webchat' | 'voice' | 'email'
  | 'sms' | 'instagram' | 'telegram' | 'webrtc'

export interface GatewayConfig {
  id:           string
  tenant_id:    string
  channel:      ChannelType
  display_name: string
  active:       boolean
  credentials:  Record<string, string>   // values are masked on read (••••••)
  settings:     Record<string, unknown>
  created_at:   string
  updated_at:   string
  created_by:   string
}

