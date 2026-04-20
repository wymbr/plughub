/**
 * mcp-client.ts
 * Wrapper around @modelcontextprotocol/sdk Client with SSEClientTransport.
 * Provides typed helpers for Agent Runtime MCP tools.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";

export interface McpCallResult {
  data: unknown;
  isError: boolean;
  errorCode?: string;
}

export interface AgentLoginResult {
  session_token: string;
  instance_id: string;
  token_expires_at?: string;
}

export interface AgentReadyResult {
  status: string;
}

export interface AgentBusyResult {
  status: string;
  current_sessions: number;
}

export interface AgentDoneSuccess {
  acknowledged: boolean;
}

export interface AgentDoneError {
  error: string;
  isError: true;
}

export type AgentDoneResult = AgentDoneSuccess | AgentDoneError;

export interface IssueStatus {
  issue_id: string;
  description: string;
  status: "resolved" | "unresolved" | "transferred" | "pending_callback";
  resolved_at?: string;
}

export interface AgentDoneParams {
  session_token: string;
  conversation_id: string;
  outcome: "resolved" | "escalated_human" | "transferred_agent" | "callback";
  issue_status: IssueStatus[];
  handoff_reason?: string;
  resolution_summary?: string;
  completed_at?: string;
}

// ── v2 API types (session_id + participant_id) ─────────────────────────────────

export interface AgentBusyV2Result {
  status: string;
  session_id: string;
  participant_id: string;
  current_sessions: number;
}

export interface AgentDoneV2Params {
  session_token: string;
  session_id: string;
  participant_id: string;
  outcome: "resolved" | "transferred" | "abandoned" | "error";
  issue_status: string;
  handoff_reason?: string;
  completed_at?: string;
  /** Present when specialist ends — session stays open */
  conference_id?: string;
}

export interface AgentDoneV2Result {
  acknowledged: boolean;
  session_id: string;
  participant_id: string;
  outcome: string;
}

export interface JoinConferenceResult {
  session_id: string;
  conference_id: string;
  participant_id: string;
  agent_type_id: string;
  pool_id: string;
  interaction_model: string;
  status: string;
  joined_at: string;
}

export interface MessageSendResult {
  message_id: string;
  event_id: string;
  session_id: string;
  timestamp: string;
}

export interface SessionEscalateResult {
  escalated: boolean;
  session_id: string;
  event_id: string;
  target_pool: string;
  handoff_reason: string;
  timestamp: string;
}

export interface QueueContextResult {
  session_id: string;
  pool_id: string | null;
  position: number | null;
  estimated_wait_ms: number | null;
  queue_length: number | null;
  available_agents: number | null;
  snapshot_at: string;
}

export interface ConversationStartResult {
  session_id: string;
  contact_id: string;
  customer_id: string;
  channel: string;
  status: string;
  started_at: string;
}

export interface OutboundContactRequestResult {
  contact_id: string;
  status: string;
  channel: string;
  customer_id: string;
  tenant_id: string;
  requested_at: string;
}

export interface NotificationSendResult {
  delivered: boolean;
  session_id: string;
  contact_id: string;
  message_id: string;
  sent_at: string;
}

export interface SessionContextResult {
  session_id: string;
  tenant_id: string;
  status: string;
  channel: string;
  messages: unknown[];
  participants: unknown[];
}

export class McpTestClient {
  private readonly mcpServerUrl: string;
  private client: Client;
  private connected: boolean = false;

  constructor(mcpServerUrl: string) {
    this.mcpServerUrl = mcpServerUrl;
    this.client = new Client(
      { name: "e2e-test-client", version: "1.0.0" },
      { capabilities: {} }
    );
  }

