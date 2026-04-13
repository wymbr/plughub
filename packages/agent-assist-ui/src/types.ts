// ── Chat messages ─────────────────────────────────────────────────────────────

export type AuthorType = "customer" | "agent_human" | "agent_ai" | "system";

export interface ChatMessage {
  id: string;
  author: AuthorType;
  text: string;
  timestamp: string;
}

// ── WebSocket envelope types (from channel-gateway / mcp-server) ──────────────

export interface WsConnectionAccepted {
  type: "connection.accepted";
  contact_id: string;
  session_id: string;
}

export interface WsMessageText {
  type: "message.text";
  message_id: string;
  author: { type: AuthorType; id?: string; display_name?: string };
  text: string;
  timestamp: string;
}

export interface WsMenuRender {
  type: "menu.render";
  menu_id: string;
  interaction: "text" | "button" | "list" | "checklist" | "form";
  prompt: string;
  options?: Array<{ id: string; label: string }>;
  fields?: Array<{ id: string; label: string; type: string }>;
}

export interface WsAgentTyping {
  type: "agent.typing";
  author_type: string;
}

export interface WsSessionClosed {
  type: "session.closed";
  reason: string;
}

export interface WsConversationAssigned {
  type: "conversation.assigned";
  session_id: string;
  contact_id?: string;
  pool_id: string;
  instance_id?: string;
  agent_type_id?: string;
  assigned_at: string;
}

export type WsServerEvent =
  | WsConnectionAccepted
  | WsMessageText
  | WsMenuRender
  | WsAgentTyping
  | WsSessionClosed
  | WsConversationAssigned
  | { type: "ping" };

// ── supervisor_state response ─────────────────────────────────────────────────

export interface SentimentState {
  current: number;          // -1 to +1
  trajectory: number[];
  trend: "improving" | "stable" | "declining";
  alert: boolean;
}

export interface IntentState {
  current: string | null;
  confidence: number;
  history: string[];
}

export interface SlaState {
  elapsed_ms: number;
  target_ms: number;
  percentage: number;
  breach_imminent: boolean;
}

export interface InsightItem {
  content: string;
  confidence?: number;
  last_seen?: string;
  turn?: number;
}

export interface CustomerContext {
  historical_insights: InsightItem[];
  conversation_insights: InsightItem[];
}

export interface SupervisorState {
  session_id: string;
  turn_count: number;
  is_stale: boolean;
  sentiment: SentimentState;
  intent: IntentState;
  flags: string[];
  sla: SlaState;
  customer_context: CustomerContext;
  issue_status?: string;
}

// ── supervisor_capabilities response ─────────────────────────────────────────

export interface SuggestedAgent {
  agent_type_id: string;
  relevance: "high" | "medium" | "low";
  interaction_model: "background" | "conference";
  available_instances: number;
  auto_join: boolean;
  circuit_breaker: "closed" | "open" | "half_open";
  reason: string;
}

export interface EscalationSuggestion {
  pool_id: string;
  reason: string;
  estimated_wait_s: number;
  recommended: boolean;
}

export interface SupervisorCapabilities {
  suggested_agents: SuggestedAgent[];
  escalations: EscalationSuggestion[];
}

// ── App state ─────────────────────────────────────────────────────────────────

export type ActiveTab = "estado" | "capacidades" | "contexto";

export interface AppState {
  sessionId: string | null;
  contactId: string | null;
  agentName: string;
  poolId: string;
  messages: ChatMessage[];
  supervisorState: SupervisorState | null;
  supervisorCapabilities: SupervisorCapabilities | null;
  conferenceActive: boolean;
  wsStatus: "connecting" | "connected" | "disconnected";
  activeTab: ActiveTab;
  lastIntentForCapabilities: string | null;
  turnCountForCapabilities: number;
  toasts: Toast[];
}

export interface Toast {
  id: string;
  message: string;
  type: "info" | "warning" | "error";
  persistent: boolean;
}

// ── Close modal ───────────────────────────────────────────────────────────────

export interface ClosePayload {
  issue_status: string;
  outcome: "resolved" | "escalated" | "abandoned";
  handoff_reason?: string;
}
