/**
 * ai-gateway.ts
 * Contratos do AI Gateway — inferência LLM stateless com fallback configurável.
 * Fonte da verdade: plughub_spec_v1.docx seção 8
 *
 * O AI Gateway é stateless: não gerencia sessão nem histórico.
 * O chamador passa o contexto completo (messages) e recebe a resposta.
 *
 * Fallback de modelo:
 *   O chamador define uma lista ordenada de modelos (models[]).
 *   O gateway tenta cada modelo na ordem até obter resposta ou esgotar a lista.
 *   fallback_on define as condições que ativam a tentativa do próximo modelo.
 */

import { z } from "zod"
import { SessionIdSchema } from "./common"

// ─────────────────────────────────────────────
// ModelEntry — modelo na lista de fallback
// ─────────────────────────────────────────────

/**
 * Condições que ativam a tentativa do próximo modelo na lista.
 *   rate_limited      — HTTP 429 ou resposta de rate limit
 *   context_exceeded  — janela de contexto estourada
 *   timeout           — sem resposta dentro do prazo
 *   error             — qualquer outro erro irrecuperável
 */
export const FallbackConditionSchema = z.enum([
  "rate_limited",
  "context_exceeded",
  "timeout",
  "error",
])
export type FallbackCondition = z.infer<typeof FallbackConditionSchema>

/**
 * ModelEntry — uma entrada na lista ordenada de modelos do chamador.
 * O gateway tenta os modelos em ordem; o primeiro a responder com sucesso
 * é o utilizado.
 *
 * Parâmetros de geração podem ser sobrescritos por entrada, permitindo
 * usar configurações diferentes por modelo (ex: temperatura menor no fallback).
 */
export const ModelEntrySchema = z.object({
  /** Identificador canônico do modelo (ex: "claude-opus-4-6", "gpt-4o") */
  model:          z.string().min(1),

  /** Provedor opcional — resolvido automaticamente pelo ModelRegistry se omitido */
  provider:       z.string().optional(),

  /** Máximo de tokens na resposta (sobrescreve o padrão do registry) */
  max_tokens:     z.number().int().positive().optional(),

  /** Temperatura (0.0 – 1.0) */
  temperature:    z.number().min(0).max(1).optional(),

  /** Timeout em segundos para esta entrada (sobrescreve o global) */
  timeout_s:      z.number().int().positive().optional(),
})
export type ModelEntry = z.infer<typeof ModelEntrySchema>

/**
 * ModelConfig — configuração de inferência passada pelo chamador.
 * Define a lista ordenada de modelos e as condições de fallback.
 */
export const ModelConfigSchema = z.object({
  /**
   * Lista ordenada de modelos a tentar.
   * O primeiro é o modelo primário; os demais são tentados em sequência
   * quando qualquer condição em fallback_on se materializa.
   * Mínimo: 1 entrada (sem fallback).
   */
  models:      z.array(ModelEntrySchema).min(1),

  /**
   * Condições que ativam a tentativa do próximo modelo.
   * Se vazio, não há fallback — o primeiro erro encerra a inferência.
   * Default: ["rate_limited", "error"] — fallback em limite de taxa e erros gerais.
   */
  fallback_on: z.array(FallbackConditionSchema).default(["rate_limited", "error"]),
})
export type ModelConfig = z.infer<typeof ModelConfigSchema>

// ─────────────────────────────────────────────
// Mensagem de inferência
// ─────────────────────────────────────────────

export const InferMessageRoleSchema = z.enum(["system", "user", "assistant", "tool"])
export type InferMessageRole = z.infer<typeof InferMessageRoleSchema>

export const InferMessageSchema = z.object({
  role:    InferMessageRoleSchema,
  content: z.union([
    z.string(),
    z.array(z.record(z.unknown())),  // content blocks (text, image, tool_use, tool_result)
  ]),
  /** tool_use_id — presente em mensagens de resultado de tool */
  tool_use_id: z.string().optional(),
})
export type InferMessage = z.infer<typeof InferMessageSchema>

// ─────────────────────────────────────────────
// AIInferInput — requisição ao AI Gateway
// ─────────────────────────────────────────────

/**
 * ToolSpec — declaração de ferramenta passada ao modelo.
 * Segue o formato nativo Anthropic/OpenAI para máxima compatibilidade.
 */
export const ToolSpecSchema = z.object({
  name:        z.string(),
  description: z.string().optional(),
  input_schema: z.record(z.unknown()),   // JSON Schema do input da tool
})
export type ToolSpec = z.infer<typeof ToolSpecSchema>

