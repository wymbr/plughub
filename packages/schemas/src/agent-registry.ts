/**
 * agent-registry.ts
 * Schema Zod para registro de pool e registro de tipo de agente — PlugHub Platform.
 * Fonte da verdade: PlugHub spec v24.0 seção 4.5
 */

import { z } from "zod"
import { SkillRefSchema } from "./skill"
import { ChannelSchema }  from "./common"

// ─────────────────────────────────────────────
// Pool Registration — PlugHub spec 4.5 Registro de Pool
// ─────────────────────────────────────────────

export const RoutingExpressionSchema = z.object({
  weight_sla:      z.number().min(0).max(2).default(1.0),
  weight_wait:     z.number().min(0).max(2).default(0.8),
  weight_tier:     z.number().min(0).max(2).default(0.6),
  weight_churn:    z.number().min(0).max(2).default(0.9),
  weight_business: z.number().min(0).max(2).default(0.4),
})

export const InteractionModelSchema = z.enum(["background", "conference"])

const CapabilityEntrySchema = z.union([
  z.object({
    capability:        z.string(),
    interaction_model: InteractionModelSchema,
  }),
  z.object({
    agent_type_id:     z.string(),
    interaction_model: InteractionModelSchema,
    channel_identity:  z.object({
      text:          z.string().optional(),
      voice_profile: z.string().optional(),
    }).optional(),
    auto_join: z.boolean().default(false),
  }),
])

export const RelevanceModelSchema = z.object({
  model_profile:              z.enum(["fast", "balanced"]).default("fast"),
  invoke_when:                z.enum(["confidence_below", "always"]).default("confidence_below"),
  confidence_threshold:       z.number().min(0).max(1).default(0.75),
  max_additional_capabilities: z.number().int().min(1).max(10).default(3),
  base_map_is_floor:          z.boolean().default(true),
})

const ProactiveDelegationSchema = z.object({
  enabled:          z.boolean().default(false),
  min_relevance:    z.enum(["low", "medium", "high"]).default("high"),
  delegation_mode:  z.enum(["silent", "orchestrated"]).default("silent"),
  version_policy:   z.enum(["routing", "exact", "stable"]).default("stable"),
})

export type RelevanceModel = z.infer<typeof RelevanceModelSchema>

export const SupervisorConfigSchema = z.object({
  enabled:                  z.boolean(),
  history_window_days:      z.number().int().min(1).max(365).default(30),
  insight_categories:       z.array(z.string()).default([]),
  intent_capability_map:    z.record(z.array(CapabilityEntrySchema)).default({}),
  sentiment_alert_threshold: z.number().min(-1).max(0).default(-0.30),
  relevance_model:          RelevanceModelSchema.optional(),
  proactive_delegation:     ProactiveDelegationSchema.optional(),
})
export type SupervisorConfig = z.infer<typeof SupervisorConfigSchema>

export const QueueConfigSchema = z.object({
  /**
   * LEGACY — agent_type_id of the queue agent (agent types are retired).
   * Default "": the bridge resolves the flow directly via skill_id
   * (skill-first, Fase E). Kept for backward compat with old YAML configs.
   */
  agent_type_id: z.string().default(""),

  /**
   * Maximum wait time in seconds before the platform closes the contact with
   * close_reason=max_wait_exceeded (Fase E retention bound, enforced by the
   * routing engine's periodic drain). 0 = use the platform default
   * (PLUGHUB_QUEUE_MAX_WAIT_DEFAULT_S). Default: 1800 (30 minutes).
   */
  max_wait_s: z.number().int().min(0).default(1800),

  /**
   * skill_id of the queue-treatment flow (the field that matters, skill-first).
   * The bridge activates the queue agent resolving this flow in the registry.
   */
  skill_id: z.string().optional(),
})
export type QueueConfig = z.infer<typeof QueueConfigSchema>

export const PoolEvaluationConfigSchema = z.object({
  /**
   * Fraction of closed contacts that should be evaluated (0.0 – 1.0).
   * 1.0 = evaluate every contact; 0.0 = disabled.
   * Default: 1.0 (evaluate all) — tenant must explicitly reduce to sample.
   */
  sampling_rate: z.number().min(0).max(1).default(1.0),

  /**
   * Template used to resolve the evaluation skill_id for the pool.
   * Supports {pool_id} placeholder.
   * Example: "eval_{pool_id}_v1" → "eval_retencao_humano_v1"
   */
  skill_id_template: z.string().default("eval_{pool_id}_v1"),
})
export type PoolEvaluationConfig = z.infer<typeof PoolEvaluationConfigSchema>

// ── Pool Lifecycle Hooks — spec Pool Hooks v1 ─────────────────────────────────

