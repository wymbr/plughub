/**
 * skill.ts
 * Schema Zod para o Skill Registry da PlugHub Platform.
 * Fonte da verdade: PlugHub PlugHub spec v24.0 seção 4.7
 *
 * Uma skill é a unidade mínima de capacidade reutilizável.
 * Skills de orquestração (classification.type: "orchestrator")
 * incluem o campo flow — grafo declarativo de steps.
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// Classificação da skill
// ─────────────────────────────────────────────

export const SkillTypeSchema = z.enum([
  "vertical",       // especializada num domínio de indústria
  "horizontal",     // reutilizável entre verticais
  "orchestrator",   // define um flow de coordenação
])
export type SkillType = z.infer<typeof SkillTypeSchema>

export const SkillClassificationSchema = z.object({
  type:     SkillTypeSchema,
  vertical: z.string().optional(),  // ex: "telco", "finserv", "saude"
  domain:   z.string().optional(),  // ex: "portabilidade", "cobranca"
})

// ─────────────────────────────────────────────
// Tools da skill
// ─────────────────────────────────────────────

export const SkillToolSchema = z.object({
  mcp_server: z.string(),
  tool:       z.string(),
  required:   z.boolean().default(true),
})
export type SkillTool = z.infer<typeof SkillToolSchema>

// ─────────────────────────────────────────────
// Interface (input/output schema da skill)
// ─────────────────────────────────────────────

export const SkillInterfaceSchema = z.object({
  input_schema:  z.record(z.string()),  // campo → tipo/descrição
  output_schema: z.record(z.string()),  // campo → tipo/descrição
})

// ─────────────────────────────────────────────
// Evaluation
// ─────────────────────────────────────────────

export const EvaluationCriterionSchema = z.object({
  name:   z.string(),
  weight: z.number().min(0).max(1),
})

export const SkillEvaluationSchema = z.object({
  template_id:           z.string(),
  criteria:              z.array(EvaluationCriterionSchema),
  evaluate_independently: z.boolean().default(false),
})

// ─────────────────────────────────────────────
// Flow — steps do orquestrador nativo
// Spec 4.7: oito tipos de step
// ─────────────────────────────────────────────

/** Referência JSONPath a valor no pipeline_state ou session */
const JsonPathSchema = z.string().regex(/^\$\./, "Deve ser JSONPath iniciando com $.")

/** Target de um step task ou invoke */
const TaskTargetSchema  = z.object({ skill_id: z.string() })
const InvokeTargetSchema = z.object({
  mcp_server: z.string(),
  tool:       z.string(),
})
const EscalateTargetSchema = z.object({ pool: z.string() })

/** Input de steps invoke e reason — literais ou JSONPath */
const StepInputSchema = z.record(
  z.union([z.string(), z.number(), z.boolean(), JsonPathSchema])
)

/** Condição para step choice */
const ConditionSchema = z.object({
  field:    JsonPathSchema,
  operator: z.enum(["eq", "neq", "gt", "gte", "lt", "lte", "contains"]),
  value:    z.union([z.string(), z.number(), z.boolean()]),
  next:     z.string(),
})

// ── Os 8 tipos de step ──

export const TaskStepSchema = z.object({
  id:     z.string(),
  type:   z.literal("task"),
  target: TaskTargetSchema,

  /**
   * Modo de delegação da tarefa:
   *   assist   → session_invite: especialista entra como participante paralelo
   *              (role: "specialist"). Sessão continua com múltiplos agentes.
   *   transfer → session_escalate: handoff completo para outro agente/pool.
   *              Agente atual é removido da sessão ao confirmar a transferência.
   */
  mode: z.enum(["assist", "transfer"]).default("transfer"),

  /**
   * sync  — fire-and-poll: engine aguarda conclusão na mesma chamada.
   * async — fire-and-return: engine persiste job_id e retorna; webhook via
   *          Kafka atualiza o estado e reaciona o engine para retomar.
   */
  execution_mode: z.enum(["sync", "async"]).default("sync"),
  on_success:     z.string(),
  on_failure:     z.string(),
})

export const ChoiceStepSchema = z.object({
  id:         z.string(),
  type:       z.literal("choice"),
  conditions: z.array(ConditionSchema).min(1),
  default:    z.string(),
})

