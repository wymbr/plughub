/**
 * context-store.ts
 * Schemas Zod para o ContextStore unificado da PlugHub Platform.
 *
 * O ContextStore é a camada única de persistência de todo o estado observável
 * de um contato — identidade, sentimento, SLA, pricing, insights, workflows —
 * organizado em namespaces com convenção uniforme.
 *
 * Dois hashes Redis por contato:
 *   {t}:ctx:{sessionId}         — TTL de sessão (~4h)  → session, sla, queue,
 *                                                          caller, account, insight.conversa
 *   {t}:ctx:customer:{customerId} — TTL longo (~90d)   → insight.historico, pricing
 *
 * Referência nos skill flows: @ctx.namespace.campo
 *   ex: @ctx.caller.cpf, @ctx.session.sentimento.current, @ctx.account.plano_atual
 *
 * Namespaces convencionados:
 *   caller.*          quem está ligando (pode diferir do titular da conta)
 *   account.*         a conta em discussão
 *   account.holder_*  titular da conta
 *   session.*         estado da interação atual (sentimento, intenção, motivo)
 *   sla.*             tempo e metas de atendimento
 *   queue.*           posição na fila (efêmero — TTL curto)
 *   pricing.*         dados de billing desta conta (TTL longo)
 *   insight.historico.* memória de longo prazo (TTL longo)
 *   insight.conversa.*  gerado na sessão atual, expira no fechamento
 *   workflow.*        instâncias de workflow ativas
 */

import { z } from "zod"

// ── Visibilidade — mesma semântica das mensagens ──────────────────────────────

export const ContextVisibilitySchema = z.union([
  z.literal("all"),
  z.literal("agents_only"),
  z.literal("system_only"),
  z.array(z.string()),   // lista de participant_ids
])
export type ContextVisibility = z.infer<typeof ContextVisibilitySchema>

// ── Merge strategy ────────────────────────────────────────────────────────────

export const ContextMergeStrategySchema = z.enum([
  "highest_confidence",  // só sobrescreve se confidence maior que o existente
  "overwrite",           // sempre substitui (útil para estado efêmero)
  "append",              // acumula em array (ex: resolucoes_tentadas, flags)
])
export type ContextMergeStrategy = z.infer<typeof ContextMergeStrategySchema>

// ── Entry individual do ContextStore ─────────────────────────────────────────

export const ContextEntrySchema = z.object({
  /** Valor do campo — qualquer tipo serializável */
  value:      z.unknown(),

  /**
   * Score de confiança [0,1].
   *   1.0 = dado de sistema determinístico (routing, metering)
   *   0.95 = retorno autoritativo de MCP tool (CRM, billing)
   *   0.9  = dado explicitamente declarado pelo cliente
   *   0.8  = dado claramente mencionado mas com formatação informal
   *   0.7  = inferido pelo AI Gateway com alta certeza
   *   0.6  = inferido com boa certeza
   *   < 0.4 = incerto — confirmar antes de usar
   */
  confidence: z.number().min(0).max(1),

  /**
   * Quem escreveu este valor.
   * Formato livre mas com convenções:
   *   "mcp_call:{tool_name}"   → extraído de retorno de tool
   *   "customer_input"         → fornecido pelo cliente em texto livre
   *   "ai_inferred:{step_id}"  → inferido pelo AI Gateway
   *   "system:{component}"     → escrito por componente de sistema (routing, etc.)
   */
  source: z.string(),

  /**
   * Visibilidade deste campo.
   * Default: "agents_only" — dados de contexto não são expostos ao cliente.
   */
  visibility: ContextVisibilitySchema.default("agents_only"),

  /**
   * TTL em segundos para sobrescrever o TTL padrão do hash.
   * Útil para dados efêmeros: queue.posicao (30s), sla.breach_imminent (60s).
   * null = usa TTL padrão do hash de sessão.
   */
  ttl_override_s: z.number().int().positive().optional(),

  /** Timestamp ISO 8601 da última escrita */
  updated_at: z.string().datetime(),
})
export type ContextEntry = z.infer<typeof ContextEntrySchema>

// ── Snapshot completo — resultado de getAll() ou getByPrefix() ────────────────

/** Mapa tag → ContextEntry */
export const ContextSnapshotSchema = z.record(ContextEntrySchema)
export type ContextSnapshot = z.infer<typeof ContextSnapshotSchema>

// ── context_tags: anotação declarativa em tool definitions ───────────────────

// ── Scope de armazenamento ──────────────────────────────────────────────────────

export const ContextTagScopeSchema = z.enum([
  "session",   // tag gravada no escopo da sessão (default — backward compatible)
  "segment",   // tag prefixada com segment.{segmentId}.* — isolada por participação
])
export type ContextTagScope = z.infer<typeof ContextTagScopeSchema>