const PoolHookEntrySchema = z.object({
  /**
   * pool_id do pool de onde o especialista será recrutado.
   * O routing engine aloca uma instância disponível deste pool como
   * participante de conferência na sessão existente.
   */
  pool: z.string().regex(/^[a-z0-9_]+$/),
  /**
   * Lado do segmento pós-atendimento (Arc 14).
   *
   * "agent"    — o hook interage com o agente humano (wrap-up, resumo).
   *              Quando este segmento terminar, o agente é liberado para
   *              o próximo contato.
   * "customer" — o hook interage com o cliente (NPS, pesquisa de satisfação).
   *              Quando este segmento terminar, o canal do cliente é fechado
   *              caso ainda esteja aberto.
   *
   * Default: "agent" (backward compatible — todos os hooks existentes são
   * agent-side implicitamente).
   */
  side: z.enum(["agent", "customer"]).default("agent"),
  /**
   * Arc 14 Fase D — comportamento quando o cliente desconectou antes do hook
   * (close_origin == "customer_disconnect"). Relevante apenas para side="customer".
   *
   *  "skip"    → segmento não é despachado; pulado silenciosamente.
   *  "timeout" → despachado normalmente; skill YAML decide via
   *              @ctx.session.close_origin (ou expira pelo _HOOK_TIMEOUT_S).
   *
   * Default: "timeout" (backward compatible).
   */
  nps_on_disconnect: z.enum(["skip", "timeout"]).default("timeout"),
})
export type PoolHookEntry = z.infer<typeof PoolHookEntrySchema>

export const PoolHooksSchema = z.object({
  on_human_start: z.array(PoolHookEntrySchema).default([]),
  on_human_end:   z.array(PoolHookEntrySchema).default([]),
  post_human:     z.array(PoolHookEntrySchema).default([]),
})
export type PoolHooks = z.infer<typeof PoolHooksSchema>

// ──────────────────────────────────────────────────────────────────────────────

