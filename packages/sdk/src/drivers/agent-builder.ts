/**
 * agent-builder.ts
 * AgentBuilderDriver — portabilidade para Google Agent Builder (Vertex AI).
 * Spec 4.6d: driver para ambiente proprietário Google.
 */

import type { PlugHubDriver } from "../adapter"

export interface AgentBuilderDriverConfig {
  project:  string
  location: string
  agent_id: string
}

export class AgentBuilderDriver implements PlugHubDriver {
  readonly name = "agent-builder"
  readonly config: AgentBuilderDriverConfig

  constructor(config: AgentBuilderDriverConfig) {
    this.config = config
  }

  toExternal(adapterOutput: Record<string, unknown>): AgentBuilderInput {
    return {
      session: `projects/${this.config.project}/locations/${this.config.location}/agents/${this.config.agent_id}/sessions/${adapterOutput["session_id"] ?? crypto.randomUUID()}`,
      queryInput: {
        text: {
          text: adapterOutput["message"] as string ?? JSON.stringify(adapterOutput),
        },
        languageCode: "pt-BR",
      },
    }
  }

  fromExternal(externalOutput: unknown): Record<string, unknown> {
    const output = externalOutput as AgentBuilderOutput
    return {
      message:        output.queryResult?.responseMessages?.[0]?.text?.text?.[0] ?? "",
      intent:         output.queryResult?.match?.intent?.displayName,
      confidence:     output.queryResult?.match?.confidence,
    }
  }
}

interface AgentBuilderInput {
  session: string
  queryInput: {
    text: { text: string }
    languageCode: string
  }
}

interface AgentBuilderOutput {
  queryResult?: {
    responseMessages?: Array<{ text?: { text?: string[] } }>
    match?: {
      intent?: { displayName?: string }
      confidence?: number
    }
  }
}
