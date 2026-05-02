/**
 * contact-context.ts
 * Schema Zod para o ContactContext da PlugHub Platform.
 *
 * O ContactContext é o resultado produzido pelo agente_contexto_ia_v1 e
 * armazenado em pipeline_state.contact_context. Ele representa o perfil
 * acumulado do cliente ao longo da sessão — enriquecido progressivamente
 * por múltiplos agentes sem re-coletar dados já conhecidos.
 *
 * Modelo de confiança:
 *   confidence 0.9–1.0 → dado confirmado explicitamente (cliente ou CRM)
 *   confidence 0.7–0.9 → inferido com alta certeza (histórico recente, padrão claro)
 *   confidence 0.4–0.7 → mencionado / parcialmente confirmado
 *   confidence 0.0–0.4 → incerto — agente deve confirmar antes de usar
 *
 * Fontes (source):
 *   pipeline_state   → herdado de agente anterior na mesma sessão
 *   insight_historico → memória de longo prazo (contatos anteriores)
 *   insight_conversa  → gerado na sessão atual por outro step
 *   mcp_call         → consultado via MCP tool (CRM, billing, etc.)
 *   customer_input   → fornecido diretamente pelo cliente nesta sessão
 *   ai_inferred      → inferido pelo AI Gateway a partir da conversa
 */

import { z } from "zod"

// ── Fonte de um campo de contexto ─────────────────────────────────────────────

export const ContactContextSourceSchema = z.enum([
  "pipeline_state",
  "insight_historico",
  "insight_conversa",
  "mcp_call",
  "customer_input",
  "ai_inferred",
])
export type ContactContextSource = z.infer<typeof ContactContextSourceSchema>

// ── Campo individual com rastreabilidade ──────────────────────────────────────

export const ContactContextFieldSchema = z.object({
  /** Valor do campo — string para todos os campos simples */
  value:      z.string(),
  /**
   * Score de confiança [0,1].
   * 1.0 = confirmado explicitamente; 0.0 = pura especulação.
   */
  confidence: z.number().min(0).max(1),
  /** De onde veio este valor */
  source:     ContactContextSourceSchema,
  /** Timestamp ISO 8601 de quando o campo foi resolvido */
  resolved_at: z.string().datetime().optional(),
})
export type ContactContextField = z.infer<typeof ContactContextFieldSchema>

// ── Dados brutos do CRM (quando consultado via MCP) ──────────────────────────

export const ContactContextCrmDataSchema = z.object({
  /** Payload completo retornado pelo MCP tool (estrutura livre por tenant) */
  raw:        z.record(z.unknown()),
  /** Nome do MCP server e tool que retornou os dados */
  mcp_server: z.string(),
  tool:       z.string(),
  fetched_at: z.string().datetime(),
})
export type ContactContextCrmData = z.infer<typeof ContactContextCrmDataSchema>

// ── ContactContext completo ───────────────────────────────────────────────────

export const ContactContextSchema = z.object({

  // ── Identificação do cliente ─────────────────────────────────────────────
  customer_id:   ContactContextFieldSchema.optional(),
  cpf:           ContactContextFieldSchema.optional(),
  account_id:    ContactContextFieldSchema.optional(),
  nome:          ContactContextFieldSchema.optional(),
  telefone:      ContactContextFieldSchema.optional(),
  email:         ContactContextFieldSchema.optional(),

  // ── Contexto do contato atual ────────────────────────────────────────────
  /**
   * Motivo do contato inferido ou declarado.
   * Ex: "cancelamento_plano", "falha_servico", "duvida_fatura"
   */
  motivo_contato: ContactContextFieldSchema.optional(),

  /**
   * Intenção primária detectada — mais granular que o motivo.
   * Ex: "solicitar_cancelamento", "reclamar_cobranca_indevida"
   */
  intencao_primaria: ContactContextFieldSchema.optional(),

  /**
   * Sentimento atual do cliente.
   * Valores esperados: "positivo" | "neutro" | "negativo" | "frustrado" | "irritado"
   */
  sentimento_atual: ContactContextFieldSchema.optional(),

  // ── Histórico resumido ───────────────────────────────────────────────────
  /**
   * Resumo conciso da conversa até o momento, para ser passado
   * ao próximo agente sem repetir todo o histórico bruto.
   */
  resumo_conversa: ContactContextFieldSchema.optional(),

  /**
   * Tentativas de resolução já feitas nesta sessão.
   * Ex: ["reinicialização_modem", "troca_plano_ofertada"]
   */
  resolucoes_tentadas: z.array(z.string()).default([]),

  // ── Dados do CRM (quando disponíveis) ────────────────────────────────────
  dados_crm: ContactContextCrmDataSchema.optional(),

  // ── Metadados de completude ──────────────────────────────────────────────
  /**
   * Campos que o agente de contexto identificou como ausentes e
   * que seriam úteis para o fluxo atual.
   */
  campos_ausentes: z.array(z.string()).default([]),

  /**
   * Score de completude [0,1] calculado pelo agente de contexto.
   * 1.0 = todos os campos relevantes para o fluxo atual estão presentes
   *       com confiança ≥ 0.8.
   */
  completeness_score: z.number().min(0).max(1).default(0),

  /**
   * Campos que estão presentes mas com confiança abaixo do threshold (0.7)
   * e que o agente pode querer confirmar antes de usar.
   */
  campos_incertos: z.array(z.string()).default([]),

  /** Timestamp ISO 8601 da última atualização do contexto */
  updated_at: z.string().datetime().optional(),
})

export type ContactContext = z.infer<typeof ContactContextSchema>

// ── Schema para input do agente de contexto ───────────────────────────────────
//
// Passado via pipeline_state ao invocar agente_contexto_ia_v1.
// Declara quais campos o agente solicitante precisa e qual o threshold mínimo
// de confiança aceitável para cada um.
//

export const ContextRequirementSchema = z.object({
  field:               z.string(),
  required:            z.boolean().default(true),
  min_confidence:      z.number().min(0).max(1).default(0.7),
  /** Se true, confirma com o cliente mesmo que o campo já exista com baixa confiança */
  force_confirmation:  z.boolean().default(false),
})
export type ContextRequirement = z.infer<typeof ContextRequirementSchema>

export const ContextResolutionRequestSchema = z.object({
  /**
   * ID do agente solicitante — usado para auditoria e para determinar
   * quais campos são relevantes.
   */
  requesting_agent:    z.string(),
  /**
   * Campos que o agente solicitante precisa para executar sua tarefa.
   * Se vazio, o agente de contexto usa um conjunto padrão.
   */
  required_fields:     z.array(ContextRequirementSchema).default([]),
  /**
   * Contexto já disponível — o agente de contexto evita re-coletar
   * campos que já estejam presentes com confiança ≥ min_confidence.
   */
  existing_context:    ContactContextSchema.optional(),
})
export type ContextResolutionRequest = z.infer<typeof ContextResolutionRequestSchema>
