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
import { SurveyRecordInputSchema, composeScore } from "@plughub/schemas"
import type {
  DialogForm,
  DialogDimension,
  QuestionNode,
  ScoredItem,
  SurveySignal,
} from "@plughub/schemas"
import type { KafkaProducer } from "../infra/kafka"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface SurveyDeps {
  kafka: KafkaProducer
  /** dialog-api base URL, for the compose path (form_id + answers). */
  dialogApiUrl: string
  /** Default tenant when the input omits one (compose fetch header). */
  tenantId: string
  /** channel-gateway base URL — for creating outbound survey links (Journey J4). */
  channelGatewayUrl: string
}

// ─── Server-side signal composition (ADR §D9) ─────────────────────────────────

type RawAnswer = string | number | null

/**
 * Normalize the `answers` input to a map keyed by output_key. Accepts either a
 * map (mode b direct) or the ARRAY the `loop` step accumulates
 * ([{output_key, value, …}]) — so the existing loop survey skill can feed the
 * compose path by passing its accumulator as-is. Array duplicates: last wins.
 */
export function answersToMap(
  answers: Record<string, RawAnswer> | Array<{ output_key: string; value: RawAnswer }>,
): Record<string, RawAnswer> {
  if (!Array.isArray(answers)) return answers
  const map: Record<string, RawAnswer> = {}
  for (const e of answers) {
    if (e && typeof e.output_key === "string") map[e.output_key] = e.value ?? null
  }
  return map
}

/**
 * Resolve one question's raw answer to a numeric score, or null (NA / skipped /
 * unmapped). For option questions the answer is the option's `value ?? id`
 * (see form_get's buildRender); the score is the option's `capture.value` when
 * set, else the answer parsed as a number (e.g. NPS 0–10 buttons). Scalar
 * questions use the raw numeric answer.
 */
export function scoreOfAnswer(q: QuestionNode, answer: RawAnswer | undefined): number | null {
  if (answer === undefined || answer === null || answer === "") return null
  const opts = q.options
  if (opts && opts.length) {
    const match = opts.find((o) => String(o.value ?? o.id) === String(answer))
    const capVal = match?.capture?.value
    if (capVal !== undefined) {
      const n = Number(capVal)
      return Number.isFinite(n) ? n : null
    }
  }
  const n = Number(answer)
  return Number.isFinite(n) ? n : null
}

/**
 * composeSurveySignals — build per-respondent survey signals from raw answers +
 * the DialogForm (ADR §D9). Declared `dimensions[]` compose their member
 * questions (weighted_mean, NA re-normalized) into ONE signal each
 * (metric = dimension_id); legacy `capture.metric` questions (no dimension) emit
 * a single-item signal, preserving today's behavior. Pure & deterministic.
 */
export function composeSurveySignals(
  form: DialogForm,
  answers: Record<string, RawAnswer>,
): SurveySignal[] {
  const questions = form.nodes.filter(
    (n): n is QuestionNode => n.kind === "question",
  )
  const signals: SurveySignal[] = []
  const dimVals: Array<{ dim: DialogDimension; value: number }> = []

  // (1) Declared dimensions — compose member questions into one signal.
  for (const dim of form.dimensions ?? []) {
    const members = questions.filter((q) => q.capture?.dimension_id === dim.dimension_id)
    if (members.length === 0) continue
    // Build items without an explicit `weight: undefined` (exactOptionalPropertyTypes).
    const items: ScoredItem[] = members.map((q) => {
      const score = scoreOfAnswer(q, answers[q.output_key])
      const w = q.capture?.weight
      return w === undefined ? { score } : { score, weight: w }
    })
    const scale = { min: dim.scale?.min ?? 0, max: dim.scale.max }
    const value = composeScore(items, dim.aggregation, scale)
    if (value !== null) { signals.push({ metric: dim.dimension_id, value }); dimVals.push({ dim, value }) }
  }

  // (2) Legacy standalone metrics — one signal per question (no dimension).
  for (const q of questions) {
    const metric = q.capture?.metric
    if (!metric || q.capture?.dimension_id) continue
    const score = scoreOfAnswer(q, answers[q.output_key])
    if (score !== null) signals.push({ metric, value: score })
  }

  // (3) Optional form-level composite (health score) — weighted roll-up over the
  // dimensions, each normalized by its own scale, emitted on a 0–100 scale.
  if (form.composite?.metric && dimVals.length > 0) {
    const items: ScoredItem[] = dimVals.map(({ dim, value }) => {
      const scale = { min: dim.scale?.min ?? 0, max: dim.scale.max }
      const w = dim.weight
      return w === undefined ? { score: value, scale } : { score: value, weight: w, scale }
    })
    const composite = composeScore(items, "weighted_mean", { min: 0, max: 100 })
    if (composite !== null) signals.push({ metric: form.composite.metric, value: composite })
  }

  return signals
}

