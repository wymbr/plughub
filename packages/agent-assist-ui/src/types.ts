// ── Connection status ─────────────────────────────────────────────────────────

export type WsStatus = "connecting" | "connected" | "disconnected";

// ── Chat messages ─────────────────────────────────────────────────────────────

export type AuthorType = "customer" | "agent_human" | "agent_ai" | "system";

export interface ChatMessage {
  id: string;
  author: AuthorType;
  text: string;
  timestamp: string;
  /** "all" = normal message; "agents_only" = internal note invisible to customer */
  visibility?: "all" | "agents_only" | string;
  /** Present for menu.render events — triggers rich MenuCard rendering */
  menuData?: ChatMenuData;
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
  visibility?: string;
}

export interface WsMenuRender {
  type: "menu.render";
  menu_id: string;
  interaction: "text" | "button" | "list" | "checklist" | "form";
  prompt: string;
  options?: Array<{ id: string; label: string }>;
  fields?: Array<{ id: string; label: string; type: string }>;
}

// ── Menu card data (attached to ChatMessage for rich rendering) ───────────────

export interface MenuOption {
  id:    string;
  label: string;
}

export interface MenuField {
  id:    string;
  label: string;
  type:  string;
}

/**
 * Structured representation of a menu.render event embedded in a ChatMessage.
 * Observation mode only — no submission capability yet (future: substitution mode).
 */
export interface ChatMenuData {
  menu_id:     string;
  interaction: "text" | "button" | "list" | "checklist" | "form";
  prompt:      string;
  options?:    MenuOption[];
  fields?:     MenuField[];
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
  | { type: "supervisor_state.updated" }
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

// ── Multi-contact session state ───────────────────────────────────────────────

/**
 * State for a single active contact session.
 * The App manages a Map<sessionId, ContactSession>.
 * The concept of "selected" contact lives only in App — not in the server.
 */
export interface ContactSession {
  sessionId:        string;
  contactId:        string | null;
  /** Display name resolved from contact metadata, or null if not yet known. */
  customerName:     string | null;
  channel:          string;           // "webchat" | "whatsapp" | "voice" | …
  messages:         ChatMessage[];
  supervisorState:  SupervisorState | null;
  capabilities:     SupervisorCapabilities | null;
  sessionStartedAt: Date;
  /** Count of messages received while this contact is not the selected one. */
  unreadCount:      number;
  /** true after session.closed arrives — contact is visually locked until agent submits outcome */
  sessionClosed:    boolean;
  pendingCloseModal: boolean;
}

// ── App state ─────────────────────────────────────────────────────────────────

export type ActiveTab = "estado" | "capacidades" | "contexto" | "historico";

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

// ── Customer contact history ──────────────────────────────────────────────────

export interface ContactHistoryEntry {
  session_id:   string;
  channel:      string;
  pool_id:      string;
  opened_at:    string | null;
  closed_at:    string | null;
  duration_ms:  number | null;
  outcome:      string | null;
  close_reason: string | null;
}

// ── Close modal ───────────────────────────────────────────────────────────────

export interface ClosePayload {
  issue_status: string;
  outcome: "resolved" | "escalated" | "abandoned";
  handoff_reason?: string;
}
