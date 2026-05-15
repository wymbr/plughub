/**
 * agent-events.ts
 * Schema para Arc 12 — Agent Business Events.
 *
 * Permite que agentes (AI e humanos) publiquem KPIs e eventos de negócio
 * estruturados durante sessões, com armazenamento em time-series no ClickHouse.
 *
 * Tópico Kafka: agent.events
 *
 * Categoria hierárquica em dot notation: pool_id.skill_id.metric_key
 *   ex: "retencao_humano.skill_retencao_v2.offer_accepted"
 *       "suporte_ai.skill_suporte_v1.resolution_ms"
 *       "vendas.skill_venda_v3.contract_value"
 *
 * Os campos category_l1..l4 são pré-decompostos pelo mcp-server na publicação
 * para garantir performance de query no ClickHouse sem parsing dinâmico.
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// Validações de categoria e tags
// ─────────────────────────────────────────────

/**
 * Regex para category: 2–5 segmentos snake_case separados por ponto.
 * Ex: "pool.skill.key" ou "pool.skill.group.key"
 */
export const AGENT_EVENT_CATEGORY_REGEX = /^[a-z0-9_]+(\.[a-z0-9_]+){1,4}$/

/**
 * Chaves de tags que referenciam dados pessoais — bloqueadas por governança.
 * Case-insensitive na validação da tool.
 */
export const AGENT_EVENT_PII_TAG_KEYS = new Set([
  "cpf", "cnpj", "phone", "telefone", "email",
  "document", "documento", "password", "senha",
  "token", "api_key", "credit_card", "cartao",
  "rg", "passport", "cnh", "account", "conta",
])

// ─────────────────────────────────────────────
// AgentBusinessEvent — evento publicado no tópico agent.events
// ─────────────────────────────────────────────

export const AgentBusinessEventSchema = z.object({
  /** UUID gerado pelo mcp-server no momento da publicação. */
  event_id: z.string().uuid(),

  /** Tenant da sessão — resolvido do session_token pelo mcp-server. */
  tenant_id: z.string().min(1),

  /** Sessão na qual o evento foi emitido. */
  session_id: z.string().min(1),

  /** Journey associada à sessão — null quando sessão standalone. */
  journey_id: z.string().nullable().default(null),

  /** Agent type que emitiu o evento — resolvido do session_token. */
  agent_type_id: z.string().min(1),

  /** Skill em execução no momento da emissão — resolvido do session_token. */
  skill_id: z.string().min(1),

  /** Pool da sessão — resolvido do session_token. */
  pool_id: z.string().min(1),

  /**
   * Categoria hierárquica em dot notation.
   * Regex: ^[a-z0-9_]+(\.[a-z0-9_]+){1,4}$
   * O primeiro segmento DEVE ser igual ao pool_id da sessão (namespace isolation).
   */
  category: z.string().regex(AGENT_EVENT_CATEGORY_REGEX),

  /** Primeiro segmento de category (pool_id) — pré-decomposto para query performance. */
  category_l1: z.string(),

  /** Segundo segmento de category (skill_id) — "" quando ausente. */
  category_l2: z.string().default(""),

  /** Terceiro segmento de category (metric_key) — "" quando ausente. */
  category_l3: z.string().default(""),

  /** Quarto segmento de category — "" quando ausente. */
  category_l4: z.string().default(""),

  /**
   * Valor numérico do evento.
   * Pode representar: contagem (1.0), duração (ms), valor monetário, score, etc.
   * Deve ser finito e não-NaN.
   */
  value: z.number().finite(),

  /**
   * Dimensões extras para slicing e filtering.
   * Máx 10 pares; chave/valor ≤ 64 chars; chaves PII bloqueadas.
   */
  tags: z.record(z.string(), z.string()).default({}),

  /** Timestamp ISO 8601 UTC da emissão. */
  emitted_at: z.string().datetime(),
})

export type AgentBusinessEvent = z.infer<typeof AgentBusinessEventSchema>

// ─────────────────────────────────────────────
// AgentEventInput — payload da tool MCP agent_event
// (subconjunto do evento completo — contexto resolvido internamente)
// ─────────────────────────────────────────────

export const AgentEventInputSchema = z.object({
  /**
   * Categoria hierárquica em dot notation: pool_id.skill_id.metric_key
   * O primeiro segmento deve ser igual ao pool_id da sessão corrente.
   */
  category: z.string().regex(AGENT_EVENT_CATEGORY_REGEX, {
    message:
      "category must match pattern pool_id.skill_id.key (2–5 dot-separated snake_case segments)",
  }),

  /**
   * Valor numérico do evento (contagem, duração, valor monetário, score, etc.)
   */
  value: z.number().finite({ message: "value must be a finite number" }),

  /**
   * Dimensões extras opcionais para slicing.
   * Máx 10 pares; chave/valor ≤ 64 chars; PII keywords bloqueados.
   */
  tags: z
    .record(z.string().max(64), z.string().max(64))
    .default({})
    .refine((t) => Object.keys(t).length <= 10, {
      message: "tags must have at most 10 entries",
    }),
})

export type AgentEventInput = z.infer<typeof AgentEventInputSchema>

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

/**
 * Decompõe uma categoria hierárquica em até 4 segmentos.
 * Segmentos ausentes retornam string vazia.
 *
 * @example
 * decomposeCategoryLevels("retencao_humano.skill_retencao_v2.offer_accepted")
 * // → { l1: "retencao_humano", l2: "skill_retencao_v2", l3: "offer_accepted", l4: "" }
 */
export function decomposeCategoryLevels(category: string): {
  l1: string
  l2: string
  l3: string
  l4: string
} {
  const [l1 = "", l2 = "", l3 = "", l4 = ""] = category.split(".")
  return { l1, l2, l3, l4 }
}
