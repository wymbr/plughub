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
  participant_id: z.string().uuid(),
})

// ── Arc 6 — EvaluationCriterionResponse input schema ─────────────────────────

const EvidenceRefInputSchema = z.object({
  /** event_id from the replay transcript */
  event_id:   z.string().min(1),
  turn_index: z.number().int().nonnegative(),
  quote:      z.string().max(500).optional(),
  category:   z.enum(["positive", "negative", "neutral"]).default("neutral"),
})

const EvaluationCriterionResponseInputSchema = z.object({
  criterion_id:  z.string().min(1),
  /** true when criterion is not applicable to this session */
  na:            z.boolean().default(false),
  score:         z.number().min(0).optional(),     // for type "score"
  boolean_value: z.boolean().optional(),           // for type "boolean"
  choice_value:  z.string().optional(),            // for type "choice"
  text_value:    z.string().optional(),            // for type "text"
  notes:         z.string().optional(),
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
  participant_id:     z.string().uuid(),
  evaluation_id:      z.string().uuid(),
  composite_score:    z.number().min(0).max(10),
  dimensions:         z.array(EvaluationDimensionInputSchema).default([]),
  summary:            z.string().min(1),
  highlights:         z.array(z.string()).default([]),
  improvement_points: z.array(z.string()).default([]),
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
})

// ─── Registro das tools ───────────────────────────────────────────────────────

export function registerEvaluationTools(server: McpServer, deps: EvaluationDeps): void {
  const { kafka, postgres, redis, proxyUrl, skillRegistryUrl } = deps

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
    "Disponível apenas para agentes com role evaluator ou reviewer. " +
    "Requer que o Session Replayer tenha processado a sessão previamente. " +
    "Spec: Session Replayer.",
    EvaluationContextGetInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, participant_id } =
          EvaluationContextGetInputSchema.parse(input)

        const { tenant_id } = verifySessionToken(session_token)

        // Verifica role do participante
        let role = ""
        try {
          const roleRaw = await redis.hget(
            `${tenant_id}:agent:instance:${participant_id}`,
            "role"
          )
          if (roleRaw) role = roleRaw
        } catch { /* non-fatal */ }

        if (role && role !== "evaluator" && role !== "reviewer") {
          return mcpError(
            "unauthorized",
            `evaluation_context_get requer role evaluator ou reviewer (atual: ${role || "unknown"})`
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

        return ok({
          session_id,
          participant_id,
          context,
          retrieved_at:         new Date().toISOString(),
          // Arc 6 convenience fields — undefined keys are omitted by JSON.stringify
          ...(Object.keys(arc6Meta).length > 0 ? arc6Meta : {}),
          // Participant summary (always present for transparency)
          participant_summary: participantSummary,
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
    "Quando instance_id presente, também publica eval.instance.submitted para o ciclo de vida " +
    "da EvaluationInstance na evaluation-api. " +
    "A persistência no PostgreSQL é responsabilidade de um consumer dedicado — " +
    "esta tool nunca escreve diretamente no banco. " +
    "Reduz o TTL do ReplayContext no Redis após submissão. " +
    "Spec: Session Replayer.",
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
        } = parsed

        const { tenant_id } = verifySessionToken(session_token)

        // Lê agent_type_id do avaliador
        let agent_type_id = "evaluator_unknown"
        try {
          const typeRaw = await redis.hget(
            `${tenant_id}:agent:instance:${participant_id}`,
            "agent_type_id"
          )
          if (typeRaw) agent_type_id = typeRaw
        } catch { /* non-fatal */ }

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
}
