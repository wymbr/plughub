// ── API response types (mirrors dashboard API models) ─────────────────────────

export interface PoolSummary {
  pool_id: string;
  agent_count: number;
  evaluation_count: number;
  avg_score: number;
  p25_score: number;
  p75_score: number;
  last_evaluated_at: string | null;
}

export interface PoolListResponse {
  pools: PoolSummary[];
  total: number;
}

export interface AgentScoreTrend {
  date: string;
  avg_score: number;
  evaluation_count: number;
}

export interface SectionScore {
  section_id: string;
  score_type: string;
  avg_score: number;
  evaluation_count: number;
  triggered_by_key: string | null;
  triggered_by_val: string | null;
}

export interface AgentProfile {
  agent_id: string;
  agent_type: string;
  pool_id: string;
  evaluation_count: number;
  avg_score: number;
  trend: AgentScoreTrend[];
  section_scores: SectionScore[];
}

export interface AgentListResponse {
  agents: AgentProfile[];
  total: number;
  pool_id: string;
}

export interface EvalItemDetail {
  section_id: string;
  subsection_id: string;
  item_id: string;
  value: number;
  weight: number;
  justification: string | null;
}

export interface ContactEvaluation {
  evaluation_id: string;
  contact_id: string;
  agent_id: string;
  pool_id: string;
  skill_id: string;
  evaluated_at: string;
  overall_score: number;
  items: EvalItemDetail[];
}

export interface ContactListResponse {
  contacts: ContactEvaluation[];
  total: number;
  agent_id: string;
}

// ── Navigation state ───────────────────────────────────────────────────────────

export type Screen =
  | { type: "pools" }
  | { type: "agents"; poolId: string }
  | { type: "contacts"; agentId: string; poolId: string };
