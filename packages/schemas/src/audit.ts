/**
 * audit.ts
 * Tipos de auditoria, categorias de dados LGPD e mascaramento.
 * Fonte da verdade: plughub_spec_v1.docx seção 13
 */

import { z } from "zod"
import { ParticipantRoleSchema } from "./common"

// ─────────────────────────────────────────────
// Categorias de dados LGPD
// ─────────────────────────────────────────────

export const DataCategorySchema = z.enum([
  "cpf",          // Cadastro de Pessoa Física
  "credit_card",  // número de cartão de crédito
  "phone",        // número de telefone
  "email_addr",   // endereço de e-mail
  "address",      // endereço residencial ou comercial
  "health",       // dados de saúde
  "financial",    // dados financeiros em geral
])
export type DataCategory = z.infer<typeof DataCategorySchema>

// ─────────────────────────────────────────────
// Política de auditoria — definida na tool, não por chamada
// ─────────────────────────────────────────────

/**
 * AuditPolicy — definida no registro da tool.
 * O caller NUNCA pode suprimir o registro de auditoria.
 * O caller pode apenas enriquecer via audit_context.
 */
export const AuditPolicySchema = z.object({
  data_categories:  z.array(DataCategorySchema).default([]),
  capture_input:    z.boolean().default(false),
  capture_output:   z.boolean().default(false),
  retention_days:   z.number().int().positive().default(365),
  requires_consent: z.boolean().default(false),
})
export type AuditPolicy = z.infer<typeof AuditPolicySchema>

/**
 * AuditContext — enriquecimento opcional por chamada.
 * Nunca substitui nem suprime a AuditPolicy da tool.
 */
export const AuditContextSchema = z.object({
  reason:         z.string().optional(),
  correlation_id: z.string().optional(),
})
export type AuditContext = z.infer<typeof AuditContextSchema>

// ─────────────────────────────────────────────
// Mascaramento de dados sensíveis
// ─────────────────────────────────────────────

export const MaskingRuleSchema = z.object({
  pattern:              z.string().min(1),          // regex de detecção
  category:             DataCategorySchema,
  replacement:          z.string().min(1),          // placeholder para display humano puro (ex: "***.***.***-**")
  preserve_last_digits: z.number().int().min(0).optional(), // ex: 4 para cartão, 2 para CPF
  /**
   * preserve_pattern: regex de extração do trecho visível quando não é sufixo numérico.
   * Ex: para e-mail — preserva domínio: "(@.+)$"
   * Tem precedência sobre preserve_last_digits se ambos definidos.
   */
  preserve_pattern:     z.string().optional(),
})
export type MaskingRule = z.infer<typeof MaskingRuleSchema>

export const MaskingConfigSchema = z.object({
  tenant_id: z.string().min(1),
  rules:     z.array(MaskingRuleSchema).default([]),
})
export type MaskingConfig = z.infer<typeof MaskingConfigSchema>

export const MaskedResultSchema = z.object({
  original:            z.string(),
  masked:              z.string(),
  categories_detected: z.array(DataCategorySchema).default([]),
})
export type MaskedResult = z.infer<typeof MaskedResultSchema>

// ─────────────────────────────────────────────
// Política de acesso ao original_content
// ─────────────────────────────────────────────

/**
 * MaskingAccessPolicy — define quais roles podem receber original_content
 * ao ler mensagens via session_context_get.
 *
 * Default: apenas evaluator e reviewer.
 * O tenant pode adicionar supervisor se necessário.
 * primary e specialist NUNCA recebem original_content — o AI opera via tokens.
 *
 * Redis key: {tenant_id}:masking:access_policy
 */
export const MaskingAccessPolicySchema = z.object({
  tenant_id:        z.string().min(1),
  authorized_roles: z.array(ParticipantRoleSchema).default(["evaluator", "reviewer"]),
})
export type MaskingAccessPolicy = z.infer<typeof MaskingAccessPolicySchema>

