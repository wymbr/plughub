/**
 * tools/survey.ts
 * F10 — Session Signals: tool MCP `survey_record`.
 *
 * Grava o resultado de uma pesquisa (NPS, CSAT ou métricas customizadas) feita
 * por uma survey OUTBOUND — uma sessão própria que religa à sessão original
 * via `origin_session_id` e grava o sinal CONTRA ELA. Os parâmetros
 * (origin_session_id, grain, signals) são estruturados de 1ª classe — sem a
 * checagem de namespace do Arc 12 `agent_event` nem convenção de category.
 *
 * Input (do agente de pesquisa):
 *   session_token     — JWT da sessão de survey (resolve tenant_id). Auth + audit.
 *   origin_session_id — sessão pesquisada (chave do sinal).
 *   grain             — segment | session | workflow | journey
 *   signals           — [{ metric, value, value_label? }, …] (≥1)
 *   segment_id        — obrigatório quando grain='segment' (qual segmento/agente)
 *   agent_key         — atribuição p/ grain='segment' (user_id | flow_id)
 *   survey_session_id — opcional (trace)
 *   pool_id           — opcional (contexto da sessão original)
 *
 * Intercepted by McpInterceptor — audited in mcp.audit (LGPD).
 * Publishes to Kafka topic: session.signals → analytics.session_signal.
 */

import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { SurveyRecordInputSchema } from "@plughub/schemas"
import type { KafkaProducer } from "../infra/kafka"
import {
  verifySessionToken,
  InvalidTokenError,
} from "../infra/jwt"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface SurveyDeps {
  kafka: KafkaProducer
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string): ToolResult {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }],
  }
}

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerSurveyTools(
  server: McpServer,
  deps:   SurveyDeps,
): void {
  const { kafka } = deps

  // ── survey_record ───────────────────────────────────────────────────────────
  server.tool(
    "survey_record",
    "Record a customer/agent survey result (NPS, CSAT or custom metrics) against " +
    "the ORIGINAL session being surveyed. Use from an outbound survey session: " +
    "pass origin_session_id (the surveyed session), grain " +
    "(segment|session|workflow|journey; segment_id required for segment) and " +
    "one or more signals. F10.",
    SurveyRecordInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = SurveyRecordInputSchema.parse(input)
        const {
          session_token,
          origin_session_id,
          grain,
          signals,
          segment_id,
          agent_key,
          survey_session_id,
          pool_id,
        } = parsed

        // grain='segment' exige segment_id (atribuição ao agente/segmento).
        if (grain === "segment" && !segment_id) {
          return mcpError(
            "validation_error",
            "segment_id is required when grain='segment'",
          )
        }

        // ── Decode JWT (resolve tenant) ─────────────────────────────────────
        let tenant_id: string
        try {
          tenant_id = verifySessionToken(session_token).tenant_id
        } catch (e) {
          if (e instanceof InvalidTokenError) {
            return mcpError("invalid_token", "session_token is invalid or expired")
          }
          throw e
        }

        const event_id    = crypto.randomUUID()
        const captured_at  = new Date().toISOString()

        const event = {
          event_id,
          tenant_id,
          origin_session_id,
          grain,
          segment_id:        segment_id ?? null,
          agent_key:         agent_key ?? "",
          survey_session_id: survey_session_id ?? null,
          pool_id:           pool_id ?? "",
          signals,
          captured_at,
        }

        try {
          await kafka.publish("session.signals", event)
        } catch (kafkaErr) {
          return mcpError(
            "publish_failed",
            `Failed to publish to session.signals: ${String(kafkaErr)}`,
          )
        }

        return ok({
          event_id,
          origin_session_id,
          grain,
          signals_recorded: signals.length,
          captured_at,
        })
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(er => er.message).join("; "))
        }
        if (e instanceof InvalidTokenError) {
          return mcpError("invalid_token", "session_token is invalid or expired")
        }
        return mcpError("internal_error", String(e))
      }
    },
  )
}
