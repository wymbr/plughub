// ── Connection status ─────────────────────────────────────────────────────────

export type WsStatus = "connecting" | "connected" | "disconnected";

// ── Chat messages ─────────────────────────────────────────────────────────────

export type AuthorType = "customer" | "agent_human" | "agent_ai" | "system";

export interface ChatMessage {
  id: string;
  author: AuthorType;
  /** agent_type_id for AI agents (e.g. "agente_copilot_v1") — used for labeling and coloring */
  agentTypeId?: string;
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
  author: { type: AuthorType; id?: string; display_name?: string; agent_type_id?: string };
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

export interface WsMentionCommandAck {
  type:            "mention_command.ack";
  session_id:      string;
  command:         string;
  acknowledged_at: string;
}

export type WsServerEvent =
  | WsConnectionAccepted
  | WsMessageText
  | WsMenuRender
  | WsAgentTyping
  | WsSessionClosed
  | WsConversationAssigned
  | WsMentionCommandAck
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

// ── Contact Context (enriched by agente_contexto_ia_v1 before escalation) ────

export interface ContactContextField {
  value: string;
  confidence: number;
  source: string;
}

export interface ContactContextData {
  nome?:               ContactContextField;
  cpf?:                ContactContextField;
  account_id?:         ContactContextField;
  telefone?:           ContactContextField;
  email?:              ContactContextField;
  motivo_contato?:     ContactContextField;
  intencao_primaria?:  ContactContextField;
  sentimento_atual?:   ContactContextField;
  resumo_conversa?:    ContactContextField;
  completeness_score?: number;
}

// ── ContextStore entry — new unified format (Arc ContextStore) ─────────────

/**
 * A single ContextStore entry as returned by supervisor_state.customer_context.context_snapshot.
 * The flat map is keyed by tag name (e.g. "caller.nome", "session.sentimento.current").
 */
export interface ContextEntry {
  /** The stored value — string, number, boolean or structured object. */
  value:      unknown;
  confidence: number;
  /** Source component (e.g. "mcp_call:mcp-server-crm:customer_get", "ai_inferred:sentiment_emitter"). */
  source:     string;
  /** "agents_only" | "all" */
  visibility: string;
  updated_at: string;
}

export interface CustomerContext {
  historical_insights:  InsightItem[];
  conversation_insights: InsightItem[];
  /** Legacy structured contact context (pre-ContextStore). Present when context_snapshot absent. */
  contact_context?:     ContactContextData;
  /**
   * New flat ContextStore snapshot keyed by tag name (e.g. "caller.nome", "session.sentimento.current").
   * Supersedes contact_context when present.
   */
  context_snapshot?:    Record<string, ContextEntry>;
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