export const ContextTagEntrySchema = z.object({
  /** Caminho no ContextStore: "caller.cpf", "account.nome", "session.motivo_contato" */
  tag: z.string().regex(
    /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/,
    "Tag deve ser namespace.campo em snake_case (ex: caller.cpf, account.holder_nome)"
  ),

  /**
   * Confidence determinística para este campo nesta fonte.
   * Definida uma vez na anotação, não por chamada.
   */
  confidence: z.number().min(0).max(1),

  /** Estratégia de merge quando o campo já existe no store */
  merge: ContextMergeStrategySchema.default("highest_confidence"),

  /**
   * Visibilidade desta tag.
   * Default herdado do ContextEntry.
   */
  visibility: ContextVisibilitySchema.optional(),

  /** TTL override para esta tag específica (segundos) */
  ttl_override_s: z.number().int().positive().optional(),

  /**
   * Escopo de armazenamento da tag.
   *
   * - "session" (default): tag gravada diretamente no ContextStore da sessão.
   * - "segment": tag prefixada com segment.{segmentId}. — isolada por participação
   *   do agente. Requer que o StepContext tenha segmentId definido.
   *   Ex: tag "wrapup.resumo" com scope "segment" → "segment.{seg_id}.wrapup.resumo"
   *
   * Ideal para agentes paralelos (NPS, wrap-up) que não devem compartilhar dados.
   */
  scope: ContextTagScopeSchema.default("session"),
})
export type ContextTagEntry = z.infer<typeof ContextTagEntrySchema>

/**
 * ToolContextTags — anotação numa tool definition.
 *
 * Exemplo de uso em mcp-server-crm:
 *
 *   context_tags: {
 *     inputs: {
 *       cpf: { tag: "caller.cpf", confidence: 0.9, merge: "highest_confidence" }
 *     },
 *     outputs: {
 *       nome:       { tag: "caller.nome",       confidence: 0.95 },
 *       account_id: { tag: "caller.account_id", confidence: 0.95 },
 *       plano:      { tag: "account.plano_atual", confidence: 0.95, merge: "overwrite" }
 *     }
 *   }
 *
 * O McpInterceptor lê esta anotação e chama o ContextAccumulator automaticamente.
 */
export const ToolContextTagsSchema = z.object({
  /**
   * Mapeamento de parâmetros de entrada da tool para tags do ContextStore.
   * key = nome do parâmetro de entrada, value = ContextTagEntry
   */
  inputs: z.record(ContextTagEntrySchema).optional(),

  /**
   * Mapeamento de campos do retorno da tool para tags do ContextStore.
   * Suporta dot-notation para campos aninhados no retorno:
   *   "customer.nome" → acessa response.customer.nome
   */
  outputs: z.record(ContextTagEntrySchema).optional(),
})
export type ToolContextTags = z.infer<typeof ToolContextTagsSchema>

// ── required_context: declaração no cabeçalho do skill YAML ──────────────────

export const SkillRequiredContextSchema = z.object({
  /**
   * Tag do ContextStore que deve estar presente.
   * ex: "caller.cpf", "account.plano_atual"
   */
  tag: z.string(),

  /**
   * Confiança mínima aceitável para considerar o campo presente.
   * Default: 0.7
   */
  confidence_min: z.number().min(0).max(1).default(0.7),

  /**
   * Se true, o campo aparece em @ctx.__gaps__ mesmo que esteja presente
   * com confiança < confidence_min (aciona re-coleta).
   * Default: false
   */
  required: z.boolean().default(true),
})
export type SkillRequiredContext = z.infer<typeof SkillRequiredContextSchema>

// ── context_tags em reason steps ─────────────────────────────────────────────

/**
 * ReasonStepContextTags — Opção A: declaração explícita no step YAML.
 *
 * Mapeia campos do output_schema do reason step para o ContextStore.
 * O engine chama ContextAccumulator.extractFromOutputs() após execução do LLM.
 *
 * Exemplo em agente_sac_ia_v1.yaml:
 *
 *   - id: analisar
 *     type: reason
 *     ...
 *     output_as: analise
 *     context_tags:
 *       outputs:
 *         sentimento: { tag: "session.sentimento.current", confidence: 0.8, merge: "overwrite" }
 *         escalar:    { tag: "session.escalar_solicitado",  confidence: 1.0, merge: "overwrite" }
 *         historico_mensagens:
 *           tag: "session.historico_mensagens"
 *           confidence: 1.0
 *           merge: "overwrite"
 */
export const ReasonStepContextTagsSchema = z.object({
  outputs: z.record(ContextTagEntrySchema).optional(),
})
export type ReasonStepContextTags = z.infer<typeof ReasonStepContextTagsSchema>

// ── Snapshots especiais (@ctx.__*__) ─────────────────────────────────────────

/**
 * GapsReport — retornado por @ctx.__gaps__
 * Lista as tags declaradas em required_context que estão ausentes ou
 * com confiança abaixo do threshold.
 */
export const ContextGapsReportSchema = z.object({
  missing: z.array(z.string()),   // tags completamente ausentes
  low_confidence: z.array(       // tags presentes mas abaixo do threshold
    z.object({
      tag:        z.string(),
      confidence: z.number(),
      required:   z.number(),    // confidence_min requerido
    })
  ),
  complete: z.boolean(),         // true se missing e low_confidence estão vazios
})
export type ContextGapsReport = z.infer<typeof ContextGapsReportSchema>