export const CatchStrategySchema = z.discriminatedUnion("type", [
  z.object({
    type:         z.literal("retry"),
    max_attempts: z.number().int().min(1).max(5),
    delay_ms:     z.number().int().min(0).default(1000),
    on_exhausted: z.string(),
  }),
  z.object({
    type:       z.literal("fallback"),
    id:         z.string(),
    target:     z.union([TaskTargetSchema, EscalateTargetSchema]),
    on_success: z.string(),
    on_failure: z.string(),
  }),
])

export const CatchStepSchema = z.object({
  id:            z.string(),
  type:          z.literal("catch"),
  error_context: z.string(),              // id do step que falhou
  strategies:    z.array(CatchStrategySchema).min(1),
  on_failure:    z.string(),              // após esgotar todas as strategies
})

export const EscalateStepSchema = z.object({
  id:           z.string(),
  type:         z.literal("escalate"),
  target:       EscalateTargetSchema,
  context:      z.literal("pipeline_state"),
  error_reason: z.string().optional(),
})

export const CompleteStepSchema = z.object({
  id:      z.string(),
  type:    z.literal("complete"),
  outcome: z.enum(["resolved", "escalated_human", "transferred_agent", "callback"]),
})

export const InvokeStepSchema = z.object({
  id:         z.string(),
  type:       z.literal("invoke"),
  /**
   * Full form (external agents): target.mcp_server + target.tool
   * Native form (plughub-native): top-level `tool` field — platform routes to mcp-server-plughub
   * At least one of target or tool must be present (validated at runtime by skill-flow engine).
   */
  target:     InvokeTargetSchema.optional(),
  tool:       z.string().optional(),
  input:      StepInputSchema.optional(),
  /** Output binding key — optional for fire-and-forget invocations */
  output_as:  z.string().optional(),
  on_success: z.string(),
  on_failure: z.string(),
})

/** output_schema para step reason — subconjunto de JSON Schema */
const ReasonOutputFieldSchema = z.object({
  type:     z.enum(["string", "number", "boolean", "object", "array"]),
  enum:     z.array(z.string()).optional(),
  minimum:  z.number().optional(),
  maximum:  z.number().optional(),
  required: z.boolean().optional(),
})

export const ReasonStepSchema = z.object({
  id:                 z.string(),
  type:               z.literal("reason"),
  prompt_id:          z.string(),
  input:              StepInputSchema.optional(),
  output_schema:      z.record(ReasonOutputFieldSchema),
  output_as:          z.string(),
  max_format_retries: z.number().int().min(0).max(3).default(1),
  on_success:         z.string(),
  on_failure:         z.string(),
})

export const NotifyStepSchema = z.object({
  id:         z.string(),
  type:       z.literal("notify"),
  /** Suporta {{$.pipeline_state.*}} para personalização dinâmica */
  message:    z.string().min(1),
  channel:    z.enum(["session", "whatsapp", "sms", "email"]).default("session"),
  on_success: z.string(),
  on_failure: z.string(),
})

/**
 * MenuStep — envia um prompt ao cliente e suspende a execução até receber resposta.
 * Spec: plughub_spec_v1.docx seção 9 — step type "menu"
 *
 * O Channel Gateway é responsável por renderizar botões/listas quando o canal
 * suporta. O Skill Flow Engine sempre recebe um único interaction_result
 * normalizado, independentemente de quantos turnos de canal foram necessários.
 *
 * Semântica do timeout_s:
 *   0    → retorno imediato — não aguarda resposta (fire-and-forget)
 *   > 0  → bloqueia N segundos; se não houver resposta, transita para on_timeout
 *   -1   → bloqueia indefinidamente — só avança quando o cliente responder
 *          ou quando a sessão expirar (TTL do Redis aciona on_disconnect)
 *
 * Default: 300 (5 min).
 */