/** Fetch a published DialogForm from dialog-api (compose path). */
async function fetchDialogForm(
  dialogApiUrl: string,
  tenantId:     string,
  formId:       string,
): Promise<DialogForm> {
  const resp = await fetch(
    `${dialogApiUrl}/v1/dialog/forms/${encodeURIComponent(formId)}?status=published`,
    { headers: { "X-Tenant-ID": tenantId } },
  )
  if (!resp.ok) {
    const body = await resp.text().catch(() => "")
    throw new Error(`dialog-api returned ${resp.status}: ${body}`)
  }
  return (await resp.json()) as DialogForm
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
  const { kafka, dialogApiUrl, tenantId: defaultTenantId, channelGatewayUrl } = deps

  // ── survey_link_create (Journey J4 — veículo outbound) ────────────────────────
  // Optional string fields tolerate null: an unresolved @ctx.* invoke input
  // resolves to null (getValue returns null for a missing tag), and z.string()
  // .default("") only covers `undefined`, not `null`. Coerce null → "" so a
  // missing customer_key/delivery field never fails validation and silently
  // routes the invoke to on_failure. origin_session_id stays strict (min 1) —
  // the J1 root is always present.
  const optStr = () => z.string().nullish().transform((v) => v ?? "")
  const SurveyLinkCreateInputSchema = z.object({
    tenant_id:         z.string().nullish().transform((v) => v ?? undefined),
    form_id:           z.string().min(1),
    /** Sessão pesquisada = chave do sinal. Para grain='journey', a RAIZ canônica. */
    origin_session_id: z.string().min(1),
    customer_key:      optStr(),
    grain:             z.enum(["segment", "session", "workflow", "journey"]).default("session"),
    deliver_kind:      optStr(),
    deliver_address:   optStr(),
  })

  server.tool(
    "survey_link_create",
    "Journey J4 — create an OUTBOUND survey web link (snapshots a published DialogForm) " +
    "keyed to origin_session_id at the given grain (e.g. 'journey' for an end-to-end N3 " +
    "process survey, keyed to the CANONICAL journey root). Optionally delivers the link. " +
    "The customer's later submission records session.signals at that grain — the response " +
    "is the CUSTOMER's, never fabricated. Used by an on_process_end hook agent.",
    SurveyLinkCreateInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const p = SurveyLinkCreateInputSchema.parse(input)
        const tenant_id = p.tenant_id || defaultTenantId
        const res = await fetch(`${channelGatewayUrl}/v1/survey/web/create`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tenant_id,
            form_id:           p.form_id,
            origin_session_id: p.origin_session_id,
            customer_key:      p.customer_key,
            grain:             p.grain,
            deliver_kind:      p.deliver_kind,
            deliver_address:   p.deliver_address,
          }),
        })
        if (!res.ok) {
          const text = await res.text().catch(() => "")
          return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "create_failed", status: res.status, body: text }) }] }
        }
        const data = await res.json()
        return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
      } catch (e) {
        if (e instanceof z.ZodError) {
          return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "validation_error", message: e.errors.map(er => er.message).join("; ") }) }] }
        }
        return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "internal_error", message: String(e) }) }] }
      }
    },
  )

  // ── survey_record ───────────────────────────────────────────────────────────
  server.tool(
    "survey_record",
    "Record a customer/agent survey result (NPS, CSAT or custom metrics) against " +
    "the ORIGINAL session being surveyed. Use from an outbound survey session: " +
    "pass origin_session_id (the surveyed session), grain " +
    "(segment|session|workflow|journey; segment_id required for segment) and EITHER " +
    "explicit signals[] OR form_id + answers (the tool then composes per-respondent " +
    "signals from the DialogForm dimensions, weighted with NA re-normalization). F10.",
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
          form_id,
          answers,
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

        // Resolve the signals: explicit (a) or composed from a DialogForm (b).
        let finalSignals: SurveySignal[]
        if (form_id && answers) {
          let form: DialogForm
          try {
            form = await fetchDialogForm(dialogApiUrl, tenant_id || defaultTenantId, form_id)
          } catch (fe) {
            return mcpError("dialog_api_error", `Could not load form '${form_id}': ${String(fe)}`)
          }
          finalSignals = composeSurveySignals(form, answersToMap(answers))
          if (finalSignals.length === 0) {
            return mcpError(
              "validation_error",
              `Composition of form '${form_id}' produced no signals (all answers NA or unmapped)`,
            )
          }
        } else if (signals && signals.length > 0) {
          finalSignals = signals
        } else {
          return mcpError(
            "validation_error",
            "provide either signals[] or form_id + answers",
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
          signals:           finalSignals,
          captured_at,
        }

        try {
          await kafka.publish("session.signals", event)
          console.log(
            "[survey_record] published session.signals event_id=%s origin=%s grain=%s signals=%d%s",
            event_id, origin_session_id, grain, finalSignals.length,
            form_id ? ` (composed from form=${form_id})` : "",
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
          signals_recorded: finalSignals.length,
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
