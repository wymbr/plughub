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