export const MenuStepSchema = z.object({
  id:          z.string(),
  type:        z.literal("menu"),
  /** Prompt enviado ao cliente antes de aguardar a resposta */
  prompt:      z.string().min(1),
  interaction: z.enum(["text", "button", "list", "checklist", "form"]).default("text"),
  /** Opções de interação (button/list/checklist) */
  options:     z.array(z.object({
    id:    z.string(),
    label: z.string(),
  })).optional(),
  /** Campos de formulário (form) */
  fields: z.array(z.object({
    id:       z.string(),
    label:    z.string(),
    type:     z.string(),
    required: z.boolean().default(false),
  })).optional(),
  /** Chave para armazenar a resposta do cliente em pipeline_state.results */
  output_as: z.string().optional(),
  /**
   * Tempo limite para aguardar a resposta (segundos).
   *   0  → retorno imediato (sem espera)
   *  >0  → bloqueia N segundos
   *  -1  → bloqueia indefinidamente
   * Default: 300
   */
  timeout_s:     z.number().int().min(-1).default(300),
  on_success:    z.string(),
  on_failure:    z.string(),
  /** Step para timeout — usa on_failure se não especificado */
  on_timeout:    z.string().optional(),
  /** Step para desconexão do cliente — usa on_failure se não especificado */
  on_disconnect: z.string().optional(),
})

// ── CollectStep — async multi-channel data collection (Arc 4 extension) ──────

/**
 * Target for a collect step — who to contact.
 */
export const CollectTargetSchema = z.object({
  /** "customer" = known customer in CRM, "agent" = internal agent, "external" = ad-hoc contact */
  type: z.enum(["customer", "agent", "external"]),
  /** customer_id / agent_id / phone or email for external */
  id:   z.string().min(1),
})
export type CollectTarget = z.infer<typeof CollectTargetSchema>

/**
 * CollectStep — contacts a target via any channel, presents an interaction
 * (prompt + optional options/fields), and suspends the workflow until the
 * target responds or the timeout expires.
 *
 * Timing:
 *   scheduled_at  — absolute ISO-8601 datetime to initiate contact
 *   delay_hours   — relative offset from now() (optional; ignored if scheduled_at set)
 *   If neither is set, contact is initiated immediately.
 *
 * Response deadline:
 *   timeout_hours + business_hours — uses calendar-api when business_hours=true
 *   Deadline is calculated from the actual send time, not the workflow trigger time.
 *
 * Campaign grouping:
 *   campaign_id — optional tag that groups N workflow instances into one campaign.
 *   Used by analytics for aggregate campaign reporting.
 */
export const CollectStepSchema = z.object({
  id:   z.string(),
  type: z.literal("collect"),

  // ── Who to contact ──
  target:  CollectTargetSchema,

  // ── How to contact ──
  channel: z.enum(["whatsapp", "sms", "email", "voice", "webchat"]),

  // ── What to collect ──
  interaction: z.enum(["text", "button", "form"]).default("text"),
  prompt:      z.string().min(1),
  options:     z.array(z.object({
    id:    z.string(),
    label: z.string(),
  })).optional(),
  fields: z.array(z.object({
    id:       z.string(),
    label:    z.string(),
    type:     z.string(),
    required: z.boolean().default(false),
  })).optional(),

  // ── When to initiate contact ──
  /** Absolute ISO-8601 send time. Takes precedence over delay_hours. */
  scheduled_at:  z.string().datetime().optional(),
  /** Hours from now() — used when scheduled_at is absent */
  delay_hours:   z.number().nonnegative().optional(),

  // ── How long to wait for a response (after contact is made) ──
  timeout_hours:  z.number().positive().default(48),
  business_hours: z.boolean().default(true),
  calendar_id:    z.string().uuid().optional(),

  // ── Campaign grouping (optional) ──
  campaign_id:    z.string().optional(),

  // ── Output ──
  /** Key under which the response is stored in pipeline_state.results */
  output_as: z.string(),

  // ── Transitions ──
  on_response: z.object({ next: z.string() }),
  on_timeout:  z.object({ next: z.string() }),
})
export type CollectStep = z.infer<typeof CollectStepSchema>

/** Step discriminado por type */
export const FlowStepSchema = z.discriminatedUnion("type", [
  TaskStepSchema,
  ChoiceStepSchema,
  CatchStepSchema,
  EscalateStepSchema,
  CompleteStepSchema,
  InvokeStepSchema,
  ReasonStepSchema,
  NotifyStepSchema,
  MenuStepSchema,
  CollectStepSchema,
  // Arc 4: workflow automation
  z.object({
    type:           z.literal("suspend"),
    id:             z.string(),
    reason:         z.enum(["approval", "input", "webhook", "timer"]),
    timeout_hours:  z.number().positive().default(48),
    business_hours: z.boolean().default(true),
    calendar_id:    z.string().uuid().optional(),
    notify:         z.object({
      visibility: z.enum(["all", "agents_only"]).default("agents_only"),
      text:       z.string(),
    }).optional(),
    on_resume:      z.object({ next: z.string() }),
    on_timeout:     z.object({ next: z.string() }),
    on_reject:      z.object({ next: z.string() }).optional(),
    metadata:       z.record(z.unknown()).optional(),
  }),
])
export type FlowStep = z.infer<typeof FlowStepSchema>

