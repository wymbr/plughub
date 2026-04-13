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
