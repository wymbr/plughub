/**
 * copilot.ts
 * CopilotDriver — portabilidade para Microsoft Copilot Studio.
 * Spec 4.6d: driver para ambiente proprietário Microsoft.
 *
 * Usa a Direct Line API — protocolo de atividades bidirecional.
 */

import type { PlugHubDriver } from "../adapter"

export interface CopilotDriverConfig {
  direct_line_secret: string
  bot_id:             string
}

export class CopilotDriver implements PlugHubDriver {
  readonly name = "copilot"
  readonly config: CopilotDriverConfig

  constructor(config: CopilotDriverConfig) {
    this.config = config
  }

  toExternal(adapterOutput: Record<string, unknown>): DirectLineActivity {
    return {
      type:      "message",
      from:      { id: adapterOutput["customer_id"] as string ?? "customer" },
      text:      adapterOutput["message"] as string ?? JSON.stringify(adapterOutput),
      channelData: { plughub_session_id: adapterOutput["session_id"] },
    }
  }

  fromExternal(externalOutput: unknown): Record<string, unknown> {
    const activity = externalOutput as DirectLineActivity
    return {
      message:     activity.text ?? "",
      attachments: activity.attachments,
      channel_data: activity.channelData,
    }
  }
}

interface DirectLineActivity {
  type:          string
  from?:         { id: string; name?: string }
  text?:         string
  attachments?:  unknown[]
  channelData?:  Record<string, unknown>
}