// ─────────────────────────────────────────────
// Regras de mascaramento padrão (defaults do sistema)
// ─────────────────────────────────────────────

/**
 * DEFAULT_MASKING_RULES — aplicadas quando o tenant não configurou regras próprias.
 * Alinhadas com LGPD e PCI-DSS.
 */
export const DEFAULT_MASKING_RULES: MaskingRule[] = [
  {
    pattern:              "\\b\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}\\b",
    category:             "cpf",
    replacement:          "***.***.***.--",
    preserve_last_digits: 2,
  },
  {
    pattern:              "\\b(?:\\d{4}[\\s-]?){3}\\d{4}\\b",
    category:             "credit_card",
    replacement:          "**** **** **** ****",
    preserve_last_digits: 4,
  },
  {
    pattern:              "\\b(?:\\+55\\s?)?(?:\\(?\\d{2}\\)?[\\s-]?)?9?\\d{4}[-\\s]?\\d{4}\\b",
    category:             "phone",
    replacement:          "(##) ****-####",
    preserve_last_digits: 4,
  },
  {
    pattern:              "\\b[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}\\b",
    category:             "email_addr",
    replacement:          "****@****.***",
    preserve_pattern:     "(@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,})$",
  },
]

// ─────────────────────────────────────────────
// Registro de auditoria de MCP — tópico mcp.audit
// ─────────────────────────────────────────────

/**
 * AuditRecord — evento publicado no Kafka (tópico mcp.audit) a cada chamada
 * a um domain MCP Server, seja via McpInterceptor (em-processo) ou proxy sidecar.
 *
 * Invariante: o caller nunca pode suprimir este registro.
 * O caller pode apenas enriquecer via audit_context.
 *
 * Spec: PlugHub seção 9 — MCP interception / audit policy.
 */
export const AuditRecordSchema = z.object({
  event_type:          z.literal("mcp.tool_call"),
  timestamp:           z.string().datetime(),
  tenant_id:           z.string(),
  session_id:          z.string(),
  /** instance_id do agente via JWT; "unknown" quando não disponível (proxy sidecar) */
  instance_id:         z.string().optional(),
  /** Nome do domain MCP Server — ex: "mcp-server-crm" */
  server_name:         z.string(),
  /** Nome da tool invocada — ex: "customer_get" */
  tool_name:           z.string(),
  /** true = chamada foi encaminhada; false = bloqueada por permissão ou injection */
  allowed:             z.boolean(),
  /** Lista de permissões extraídas do JWT (permissions[]) */
  permissions_checked: z.array(z.string()),
  /** true quando injection_guard detectou padrão malicioso */
  injection_detected:  z.boolean(),
  /** pattern_id do injection_guard quando injection_detected = true */
  injection_pattern:   z.string().optional(),
  /** Latência total da chamada (0 se bloqueada antes do encaminhamento) */
  duration_ms:         z.number().nonnegative(),
  /** Categorias de dados LGPD sensíveis presentes na tool (audit_policy.data_categories) */
  data_categories:     z.array(DataCategorySchema).optional(),
  /** Snapshot do input — capturado apenas quando audit_policy.capture_input = true */
  input_snapshot:      z.unknown().optional(),
  /** Snapshot do output — capturado apenas quando audit_policy.capture_output = true */
  output_snapshot:     z.unknown().optional(),
  /** Enriquecimento opcional por chamada (nunca suprime a política da tool) */
  audit_context:       AuditContextSchema.optional(),
  /** Origem do registro: interceptor em-processo ou proxy sidecar */
  source:              z.enum(["in_process", "proxy_sidecar"]),
  /**
   * Campos cujos valores foram omitidos por serem mascarados (originados do masked_scope).
   * Registra QUAIS campos foram enviados, mas nunca seus valores.
   * Presente quando a tool recebe inputs via namespace @masked.*.
   * Quando todos os inputs são mascarados, input_snapshot = null.
   */
  masked_input_fields: z.array(z.string()).optional(),
})
export type AuditRecord = z.infer<typeof AuditRecordSchema>
