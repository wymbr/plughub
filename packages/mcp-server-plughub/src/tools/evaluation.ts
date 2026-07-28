/**
 * tools/evaluation.ts
 * Tools de Avaliação — consumidas pelo agente nativo agente_avaliacao_v1.
 * Spec: PlugHub v24.0 seção 10.2
 *
 * Grupo: Evaluation (3 tools)
 *   transcript_get, evaluation_context_resolve, evaluation_publish
 *
 * Invariantes:
 * - Nenhuma lógica de negócio — apenas acesso a infraestrutura
 * - transcript_get: leitura somente de PostgreSQL (tabela transcript_messages)
 * - evaluation_context_resolve: orquestra chamadas MCP declaradas na evaluation skill
 * - evaluation_publish: calcula scores deterministicamente, publica evaluation.completed
 */

import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import type { KafkaProducer }  from "../infra/kafka"
import type { PostgresClient } from "../infra/postgres"
import type { RedisClient }    from "../infra/redis"
import {
  verifySessionToken,
  InvalidTokenError,
} from "../infra/jwt"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface EvaluationDeps {
  kafka:       KafkaProducer
  postgres:    PostgresClient
  redis:       RedisClient
  /** URL do proxy sidecar MCP para chamadas requires_context */
  proxyUrl:    string
  /** URL do Skill Registry para carregar evaluation skills */
  skillRegistryUrl: string
  /** URL da evaluation-api para evaluation_lock (Arc 6 v2) */
  evaluationApiUrl?: string
  /** URL da analytics-api — R5: tool_trace (GET /v1/audit/mcp-calls?session_id) */
  analyticsApiUrl?: string
  /** URL do agent-registry — R5: trajetória esperada (GET /v1/skills/:flow_id → flow.steps) */
  agentRegistryUrl?: string
  /** G-PROBE fase 2 — credencial de serviço (X-Service-Token) p/ endpoints de sistema da evaluation-api */
  serviceToken?: string
}

// ─── Schemas de input ─────────────────────────────────────────────────────────

const TranscriptGetInputSchema = z.object({
  transcript_id: z.string().uuid(),
})

const TemplateVarsSchema = z.object({
  evaluation_id: z.string().uuid(),
  agent: z.object({
    agent_id:   z.string(),
    agent_type: z.string(),
    pool_id:    z.string(),
  }),
  contact: z.object({
    contact_id: z.string().uuid(),
    channel:    z.string(),
  }),
  context: z.record(z.unknown()),
})

const EvaluationContextResolveInputSchema = z.object({
  skill_id:        z.string().min(1),
  template_vars:   TemplateVarsSchema,
  context_package: z.record(z.unknown()).default({}),
})

const AgentQueueItemSchema = z.object({
  skill_id:   z.string().min(1),
  output_key: z.string().min(1),
})

const EvaluationAgentContextNextInputSchema = z.object({
  /** Fila atual de agentes pendentes (retornada por evaluation_context_resolve ou pelo passo anterior). */
  queue:              z.array(AgentQueueItemSchema).default([]),
  /** Resultado do último step task (output do agente especialista executado). Ausente na primeira iteração. */
  task_result:        z.unknown().optional(),
  /** output_key do agente que acabou de executar — onde o resultado deve ser armazenado. */
  current_output_key: z.string().optional(),
  /** Acumulador com os resultados de todos os agentes já executados (objeto JSON). */
  accumulated:        z.record(z.unknown()).default({}),
})

const LlmItemSchema = z.object({
  item_id:       z.string(),
  section_id:    z.string(),
  subsection_id: z.string(),
  value:         z.number().min(0).max(10),
  justification: z.string(),
})

const EvaluationPublishInputSchema = z.object({
  evaluation_id:       z.string().uuid(),
  tenant_id:           z.string(),
  contact_id:          z.string().uuid(),
  agent_id:            z.string(),
  agent_type:          z.enum(["human", "ai"]),
  pool_id:             z.string(),
  skill_id:            z.string(),
  triggered_by:        z.string(),
  llm_items:           z.array(LlmItemSchema).min(1),
  overall_observation: z.string().optional(),
  context_package:     z.record(z.unknown()),
})

// ─── Tipos internos ───────────────────────────────────────────────────────────

interface EvalItem {
  id:        string
  weight:    number
  applies_to?: string
}

interface EvalSubsection {
  id:      string
  weight:  number
  items:   EvalItem[]
}

interface EvalSection {
  id:             string
  applies_when?:  Record<string, unknown> | null
  requires_context?: Array<{
    tool:       string
    input:      Record<string, unknown>
    output_key: string
  }>
  /**
   * Delegação A2A para coleta de contexto especializado.
   * O agente recebe o context_package e retorna dados adicionais
   * que ficam disponíveis no pipeline_state para o step evaluate.
   * Apenas uma declaração por seção; prevalece a primeira seção
   * ativa (após applies_when) que declarar requires_agent.
   */
  requires_agent?: {
    skill_id:   string
    output_key: string
  }
  subsections: EvalSubsection[]
}

interface EvalSkill {
  sections: EvalSection[]
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

function handleCaughtError(e: unknown): ToolResult {
  if (e instanceof z.ZodError) {
    return mcpError(
      "validation_error",
      e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
    )
  }
  return mcpError("internal_error", e instanceof Error ? e.message : String(e))
}

/** Resolve {{ key }} e {{ dot.path }} usando um mapa plano de variáveis. */
function resolveTemplate(text: string, vars: Record<string, unknown>): string {
  return text.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_match, path: string) => {
    const parts = path.split(".")
    let cursor: unknown = vars
    for (const part of parts) {
      if (cursor === null || cursor === undefined || typeof cursor !== "object") return _match
      cursor = (cursor as Record<string, unknown>)[part]
    }
    if (cursor === undefined || cursor === null) return _match
    return typeof cursor === "string" ? cursor : JSON.stringify(cursor)
  })
}

/** Aplica resolveTemplate recursivamente em todos os valores string de um objeto. */
function resolveTemplatesInObject(
  obj: Record<string, unknown>,
  vars: Record<string, unknown>
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(obj).map(([k, v]) => [
      k,
      typeof v === "string"
        ? resolveTemplate(v, vars)
        : v && typeof v === "object" && !Array.isArray(v)
          ? resolveTemplatesInObject(v as Record<string, unknown>, vars)
          : v,
    ])
  )
}

/**
 * Verifica se uma seção deve ser incluída na avaliação com base em applies_when
 * e no context_package.
 */
function sectionApplies(section: EvalSection, contextPackage: Record<string, unknown>): boolean {
  const when = section.applies_when
  if (!when) return true  // sem condição = mandatory

  if ("agent_type" in when) {
    return contextPackage["agent_type"] === when["agent_type"]
  }
  if ("flags_include" in when) {
    const flags = contextPackage["flags"] as string[] | undefined
    return Array.isArray(flags) && flags.includes(when["flags_include"] as string)
  }
  if ("intent" in when) {
    return contextPackage["intent"] === when["intent"]
  }
  return true
}

/**
 * Calcula scores deterministicamente a partir dos itens preenchidos pelo LLM.
 * Fórmula: média ponderada bottom-up (item → subsection → section).
 */
