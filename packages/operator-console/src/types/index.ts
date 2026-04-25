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
