// ─── Pool snapshot (from GET /dashboard/operational SSE) ─────────────────────

export interface PoolSnapshot {
  pool_id:       string
  tenant_id:     string
  available:     number
  queue_length:  number
  sla_target_ms: number
  channel_types: string[]
  updated_at:    string
}

// ─── Sentiment live (from GET /dashboard/sentiment) ──────────────────────────

export interface SentimentEntry {
  pool_id:          string
  tenant_id:        string
  avg_score:        number
  count:            number
  distribution: {
    satisfied:  number
    neutral:    number
    frustrated: number
    angry:      number
  }
  last_session_id:  string | null
  updated_at:       string | null
}

// ─── Merged pool view ─────────────────────────────────────────────────────────

export interface PoolView {
  pool_id:         string
  tenant_id:       string
  available:       number
  queue_length:    number
  sla_target_ms:   number
  channel_types:   string[]
  updated_at:      string
  avg_score:       number | null
  sentiment_count: number
  distribution:    SentimentEntry['distribution'] | null
}

// ─── 24h metrics ──────────────────────────────────────────────────────────────

export interface Metrics24h {
  period:    string
  tenant_id: string
  sessions: {
    total:           number
    avg_handle_ms:   number | null
    by_channel:      Record<string, number>
    by_outcome:      Record<string, number>
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

// ─── Active session ───────────────────────────────────────────────────────────

export interface ActiveSession {
  session_id:      string
  channel:         string
  opened_at:       string
  handle_time_ms:  number | null
  wait_time_ms:    number | null
  latest_score:    number | null
  latest_category: string | null
}

// ─── Stream entry ─────────────────────────────────────────────────────────────

export interface StreamEntry {
  entry_id:    string
  type:        string
  timestamp:   string | null
  author_id:   string | null
  author_role: string | null
  visibility:  string
  content:     unknown
  payload:     unknown
}

// ─── Contact segment (per-agent participation window within a session) ────────

export type SegmentRole    = "primary" | "specialist" | "supervisor" | "evaluator" | "reviewer"
export type SegmentOutcome = "resolved" | "escalated" | "transferred" | "abandoned" | "timeout"

export interface ContactSegment {
  segment_id:        string
  session_id:        string
  tenant_id:         string
  participant_id:    string
  pool_id:           string
  agent_type_id:     string
  instance_id:       string | null
  role:              SegmentRole
  agent_type:        "ai" | "human"
  parent_segment_id: string | null   // null for primary; specialist points to primary segment
  sequence_index:    number          // 0 = first primary; increments on handoffs
  started_at:        string          // ISO-8601
  ended_at:          string | null   // null = segment still active
  duration_ms:       number | null
  outcome:           SegmentOutcome | null
  close_reason:      string | null
  handoff_reason:    string | null
  issue_status:      string | null
}

// ─── Connection status ────────────────────────────────────────────────────────

export type ConnectionStatus = 'connecting' | 'connected' | 'error' | 'closed'

// ─── Supervisor ───────────────────────────────────────────────────────────────

export type SupervisorStatus = 'idle' | 'joining' | 'active' | 'leaving' | 'error'

export interface SupervisorState {
  status:        SupervisorStatus
  participantId: string | null
  joinedAt:      string | null
  error:         string | null
}
