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
  /** "all" = normal message; "agents_only" = internal note invisible to customer;
   *  string[] = array of participant_ids (targeted visibility — treated as internal) */
  visibility?: "all" | "agents_only" | string | string[];
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
  /** Field IDs whose values must be masked (rendered as ●●●●●● in echoes). */
  masked_fields?: string[] | null;
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
 *
 * targetsSelf: true when the menu's visibility targets the current human agent
 * (array visibility or agents_only). In this case the agent IS the respondent —
 * MenuCard renders in interactive mode automatically, no manual toggle needed.
 */
export interface ChatMenuData {
  menu_id:       string;
  interaction:   "text" | "button" | "list" | "checklist" | "form";
  prompt:        string;
  options?:      MenuOption[];
  fields?:       MenuField[];
  targetsSelf?:  boolean;
  /** Field IDs that are masked (type="password" in webchat). Used to redact values
   *  in the stream echo so the operator Console never displays raw masked input. */
  masked_fields?: string[];
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
  | { type: "session.agent_done"; reason?: string }
  | { type: "supervisor_state.updated" }
  | { type: "copilot.updated"; session_id: string }
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
  /**
   * PII masking metadata — set by supervisor_state when the caller's role cannot
   * see the original value. `masked: true` means `value` is already the display_partial.
   */
  pii?:      boolean;
  masked?:   boolean;
  category?: string;
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
  /** Arc 11 — AI participants active in this session, with Skill-Flow step state. */
  ai_participants?: AiParticipantInfo[];
  /** Arc 11 Fase D — Skill-Flow step transition history from pipeline_state.transitions[] */
  pipeline_transitions?: PipelineTransition[];
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

// ── Pool presence ─────────────────────────────────────────────────────────────

/** Status of a single pool WebSocket connection. */
export type PoolConnectionStatus = "connecting" | "connected" | "disconnected";

/** Agent's presence state in a pool. */
export type PoolPresenceStatus = "ready" | "offline";

/** A pool available for the agent to join. */
export interface PoolInfo {
  pool_id:              string;
  display_name?:        string;
  channel_types:        string[];
  sla_target_ms:        number | null;
  /** Maximum time to reply to a customer message (ms). Null = no per-message SLA. */
  max_reply_time_ms:    number | null;
}

// ── Multi-contact session state ───────────────────────────────────────────────

/**
 * State for a single active contact session.
 * The App manages a Map<sessionId, ContactSession>.
 * The concept of "selected" contact lives only in App — not in the server.
 */
/**
 * Response timer state machine.
 *
 * counting — agent has an open obligation; live orange/red counter ticking from startedAt.
 * frozen   — agent replied; green display showing how long the reply took (elapsedMs).
 *
 * Transitions:
 *   Assignment created          → counting(now)
 *   Customer msg + counting     → no change  (keep counting from original start)
 *   Customer msg + frozen       → counting(now)   [reset and restart]
 *   Agent reply  + counting     → frozen(now - startedAt)
 *   Agent reply  + frozen       → no change
 */
export type ResponseTimer =
  | { status: 'counting'; startedAt: number }
  | { status: 'frozen';   elapsedMs: number };

export interface ContactSession {
  sessionId:        string;
  contactId:        string | null;
  /** Display name resolved from contact metadata, or null if not yet known. */
  customerName:     string | null;
  channel:          string;           // "webchat" | "whatsapp" | "voice" | …
  /** Pool this contact was assigned through — from conversation.assigned.pool_id */
  poolId:           string;
  /** SLA target in ms for this contact — from pool config or supervisorState */
  slaTargetMs:      number | null;
  /** Maximum reply time per customer message (ms). Null = no per-message SLA configured. */
  maxReplyTimeMs:   number | null;
  messages:         ChatMessage[];
  supervisorState:  SupervisorState | null;
  capabilities:     SupervisorCapabilities | null;
  sessionStartedAt: Date;
  /** Count of messages received while this contact is not the selected one. */
  unreadCount:      number;
  /** true after session.closed arrives — contact is visually locked until agent submits outcome */
  sessionClosed:    boolean;
  pendingCloseModal: boolean;
  /**
   * Response timer — tracks whether the agent still owes the customer a reply.
   * Starts in `counting` on assignment (agent is obligated to initiate regardless
   * of who sent the first message). See ResponseTimer for full transition table.
   */
  responseTimer: ResponseTimer;
}

// ── App state ─────────────────────────────────────────────────────────────────

// ── Arc 11 — AI Participant state ─────────────────────────────────────────────

export interface AiState {
  /** Current Skill-Flow step ID, or null if not running a flow. */
  current_step:  string | null;
  /** Inferred step type: "reason" | "invoke" | "task" | "menu" | "receive" | "suspend" | "unknown" */
  step_type:     string;
  /** Execution status derived from pipeline_state + waiting hashes. */
  step_status:   "running" | "waiting" | "done" | "error";
  /** What the step is waiting for, e.g. "menu" | "receive" | "approval" */
  waiting_for:   string | null;
  /** Milliseconds elapsed in the current step. */
  since_ms:      number;
}

export interface AiParticipantInfo {
  instance_id:   string;
  agent_type_id: string;
  pool_id:       string;
  role:          string;   // "primary" | "specialist" | "supervisor"
  segment_id:    string;
  joined_at:     string;
  ai_state:      AiState;
}

/** Arc 11 Fase D — a single Skill-Flow step transition logged in pipeline_state.transitions[] */
export interface PipelineTransition {
  from_step:  string | null;
  to_step:    string;
  reason?:    string;
  timestamp:  string;
}

/** Right panel tabs: Ações · Contexto · Histórico (AcoesTab replaces AgentesTab) */
export type ActiveTab = "acoes" | "contexto" | "historico";

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

// ── Co-pilot Phase 2 ─────────────────────────────────────────────────────────

/**
 * Co-pilot suggestions written by AI Gateway (copilot_emitter.py)
 * and read from ContextStore (session.copilot.*) via GET /copilot_state/:sessionId.
 * Refreshed after each customer message; displayed in the Capacidades tab.
 */
export interface CopilotSuggestions {
  /** Concise suggested response the agent can adapt */
  sugestao_resposta: string | null;
  /** Risk flags detected from the customer message (e.g. "intencao_cancelamento") */
  flags_risco: string[];
  acoes_recomendadas: string[];
  /** ISO-8601 timestamp of the last analysis */
  last_analysis: string | null;
}

// ── Mentionable agent (Arc 11 Fase B) ────────────────────────────────────────

export interface MentionableAgent {
  /** The @mention alias (key in mentionable_pools, e.g. "copilot", "auth", "auth_form").
   *  Always use this for @mention commands — NOT agent_type_id. */
  alias:         string;
  agent_type_id: string;
  pool_id:       string;
  description?:  string;
}

// ── Delegation input schema — typed fields for AcaoItemRow / DelegarTarefaDrawer

/** A single field in a delegation_input / delegation_params schema. */
export interface DelegationField {
  /** Field key, used as the serialization label. */
  id:           string;
  /** Human-readable label shown above the field. */
  label:        string;
  /** Renders as <select>, <input type="text">, or <input type="number">. */
  type:         "select" | "text" | "number";
  placeholder?: string;
  required?:    boolean;
  /** Only for type === "select". */
  options?:     Array<{ value: string; label: string }>;
}

/**
 * Typed parameter schema declared in a skill YAML (delegation_input / delegation_params).
 * When present, renders typed fields instead of free-text textarea.
 *
 * delegation_visibility:
 *   "agents_only" | "all" → locked at YAML level; visibility radio hidden
 *   null                  → show radio with default "agents_only"
 */
export interface DelegationSchema {
  fields:               DelegationField[];
  /** Visibility lock declared at agent-type (capabilities) or skill (flow) YAML level. */
  delegation_visibility?: "all" | "agents_only" | null;
}
