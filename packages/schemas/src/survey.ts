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
 * TODOS os grãos (incl. segment) usam esta tool (cutover F10.3b); segments.nps_score
 * foi dropada (item 5).
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
 * `segment_id` + `agent_key` (atribuição). `segments.nps_score` (F5) foi dropada
 * (item 5) — session_signal é a fonte única de NPS de segmento.
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
  // Two ways to supply the metrics (validated in the handler — schema stays a
  // plain z.object so `.shape` works for the MCP tool registration):
  //   (a) explicit `signals[]` (legacy / already-composed), OR
  //   (b) `form_id` + `answers` → the tool composes per-respondent signals
  //       server-side via the DialogForm dimensions (ADR §D9). One of the two.
  signals: z.array(SurveySignalSchema).min(1).max(20).optional(),
  /** DialogForm to compose against (mode b). Requires `answers`. */
  form_id: z.string().optional(),
  /**
   * Raw answers for mode b. Either a map keyed by question `output_key`, or the
   * ARRAY the `loop` step accumulates ([{output_key, value, …}]) — the tool
   * normalizes both to a map. `null` = skipped/NA (re-normalizes the dimension).
   * The tool maps each answer to a numeric score (option `capture.value` or the
   * raw numeric) and composes via `composeScore`.
   */
  answers: z
    .union([
      z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
      z.array(
        z.object({
          output_key: z.string(),
          value:      z.union([z.string(), z.number(), z.null()]),
        }),
      ),
    ])
    .optional(),
  // Obrigatório quando grain='segment' (validado no handler — manter z.object puro
  // para preservar .shape usado no registro da tool MCP).
  //
  // `.nullish()` e NÃO `.optional()`: o runner de survey é UM só para todos os grãos,
  // então ele sempre passa `segment_id`/`agent_key` — e num grão que não é `segment` a
  // ref `@ctx.session.survey_segment_id` resolve para **null** (é assim que um @ctx
  // ausente se comporta). `.optional()` aceita *ausente*, não `null`, e rejeitaria a
  // chamada inteira. Foi exatamente essa a causa do `survey_link_create` que falhava em
  // silêncio no J4b (`customer_key: z.string().default("")` vs `null`) — o mesmo erro,
  // duas vezes, custa caro porque a tool devolve isError e o `invoke` segue por
  // `on_failure` sem log.
  segment_id: z.string().nullish().transform(v => v ?? undefined),
  agent_key: z.string().nullish().transform(v => v ?? undefined),
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