  async connect(): Promise<void> {
    const sseUrl = new URL(`${this.mcpServerUrl}/sse`);
    const transport = new SSEClientTransport(sseUrl);
    await this.client.connect(transport);
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    if (this.connected) {
      await this.client.close();
      this.connected = false;
    }
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<McpCallResult> {
    const result = await this.client.callTool({ name, arguments: args });

    // MCP CallToolResult: content array + optional isError flag
    const isError = result.isError === true;
    let data: unknown = result.content;

    // Extract text content if present
    if (Array.isArray(result.content) && result.content.length > 0) {
      const firstContent = result.content[0];
      if (
        firstContent &&
        typeof firstContent === "object" &&
        "type" in firstContent &&
        firstContent.type === "text" &&
        "text" in firstContent
      ) {
        try {
          data = JSON.parse(firstContent.text as string);
        } catch {
          data = firstContent.text;
        }
      }
    }

    let errorCode: string | undefined;
    if (isError && data && typeof data === "object" && "code" in data) {
      errorCode = String((data as Record<string, unknown>).code);
    }

    return { data, isError, errorCode };
  }

  async agentLogin(
    tenantId: string,
    agentTypeId: string,
    instanceId: string
  ): Promise<AgentLoginResult> {
    const result = await this.callTool("agent_login", {
      agent_type_id: agentTypeId,
      instance_id: instanceId,
      tenant_id: tenantId,
    });

    if (result.isError) {
      throw new Error(`agent_login failed: ${JSON.stringify(result.data)}`);
    }

    const data = result.data as AgentLoginResult;
    return {
      session_token: data.session_token,
      instance_id: data.instance_id,
      token_expires_at: data.token_expires_at,
    };
  }

  async agentReady(sessionToken: string): Promise<AgentReadyResult> {
    const result = await this.callTool("agent_ready", {
      session_token: sessionToken,
    });

    if (result.isError) {
      throw new Error(`agent_ready failed: ${JSON.stringify(result.data)}`);
    }

    return result.data as AgentReadyResult;
  }

  async agentBusy(
    sessionToken: string,
    conversationId: string
  ): Promise<AgentBusyResult> {
    const result = await this.callTool("agent_busy", {
      session_token: sessionToken,
      conversation_id: conversationId,
    });

    if (result.isError) {
      throw new Error(`agent_busy failed: ${JSON.stringify(result.data)}`);
    }

    return result.data as AgentBusyResult;
  }

  // ── v2 API methods ───────────────────────────────────────────────────────────

  async agentBusyV2(
    sessionToken: string,
    sessionId: string,
    participantId: string
  ): Promise<AgentBusyV2Result> {
    const result = await this.callTool("agent_busy", {
      session_token:  sessionToken,
      session_id:     sessionId,
      participant_id: participantId,
    });

    if (result.isError) {
      throw new Error(`agent_busy (v2) failed: ${JSON.stringify(result.data)}`);
    }

    return result.data as AgentBusyV2Result;
  }

  async agentDoneV2(params: AgentDoneV2Params): Promise<AgentDoneV2Result | AgentDoneError> {
    const args: Record<string, unknown> = {
      session_token:  params.session_token,
      session_id:     params.session_id,
      participant_id: params.participant_id,
      outcome:        params.outcome,
      issue_status:   params.issue_status,
    };

    if (params.handoff_reason !== undefined) args.handoff_reason = params.handoff_reason;
    if (params.completed_at   !== undefined) args.completed_at   = params.completed_at;
    if (params.conference_id  !== undefined) args.conference_id  = params.conference_id;

    const result = await this.callTool("agent_done", args);

    if (result.isError) {
      const errData = result.data as Record<string, unknown>;
      return {
        error:   String(errData?.message ?? errData?.error ?? JSON.stringify(result.data)),
        isError: true,
      };
    }

    return result.data as AgentDoneV2Result;
  }

  async agentJoinConference(
    sessionId: string,
    agentTypeId: string,
    poolId: string,
    interactionModel: "background" | "conference",
    channelIdentity?: { text?: string; voice_profile?: string }
  ): Promise<JoinConferenceResult | AgentDoneError> {
    const args: Record<string, unknown> = {
      session_id:        sessionId,
      agent_type_id:     agentTypeId,
      pool_id:           poolId,
      interaction_model: interactionModel,
    };

    if (channelIdentity !== undefined) args.channel_identity = channelIdentity;

    const result = await this.callTool("agent_join_conference", args);

    if (result.isError) {
      const errData = result.data as Record<string, unknown>;
      return {
        error:   String(errData?.message ?? errData?.error ?? JSON.stringify(result.data)),
        isError: true,
      };
    }

    // agent_join_conference may return an error payload (non-MCP) when session not found
    const data = result.data as Record<string, unknown>;
    if (data && "error" in data && !("conference_id" in data)) {
      return { error: String(data["message"] ?? data["error"]), isError: true };
    }

    return result.data as JoinConferenceResult;
  }

  /**
   * Reconnect: dispose the current transport and create a fresh connection.
   * Simulates an mcp-server restart — all in-memory transport state is lost
   * but Redis state (agent instance, sessions) persists.
   */
  async reconnect(): Promise<void> {
    await this.disconnect();
    this.client = new Client(
      { name: "e2e-test-client", version: "1.0.0" },
      { capabilities: {} }
    );
    await this.connect();
  }

  // ── Session tools ────────────────────────────────────────────────────────────

  async sessionContextGet(
    sessionToken: string,
    sessionId: string,
    participantId: string
  ): Promise<SessionContextResult | AgentDoneError> {
    const result = await this.callTool("session_context_get", {
      session_token:  sessionToken,
      session_id:     sessionId,
      participant_id: participantId,
    });
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as SessionContextResult;
  }

  async messageSend(
    sessionToken: string,
    sessionId: string,
    participantId: string,
    content: { type: string; text: string },
    visibility: string | string[] = "all"
  ): Promise<MessageSendResult | AgentDoneError> {
    const result = await this.callTool("message_send", {
      session_token:  sessionToken,
      session_id:     sessionId,
      participant_id: participantId,
      content,
      visibility,
    });
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as MessageSendResult;
  }

  async sessionEscalate(
    sessionToken: string,
    sessionId: string,
    participantId: string,
    targetPool: string,
    handoffReason: string,
    pipelineState?: Record<string, unknown>
  ): Promise<SessionEscalateResult | AgentDoneError> {
    const args: Record<string, unknown> = {
      session_token:  sessionToken,
      session_id:     sessionId,
      participant_id: participantId,
      target_pool:    targetPool,
      handoff_reason: handoffReason,
    };
    if (pipelineState !== undefined) args.pipeline_state = pipelineState;
    const result = await this.callTool("session_escalate", args);
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as SessionEscalateResult;
  }

  // ── BPM tools ─────────────────────────────────────────────────────────────────

  async conversationStart(params: {
    channel: string;
    customer_id: string;
    tenant_id: string;
    intent?: string;
  }): Promise<ConversationStartResult | AgentDoneError> {
    const result = await this.callTool("conversation_start", params as Record<string, unknown>);
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as ConversationStartResult;
  }

  async outboundContactRequest(params: {
    tenant_id: string;
    customer_id: string;
    channel: string;
    agent_type_id?: string;
    pool_id?: string;
    metadata?: Record<string, unknown>;
  }): Promise<OutboundContactRequestResult | AgentDoneError> {
    const result = await this.callTool("outbound_contact_request", params as Record<string, unknown>);
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as OutboundContactRequestResult;
  }

  async notificationSend(
    sessionId: string,
    message: string,
    channel: string = "session"
  ): Promise<NotificationSendResult | AgentDoneError> {
    const result = await this.callTool("notification_send", {
      session_id: sessionId,
      message,
      channel,
    });
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as NotificationSendResult;
  }

  async queueContextGet(
    sessionId: string,
    tenantId: string,
    poolId?: string
  ): Promise<QueueContextResult | AgentDoneError> {
    const result = await this.callTool("queue_context_get", {
      session_id: sessionId,
      tenant_id:  tenantId,
      ...(poolId ? { pool_id: poolId } : {}),
    });
    if (result.isError) {
      return { error: String((result.data as Record<string, unknown>)?.message ?? result.data), isError: true };
    }
    return result.data as QueueContextResult;
  }

  async agentDone(params: AgentDoneParams): Promise<AgentDoneResult> {
    const args: Record<string, unknown> = {
      session_token: params.session_token,
      conversation_id: params.conversation_id,
      outcome: params.outcome,
      issue_status: params.issue_status,
    };

    if (params.handoff_reason !== undefined) {
      args.handoff_reason = params.handoff_reason;
    }
    if (params.resolution_summary !== undefined) {
      args.resolution_summary = params.resolution_summary;
    }
    if (params.completed_at !== undefined) {
      args.completed_at = params.completed_at;
    }

    const result = await this.callTool("agent_done", args);

    if (result.isError) {
      const errData = result.data as Record<string, unknown>;
      return {
        error: String(errData?.message ?? errData?.error ?? JSON.stringify(result.data)),
        isError: true,
      };
    }

    return result.data as AgentDoneSuccess;
  }
}
