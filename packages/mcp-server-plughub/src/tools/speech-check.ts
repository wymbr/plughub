/**
 * tools/speech-check.ts
 * VOZ-23 — pede UMA verificação ativa do caminho de fala ao processo `speech-check`.
 *
 *   speech_check_run — POST {SPEECH_CHECK_URL}/v1/speech-checks com `x-service-token`. Devolve o
 *                      `check_id` (a verificação roda em segundo plano e o resultado sai em
 *                      `speech.metrics`). Usada pelo skill do pool webhook de disparo manual.
 *
 * Não-2xx vira isError com o motivo do serviço — perfil inexistente (422), verificação em curso (409),
 * credencial ausente (503) —, para o `invoke` rotear `on_failure`: um pedido recusado nunca parece aceito.
 *
 * ⚠️ Como as demais tools que embrulham HTTP interno (`pool_promote`, `campaign_drain`), esta NÃO passa
 * por `withGuard` nem publica `mcp.audit` — classificada em `infra/test/probe_mcp_tool_guard_census.sh`.
 */

import { z }         from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"

export interface SpeechCheckDeps {
  speechCheckUrl: string   // e.g. http://speech-check:3870
  tenantId:       string
}

const SpeechCheckRunInputSchema = z.object({
  speech_profile_id: z.string().min(1).nullable().optional(),
  requested_by:      z.string().min(1).optional(),
  tenant_id:         z.string().optional(),
})

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string): ToolResult {
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }] }
}

export function registerSpeechCheckTools(server: McpServer, deps: SpeechCheckDeps): void {
  server.tool(
    "speech_check_run",
    "Start an active speech-path check (VOZ-23): one synthetic call through a temporary WebRTC " +
    "endpoint pointing to the calibration pool with the given speech profile (omit for the tenant " +
    "settings). Returns the check_id; the result is published to speech.metrics and compared to the " +
    "baseline in /reports/speech/checks/compare. Errors on unknown profile or a check already running.",
    SpeechCheckRunInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof SpeechCheckRunInputSchema>
      try {
        input = SpeechCheckRunInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }
      const token = process.env["SPEECH_CHECK_SERVICE_TOKEN"] ?? ""
      if (!token) {
        return mcpError("not_configured", "SPEECH_CHECK_SERVICE_TOKEN ausente no mcp-server — a verificacao NAO foi pedida")
      }
      try {
        const res = await fetch(`${deps.speechCheckUrl}/v1/speech-checks`, {
          method:  "POST",
          headers: { "Content-Type": "application/json", "x-service-token": token },
          body:    JSON.stringify({
            tenant_id:         input.tenant_id ?? deps.tenantId,
            speech_profile_id: input.speech_profile_id ?? null,
            requested_by:      input.requested_by ?? "mcp:speech_check_run",
          }),
        })
        const texto = await res.text()
        if (!res.ok) {
          return mcpError("speech_check_refused", `speech-check respondeu ${res.status}: ${texto}`)
        }
        return ok({ success: true, ...(JSON.parse(texto) as Record<string, unknown>) })
      } catch (e) {
        return mcpError("network_error", e instanceof Error ? e.message : String(e))
      }
    },
  )
}
