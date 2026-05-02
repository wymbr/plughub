/**
 * bedrock.ts
 * BedrockDriver — portabilidade para AWS Bedrock Agents.
 * Spec 4.6d: driver para ambiente proprietário AWS.
 *
 * Traduz entre o formato do PlugHubAdapter e a API de invocação do Bedrock.
 * O agente Bedrock roda no ambiente AWS — o driver é o cliente HTTP.
 */

import type { PlugHubDriver } from "../adapter"

export interface BedrockDriverConfig {
  agent_id: string
  agent_alias_id?: string
  region: string
}

export class BedrockDriver implements PlugHubDriver {
  readonly name = "bedrock"
  readonly config: BedrockDriverConfig

  constructor(config: BedrockDriverConfig) {
    this.config = config
  }

  toExternal(adapterOutput: Record<string, unknown>): BedrockInvokeInput {
    return {
      agentId:        this.config.agent_id,
      agentAliasId:   this.config.agent_alias_id ?? "TSTALIASID",
      sessionId:      adapterOutput["session_id"] as string ?? crypto.randomUUID(),
      inputText:      adapterOutput["message"] as string ?? JSON.stringify(adapterOutput),
      sessionState:   adapterOutput["session_state"] as Record<string, unknown> | undefined,
    }
  }

  fromExternal(externalOutput: unknown): Record<string, unknown> {
    const output = externalOutput as BedrockInvokeOutput
    return {
      message:        output.completion ?? "",
      session_state:  output.sessionState,
      stop_reason:    output.stopReason,
    }
  }
}

// Tipos mínimos da API Bedrock (sem dependência do AWS SDK)
interface BedrockInvokeInput {
  agentId:       string
  agentAliasId:  string
  sessionId:     string
  inputText:     string
  sessionState?: Record<string, unknown>
}

interface BedrockInvokeOutput {
  completion?:   string
  sessionState?: Record<string, unknown>
  stopReason?:   string
}
