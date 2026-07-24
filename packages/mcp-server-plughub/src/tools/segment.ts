/**
 * tools/segment.ts — Camada E2 (wrap-up-α), sub-fatia 2.
 *
 * Tool MCP `segment_outcome_record`: grava a DISPOSIÇÃO do wrap-up destacado no
 * SEGMENTO da sessão de ORIGEM, por REFERÊNCIA (origin_session_id + segment_id) —
 * sem que o wrap-up seja fisicamente um segmento da conferência.
 *
 * Replica fielmente o par do orchestrator-bridge:
 *   _apply_wrapup_to_segment  → normaliza classificação→outcome, acumula no hash
 *                                Redis `session:{origin}:seg_signal:{seg}`
 *   _republish_segment_from_signal → lê o hash COMPLETO e re-publica
 *                                `participant_left` em `conversations.participants`
 *
 * CUIDADO (ReplacingMergeTree substitui a LINHA INTEIRA — CLAUDE.md § Postura de
 * Engenharia): o hash `seg_signal` já foi semeado com os campos ESTÁTICOS do
 * segmento (instance_id, pool_id, joined_at, duration_ms, …) por `_seed_segment_signal`
 * quando o hook on_human_end disparou. Publicamos a linha COMPLETA (estáticos do hash
 * + os dinâmicos do wrap-up). Se o hash não tem `segment_id` (nunca semeado), NÃO
 * publicamos (no-op barulhento) — publicar uma linha parcial zeraria as colunas do
 * segmento no analytics. Mesma guarda do bridge.
 *
 * Idempotente por construção (dedup do ReplacingMergeTree por segment_id).
 * Interceptado pelo McpInterceptor — auditado em mcp.audit (LGPD).
 * Publica em: conversations.participants → analytics.segments.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { z }         from "zod"
import { randomUUID } from "crypto"
import type { RedisClient } from "../infra/redis"

export interface SegmentDeps {
  redis: RedisClient
  kafka: { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> }
  tenantId: string
}

const TOPIC_PARTICIPANTS = "conversations.participants"

// Mesmo mapa do bridge (_WRAPUP_OUTCOME_MAP): classificação CRUA → outcome canônico.
const WRAPUP_OUTCOME_MAP: Record<string, string> = {
  resolvido: "resolved",
  pendente:  "suspended",
  escalado:  "escalated",
  cancelado: "abandoned",
}

const segSignalKey = (sessionId: string, segmentId: string) =>
  `session:${sessionId}:seg_signal:${segmentId}`

function mcpOk(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}
function mcpError(code: string, message: string) {
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }] }
}

export function registerSegmentTools(server: McpServer, deps: SegmentDeps): void {
  const { redis, kafka, tenantId: defaultTenant } = deps

  server.tool(
    "segment_outcome_record",
    "Grava a disposição do WRAP-UP destacado no SEGMENTO da sessão de origem, por " +
    "referência (origin_session_id + segment_id). Normaliza a classificação em outcome, " +
    "acumula no seg_signal e re-publica participant_left para o analytics. Usado pelo " +
    "workflow de wrap-up no on_resume (skill_wrapup_detached_v1).",
    {
      origin_session_id: z.string().describe("Sessão de ORIGEM (o contato que fechou) — chaveia o seg_signal"),
      segment_id:        z.string().describe("Segmento humano a atribuir (@ctx.session.surveyed_segment_id)"),
      classificacao:     z.string().describe("Disposição crua: resolvido | pendente | escalado | cancelado"),
      resumo:            z.string().optional().describe("Resumo do atendimento (→ handoff_reason quando não-resolvido)"),
      escalation_reason: z.string().optional().describe("Motivo da escalação (quando classificacao=escalado)"),
      proximos_passos:   z.string().optional().describe("Próximos passos (apensado ao handoff_reason quando não-resolvido)"),
      tenant_id:         z.string().optional(),
    } as any,
    async (args: {
      origin_session_id: string; segment_id: string; classificacao: string
      resumo?: string; escalation_reason?: string; proximos_passos?: string; tenant_id?: string
    }) => {
      const { origin_session_id, segment_id } = args
      const tenant = args.tenant_id || defaultTenant
      const raw = String(args.classificacao || "").trim().toLowerCase()
      const outcome = WRAPUP_OUTCOME_MAP[raw]
      if (!outcome) {
        console.warn("[segment_outcome_record] unknown_classification: %j", args.classificacao)
        // Degradação nunca silenciosa: classificação desconhecida não vira placeholder.
        return mcpError("unknown_classification",
          `classificacao inválida: ${args.classificacao} (esperado: ${Object.keys(WRAPUP_OUTCOME_MAP).join("|")})`)
      }

      const key = segSignalKey(origin_session_id, segment_id)

      // ── 1. Acumula os campos DINÂMICOS no hash (mesma semântica de _apply_wrapup_to_segment) ──
      const dyn: Record<string, string> = { outcome, issue_status: raw }
      if (outcome !== "resolved") {
        const parts: string[] = []
        if (args.resumo) parts.push(args.resumo)
        if (args.proximos_passos) parts.push(`Próximos: ${args.proximos_passos}`)
        if (parts.length) dyn["handoff_reason"] = parts.join(" | ")
      }
      if (outcome === "escalated" && args.escalation_reason) {
        dyn["escalation_reason"] = args.escalation_reason
      }
      try {
        await redis.hset(key, dyn)
        await redis.expire(key, 604800)
        // last_outcome de sessão (último primary humano) — espelha o bridge.
        await redis.set(
          `session:${origin_session_id}:last_outcome`,
          JSON.stringify({ outcome, agent_kind: "human" }),
          "EX", 604800,
        )
      } catch (err) {
        return mcpError("redis_error", `hset seg_signal falhou: ${String(err)}`)
      }

      // ── 2. Lê o hash COMPLETO e re-publica participant_left (estáticos + dinâmicos) ──
      let h: Record<string, string> = {}
      try {
        h = await redis.hgetall(key)
      } catch (err) {
        return mcpError("redis_error", `hgetall seg_signal falhou: ${String(err)}`)
      }
      const segId = h["segment_id"]
      if (!segId) {
        console.warn(
          "[segment_outcome_record] seg_signal_not_seeded: origin=%s seg=%s (hash sem segment_id; outcome %s persistido, participant_left NÃO publicado)",
          origin_session_id, segment_id, outcome,
        )
        // Sem os campos ESTÁTICOS (o hook não semeou / segment_id errado): NÃO publica
        // (publicar linha parcial zeraria colunas no ReplacingMergeTree). Barulhento.
        return mcpOk({
          recorded: false, reason: "seg_signal_not_seeded", outcome,
          note: "hash sem segment_id (estáticos não semeados) — outcome persistido no hash, participant_left NÃO publicado para não corromper a linha do segmento",
        })
      }

      const participantId = h["instance_id"] || ""
      const event: Record<string, unknown> = {
        event_id:       randomUUID(),
        type:           "participant_left",
        session_id:     origin_session_id,
        tenant_id:      h["tenant_id"] || tenant,
        segment_id:     segId,
        participant_id: participantId,
        pool_id:        h["pool_id"] || "",
        agent_type_id:  h["agent_type_id"] || "",
        role:           "primary",
        agent_type:     "human",
        sequence_index: Number(h["sequence_index"] || 0),
        timestamp:      new Date().toISOString(),
      }
      // C1 — user_id derivado de human-{userId} (mesma regra do bridge).
      if (participantId.startsWith("human-")) {
        const uid = participantId.slice("human-".length)
        if (uid) event["user_id"] = uid
      }
      if (h["user_login"])   event["user_login"]   = h["user_login"]
      if (h["joined_at"])    event["joined_at"]    = h["joined_at"]
      if (h["duration_ms"] !== undefined && h["duration_ms"] !== "") {
        event["duration_ms"] = Number(h["duration_ms"])
      }
      event["outcome"]      = outcome
      event["issue_status"] = h["issue_status"] ?? raw
      if (h["handoff_reason"])    event["handoff_reason"]    = h["handoff_reason"]
      if (h["close_reason"])      event["close_reason"]      = h["close_reason"]
      if (h["escalation_reason"]) event["escalation_reason"] = h["escalation_reason"]

      try {
        await kafka.publish(TOPIC_PARTICIPANTS, event)
      } catch (err) {
        return mcpError("publish_failed", `publish conversations.participants falhou: ${String(err)}`)
      }

      console.log(
        "[segment_outcome_record] recorded origin=%s seg=%s outcome=%s (participant_left published)",
        origin_session_id, segId, outcome,
      )
      return mcpOk({
        recorded: true, segment_id: segId, outcome, session_id: origin_session_id,
      })
    },
  )
}
