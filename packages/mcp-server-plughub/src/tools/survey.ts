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
 * Input (do skill-flow de pesquisa):
 *   tenant_id         — tenant (explícito, como workflow_trigger/context_set; em
 *                       YAML use $.tenant_id). Sem session_token — workflows não têm.
 *   origin_session_id — sessão pesquisada (chave do sinal; em YAML use $.session_id).
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
      console.log("[survey_record] invoked input=%j", input)
      try {
        const parsed = SurveyRecordInputSchema.parse(input)
        const {
          tenant_id,
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
          console.log(
            "[survey_record] published session.signals event_id=%s origin=%s grain=%s signals=%d",
            event_id, origin_session_id, grain, signals.length,
          )
        } catch (kafkaErr) {
          console.error("[survey_record] publish failed: %s", String(kafkaErr))
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
          console.error("[survey_record] validation_error: %s", e.errors.map(er => er.message).join("; "))
          return mcpError("validation_error", e.errors.map(er => er.message).join("; "))
        }
        console.error("[survey_record] internal_error: %s", String(e))
        return mcpError("internal_error", String(e))
      }
    },
  )
}