// ── Inferred step types — consumed by skill-flow-engine ──
export type TaskStep      = z.infer<typeof TaskStepSchema>
export type ChoiceStep    = z.infer<typeof ChoiceStepSchema>
export type CatchStrategy = z.infer<typeof CatchStrategySchema>
export type CatchStep     = z.infer<typeof CatchStepSchema>
export type EscalateStep  = z.infer<typeof EscalateStepSchema>
export type CompleteStep  = z.infer<typeof CompleteStepSchema>
export type InvokeStep    = z.infer<typeof InvokeStepSchema>
export type ReasonStep    = z.infer<typeof ReasonStepSchema>
export type NotifyStep    = z.infer<typeof NotifyStepSchema>
export type MenuStep      = z.infer<typeof MenuStepSchema>
// Arc 4 — suspend step (inline schema in FlowStepSchema discriminated union)
export type SuspendStep   = Extract<FlowStep, { type: "suspend" }>
// Arc 4 extension — collect step
// CollectStep type already exported above

/** Flow de orquestração — presente apenas quando type === "orchestrator" */
export const SkillFlowSchema = z.object({
  entry: z.string(),
  steps: z.array(FlowStepSchema).min(1),
}).refine(
  (flow: { entry: string; steps: Array<{ id: string }> }) => flow.steps.some((s) => s.id === flow.entry),
  { message: "entry deve referenciar um step existente", path: ["entry"] }
).refine(
  (flow: { steps: Array<{ type: string }> }) => flow.steps.some((s) => s.type === "complete" || s.type === "escalate"),
  { message: "Flow deve ter pelo menos um step do tipo complete ou escalate" }
)
export type SkillFlow = z.infer<typeof SkillFlowSchema>

// ─────────────────────────────────────────────
// Skill — schema completo
// ─────────────────────────────────────────────

export const VersionPolicySchema = z.enum(["stable", "latest", "exact"])

export const SkillSchema = z.object({
  skill_id:    z.string().regex(/^skill_[a-z0-9_]+_v\d+$/, "Formato: skill_{nome}_v{n}"),
  name:        z.string(),
  version:     z.string().regex(/^\d+\.\d+$/, "Formato: major.minor"),
  description: z.string(),

  classification: SkillClassificationSchema,

  instruction: z.object({
    prompt_id: z.string(),
    language:  z.string().default("pt-BR"),
  }),

  tools:      z.array(SkillToolSchema).default([]),
  interface:  SkillInterfaceSchema.optional(),
  evaluation: SkillEvaluationSchema.optional(),

  knowledge_domains: z.array(z.string()).default([]),

  compatibility: z.object({
    frameworks: z.array(z.string()).default([]),
    channels:   z.array(z.string()).default([]),
  }).optional(),

  /** Presente apenas quando classification.type === "orchestrator" */
  flow: SkillFlowSchema.optional(),
}).refine(
  (skill: { classification: { type: string }; flow?: unknown }) => {
    if (skill.classification.type === "orchestrator") {
      return skill.flow !== undefined
    }
    return true
  },
  { message: "Skills de orquestração devem ter o campo flow", path: ["flow"] }
)

export type Skill = z.infer<typeof SkillSchema>

/**
 * SkillRegistration — alias canônico de SkillSchema para a API pública.
 * Mesma validação; nomenclatura alinhada com o Agent Registry.
 */
export const SkillRegistrationSchema = SkillSchema
export type SkillRegistration = Skill

// ─────────────────────────────────────────────
// Referência a skill no registro de tipo de agente
// ─────────────────────────────────────────────

export const SkillRefSchema = z.object({
  skill_id:       z.string(),
  version_policy: VersionPolicySchema.default("stable"),
  exact_version:  z.string().optional(),
}).refine(
  (ref) => ref.version_policy !== "exact" || ref.exact_version !== undefined,
  { message: "exact_version é obrigatório quando version_policy === 'exact'" }
)
export type SkillRef = z.infer<typeof SkillRefSchema>
