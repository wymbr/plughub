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
// ContextStore field-level masking (dynamic rules)
// ─────────────────────────────────────────────

/**
 * ContextMaskingType — visual presentation applied to a ContextStore tag value
 * when delivered to a given role.
 *
 * These are purely display semantics — they carry no implied data-type semantics
 * (e.g. "last_4" works on CPF, contract number, credit card, etc.).
 *
 * Stored in Config API: namespace "masking", key "context_rules" (global default
 * seeded in config-api seed.py; tenant overrides via the Masking page).
 * Consumed by mcp-server via GET /config/masking (config-http-propagation arc) —
 * not by direct Redis reads.
 */
export const ContextMaskingTypeSchema = z.enum([
  "plain",        // no masking — show value as-is
  "hidden",       // remove the field entirely from the response
  "full",         // mask entire value → "***"
  "last_2",       // show only last 2 chars → "***XX"
  "last_4",       // show only last 4 chars → "***XXXX"
  "first_1",      // show only first char → "X***"
  "first_word",   // show only the first word, mask the rest
  "email_domain", // keep domain, mask local part → "X***@domain.com"
  "financial",    // generic financial mask → "R$ ****,**"
])
export type ContextMaskingType = z.infer<typeof ContextMaskingTypeSchema>

/**
 * ContextMaskingRule — maps a tag name pattern × role to a masking type.
 *
 * pattern: exact tag name ("caller.cpf") or glob with single wildcard
 *          ("caller.*" matches "caller.cpf", "caller.nome", etc.)
 *          "*" matches any tag.
 *
 * role:    "operator"  — agents / human operators in the Console
 *          "supervisor" — covers supervisor, admin, evaluator, reviewer
 *          "*"          — applies to all roles (base-layer wildcard)
 *
 * Resolution: most-specific match wins.
 * Specificity score: exact > glob > "*"; role "operator" > "supervisor" > "*".
 * Ties broken by position in the rules array (first wins).
 */
export const ContextMaskingRuleSchema = z.object({
  /** Exact tag name or glob pattern with optional trailing "*" */
  pattern: z.string().min(1),
  /** Role this rule applies to */
  role:    z.enum(["operator", "supervisor", "*"]),
  /** Masking type to apply when pattern × role match */
  type:    ContextMaskingTypeSchema,
  /** Optional human-readable label for the Config UI */
  label:   z.string().optional(),
})
export type ContextMaskingRule = z.infer<typeof ContextMaskingRuleSchema>

/**
 * ContextMaskingConfig — full set of rules for a tenant.
 *
 * Loaded from Config API on first request, cached in-process with TTL.
 * Falls back to global defaults when no tenant-level config exists.
 */
export const ContextMaskingConfigSchema = z.object({
  /**
   * Ordered list of masking rules.
   * Evaluation stops at the first matching rule (most-specific first).
   */
  rules: z.array(ContextMaskingRuleSchema).default([]),
  /**
   * Masking type applied when no rule matches a tag for the "operator" role.
   * "plain" is the permissive default (most ContextStore tags are non-PII).
   * Conservative deployments may set "hidden".
   */
  default_unmatched_operator: ContextMaskingTypeSchema.default("plain"),
  /**
   * Roles treated as "supervisor" category for masking (bypass the namespace
   * gate, see PII plain). Config-driven so "who is elevated" is UI-editable and
   * not fixed in code. Any role NOT in this list is treated as "operator".
   * Default preserves the previous hardcoded behavior.
   */
  supervisor_roles: z.array(z.string()).default(["supervisor", "admin", "evaluator", "reviewer"]),
})
export type ContextMaskingConfig = z.infer<typeof ContextMaskingConfigSchema>

/**
 * DEFAULT_CONTEXT_MASKING_RULES — global fallback rules.
 *
 * Converts the original hardcoded TAG_PII_CATEGORY map exactly:
 *   caller.cpf              → last_2   (operator)
 *   caller.cnpj             → last_2   (operator)
 *   caller.telefone         → last_4   (operator)
 *   caller.email            → email_domain (operator)
 *   account.numero_contrato → last_4   (operator)
 *   account.valor_fatura    → financial (operator)
 *   account.limite_credito  → hidden   (operator)
 *   caller.*                → last_4   (operator, catch-all for caller namespace)
 *   account.*               → financial (operator, catch-all for account namespace)
 *
 * supervisor/* → plain (no masking for elevated roles).
 */
export const DEFAULT_CONTEXT_MASKING_CONFIG: ContextMaskingConfig = {
  default_unmatched_operator: "plain",
  supervisor_roles: ["supervisor", "admin", "evaluator", "reviewer"],
  rules: [
    // ── exact rules (highest specificity) ──────────────────────────────────
    // caller.customer_id — internal reference id (not PII); plain so operators can
    // identify the customer / load history / 360 (exact beats the caller.* catch-all).
    { pattern: "caller.customer_id",      role: "operator",   type: "plain",        label: "ID interno do cliente (não-PII)" },
    { pattern: "caller.cpf",              role: "operator",   type: "last_2",       label: "CPF do cliente" },
    { pattern: "caller.cnpj",             role: "operator",   type: "last_2",       label: "CNPJ do cliente" },
    { pattern: "caller.telefone",         role: "operator",   type: "last_4",       label: "Telefone do cliente" },
    { pattern: "caller.email",            role: "operator",   type: "email_domain", label: "E-mail do cliente" },
    { pattern: "account.numero_contrato", role: "operator",   type: "last_4",       label: "Número do contrato" },
    { pattern: "account.valor_fatura",    role: "operator",   type: "financial",    label: "Valor da fatura" },
    { pattern: "account.limite_credito",  role: "operator",   type: "hidden",       label: "Limite de crédito" },
    // ── glob catch-alls (medium specificity) ────────────────────────────────
    { pattern: "caller.*",                role: "operator",   type: "last_4",       label: "Dados do cliente (genérico)" },
    { pattern: "account.*",               role: "operator",   type: "financial",    label: "Dados da conta (genérico)" },
    // ── supervisor: no masking on any field ────────────────────────────────
    { pattern: "*",                       role: "supervisor", type: "plain",        label: "Supervisor vê tudo sem máscara" },
  ],
}

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
  /**
   * R7a — simétrico ao input: paths (dot-notation) do output cujo conteúdo continha
   * PII e foi mascarado antes de persistir o `output_snapshot`. O snapshot NUNCA
   * carrega o valor cru de PII (fix de vazamento). As categorias detectadas no output
   * são unidas a `data_categories`. Presente quando capture_output=true e o retorno
   * da tool casou alguma regra de masking.
   */
  masked_output_fields: z.array(z.string()).optional(),
})
export type AuditRecord = z.infer<typeof AuditRecordSchema>