/**
 * OutputFieldSchema — subconjunto de JSON Schema para output estruturado
 * (usado pelo step reason do Skill Flow).
 */
export const OutputFieldSchema = z.object({
  type:     z.enum(["string", "number", "boolean", "object", "array"]),
  enum:     z.array(z.string()).optional(),
  minimum:  z.number().optional(),
  maximum:  z.number().optional(),
  required: z.boolean().optional(),
  description: z.string().optional(),
})
export type OutputField = z.infer<typeof OutputFieldSchema>

/**
 * AIInferInput — contrato completo de entrada do AI Gateway.
 *
 * O chamador é responsável por passar o histórico relevante em messages[].
 * O gateway não acessa Redis, stream ou qualquer estado de sessão.
 */
export const AIInferInputSchema = z.object({
  /** Contexto de rastreamento (não afeta o LLM) */
  session_id:     SessionIdSchema,
  participant_id: z.string().uuid(),

  /** Configuração de modelo e fallback — definida pelo chamador */
  model_config:   ModelConfigSchema,

  /** Instrução de sistema (prompt do agente/skill) */
  system_prompt:  z.string().optional(),

  /**
   * Histórico de mensagens a enviar ao modelo.
   * O AI Gateway não acessa o stream da sessão — o chamador decide
   * quantas mensagens incluir (janela de contexto).
   */
  messages:       z.array(InferMessageSchema).min(1),

  /**
   * Ferramentas disponíveis para o modelo nesta inferência.
   * Se presente, o gateway gerencia o loop tool_use → tool_result automaticamente.
   */
  tools:          z.array(ToolSpecSchema).optional(),

  /**
   * Schema de saída estruturada (step reason do Skill Flow).
   * Quando presente, o gateway força o modelo a responder em JSON
   * e valida o output contra o schema. Até max_format_retries tentativas.
   */
  output_schema:  z.record(OutputFieldSchema).optional(),

  /** Tentativas de reformatação em caso de JSON inválido (default: 1) */
  max_format_retries: z.number().int().min(0).max(3).default(1),

  /** Contexto de auditoria opcional — enriquece o registro, nunca o suprime */
  audit_context: z.object({
    reason:         z.string().optional(),
    correlation_id: z.string().optional(),
  }).optional(),
})
export type AIInferInput = z.infer<typeof AIInferInputSchema>

// ─────────────────────────────────────────────
// AIInferOutput — resposta do AI Gateway
// ─────────────────────────────────────────────

/**
 * SentimentScore — score de sentimento extraído da inferência.
 * Armazenado em session:{id}:sentiment como SentimentEntry.
 * O label NÃO é incluído — calculado no read time com SentimentConfig do tenant.
 */
export const SentimentScoreSchema = z.object({
  score:     z.number().min(-1).max(1),
  timestamp: z.string().datetime(),
})
export type SentimentScore = z.infer<typeof SentimentScoreSchema>

/**
 * AIInferOutput — resposta do AI Gateway.
 *
 * Campos de fallback:
 *   fallback_used      → true se ao menos um modelo da lista foi pulado
 *   fallback_exhausted → true se todos os modelos falharam (content será null/vazio)
 */
export const AIInferOutputSchema = z.object({
  /** Resposta em texto ou objeto estruturado (quando output_schema presente) */
  content:            z.union([z.string(), z.record(z.unknown())]).nullable(),

  /** Modelo efetivamente utilizado para gerar a resposta */
  model_used:         z.string(),

  /** true se ao menos um modelo da lista de fallback foi pulado */
  fallback_used:      z.boolean().default(false),

  /**
   * true se todos os modelos falharam.
   * Quando true, content é null e o chamador deve tratar como erro irrecuperável.
   */
  fallback_exhausted: z.boolean().default(false),

  /** Motivos pelos quais os modelos anteriores foram pulados (para auditoria) */
  fallback_reasons:   z.array(z.object({
    model:     z.string(),
    condition: FallbackConditionSchema,
  })).default([]),

  tokens_used: z.object({
    input:  z.number().int().nonnegative(),
    output: z.number().int().nonnegative(),
  }),

  /**
   * Score de sentimento extraído pelo AI Gateway nesta inferência.
   * Presente apenas quando o modelo retorna análise de sentimento.
   * O chamador deve persistir em session:{id}:sentiment.
   */
  sentiment:   SentimentScoreSchema.optional(),

  latency_ms:  z.number().int().nonnegative().optional(),
})
export type AIInferOutput = z.infer<typeof AIInferOutputSchema>