export const PoolRegistrationSchema = z.object({
  pool_id:                z.string().regex(/^[a-z0-9_]+$/),
  description:            z.string().optional(),
  channel_types:          z.array(ChannelSchema).min(1),
  sla_target_ms:          z.number().int().positive(),
  /**
   * Arc 19 — Pool webhook: skill_id que este pool executa (o "DIN" do canal webhook).
   * Obrigatório quando channel_types inclui "webhook". Um pool webhook tem exactamente
   * um skill (o processo de negócio que ele gerencia). Determina o endpoint:
   * POST /v1/channels/webhook/{skill_id}
   */
  webhook_skill_id:       z.string().optional(),
  /**
   * Arc 19 (revisado 2026-06-04) — throttle OPCIONAL de downstream.
   * NÃO é capacidade: alocação real é capada pelos slots de instância do
   * deploy (Bootstrap) + admissão híbrida. Quando setado num pool webhook,
   * o Monitor exibe `available = max − busy` como teto de backpressure
   * (sistemas downstream frágeis, ex. ERP). Ausente (caso normal) = sem
   * throttle; admissão é o único teto. Enforcement no routing = deferred.
   */
  // .nullable(): PUT com null LIMPA o campo (antes só SQL — gap recorrente).
  max_concurrent_sessions: z.number().int().positive().nullable().optional(),
  /**
   * Fase B (queue-attended-model) — admissão híbrida de sessões.
   * Fatia dedicada de sessões simultâneas deste pool (teto E garantia),
   * subtraída do max_session_total da instalação. Ausente = o pool consome
   * o bucket compartilhado (total − Σ reservas). Billing é só sobre o total.
   * Invariante: Σ session_reservation ≤ max_session_total.
   */
  session_reservation:    z.number().int().positive().nullable().optional(),
  /**
   * Tempo máximo (ms) para o agente responder a cada mensagem do cliente.
   * Opcional — sem limite por mensagem quando ausente.
   * Independente do sla_target_ms (que mede o atendimento como um todo).
   */
  max_reply_time_ms:      z.number().int().positive().nullable().optional(),
  routing_expression:     RoutingExpressionSchema.optional(),
  evaluation:             PoolEvaluationConfigSchema.optional(),
  /** ID explícito do evaluation template (alternativa ao template resolvido por skill_id_template). */
  evaluation_template_id: z.string().optional(),
  supervisor_config:      SupervisorConfigSchema.optional(),
  queue_config:           QueueConfigSchema.nullable().optional(),
  /**
   * Mapa alias → pool_id dos agentes que podem ser endereçados via @mention
   * por participantes desta fila (role: primary ou human).
   *
   * Domínio fechado — apenas aliases declarados aqui têm efeito de roteamento.
   * Aliases não declarados são texto inerte (entregues normalmente sem roteamento).
   *
   * Exemplo:
   *   mentionable_pools:
   *     copilot:  copilot_retencao     # @copilot → pool copilot_retencao
   *     billing:  billing_especialista # @billing → pool billing_especialista
   */
  mentionable_pools:      z.record(z.string()).optional(),
  /**
   * IDs dos Agent Groups (Arc 9) aos quais este pool pertence.
   * Escrito no ContextStore como session.pool.agent_groups[] após cada roteamento.
   */
  agent_groups:           z.array(z.string()).optional(),
  /**
   * @deprecated Usar `hooks.on_human_start` com pool explícito.
   * skill_id do Co-pilot associado a esta fila.
   */
  copilot_skill_id:       z.string().optional(),
  /**
   * Lifecycle hooks — acionam especialistas em pontos específicos do atendimento humano.
   *
   * on_human_start  — disparado quando um agente humano entra na sessão
   *                   (após conversation.assigned ser publicado).
   *                   Uso típico: auto-activar co-pilot ou assistente de contexto.
   *
   * on_human_end    — disparado quando o agente humano chama agent_done
   *                   mas ANTES do contato ser encerrado (requer Fase B).
   *                   Uso típico: pesquisa NPS, atualização de CRM, encerramento guiado.
   *
   * post_human      — disparado após todos os hooks on_human_end concluírem,
   *                   imediatamente antes do contact_close (requer Fase B).
   *                   Uso típico: agente de qualidade, resumo automático.
   *
   * Cada entrada { pool } define o pool de onde o especialista será recrutado.
   * O routing engine seleciona a instância disponível do pool alvo e a ativa
   * como participante de conferência na sessão existente.
   */
  hooks:                  PoolHooksSchema.optional(),
  /**
   * UUID of the associated calendar in calendar-api.
   * Stored here as a display cache — the authoritative link lives in
   * calendar.calendar_associations. The UI keeps both in sync.
   */
  calendar_id:            z.string().uuid().nullable().optional(),
  /**
   * ContextStore visibility configuration for the ContextoTab.
   * Controls which namespaces the operator role can see.
   *
   * Default (when absent): ["service", "journey", "session"]
   * PII namespaces (caller, account, history) always appear masked when included.
   * supervisor/admin always see all namespaces with full values.
   *
   * Examples:
   *   SAC genérico:  operator_namespaces: ["service", "journey", "session"]
   *   Cobrança:      operator_namespaces: ["service", "journey", "session", "account", "history"]
   */
  context_visibility:     z.object({
    operator_namespaces: z.array(z.string()).min(1),
  }).optional(),
})
export type PoolRegistration = z.infer<typeof PoolRegistrationSchema>

// ─────────────────────────────────────────────
// Agent Type Registration — PlugHub spec 4.5 Registro de Tipo
// ─────────────────────────────────────────────

export const AgentFrameworkSchema = z.enum([
  "plughub-native",   // Orchestrador nativo da plataforma — executa via Skill Flow
  "external-mcp",     // Agente externo integrado via MCP (spec 4.6k) — bridge faz LPUSH
  "langgraph",
  "crewai",
  "anthropic_sdk",
  "azure_ai",
  "google_vertex",
  "generic_mcp",
  "human",
])
export type AgentFramework = z.infer<typeof AgentFrameworkSchema>

export const AgentRoleSchema = z.enum([
  "executor",       // resolve diretamente — padrão
  "orchestrator",   // coordena outros via skill flow
  "evaluator",      // avalia qualidade pós-sessão (nunca recebe tráfego ao vivo)
])
export type AgentRole = z.infer<typeof AgentRoleSchema>

export const AgentClassificationSchema = z.object({
  type:     z.enum(["vertical", "horizontal"]),
  industry: z.string().optional(),
  domain:   z.string().optional(),
})