function calculateScores(
  skill: EvalSkill,
  llmItems: z.infer<typeof LlmItemSchema>[],
  contextPackage: Record<string, unknown>
): { scores: unknown[]; itemsExcluded: unknown[] } {
  const agentType  = contextPackage["agent_type"] as string | undefined
  const llmByKey   = new Map(llmItems.map(i => [`${i.section_id}:${i.subsection_id}:${i.item_id}`, i]))
  const scores: unknown[]        = []
  const itemsExcluded: unknown[] = []

  for (const section of skill.sections) {
    if (!sectionApplies(section, contextPackage)) continue

    const sectionItems: unknown[]          = []
    let sectionWeightedSum                 = 0
    let sectionWeightSum                   = 0
    const subsectionScores: unknown[]      = []

    for (const sub of section.subsections) {
      const subItems: unknown[]  = []
      let subWeightedSum         = 0
      let subWeightSum           = 0

      for (const item of sub.items) {
        // applies_to filtering
        if (item.applies_to && item.applies_to !== "all") {
          if (item.applies_to !== agentType) {
            itemsExcluded.push({
              item_id: item.id,
              reason:  `applies_to: ${item.applies_to} — agente avaliado é ${agentType ?? "unknown"}`,
            })
            continue
          }
        }

        const key     = `${section.id}:${sub.id}:${item.id}`
        const llmItem = llmByKey.get(key)
        if (!llmItem) continue

        subWeightedSum += llmItem.value * item.weight
        subWeightSum   += item.weight
        subItems.push({ item_id: item.id, value: llmItem.value, weight: item.weight, justification: llmItem.justification })
        sectionItems.push(item.id)
      }

      if (subWeightSum === 0) continue
      const subScore = subWeightedSum / subWeightSum
      sectionWeightedSum += subScore * sub.weight
      sectionWeightSum   += sub.weight

      subsectionScores.push({ subsection_id: sub.id, score: Math.round(subScore * 100) / 100, items: subItems })
    }

    if (sectionWeightSum === 0) continue

    const sectionScore = sectionWeightedSum / sectionWeightSum
    const scoreType    = section.applies_when ? "context_score" : "base_score"

    scores.push({
      section_id:   section.id,
      score_type:   scoreType,
      score:        Math.round(sectionScore * 100) / 100,
      ...(section.applies_when ? { triggered_by: section.applies_when } : {}),
      subsections:  subsectionScores,
    })
  }

  return { scores, itemsExcluded }
}

// ─── T7b-2b — JSON Schema do form (montado UPSTREAM; o ai-gateway não monta) ───
/**
 * Deriva o JSON Schema de saída do avaliador a partir do EvaluationForm.
 * Modelo de saída = `criterion_responses[]` (compatível com a agregação T7a, que lê
 * `score`/`na`/`justification`/`evidence` por critério). `criterion_id` é um enum dos
 * critérios não-auto do form; `score` é 0..max (nullable p/ `na`). Critérios
 * `auto_computed` são omitidos (preenchidos pelo SessionMetricsExtractor no ingest).
 * Retorna undefined quando o form não tem critérios pontuáveis.
 */
function buildEvaluationOutputSchema(form: unknown): Record<string, unknown> | undefined {
  if (!form || typeof form !== "object") return undefined
  const f = form as Record<string, unknown>

  // Achata dimensions[].criteria[] (modelo aninhado) com fallback a criteria[] flat.
  const crits: Array<Record<string, unknown>> = []
  const dims = f["dimensions"]
  if (Array.isArray(dims)) {
    for (const d of dims) {
      const cs = (d as Record<string, unknown>)?.["criteria"]
      if (Array.isArray(cs)) for (const c of cs) if (c && typeof c === "object") crits.push(c as Record<string, unknown>)
    }
  }
  if (crits.length === 0 && Array.isArray(f["criteria"])) {
    for (const c of f["criteria"] as unknown[]) if (c && typeof c === "object") crits.push(c as Record<string, unknown>)
  }

  const scorable = crits.filter(c => (c["type"] ?? "score") !== "auto_computed")
  const ids = scorable
    .map(c => (c["criterion_id"] ?? c["id"]) as string | undefined)
    .filter((x): x is string => typeof x === "string" && x.length > 0)
  if (ids.length === 0) return undefined

  const maxScore = Math.max(
    10,
    ...scorable.map(c => (typeof c["max_score"] === "number" ? (c["max_score"] as number) : 10)),
  )

  return {
    type: "object",
    required: ["criterion_responses"],
    properties: {
      criterion_responses: {
        type: "array",
        description:
          "Uma entrada por critério avaliável do formulário (omita os auto_computed). " +
          "Quando o critério não se aplica, na=true e score=null.",
        items: {
          type: "object",
          required: ["criterion_id", "score", "justification"],
          properties: {
            criterion_id:  { type: "string", enum: ids },
            score:         { type: ["number", "null"], minimum: 0, maximum: maxScore,
                             description: "Nota 0–" + maxScore + " ou null quando na=true" },
            na:            { type: "boolean", description: "true quando o critério não é aplicável" },
            justification: { type: "string", description: "Fundamentação (≥ 20 palavras)" },
            evidence: {
              type: "array",
              description: "Evidências do transcript que sustentam a nota.",
              items: {
                type: "object",
                required: ["stream_entry_id"],
                properties: {
                  stream_entry_id: { type: "string" },
                  excerpt:         { type: "string" },
                  relevance_note:  { type: "string" },
                },
              },
            },
          },
        },
      },
      overall_observation: { type: "string", description: "Síntese da avaliação (≥ 50 palavras)" },
      highlights:          { type: "array", items: { type: "string" } },
      improvement_points:  { type: "array", items: { type: "string" } },
    },
  }
}


// ─── Helpers — comparação turn-a-turn ────────────────────────────────────────

/**
 * Jaccard similarity sobre tokens normalizados.
 * Coeficiente J(A,B) = |A ∩ B| / |A ∪ B|
 * Sem dependências externas. Determinístico.
 */
function jaccardSimilarity(a: string, b: string): number {
  const tokenize = (s: string): Set<string> => {
    const normalized = s.toLowerCase().replace(/[^\w\s]/g, " ")
    const tokens = new Set<string>()
    for (const t of normalized.split(/\s+/)) {
      if (t) tokens.add(t)
    }
    return tokens
  }

  const ta = tokenize(a)
  const tb = tokenize(b)

  if (ta.size === 0 && tb.size === 0) return 1.0
  if (ta.size === 0 || tb.size === 0) return 0.0

  let intersectionSize = 0
  for (const token of ta) {
    if (tb.has(token)) intersectionSize++
  }
  const unionSize = ta.size + tb.size - intersectionSize

  return intersectionSize / unionSize
}

/**
 * Computa ComparisonReport a partir dos pares (production_text, replay_text).
 * Threshold padrão: 0.4 — distingue paráfrases de respostas completamente diferentes.
 */
function buildComparisonReport(
  turns: Array<{
    turn_index:             number
    production_text:        string
    replay_text:            string
    production_latency_ms?: number
    replay_latency_ms?:     number
  }>,
  opts?: {
    threshold?:                  number
    production_outcome?:         string
    replay_outcome?:             string
    production_final_sentiment?: number
    replay_final_sentiment?:     number
  }
): Record<string, unknown> {
  const threshold = opts?.threshold ?? 0.4

  if (turns.length === 0) {
    return { similarity_score: 1.0, divergence_points: [] }
  }

  const similarities = turns.map(t => jaccardSimilarity(t.production_text, t.replay_text))
  const avgSimilarity = similarities.reduce((a, b) => a + b, 0) / similarities.length

  const divergencePoints = turns
    .map((t, i) => ({ ...t, similarity: similarities[i] as number }))
    .filter(t => t.similarity < threshold)
    .map(t => ({
      turn_index:      t.turn_index,
      production_text: t.production_text,
      replay_text:     t.replay_text,
      similarity:      Math.round(t.similarity * 10000) / 10000,
    }))

  const report: Record<string, unknown> = {
    similarity_score:  Math.round(avgSimilarity * 10000) / 10000,
    divergence_points: divergencePoints,
  }

  if (opts?.production_outcome !== undefined && opts?.replay_outcome !== undefined) {
    report["outcome_delta"] = {
      production_outcome: opts.production_outcome,
      replay_outcome:     opts.replay_outcome,
      diverged:           opts.production_outcome !== opts.replay_outcome,
    }
  }

  if (opts?.production_final_sentiment !== undefined && opts?.replay_final_sentiment !== undefined) {
    const delta = opts.replay_final_sentiment - opts.production_final_sentiment
    report["sentiment_delta"] = {
      production_final: Math.round(opts.production_final_sentiment * 10000) / 10000,
      replay_final:     Math.round(opts.replay_final_sentiment * 10000)    / 10000,
      delta:            Math.round(delta * 10000) / 10000,
    }
  }

  const prodLatencies  = turns.map(t => t.production_latency_ms).filter((v): v is number => v !== undefined)
  const replayLatencies = turns.map(t => t.replay_latency_ms).filter((v): v is number => v !== undefined)

  if (prodLatencies.length > 0 && replayLatencies.length > 0) {
    const prodAvg   = prodLatencies.reduce((a, b) => a + b, 0)   / prodLatencies.length
    const replayAvg = replayLatencies.reduce((a, b) => a + b, 0) / replayLatencies.length
    report["latency_delta"] = {
      production_avg_ms: Math.round(prodAvg * 100)   / 100,
      replay_avg_ms:     Math.round(replayAvg * 100)  / 100,
      delta_ms:          Math.round((replayAvg - prodAvg) * 100) / 100,
    }
  }

  return report
}

