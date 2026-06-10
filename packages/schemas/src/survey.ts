/**
 * survey.ts
 * F10 — Session Signals (voz do cliente/agente no grão contato/jornada).
 *
 * A tool MCP `survey_record` grava o resultado de uma pesquisa (NPS, CSAT ou
 * métricas customizadas) feita por uma survey OUTBOUND — uma sessão própria que
 * religa ao sessão original via `origin_session_id` e grava o sinal
 * contra ele. Diferente de Arc 12 `agent_event` (KPI inline, namespace por pool),
 * aqui `origin_session_id`, `grain` e as métricas são parâmetros estruturados de
 * 1ª classe — sem checagem de namespace nem convenção de category.
 *
 * Tópico Kafka: session.signals  → analytics.session_signal (ClickHouse).
 * Grão segmento NÃO usa esta tool (vive em segments.nps_score, F5).
 */

import { z } from "zod"

/**
 * Grão do sinal — taxonomia canônica do que a pesquisa COBRE:
 *   segment   — a janela de UM agente (seu slice do contato). Atribuível a agente.
 *   session   — a sessão/contato inteiro (todos os segmentos/agentes).
 *   workflow  — uma execução de workflow (sessão channel webhook, Arc 19).
 *   journey   — atravessa MÚLTIPLAS sessões (o relacionamento) — NÃO a entidade
 *               Journey (eliminada no Arc 19); aqui é só rótulo de grão.
 * O timing (no ato × diferido) NÃO é grão — fica em captured_at × session_at.
 *
 * Armazenamento (alvo): TODOS os grãos moram em `session_signal`, gravados
 * explicitamente via a tool `survey_record` (um invoke no skill-flow de pesquisa)
 * — sem mecanismo de eventos/derivação. Para `segment`, o skill informa
 * `segment_id` + `agent_key` (atribuição). `segments.nps_score` (F5) é legado, a
 * ser aposentado no cutover da bancada (F10.3).
 */
export const SignalGrainSchema = z.enum(["segment", "session", "workflow", "journey"])
export type SignalGrain = z.infer<typeof SignalGrainSchema>

/** Grãos que a tabela session_signal possui (todos — gravação explícita). */
export const SESSION_SIGNAL_GRAINS = ["segment", "session", "workflow", "journey"] as const

/**
 * Uma métrica de pesquisa. `metric` é livre (snake_case) — nps/csat são
 * normalizados (escala + label) pelo consumer; métricas extras passam o valor
 * cru. `value` aceita número em string (output_as de menu em skill-flow).
 * `value_label` opcional sobrepõe a normalização do consumer.
 */
export const SurveySignalSchema = z.object({
  metric: z.string().regex(/^[a-z0-9_]+$/, {
    message: "metric must be snake_case (a-z0-9_)",
  }),
  value: z.coerce.number().finite({ message: "value must be a finite number" }),
  value_label: z.string().max(64).optional(),
})
export type SurveySignal = z.infer<typeof SurveySignalSchema>

/**
 * Input da tool MCP `survey_record`.
 *  - tenant_id       — tenant da sessão. Em skill-flow YAML use $.tenant_id
 *                      (built-in). Explícito (como workflow_trigger/context_set) —
 *                      workflows não têm session_token; auditoria via McpInterceptor.
 *  - origin_session_id — OBRIGATÓRIO: a sessão pesquisada (chave do sinal). Use $.session_id.
 *  - grain           — segment | session | workflow | journey.
 *  - signals         — ≥1 métrica; várias numa só chamada.
 *  - segment_id      — OBRIGATÓRIO quando grain='segment' (qual segmento/agente).
 *  - agent_key       — atribuição p/ grain='segment' (user_id humano | flow_id IA).
 *  - survey_session_id — opcional: a sessão de survey em si (trace/audit).
 *  - pool_id         — opcional: pool da sessão original (só contexto).
 */
export const SurveyRecordInputSchema = z.object({
  tenant_id: z.string().min(1),
  origin_session_id: z.string().min(1),
  grain: SignalGrainSchema,
  signals: z.array(SurveySignalSchema).min(1).max(20),
  // Obrigatório quando grain='segment' (validado no handler — manter z.object puro
  // para preservar .shape usado no registro da tool MCP).
  segment_id: z.string().optional(),
  agent_key: z.string().optional(),
  survey_session_id: z.string().optional(),
  pool_id: z.string().optional(),
})
export type SurveyRecordInput = z.infer<typeof SurveyRecordInputSchema>

/**
 * Evento publicado no tópico session.signals (consumido pela analytics-api).
 * O mcp-server resolve tenant_id do session_token e carimba captured_at.
 */
export const SessionSignalEventSchema = z.object({
  event_id: z.string().uuid(),
  tenant_id: z.string().min(1),
  origin_session_id: z.string().min(1),
  grain: SignalGrainSchema,
  segment_id: z.string().nullable().default(null),
  agent_key: z.string().default(""),
  survey_session_id: z.string().nullable().default(null),
  pool_id: z.string().default(""),
  signals: z.array(SurveySignalSchema).min(1),
  captured_at: z.string().datetime(),
})
export type SessionSignalEvent = z.infer<typeof SessionSignalEventSchema>