export const AgentTypeRegistrationSchema = z.object({
  agent_type_id: z.string().regex(
    /^[a-z][a-z0-9_]+_v\d+$/,
    "Formato: {nome}_v{n}"
  ),

  framework:       AgentFrameworkSchema,
  execution_model: z.enum(["stateless", "stateful"]),
  role:            AgentRoleSchema.default("executor"),

  /** Para humanos: total de conversas incluindo conferências ativas */
  max_concurrent_sessions: z.number().int().min(1).default(1),

  pools: z.array(z.string()).min(1),

  /** Skills referenciadas — herda tools e knowledge_domains */
  skills: z.array(SkillRefSchema).default([]),

  /** Tools MCP não cobertas por nenhuma skill */
  permissions: z.array(
    z.string().regex(/^[a-z0-9_-]+:[a-z0-9_]+$/, "Formato: mcp-server-nome:tool_name")
  ).default([]),

  /** Capabilities declaradas — união das skills + capacidades próprias */
  capabilities: z.record(z.string()).default({}),

  /**
   * Meios de mídia suportados pelo agente para o canal WebRTC.
   * Valores possíveis: "video" | "voice" | "text"
   * Ordem implica preferência (primeiro = preferido).
   * Vazio ou ausente → apenas text (fallback universal).
   */
  media_capabilities: z.array(
    z.enum(["video", "voice", "text"])
  ).default([]),

  agent_classification: AgentClassificationSchema.optional(),

  /** null para agentes humanos */
  prompt_id: z.string().nullable().optional(),
}).refine(
  (agent: { role: string; skills: unknown[] }) => {
    if (agent.role === "orchestrator") {
      return agent.skills.length > 0
    }
    return true
  },
  { message: "Agentes orchestrator devem referenciar ao menos uma skill de orquestração" }
)
export type AgentTypeRegistration = z.infer<typeof AgentTypeRegistrationSchema>

// ─────────────────────────────────────────────
// Pipeline State — spec 4.7 / 9.5i
// Estado do orquestrador persistido no Redis
// ─────────────────────────────────────────────

export const PipelineStateSchema = z.object({
  /** Identificador do flow sendo executado */
  flow_id:         z.string(),
  current_step_id: z.string(),
  status:          z.enum(["in_progress", "completed", "failed", "suspended"]),
  started_at:      z.string().datetime(),
  updated_at:      z.string().datetime(),

  /** Resultados de cada step — chave = output_as do step */
  results: z.record(z.unknown()).default({}),

  /** Contadores de retry por step catch — chave = step id */
  retry_counters: z.record(z.number().int()).default({}),

  /**
   * Contexto do último erro — presente quando status === "failed" ou
   * quando um step catch está em progresso.
   */
  error_context: z.object({
    step_id:   z.string(),
    error:     z.string(),
    timestamp: z.string().datetime(),
  }).optional(),

  /** Histórico de transições para auditoria */
  transitions: z.array(z.object({
    from_step:   z.string(),
    to_step:     z.string(),
    reason:      z.enum(["on_success", "on_failure", "condition_match", "default", "suspended", "resumed"]),
    timestamp:   z.string().datetime(),
  })).default([]),
})
export type PipelineState = z.infer<typeof PipelineStateSchema>

// ─────────────────────────────────────────────
// Routing Decision — spec 3.3
// Retorno do Routing Engine para cada decisão de alocação
// ─────────────────────────────────────────────

export const RoutingModeSchema = z.enum(["autonomous", "hybrid", "supervised"])
export type RoutingMode = z.infer<typeof RoutingModeSchema>

export const RoutingDecisionSchema = z.object({
  /** Agente primário alocado */
  agent_type_id: z.string().regex(/^[a-z][a-z0-9_]+_v\d+$/, "Formato: {nome}_v{n}"),

  /** Agente de fallback caso o primário fique indisponível */
  fallback: z.string().regex(/^[a-z][a-z0-9_]+_v\d+$/, "Formato: {nome}_v{n}").optional(),

  /** Operation mode of the session */
  mode: RoutingModeSchema,

  /**
   * Turno em que o Routing Engine deve reavaliar a alocação.
   * null = sem reavaliação programada.
   */
  reevaluation_turn: z.number().int().positive().nullable().default(null),
})
export type RoutingDecision = z.infer<typeof RoutingDecisionSchema>

// ─────────────────────────────────────────────
// Tenant Config — spec 14
// ─────────────────────────────────────────────

export const TenantTierSchema = z.enum(["standard", "enterprise"])
export type TenantTier = z.infer<typeof TenantTierSchema>

export const TenantConfigSchema = z.object({
  tenant_id:    z.string().min(1),
  tier:         TenantTierSchema,
  workspace_id: z.string().min(1),
  rate_limits:  z.object({
    /** Requisições por minuto por endpoint da API administrativa */
    requests_per_minute:   z.number().int().positive().default(1000),
    /** Sessões simultâneas ativas */
    max_concurrent_sessions: z.number().int().positive().default(500),
    /** Chamadas MCP por segundo */
    mcp_calls_per_second:  z.number().int().positive().default(100),
  }).default({}),
})
export type TenantConfig = z.infer<typeof TenantConfigSchema>

// ─────────────────────────────────────────────
// AgentType — alias de AgentTypeRegistration para a API pública
// ─────────────────────────────────────────────

export const AgentTypeSchema = AgentTypeRegistrationSchema
export type AgentType = AgentTypeRegistration