// ─── Schemas — Session Replayer tools ────────────────────────────────────────

const EvaluationContextGetInputSchema = z.object({
  session_token:  z.string().min(1),
  session_id:     z.string().min(1),
  // participant_id é um identificador OPACO (instance_id do avaliador, ex.
  // "evinstance_<hex>" ou "teste_demo-009"), nunca um UUID canônico — só humanos
  // recebem UUID. Exigir .uuid() aqui fazia TODA avaliação falhar no get_context.
  participant_id: z.string().min(1),
})

// ── Arc 6 — EvaluationCriterionResponse input schema ─────────────────────────

const EvidenceRefInputSchema = z.object({
  // Form-driven (T7b-2+) — shape que o LLM emite via buildEvaluationOutputSchema:
  // evidência do transcript por stream entry. `stream_entry_id` é o que alinha com a
  // evidência clicável do nível 3 (T9-C, C.3) e o que a UI/ingest consomem.
  stream_entry_id: z.string().min(1).optional(),
  excerpt:         z.string().max(1000).optional(),
  relevance_note:  z.string().max(1000).optional(),
  // Legado (Arc 6 pré-form-driven) — mantido opcional p/ compat (não rejeitar payloads antigos).
  event_id:        z.string().min(1).optional(),
  turn_index:      z.number().int().nonnegative().optional(),
  quote:           z.string().max(500).optional(),
  category:        z.enum(["positive", "negative", "neutral"]).optional(),
})

// ── Arc 13 Fase B — EvidenceEntry (stream-based, for ContestationThread) ─────

const EvidenceEntryInputSchema = z.object({
  /** Stream entry ID from the canonical Redis stream (e.g. "1715700000000-0") */
  stream_entry_id: z.string().min(1),
  /** Short excerpt of the message content (masked) */
  excerpt:         z.string().max(500),
  /** Why this excerpt supports the score assigned */
  relevance_note:  z.string().max(500),
})

/**
 * DimensionThreadInput — Arc 13 Fase B: per-dimension evaluation with evidence.
 * Required for each scored dimension. Skipped for auto_computed criteria.
 */
// T7b-3 — DEPRECATED input. A saída form-driven (T7b-2) usa `criterion_responses`; os
// threads round-1 nascem POR CRITÉRIO no ingest (T7a). Mantido como entrada opcional
// (compat), SEM os shims antigos (observation→justification, default de evidence_entries):
// o tool-use garante o shape e a validação recursiva + retry do ai-gateway (T7b-1) é a rede.
const DimensionThreadInputSchema = z.object({
  dimension_id:     z.string().min(1),
  /** Score assigned (0–max_score). Nullable when the dimension was N/A. */
  score:            z.number().min(0).nullable(),
  justification:    z.string().min(1).optional(),
  evidence_entries: z.array(EvidenceEntryInputSchema).default([]),
})

const EvaluationCriterionResponseInputSchema = z.object({
  criterion_id:  z.string().min(1),
  /** true when criterion is not applicable to this session */
  na:            z.boolean().default(false),
  // Contrato form-driven: score é null quando na=true (não é shim).
  score:         z.number().min(0).nullable().optional(),  // for type "score"
  boolean_value: z.boolean().optional(),           // for type "boolean"
  choice_value:  z.string().optional(),            // for type "choice"
  text_value:    z.string().optional(),            // for type "text"
  notes:         z.string().optional(),
  // T9-C.fix(mcp) — a saída form-driven (buildEvaluationOutputSchema) usa `justification`,
  // não `notes`. Sem aceitá-lo aqui, o Zod o descartava antes do ingest → a justificativa
  // por critério sumia no caminho do avaliador REAL. O ingest faz `notes || justification`.
  justification: z.string().optional(),
  evidence:      z.array(EvidenceRefInputSchema).default([]),
})

const KnowledgeSnippetInputSchema = z.object({
  snippet_id:   z.string().min(1),
  content:      z.string().min(1),
  score:        z.number().min(0).max(1),
  source_ref:   z.string().optional(),
  retrieved_at: z.string().optional(),
})

const EvaluationDimensionInputSchema = z.object({
  dimension_id: z.string().min(1),
  name:         z.string().min(1),
  score:        z.number().min(0).max(10),
  weight:       z.number().min(0).max(1).default(1),
  notes:        z.string().optional(),
  flags:        z.array(z.string()).default([]),
})

const ComparisonTurnInputSchema = z.object({
  turn_index:             z.number().int().nonnegative(),
  production_text:        z.string(),
  replay_text:            z.string(),
  production_latency_ms:  z.number().nonnegative().optional(),
  replay_latency_ms:      z.number().nonnegative().optional(),
})

const EvaluationSubmitInputSchema = z.object({
  session_token:      z.string().min(1),
  session_id:         z.string().min(1),
  // IDs opacos (instance_id do avaliador / evaluation_id prefixado tipo
  // "evinstance_<hex>") — não são UUID canônico. Exigir .uuid() fazia o submit
  // falhar mesmo após a pontuação. Ver get_context.
  participant_id:     z.string().min(1),
  evaluation_id:      z.string().min(1),
  // T7b — a nota geral NÃO é mais saída do LLM (form-driven): a evaluation-api a
  // recomputa de criterion_responses pelos pesos do form (T7a). Opcional/default 0;
  // o ingest a descarta e recomputa.
  composite_score:    z.number().min(0).max(10).optional().default(0),
  dimensions:         z.array(EvaluationDimensionInputSchema).default([]),
  summary:            z.string().min(1),
  highlights:         z.array(z.string()).default([]),
  improvement_points: z.array(z.string()).default([]),
  // T7b-3 — sem shim de coerção: a saída form-driven (tool-use) não emite
  // compliance_flags como objetos; o contrato é string[].
  compliance_flags:   z.array(z.string()).default([]),
  is_benchmark:       z.boolean().default(false),

  /**
   * Pares (produção vs replay) fornecidos pelo agente evaluator quando
   * comparison_mode: true no ReplayContext.
   * Quando presente, evaluation_submit computa o ComparisonReport e o
   * inclui no EvaluationResult publicado.
   */
  comparison_turns:           z.array(ComparisonTurnInputSchema).optional(),
  /** Outcome que o agente avaliaria para o replay (ex: "resolved", "abandoned") */
  comparison_replay_outcome:  z.string().optional(),
  /** Sentimento final estimado para o replay (−1 a 1) */
  comparison_replay_sentiment: z.number().min(-1).max(1).optional(),

  // ── Arc 6 — form-aware evaluation fields (optional, backward-compatible) ────

  /**
   * Structured responses to each criterion in the EvaluationForm.
   * Provided when the evaluator used an EvaluationForm (campaign-triggered evaluation).
   * Each entry maps one criterion_id to its scored response + evidence.
   */
  criterion_responses: z.array(EvaluationCriterionResponseInputSchema).optional(),

  /**
   * EvaluationForm ID that was used for this evaluation.
   * Taken from ReplayContext.evaluation_form.form_id when present.
   */
  form_id:     z.string().optional(),

  /**
   * EvaluationCampaign that triggered this evaluation (Arc 6).
   * Taken from ReplayContext.campaign_id when present.
   */
  campaign_id: z.string().optional(),

  /**
   * EvaluationInstance tracking record ID (Arc 6).
   * When present, evaluation_submit also publishes eval.instance.submitted
   * to evaluation.events so the evaluation-api can advance the instance lifecycle.
   */
  instance_id: z.string().optional(),

  /**
   * RAG snippets from mcp-server-knowledge used during evaluation.
   * Attached to the EvaluationResult for audit and feedback loop.
   */
  knowledge_snippets: z.array(KnowledgeSnippetInputSchema).optional(),

  // ── Arc 13 Fase B — per-dimension threads with evidence ──────────────────

  /**
   * Per-dimension evaluation with evidence entries.
   * Arc 13: required for all scored (non-auto_computed) dimensions.
   * Creates ContestationThread round=1 for each — the immutable audit trail.
   */
  dimension_threads: z.array(DimensionThreadInputSchema).optional(),

  /**
   * Type of agent being evaluated.
   * "ai_agent"    → Fluxo 2: evaluation_finalized immediately, no contestation
   * "human_agent" → Fluxo 1: contestation_open (or pre_review_pending)
   */
  evaluated_agent_type: z.enum(["human_agent", "ai_agent"]).default("human_agent"),
})

// ─── Registro das tools ───────────────────────────────────────────────────────

// ─── Schemas — evaluation_lock ────────────────────────────────────────────────

const EvaluationLockInputSchema = z.object({
  result_id:   z.string().min(1).describe("ID of the EvaluationResult to lock"),
  lock_reason: z.enum(["review_timeout", "max_rounds_reached", "manual"])
                .default("review_timeout")
                .describe("Reason the result is being locked permanently"),
})

export function registerEvaluationTools(server: McpServer, deps: EvaluationDeps): void {
  const { kafka, postgres, redis, proxyUrl, skillRegistryUrl, evaluationApiUrl } = deps
  // R5 — tool_trace + expected-trajectory enrichment sources (optional; degrade gracefully).
  const analyticsApiUrl  = deps.analyticsApiUrl  ?? process.env["ANALYTICS_API_URL"]    ?? "http://localhost:3500"
  const agentRegistryUrl = deps.agentRegistryUrl ?? process.env["AGENT_REGISTRY_URL"]   ?? "http://localhost:3300"
  // G-PROBE fase 2 — credencial de serviço para os endpoints de sistema/agente da
  // evaluation-api (ex.: pre-review). Lida do env; header omitido quando vazia (demo).
  const evalServiceToken = deps.serviceToken ?? process.env["PLUGHUB_EVALUATION_SERVICE_TOKEN"] ?? ""

  /**
   * Resolve a instância de um `participant_id` e devolve seu propósito declarado
   * (`agent_role`) junto com o `agent_type_id`.
   *
   * Por que resolver em vez de usar o `participant_id` cru: o hash é indexado por
   * `instance_id`, e os dois só coincidem por acidente no avaliador do Arc 6 (que
   * faz `agent_login` com `instance_id = evaluation_id` e passa o MESMO valor como
   * `participant_id`). Para qualquer outro participante — especialista, humano,
   * agente registrado via `agent_busy` — são valores distintos. O mapa
   * `{tenant}:participant:{id}:instance` (escrito por `agent_busy`) é a mesma via
   * que o `message_send` já usa; o fallback ao id cru cobre o caso do avaliador.
   *
   * `resolved: false` significa "não consegui PROVAR o papel" — distinto de
   * "provei que é executor". Quem chama deve NEGAR nos dois casos, mas a mensagem
   * de erro muda, e um gate que não prova nada não deve autorizar.
   */
  async function readAgentIdentity(
    tenantId: string,
    participantId: string,
  ): Promise<{ resolved: boolean; agentRole: string; agentTypeId: string; instanceKey: string }> {
    let instanceId = participantId
    try {
      const mapped = await redis.get(`${tenantId}:participant:${participantId}:instance`)
      if (mapped) instanceId = mapped
    } catch { /* fallback: participant_id cru (caminho do avaliador) */ }

    const instanceKey = `${tenantId}:agent:instance:${instanceId}`
    try {
      const [roleRaw, typeRaw] = await Promise.all([
        redis.hget(instanceKey, "agent_role"),
        redis.hget(instanceKey, "agent_type_id"),
      ])
      return {
        resolved:    Boolean(roleRaw),
        agentRole:   roleRaw ?? "",
        agentTypeId: typeRaw ?? "",
        instanceKey,
      }
    } catch (err) {
      console.warn(`[evaluation] leitura da instância falhou: ${instanceKey} — ${String(err)}`)
      return { resolved: false, agentRole: "", agentTypeId: "", instanceKey }
    }
  }

  // ── transcript_get ────────────────────────────────────────────────────────
  server.tool(
    "transcript_get",
    "Busca mensagens do transcript no PostgreSQL por transcript_id. " +
    "Retorna lista vazia se transcript não existe ou ainda não foi persistido. Spec 10.2.",
    TranscriptGetInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { transcript_id } = TranscriptGetInputSchema.parse(input)
        const messages = await postgres.fetchTranscript(transcript_id)
        return ok({ transcript_id, messages })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_context_resolve ────────────────────────────────────────────
  server.tool(
    "evaluation_context_resolve",
    "Lê declarações requires_context e requires_agent da evaluation skill. " +
    "Executa requires_context via proxy sidecar (localhost:7422). " +
    "Retorna external_context (mapa de resultados) e agent_context_needed + " +
    "agent_context_skill_id quando uma seção ativa declara requires_agent. " +
    "Falhas individuais de tool são logadas e omitidas. Spec 10.2.",
    EvaluationContextResolveInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { skill_id, template_vars, context_package } = EvaluationContextResolveInputSchema.parse(input)

        // Carrega evaluation skill do Skill Registry
        const skillRes = await fetch(`${skillRegistryUrl}/skills/${skill_id}`)
        if (!skillRes.ok) {
          return mcpError("skill_not_found", `Evaluation skill '${skill_id}' não encontrada`)
        }
        const skill = (await skillRes.json()) as EvalSkill

        // Filtra seções ativas (applies_when) — base para requires_context e requires_agent
        const activeSections = skill.sections.filter(s => sectionApplies(s, context_package))

        // Constrói mapa plano de variáveis para resolução de templates
        const baseVars: Record<string, unknown> = {
          evaluation_id: template_vars.evaluation_id,
          agent:         template_vars.agent,
          contact:       template_vars.contact,
          context:       template_vars.context,
        }

        const externalContext: Record<string, unknown> = {}

        // requires_context — seções ativas em sequência; fetches da mesma seção em paralelo
        for (const section of activeSections) {
          if (!section.requires_context || section.requires_context.length === 0) continue

          await Promise.all(
            section.requires_context.map(async (req) => {
              try {
                const vars          = { ...baseVars, ...externalContext }
                const resolvedInput = resolveTemplatesInObject(req.input, vars)

                // Chama a tool via proxy sidecar (JSON-RPC 2.0)
                const rpcReq = {
                  jsonrpc: "2.0",
                  id:      1,
                  method:  "tools/call",
                  params:  { name: req.tool, arguments: resolvedInput },
                }
                const res = await fetch(proxyUrl, {
                  method:  "POST",
                  headers: { "Content-Type": "application/json" },
                  body:    JSON.stringify(rpcReq),
                })

                if (!res.ok) {
                  console.warn(`evaluation_context_resolve: tool ${req.tool} HTTP ${res.status}`)
                  return
                }

                const rpcRes = (await res.json()) as {
                  result?: { content?: Array<{ type: string; text: string }> }
                  error?: unknown
                }

                if (rpcRes.error) {
                  console.warn(`evaluation_context_resolve: tool ${req.tool} RPC error`, rpcRes.error)
                  return
                }

                const textBlock = rpcRes.result?.content?.find(c => c.type === "text")
                if (textBlock) {
                  try {
                    externalContext[req.output_key] = JSON.parse(textBlock.text)
                  } catch {
                    externalContext[req.output_key] = textBlock.text
                  }
                }
              } catch (err) {
                console.warn(`evaluation_context_resolve: tool ${req.tool} failed`, err)
              }
            })
          )
        }

        // requires_agent — todas as seções ativas que declaram delegação a agente especialista
        // Cada seção pode declarar um agente diferente; o flow percorre a fila até esgotar.
        const agentQueue = activeSections
          .filter(s => s.requires_agent)
          .map(s => ({ skill_id: s.requires_agent!.skill_id, output_key: s.requires_agent!.output_key }))

        return ok({
          external_context:   externalContext,
          agent_context_queue: agentQueue,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_agent_context_next ────────────────────────────────────────
  // Pop-and-accumulate: desempilha o próximo agente especialista da fila,
  // mescla o resultado do agente anterior no acumulador e devolve o estado
  // atualizado da iteração para que o flow possa continuar ou avançar para evaluate.
  server.tool(
    "evaluation_agent_context_next",
    "Gerencia a fila de agentes especialistas do fluxo de avaliação. " +
    "Mescla o resultado do agente anterior (task_result) no acumulador e desempilha o " +
    "próximo agente da fila. Retorna has_next (bool), current_skill_id, current_output_key, " +
    "remaining (fila restante) e accumulated (contexto acumulado de todos os agentes já executados). " +
    "Spec 10.2.",
    EvaluationAgentContextNextInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { queue, task_result, current_output_key, accumulated } =
          EvaluationAgentContextNextInputSchema.parse(input)

        // Mescla resultado do agente anterior no acumulador
        const newAccumulated: Record<string, unknown> = { ...accumulated }
        if (task_result !== undefined && current_output_key) {
          newAccumulated[current_output_key] = task_result
        }

        // Desempilha o próximo agente
        const [next, ...remaining] = queue

        if (!next) {
          return ok({
            has_next:           false,
            current_skill_id:   "",
            current_output_key: "",
            remaining:          [],
            accumulated:        newAccumulated,
          })
        }

        return ok({
          has_next:           true,
          current_skill_id:   next.skill_id,
          current_output_key: next.output_key,
          remaining,
          accumulated:        newAccumulated,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_publish ────────────────────────────────────────────────────
  server.tool(
    "evaluation_publish",
    "Calcula scores deterministicamente (média ponderada bottom-up), " +
    "monta e publica evento evaluation.completed em evaluation.results. Spec 10.2.",
    EvaluationPublishInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = EvaluationPublishInputSchema.parse(input)
        const {
          evaluation_id, tenant_id, contact_id, agent_id, agent_type, pool_id,
          skill_id, triggered_by, llm_items, overall_observation, context_package,
        } = parsed

        // Carrega evaluation skill para calcular scores
        const skillRes = await fetch(`${skillRegistryUrl}/skills/${skill_id}`)
        if (!skillRes.ok) {
          return mcpError("skill_not_found", `Evaluation skill '${skill_id}' não encontrada`)
        }
        const skill = (await skillRes.json()) as EvalSkill

        // Calcula scores deterministicamente
        const { scores, itemsExcluded } = calculateScores(skill, llm_items, context_package)

        const evaluated_at = new Date().toISOString()

        const event: Record<string, unknown> = {
          evaluation_id,
          tenant_id,
          contact_id,
          agent_id,
          agent_type,
          pool_id,
          skill_id,
          evaluated_at,
          triggered_by,
          scores,
          overall_observation: overall_observation ?? null,
        }

        if (itemsExcluded.length > 0) {
          event["items_excluded"] = itemsExcluded
        }

        await kafka.publish("evaluation.results", event)

        return ok({ evaluation_id, evaluated_at, sections_scored: scores.length })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_context_get (Session Replayer) ─────────────────────────────
  server.tool(
    "evaluation_context_get",
    "Retorna o ReplayContext completo para avaliação de qualidade pós-sessão. " +
    "Inclui todos os eventos do stream com original_content desmascarado, " +
    "sentimento, participantes e metadados da sessão original. " +
    "Arc 6: quando o ReplayContext contém evaluation_form, campaign_id ou instance_id, " +
    "esses campos são também surfaced como top-level convenience fields na resposta. " +
    "Disponível apenas para agentes cujo skill declara agent_role: evaluator no registry. " +
    "Requer que o Session Replayer tenha processado a sessão previamente. " +
    "Spec: Session Replayer.",
    EvaluationContextGetInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id } =
          EvaluationContextGetInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        // ── Gate de autorização — FALHA FECHADO ──────────────────────────────
        //
        // Este endpoint devolve o ReplayContext, que carrega `original_content`
        // DESMASCARADO. O gate anterior lia o campo `role` (que produtor nenhum
        // escrevia) com default `""`, e a condição era `if (role && role !== ...)`:
        // string vazia curto-circuitava e a verificação simplesmente NÃO ACONTECIA.
        // Qualquer agente com session_token válido lia PII desmascarada.
        //
        // Agora lê `agent_role`, carimbado pelo `agent_login` a partir do REGISTRY
        // (nunca do input do agente — auto-declaração é asserção, não autorização),
        // e exige leitura POSITIVA: sem prova de propósito, nega. Um gate que não
        // consegue provar nada não deve autorizar.
        //
        // `reviewer` saiu do vocabulário aceito aqui: a revisão humana do Arc 13 é
        // o contrato REST (`contestation_router`) com Bearer JWT + ABAC, não MCP.
        const identity = await readAgentIdentity(tenant_id, participant_id)

        if (!identity.resolved) {
          return mcpError(
            "unauthorized",
            `evaluation_context_get requer agent_role 'evaluator' declarado no registry. ` +
            `Não foi possível LER o papel do participante '${participant_id}' ` +
            `(hash ${identity.instanceKey} sem campo "agent_role"). ` +
            `A instância fez agent_login? O skill declara agent_role: evaluator?`
          )
        }
        if (identity.agentRole !== "evaluator") {
          return mcpError(
            "unauthorized",
            `evaluation_context_get requer agent_role 'evaluator' (atual: ${identity.agentRole})`
          )
        }

        // Lê ReplayContext do Redis — escrito pelo Replayer
        const contextKey = `${tenant_id}:replay:${session_id}:context`
        const raw = await redis.get(contextKey)

        if (!raw) {
          return mcpError(
            "replay_not_ready",
            `ReplayContext não encontrado para sessão '${session_id}'. ` +
            "O Session Replayer pode ainda não ter processado esta sessão."
          )
        }

        let context: Record<string, unknown>
        try {
          context = JSON.parse(raw) as Record<string, unknown>
        } catch {
          return mcpError("parse_error", "ReplayContext inválido no Redis")
        }

        // ── Arc 6: surface form + campaign metadata as top-level fields ───────
        // The evaluator agent can read these directly rather than digging into context.
        // All fields are optional — Arc 3 contexts will simply not have them.
        const arc6Meta: Record<string, unknown> = {}

        if (context["evaluation_form"] !== undefined && context["evaluation_form"] !== null) {
          arc6Meta["evaluation_form"] = context["evaluation_form"]
          // T7b-2b — JSON Schema derivado do form (montado UPSTREAM). O skill o referencia
          // via json_schema_ref → reason usa tool-use nativo no ai-gateway (T7b-1).
          const outSchema = buildEvaluationOutputSchema(context["evaluation_form"])
          if (outSchema) arc6Meta["evaluation_output_schema"] = outSchema
        }
        if (typeof context["campaign_id"] === "string") {
          arc6Meta["campaign_id"] = context["campaign_id"]
        }
        if (typeof context["instance_id"] === "string") {
          arc6Meta["instance_id"] = context["instance_id"]
        }
        if (typeof context["comparison_mode"] === "boolean") {
          arc6Meta["comparison_mode"] = context["comparison_mode"]
        }

        // Surface participant role/type summary for the evaluator — extracted from
        // context.participants so the agent doesn't need to iterate events.
        const participantSummary: Array<Record<string, unknown>> = []
        const participants = context["participants"]
        if (Array.isArray(participants)) {
          for (const p of participants) {
            if (p && typeof p === "object") {
              const pt = p as Record<string, unknown>
              participantSummary.push({
                participant_id: pt["participant_id"],
                role:           pt["role"],
                agent_type_id:  pt["agent_type_id"] ?? null,
              })
            }
          }
        }

        // ── Arc 13 Fase B: fetch CalibrationNotes for this campaign ──────────
        // The evaluator AI reads these before scoring to calibrate its judgment.
        let calibrationNotes: unknown[] = []
        const campaignId = arc6Meta["campaign_id"] as string | undefined
        if (campaignId && evaluationApiUrl) {
          try {
            const notesRes = await fetch(
              `${evaluationApiUrl}/v1/evaluation/calibration-notes?campaign_id=${encodeURIComponent(campaignId)}&published_to_kb=true&limit=20`,
              { headers: { "X-Tenant-ID": tenant_id } }
            )
            if (notesRes.ok) {
              const notesData = await notesRes.json() as { notes?: unknown[] }
              calibrationNotes = notesData.notes ?? []
            }
          } catch (err) {
            console.warn("evaluation_context_get: failed to fetch calibration_notes", err)
          }
        }

        // ── T8-B2: rubrica-template efetiva (instruções gerais do avaliador) ──
        // Resolve override pub. da campanha → default pub. do tenant → built-in (sempre
        // devolve um body). O skill passa isto ao reason como `rubric_instructions`.
        let rubricInstructions = ""
        let rubricSource = ""
        if (evaluationApiUrl) {
          try {
            const q = campaignId ? `&campaign_id=${encodeURIComponent(campaignId)}` : ""
            const rubRes = await fetch(
              `${evaluationApiUrl}/v1/evaluation/rubric-templates/effective?tenant_id=${encodeURIComponent(tenant_id)}${q}`,
            )
            if (rubRes.ok) {
              const r = await rubRes.json() as { body?: string; source?: string }
              rubricInstructions = r.body ?? ""
              rubricSource = r.source ?? ""
            }
          } catch (err) {
            console.warn("evaluation_context_get: failed to fetch effective rubric", err)
          }
        }

        // ── R5: tool_trace — evidência de execução (tool correctness) ─────────
        // Busca os mcp.tool_call da sessão (analytics-api, escopo session_id, ordem
        // cronológica). Campos sempre-gravados: tool_name, allowed, injection_detected,
        // duration_ms (sem input/output snapshot — isso é R7). Degrada para [].
        let toolTrace: unknown[] = []
        if (analyticsApiUrl) {
          try {
            const trRes = await fetch(
              `${analyticsApiUrl}/v1/audit/mcp-calls?tenant_id=${encodeURIComponent(tenant_id)}` +
              `&session_id=${encodeURIComponent(session_id)}&limit=500`,
            )
            if (trRes.ok) {
              const trData = await trRes.json() as { calls?: unknown[] }
              toolTrace = trData.calls ?? []
            }
          } catch (err) {
            console.warn("evaluation_context_get: failed to fetch tool_trace", err)
          }
        }

        // ── R5: flow_definition — trajetória ESPERADA (policy adherence) ──────
        // A trajetória REAL já vem em context.pipeline_state (R5/B, session-replayer).
        // Aqui buscamos a definição do flow executado (flow_id) no agent-registry para
        // o avaliador comparar esperado × real. Degrada para null.
        let flowDefinition: unknown = null
        const pipelineState = context["pipeline_state"]
        const flowId =
          pipelineState && typeof pipelineState === "object"
            ? (pipelineState as Record<string, unknown>)["flow_id"]
            : undefined
        if (typeof flowId === "string" && flowId.length > 0 && agentRegistryUrl) {
          try {
            const skRes = await fetch(
              `${agentRegistryUrl}/v1/skills/${encodeURIComponent(flowId)}`,
              { headers: { "x-tenant-id": tenant_id } },
            )
            if (skRes.ok) {
              const sk = await skRes.json() as Record<string, unknown>
              // Surface só o necessário p/ comparar trajetória: entry + steps (id/type/edges).
              flowDefinition = {
                skill_id: sk["skill_id"] ?? flowId,
                version:  sk["version"] ?? null,
                flow:     sk["flow"] ?? null,
              }
            }
          } catch (err) {
            console.warn("evaluation_context_get: failed to fetch flow_definition", err)
          }
        }

        // R5 — diagnóstico (sem PII): o que a evidência de execução trouxe.
        console.warn(
          `evaluation_context_get evidence: session=${session_id} ` +
          `tool_trace=${Array.isArray(toolTrace) ? toolTrace.length : "n/a"} ` +
          `flow_definition=${flowDefinition ? "present" : "null"} ` +
          `pipeline_state=${context["pipeline_state"] ? "present" : "absent"}`
        )

        return ok({
          session_id,
          participant_id,
          context,
          retrieved_at:         new Date().toISOString(),
          // Arc 6 convenience fields — undefined keys are omitted by JSON.stringify
          ...(Object.keys(arc6Meta).length > 0 ? arc6Meta : {}),
          // Participant summary (always present for transparency)
          participant_summary: participantSummary,
          // Arc 13 Fase B: calibration notes for RAG-based evaluator calibration
          calibration_notes: calibrationNotes,
          // T8-B2: instruções gerais (rubrica-template efetiva) — fonte do prompt do avaliador
          rubric_instructions: rubricInstructions,
          rubric_source:       rubricSource,
          // R5: execution evidence for AI tier-2 criteria (tool correctness, policy
          // adherence). tool_trace = mcp.tool_call da sessão; flow_definition = trajetória
          // esperada. A trajetória real está em context.pipeline_state.
          tool_trace:      toolTrace,
          flow_definition: flowDefinition,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_submit (Session Replayer) ──────────────────────────────────
  server.tool(
    "evaluation_submit",
    "Submete o resultado de avaliação de qualidade pós-sessão. " +
    "Publica EvaluationResult em evaluation.events (Kafka). " +
    "Arc 6: aceita criterion_responses[], form_id, campaign_id, instance_id e knowledge_snippets. " +
    "Arc 13 Fase B: aceita dimension_threads[] (obrigatório por dimensão pontuada — " +
    "cada entry com dimension_id, score, justification e evidence_entries[] de stream entries) e " +
    "evaluated_agent_type ('human_agent'|'ai_agent'). " +
    "Critérios auto_computed são ignorados — preenchidos automaticamente pelo SessionMetricsExtractor. " +
    "Quando instance_id presente, também publica eval.instance.submitted para o ciclo de vida " +
    "da EvaluationInstance na evaluation-api. " +
    "A persistência no PostgreSQL é responsabilidade de um consumer dedicado — " +
    "esta tool nunca escreve diretamente no banco. " +
    "Reduz o TTL do ReplayContext no Redis após submissão. " +
    "Spec: Session Replayer + arc13-review-contestation.md.",
    EvaluationSubmitInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = EvaluationSubmitInputSchema.parse(input)
        const {
          session_token, session_id, participant_id, evaluation_id,
          composite_score, dimensions, summary, highlights,
          improvement_points, compliance_flags, is_benchmark,
          comparison_turns, comparison_replay_outcome, comparison_replay_sentiment,
          // Arc 6
          criterion_responses, form_id, campaign_id, instance_id, knowledge_snippets,
          // Arc 13 Fase B
          dimension_threads, evaluated_agent_type,
        } = parsed

        const { tenant_id } = verifySessionToken(session_token)

        // ── Identidade + propósito do avaliador ──────────────────────────────
        const identity = await readAgentIdentity(tenant_id, participant_id)

        // Gate simétrico ao do evaluation_context_get: só quem tem propósito
        // `evaluator` DECLARADO NO REGISTRY escreve resultado de avaliação.
        // Falha fechado — sem prova de propósito, nega.
        if (identity.agentRole !== "evaluator") {
          return mcpError(
            "unauthorized",
            `evaluation_submit requer agent_role 'evaluator' declarado no registry ` +
            `(lido: ${identity.agentRole || "<ausente>"}). ` +
            `Participante '${participant_id}' → ${identity.instanceKey}.`
          )
        }

        // Degradação nunca é silenciosa: "evaluator_unknown" é um valor PLAUSÍVEL
        // que atravessa o pipeline inteiro e só aparece como buraco nos cortes por
        // avaliador do Arc 13. Se cair aqui, o log diz por quê.
        let agent_type_id = identity.agentTypeId
        if (!agent_type_id) {
          agent_type_id = "evaluator_unknown"
          console.warn(
            `[evaluation_submit] agent_type_id AUSENTE em ${identity.instanceKey} — ` +
            `resultado será carimbado como "evaluator_unknown" e os cortes por ` +
            `avaliador (calibração Arc 13) ficam cegos para esta avaliação.`
          )
        }

        // Lê ReplayContext para enriquecer o resultado
        let session_outcome: string   | undefined
        let production_final_sentiment: number | undefined
        // Arc 6: fallback — read campaign context from ReplayContext if not supplied by caller
        let resolved_form_id     = form_id
        let resolved_campaign_id = campaign_id
        let resolved_instance_id = instance_id

        try {
          const ctxRaw = await redis.get(`${tenant_id}:replay:${session_id}:context`)
          if (ctxRaw) {
            const ctx  = JSON.parse(ctxRaw) as Record<string, unknown>
            const meta = ctx["session_meta"] as Record<string, unknown> | undefined
            session_outcome = meta?.["outcome"] as string | undefined

            // Sentimento final da produção (último entry do array)
            const sentiment = ctx["sentiment"] as Array<{ score: number }> | undefined
            if (Array.isArray(sentiment) && sentiment.length > 0) {
              production_final_sentiment = sentiment[sentiment.length - 1]?.score
            }

            // Arc 6: use ReplayContext values as fallback when caller didn't supply them
            if (!resolved_form_id && typeof ctx["evaluation_form"] === "object" && ctx["evaluation_form"] !== null) {
              const form = ctx["evaluation_form"] as Record<string, unknown>
              if (typeof form["form_id"] === "string") resolved_form_id = form["form_id"]
            }
            if (!resolved_campaign_id && typeof ctx["campaign_id"] === "string") {
              resolved_campaign_id = ctx["campaign_id"]
            }
            if (!resolved_instance_id && typeof ctx["instance_id"] === "string") {
              resolved_instance_id = ctx["instance_id"]
            }
          }
        } catch { /* non-fatal */ }

        const evaluated_at = new Date().toISOString()

        // ── Comparison Mode: computa ComparisonReport se turns fornecidos ──────
        let comparison: Record<string, unknown> | undefined
        if (comparison_turns && comparison_turns.length > 0) {
          // Normalise optional number fields: strip `undefined` so exactOptionalPropertyTypes is satisfied
          const normalisedTurns = comparison_turns.map(t => {
            const r: { turn_index: number; production_text: string; replay_text: string; production_latency_ms?: number; replay_latency_ms?: number } = {
              turn_index:      t.turn_index,
              production_text: t.production_text,
              replay_text:     t.replay_text,
            }
            if (t.production_latency_ms !== undefined) r.production_latency_ms = t.production_latency_ms
            if (t.replay_latency_ms     !== undefined) r.replay_latency_ms     = t.replay_latency_ms
            return r
          })
          const compOpts: { production_outcome?: string; replay_outcome?: string; production_final_sentiment?: number; replay_final_sentiment?: number } = {}
          if (session_outcome             !== undefined) compOpts.production_outcome         = session_outcome
          if (comparison_replay_outcome   !== undefined) compOpts.replay_outcome             = comparison_replay_outcome
          if (production_final_sentiment  !== undefined) compOpts.production_final_sentiment = production_final_sentiment
          if (comparison_replay_sentiment !== undefined) compOpts.replay_final_sentiment     = comparison_replay_sentiment
          comparison = buildComparisonReport(normalisedTurns, compOpts)
        }

        const result: Record<string, unknown> = {
          event_type:         "evaluation.completed",
          evaluation_id,
          session_id,
          tenant_id,
          evaluator_id:       participant_id,
          agent_type_id,
          composite_score,
          dimensions,
          summary,
          highlights,
          improvement_points,
          compliance_flags,
          session_outcome,
          is_benchmark,
          evaluated_at,
          // eval_status is always "submitted" on first publish — reviewer may change it later
          eval_status:        "submitted",
        }

        // Inclui comparison apenas quando presente (comparison_mode: true)
        if (comparison !== undefined) {
          result["comparison"] = comparison
        }

        // ── Arc 6: include form-aware fields when present ─────────────────────
        if (resolved_form_id     !== undefined) result["form_id"]     = resolved_form_id
        if (resolved_campaign_id !== undefined) result["campaign_id"] = resolved_campaign_id
        if (resolved_instance_id !== undefined) result["instance_id"] = resolved_instance_id

        if (criterion_responses && criterion_responses.length > 0) {
          result["criterion_responses"] = criterion_responses
        }
        if (knowledge_snippets && knowledge_snippets.length > 0) {
          result["knowledge_snippets"] = knowledge_snippets
        }

        // ── Arc 13 Fase B: include dimension_threads and evaluated_agent_type ──
        if (dimension_threads && dimension_threads.length > 0) {
          result["dimension_threads"] = dimension_threads
        }
        result["evaluated_agent_type"] = evaluated_agent_type ?? "human_agent"

        // Publica evaluation.completed em evaluation.events — consumer persiste no PostgreSQL
        await kafka.publish("evaluation.events", result)

        // ── Arc 6: publish lifecycle event when instance_id is present ────────
        // This allows the evaluation-api to advance the EvaluationInstance from
        // "in_progress" → "submitted" without polling.
        if (resolved_instance_id) {
          try {
            const instanceEvent: Record<string, unknown> = {
              event_type:    "eval.instance.submitted",
              instance_id:   resolved_instance_id,
              evaluation_id,
              session_id,
              tenant_id,
              evaluator_id:  participant_id,
              agent_type_id,
              composite_score,
              evaluated_at,
            }
            if (resolved_form_id)     instanceEvent["form_id"]     = resolved_form_id
            if (resolved_campaign_id) instanceEvent["campaign_id"] = resolved_campaign_id
            await kafka.publish("evaluation.events", instanceEvent)
          } catch (e) {
            // Non-fatal — the main result was already published
            console.warn("evaluation_submit: failed to publish eval.instance.submitted", e)
          }
        }

        // Reduz TTL do ReplayContext — já foi consumido
        try {
          await redis.expire(`${tenant_id}:replay:${session_id}:context`, 60)
        } catch { /* non-fatal */ }

        return ok({
          submitted:            true,
          evaluation_id,
          session_id,
          composite_score,
          evaluated_at,
          comparison_included:  comparison !== undefined,
          // Arc 6 — indicates which optional fields were included
          criterion_responses_included: (criterion_responses?.length ?? 0) > 0,
          knowledge_snippets_included:  (knowledge_snippets?.length ?? 0) > 0,
          instance_lifecycle_published: resolved_instance_id !== undefined,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_threads_get (Arc 13 Fase C) ───────────────────────────────
  // Busca os ContestationThreads de uma EvaluationInstance por instância.
  // Usado pelo agente_pre_revisor_v1 para ler os threads round=1 do avaliador.
  server.tool(
    "evaluation_threads_get",
    "Retorna os ContestationThreads de uma EvaluationInstance. " +
    "Cada thread corresponde a uma dimensão do formulário de avaliação. " +
    "Arc 13 Fase C: usado pelo agente_pre_revisor_v1 para ler o thread round=1 " +
    "(author_type=evaluator_ai) antes de submeter a revisão pré-publicação. " +
    "Requer role evaluator ou reviewer no session_token.",
    {
      session_token: z.string().min(1).describe("JWT do agente revisor (role: evaluator ou reviewer)"),
      instance_id:   z.string().min(1).describe("ID da EvaluationInstance a consultar"),
    } as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = z.object({
          session_token: z.string().min(1),
          instance_id:   z.string().min(1),
        }).parse(input)

        const { tenant_id } = verifySessionToken(parsed.session_token)
        const apiBase = evaluationApiUrl ?? "http://localhost:3400"

        const resp = await fetch(
          `${apiBase}/v1/evaluation/instances/${encodeURIComponent(parsed.instance_id)}/threads`,
          { headers: { "X-Tenant-ID": tenant_id } }
        )

        if (!resp.ok) {
          const text = await resp.text().catch(() => "")
          return mcpError("threads_fetch_failed", `evaluation-api returned ${resp.status}: ${text}`)
        }

        const data = await resp.json() as Record<string, unknown>
        return ok({ instance_id: parsed.instance_id, threads: data["threads"] ?? [] })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_pre_review_submit (Arc 13 Fase C) ─────────────────────────
  // Submete a revisão pré-publicação do revisor AI (agente_pre_revisor_v1).
  // Escreve um ContestationThread round=1 com author_type=pre_reviewer_ai
  // por dimensão revisada. Calibration_signal opcional → Curator Queue.
  const EvidenceEntryPreReviewSchema = z.object({
    stream_entry_id: z.string().min(1),
    excerpt:         z.string().max(500),
    relevance_note:  z.string().max(500),
  })

  const DimensionReviewSchema = z.object({
    /** ID da dimensão do formulário */
    dimension_id:     z.string().min(1),
    /** approve → aceita o score original; adjust → propõe score_override */
    action:           z.enum(["approve", "adjust"]),
    /** Obrigatório quando action=adjust */
    score_override:   z.number().min(0).optional(),
    /** Evidências revisadas — obrigatório quando action=adjust */
    revised_evidence: z.array(EvidenceEntryPreReviewSchema).optional(),
    /** Justificativa — obrigatória em todos os casos */
    justification:    z.string().min(10),
  })

  const CalibrationSignalPreReviewSchema = z.object({
    severity:    z.enum(["low", "medium", "high"]),
    dimension_id: z.string().min(1),
    observation: z.string().min(10),
  })

  const EvaluationPreReviewSubmitInputSchema = z.object({
    session_token:      z.string().min(1),
    instance_id:        z.string().min(1),
    /** Revisão por dimensão — mínimo 1 entry */
    dimension_reviews:  z.array(DimensionReviewSchema).min(1),
    /** Sinal de calibração (opcional) — disparado apenas quando há padrão sistemático */
    calibration_signal: CalibrationSignalPreReviewSchema.optional(),
  })

  server.tool(
    "evaluation_pre_review_submit",
    "Submete a revisão pré-publicação do revisor AI (agente_pre_revisor_v1). " +
    "Para cada dimensão: action=approve aceita o score original; " +
    "action=adjust propõe score_override com revised_evidence[] e justification obrigatórios. " +
    "calibration_signal (opcional): emitido apenas quando há evidência de padrão sistemático — " +
    "não por discordância pontual. Dispara automaticamente uma CurationReview " +
    "para o curador humano de forma assíncrona (não bloqueia). " +
    "Endpoint: POST /v1/evaluation/instances/{id}/pre-review na evaluation-api. " +
    "Arc 13 Fase C.",
    EvaluationPreReviewSubmitInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = EvaluationPreReviewSubmitInputSchema.parse(input)
        const { session_token, instance_id, dimension_reviews, calibration_signal } = parsed

        const { tenant_id } = verifySessionToken(session_token)
        const apiBase = evaluationApiUrl ?? process.env["EVALUATION_API_URL"] ?? "http://localhost:3400"

        // Extrai agent_type_id do revisor para registrar como author_id
        let reviewer_agent_id = "pre_reviewer_unknown"
        try {
          // O session_token do revisor contém sub = agent_type_id
          const payload = JSON.parse(
            Buffer.from(session_token.split(".")[1] ?? "", "base64url").toString("utf8")
          ) as Record<string, unknown>
          if (typeof payload["sub"] === "string") reviewer_agent_id = payload["sub"]
          else if (typeof payload["agent_type_id"] === "string") reviewer_agent_id = payload["agent_type_id"]
        } catch { /* non-fatal */ }

        const body: Record<string, unknown> = {
          dimension_reviews,
          reviewer_agent_id,
        }
        if (calibration_signal !== undefined) {
          body["calibration_signal"] = calibration_signal
        }

        const resp = await fetch(
          `${apiBase}/v1/evaluation/instances/${encodeURIComponent(instance_id)}/pre-review`,
          {
            method:  "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Tenant-ID":  tenant_id,
              // G-PROBE fase 2 — credencial de serviço (omitida quando não configurada).
              ...(evalServiceToken ? { "X-Service-Token": evalServiceToken } : {}),
            },
            body: JSON.stringify(body),
          }
        )

        if (!resp.ok) {
          const text = await resp.text().catch(() => "")
          return mcpError("pre_review_failed", `evaluation-api returned ${resp.status}: ${text}`)
        }

        const data = await resp.json() as Record<string, unknown>
        return ok({
          submitted:             true,
          instance_id,
          pre_review_complete:   data["pre_review_complete"] ?? true,
          contestation_state:    data["contestation_state"],
          dimensions_adjusted:   dimension_reviews.filter(d => d.action === "adjust").length,
          dimensions_approved:   dimension_reviews.filter(d => d.action === "approve").length,
          calibration_signal_sent: calibration_signal !== undefined,
          curation_review_created: data["curation_review_created"] ?? false,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_review_submit (Arc 13 Fase D) ─────────────────────────────
  // Submete a decisão do revisor pós-contestação (agente_revisor_v1 ou revisor humano).
  // Para cada dimensão contestada: upheld mantém a nota; revised propõe score_override.
  const EvidenceEntryReviewSchema = z.object({
    stream_entry_id: z.string().min(1),
    excerpt:         z.string().max(500),
    relevance_note:  z.string().max(500),
  })

  const DimensionDecisionSchema = z.object({
    /** ID da dimensão contestada — deve ter round=2 na instância */
    dimension_id:     z.string().min(1),
    /** upheld: mantém nota do avaliador; revised: altera com score_override */
    decision:         z.enum(["upheld", "revised"]),
    /** Obrigatório quando decision=revised */
    score_override:   z.number().min(0).optional(),
    /** Obrigatório quando decision=revised — evidências que suportam a alteração */
    evidence_entries: z.array(EvidenceEntryReviewSchema).optional(),
    /** Justificativa — obrigatória em todos os casos */
    justification:    z.string().min(10),
  })

  const EvaluationReviewSubmitInputSchema = z.object({
    session_token:       z.string().min(1),
    instance_id:         z.string().min(1),
    /** Decisão por dimensão — apenas dimensões contestadas (round=2) */
    dimension_decisions: z.array(DimensionDecisionSchema).min(1),
    /** ID do revisor (user_id para humano, agent_type_id para AI) */
    reviewer_id:         z.string().optional(),
  })

  server.tool(
    "evaluation_review_submit",
    "Submete a decisão do revisor pós-contestação por dimensão contestada. " +
    "Usado pelo agente_revisor_v1 (reviewer_type=ai) e pelo workflow de revisão humana. " +
    "decision=upheld mantém a nota original do avaliador. " +
    "decision=revised exige score_override + evidence_entries[] obrigatórios. " +
    "Justificativa obrigatória em todos os casos — mesmo quando upheld. " +
    "Endpoint: POST /v1/evaluation/instances/{id}/review na evaluation-api. " +
    "Arc 13 Fase D.",
    EvaluationReviewSubmitInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = EvaluationReviewSubmitInputSchema.parse(input)
        const { session_token, instance_id, dimension_decisions, reviewer_id } = parsed

        const { tenant_id } = verifySessionToken(session_token)
        const apiBase = evaluationApiUrl ?? "http://localhost:3400"

        // Extrai reviewer_id do token se não fornecido
        let resolvedReviewerId = reviewer_id
        if (!resolvedReviewerId) {
          try {
            const payload = JSON.parse(
              Buffer.from(session_token.split(".")[1] ?? "", "base64url").toString("utf8")
            ) as Record<string, unknown>
            if (typeof payload["sub"] === "string")              resolvedReviewerId = payload["sub"]
            else if (typeof payload["agent_type_id"] === "string") resolvedReviewerId = payload["agent_type_id"]
          } catch { /* non-fatal */ }
        }

        const body: Record<string, unknown> = {
          dimension_decisions,
          reviewer_id: resolvedReviewerId,
        }

        const resp = await fetch(
          `${apiBase}/v1/evaluation/instances/${encodeURIComponent(instance_id)}/review`,
          {
            method:  "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Tenant-ID":  tenant_id,
            },
            body: JSON.stringify(body),
          }
        )

        if (!resp.ok) {
          const text = await resp.text().catch(() => "")
          return mcpError("review_failed", `evaluation-api returned ${resp.status}: ${text}`)
        }

        const data = await resp.json() as Record<string, unknown>
        return ok({
          submitted:           true,
          instance_id,
          contestation_state:  data["contestation_state"],
          current_round:       data["current_round"],
          finalized:           data["finalized"] ?? false,
          dimensions_upheld:   dimension_decisions.filter(d => d.decision === "upheld").length,
          dimensions_revised:  dimension_decisions.filter(d => d.decision === "revised").length,
        })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── evaluation_lock (Arc 6 v2) ────────────────────────────────────────────
  // Called by the `congelar_resultado` step in review workflow YAMLs.
  // Permanently locks an EvaluationResult — no further review or contestation actions possible.
  server.tool(
    "evaluation_lock",
    "Congela permanentemente um EvaluationResult. " +
    "Chamado pelo step congelar_resultado nos workflows de revisão (skill_revisao_*.yaml). " +
    "Após lock, eval_status='locked' é irreversível — ações de revisão ou contestação retornam 409. " +
    "Arc 6 v2.",
    EvaluationLockInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { result_id, lock_reason } = EvaluationLockInputSchema.parse(input)

        const apiBase = evaluationApiUrl ?? "http://localhost:3400"

        const resp = await fetch(`${apiBase}/v1/evaluation/results/${result_id}/lock`, {
          method:  "POST",
          headers: { "content-type": "application/json" },
          body:    JSON.stringify({ locked_by: "workflow", lock_reason }),
        })

        if (resp.status === 409) {
          // Already locked — idempotent, treat as success
          return ok({ result_id, locked: true, already_locked: true, lock_reason })
        }

        if (!resp.ok) {
          const text = await resp.text().catch(() => "")
          return mcpError("lock_failed", `evaluation-api returned ${resp.status}: ${text}`)
        }

        const data = await resp.json() as Record<string, unknown>
        return ok({ result_id, locked: true, lock_reason, eval_status: data["eval_status"] })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )
}
