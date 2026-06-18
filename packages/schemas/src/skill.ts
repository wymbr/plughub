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
import { ToolContextTagsSchema, ReasonStepContextTagsSchema, SkillRequiredContextSchema, ContextTagScopeSchema } from "./context-store"

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

/**
 * Referência a valor no pipeline_state (JSONPath) ou ContextStore (@ctx).
 * JSONPath: $.pipeline_state.results.analise.sentimento
 * ContextStore: @ctx.session.sentimento.current
 * ContextStore special: @ctx.__gaps__, @ctx.__pending_question__
 */
const JsonPathSchema = z.string().regex(
  /^(\$\.|@ctx\.)/,
  "Deve ser JSONPath ($.) ou referência ao ContextStore (@ctx.)"
)

/** Target de um step task ou invoke */
const TaskTargetSchema  = z.object({ skill_id: z.string() })
const InvokeTargetSchema = z.object({
  mcp_server: z.string(),
  tool:       z.string(),
})
const EscalateTargetSchema = z.object({ pool: z.string() })

/**
 * Input de steps invoke e reason — literais, JSONPath, objetos OU arrays aninhados.
 * Aceita JSON arbitrário (recursivo) porque tools MCP podem receber parâmetros
 * estruturados: objetos aninhados (ex.: template_vars) E arrays de objetos
 * (ex.: survey_record `signals: [{metric, value}]`). O engine (resolveInputValue)
 * resolve refs em objetos/arrays e mantém literais — o schema só precisa permitir
 * a forma. Valores nulos são aceitos (campos opcionais).
 */
type StepInputValue =
  | string
  | number
  | boolean
  | null
  | StepInputValue[]
  | { [key: string]: StepInputValue }
const StepInputValueSchema: z.ZodType<StepInputValue> = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(StepInputValueSchema),
    z.record(StepInputValueSchema),
  ]),
)
const StepInputSchema = z.record(StepInputValueSchema)

/** Condição para step choice */
const ConditionSchema = z.object({
  field:    JsonPathSchema,
  /**
   * Operadores disponíveis:
   *   eq, neq, gt, gte, lt, lte, contains — comparação de valor
   *   exists          — tag presente no ContextStore com qualquer valor
   *   confidence_gte  — confidence da ContextEntry ≥ value (apenas @ctx.*)
   */
  operator: z.enum(["eq", "neq", "gt", "gte", "lt", "lte", "contains", "exists", "confidence_gte"]),
  value:    z.union([z.string(), z.number(), z.boolean()]).optional(),  // opcional para "exists"
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
  on_failure:   z.string().optional(),
  handoff_reason: z.string().optional(),
  // F7: normalized escalation reason id (from config escalation_reasons). Lets an
  // AI agent declare WHY it escalated; the bridge stamps it on the AI segment.
  reason:       z.string().optional(),
})

export const CompleteStepSchema = z.object({
  id:      z.string(),
  type:    z.literal("complete"),
  // F1.1 (bancada de agentes): subset declarável do SegmentOutcomeSchema (cânone).
  // Adicionados escalated/suspended/abandoned (mapa do wrap-up: resolvido→resolved,
  // escalado→escalated, cancelado→abandoned, pendente→suspended). transferred_agent
  // permanece (valor de contrato, load-bearing no SDK/adapter); a divergência
  // transferred_agent↔transferred e o folding da família escalate são tratados na
  // leitura (F3), não por rename aqui.
  outcome: z.enum([
    "resolved",
    "escalated",
    "escalated_human",
    "transferred_agent",
    "callback",
    "failed",
    "suspended",
    "abandoned",
  ]),
  // F1.2 (bancada de agentes): outcome dinâmico. Quando presente, o engine resolve
  // o valor desta chave em pipeline_state (ex. `output_as` de um menu), valida contra
  // SegmentOutcomeSchema e usa-o; se ausente/inválido, cai no `outcome` literal acima
  // (fallback obrigatório). Ex.: wrap-up usa `outcome_from: wrapup_classificacao`.
  outcome_from: z.string().optional(),
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
  /**
   * context_tags — mapeamento declarativo de inputs/outputs para o ContextStore.
   * Complementa o McpInterceptor: se o interceptor não tiver a anotação registrada,
   * o engine a aplica diretamente ao processar o invoke step.
   */
  context_tags: ToolContextTagsSchema.optional(),
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
  // T7b — JSON Schema completo (montado UPSTREAM a partir do form) para tool-use
  // nativo no ai-gateway. Inline (`json_schema`) OU referência JSONPath resolvida do
  // pipeline_state (`json_schema_ref`, ex. "$.pipeline_state.eval_context.evaluation_output_schema").
  // Quando presente, o reason usa tool-use e PULA a validação estática local
  // (o ai-gateway valida recursivamente). `output_schema` permanece como fallback.
  json_schema:        z.record(z.unknown()).optional(),
  json_schema_ref:    z.string().optional(),
  output_as:          z.string(),
  max_format_retries: z.number().int().min(0).max(3).default(1),
  /**
   * Mapeamento declarativo de campos do output para o ContextStore (Opção A).
   * O engine chama ContextAccumulator.extractFromOutputs() após execução do LLM.
   *
   * Exemplo:
   *   context_tags:
   *     outputs:
   *       sentimento: { tag: "session.sentimento.current", confidence: 0.8, merge: "overwrite" }
   *       escalar:    { tag: "session.escalar_solicitado",  confidence: 1.0, merge: "overwrite" }
   */
  context_tags:       ReasonStepContextTagsSchema.optional(),
  on_success:         z.string(),
  on_failure:         z.string(),
})

export const NotifyStepSchema = z.object({
  id:         z.string(),
  type:       z.literal("notify"),
  /** Suporta {{$.pipeline_state.*}} e {{@ctx.*}} para personalização dinâmica */
  message:    z.string().min(1),
  channel:    z.enum(["session", "whatsapp", "sms", "email"]).default("session"),
  /**
   * Visibilidade da mensagem.
   *   "all"             → entregue ao cliente e a todos os agentes (padrão quando ausente)
   *   "agents_only"     → entregue somente aos agentes; o cliente não vê a mensagem.
   *                       Usado por especialistas em conferência (ex: co-pilot, wrap-up).
   *   ["participant_id"] → entregue APENAS aos participant_ids listados.
   *                       Permite que um agente converse exclusivamente com o cliente
   *                       (via customer_participant_id) sem que o agente humano veja.
   *                       Usado pelo agente NPS para isolar avaliação do agente.
   *                       Suporta @ctx.* para resolução dinâmica do participant_id.
   */
  visibility: z.union([
    z.enum(["all", "agents_only"]),
    z.array(z.string().min(1)).min(1),
  ]).optional(),
  /**
   * Mapeamento de saída para o ContextStore.
   * Permite que o notify step registre dados no ContextStore após entrega.
   * Ex: registrar que o NPS foi enviado, ou gravar timestamp de notificação.
   */
  context_tags: z.object({
    outputs: z.record(z.object({
      tag:        z.string(),
      confidence: z.number().min(0).max(1).default(1.0),
      merge:      z.enum(["overwrite", "append"]).default("overwrite"),
      // F1.4b (bancada de agentes): o scope era declarado nos YAMLs (wrap-up/NPS)
      // mas descartado pelo Zod (campo ausente neste schema inline). Default
      // "session" — o desejado para fatos de sessão como a disposição do wrap-up.
      scope:      ContextTagScopeSchema.default("session"),
    })),
  }).optional(),
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
  /**
   * Quando true, todos os campos deste step são mascarados.
   * Pode ser sobrescrito por campo individual (field-level tem precedência).
   * Valores mascarados nunca entram no stream, pipeline_state ou logs.
   */
  masked: z.boolean().optional(),
  /**
   * Standby de @mention (mention-protocol): quando true, este menu NÃO recebe
   * mensagens comuns dos roteadores (texto do agente humano / menu_submit) —
   * acorda exclusivamente pelos interrupts do mention_command_dispatch
   * (_mention_trigger_step / _mention_terminate) e por session:closed.
   * Usado pelo step de espera de especialistas controlados por @mention
   * (ex.: co-pilot), que sem a flag acordava com qualquer mensagem do primary.
   */
  standby: z.boolean().optional(),
  /** Campos de formulário (form) */
  fields: z.array(z.object({
    id:       z.string(),
    label:    z.string(),
    type:     z.string(),
    required: z.boolean().default(false),
    /**
     * Quando true, este campo específico é mascarado — mesmo que masked=false no step.
     * Quando false, este campo NÃO é mascarado — mesmo que masked=true no step.
     * (field-level tem precedência sobre step-level)
     */
    masked:   z.boolean().optional(),
  })).optional(),
  /** Chave para armazenar a resposta do cliente em pipeline_state.results */
  output_as: z.string().optional(),
  /**
   * Visibilidade do prompt enviado antes de aguardar a resposta.
   *   "all"             → prompt entregue ao cliente e a todos os agentes (padrão quando ausente)
   *   "agents_only"     → prompt entregue somente aos agentes; o cliente não vê o prompt.
   *                       Útil para menus internos de co-pilot ou aprovação entre agentes.
   *   ["participant_id"] → prompt entregue APENAS aos participant_ids listados.
   *                       Usado para interações exclusivas com o cliente (ex: NPS).
   */
  visibility: z.union([
    z.enum(["all", "agents_only"]),
    z.array(z.string().min(1)).min(1),
  ]).optional(),
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
 * Arc 16 Phase D — Channel capability flags.
 * Used in CollectStep.requires[] to express what the channel must support,
 * allowing Channel Gateway to select the best available outbound channel
 * from journey.available_channels rather than hardcoding a specific channel.
 */
export const ChannelCapabilitySchema = z.enum([
  "text",         // basic text I/O (all channels)
  "audio",        // voice / audio recording (voice, webrtc)
  "video",        // video stream (webrtc)
  "file_upload",  // file / document upload (whatsapp, email, webchat)
  "masked_input", // password-overlay masked field (webchat)
  "rich_menu",    // buttons / list menus (whatsapp, webchat)
])
export type ChannelCapability = z.infer<typeof ChannelCapabilitySchema>

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
  /**
   * Explicit channel override. When set, the Channel Gateway uses this channel directly.
   * When omitted, the Gateway selects the best available channel from
   * journey.available_channels that satisfies all capabilities in `requires`.
   * At least one of `channel` or `requires` must be present.
   */
  channel:  z.enum(["whatsapp", "sms", "email", "voice", "webchat", "webrtc"]).optional(),

  /**
   * Arc 16 Phase D — Channel capability requirements.
   * When `channel` is omitted, the Channel Gateway selects an outbound channel
   * whose capability set is a superset of this array.
   * Resolved via journey.available_channels + journey.canal_preferido context tags.
   */
  requires: z.array(ChannelCapabilitySchema).optional(),

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

// ── BeginTransactionStep / EndTransactionStep — unidade atômica para dados mascarados ──

/**
 * BeginTransactionStep — abre um bloco atômico de captura sensível.
 *
 * Todos os steps até o EndTransactionStep correspondente são tratados como
 * uma unidade. Se qualquer step dentro do bloco falhar, o masked_scope é
 * descartado e o engine executa o step declarado em on_failure (rewind explícito).
 *
 * Regras:
 *   - on_failure aponta para o step de recoleta (dentro ou fora do bloco)
 *   - reason steps dentro do bloco que recebam @masked.* são inválidos
 *   - retry nunca reutiliza valores mascarados — sempre recoleta do usuário
 */
export const BeginTransactionStepSchema = z.object({
  id:         z.string(),
  type:       z.literal("begin_transaction"),
  /**
   * Step para rewind em caso de falha em qualquer step dentro do bloco.
   * Declarado explicitamente pelo autor — o engine nunca infere.
   * Pode apontar para dentro ou fora do bloco.
   */
  on_failure: z.string(),
})
export type BeginTransactionStep = z.infer<typeof BeginTransactionStepSchema>

/**
 * EndTransactionStep — fecha o bloco atômico no caminho de sucesso.
 *
 * Escreve o status da operação em pipeline_state.results[result_as].
 * Limpa o masked_scope da memória (valores sensíveis são descartados).
 *
 * Nota: rollback é sempre implícito e automático (via on_failure do begin_transaction).
 * Nunca existe rollback explícito no YAML.
 */
export const EndTransactionStepSchema = z.object({
  id:         z.string(),
  type:       z.literal("end_transaction"),
  /**
   * Chave em pipeline_state.results onde o status da operação será gravado.
   * Formato: { status: "ok", fields_collected: string[], completed_at: string }
   */
  result_as:  z.string().optional(),
  on_success: z.string().optional(),
})
export type EndTransactionStep = z.infer<typeof EndTransactionStepSchema>

// ── ResolveStep — coleta de contexto declarativa inline (Fase 3 — Arc 5 / Context-Aware) ──

/**
 * Campo do ContextStore que o resolve step deve garantir.
 * Análogo ao SkillRequiredContextSchema mas com semântica de coleta ativa.
 */
export const ResolveRequiredFieldSchema = z.object({
  /** Tag do ContextStore no formato namespace.campo (ex: "caller.cpf") */
  tag:            z.string().min(1),
  /** Confiança mínima aceitável — entradas abaixo disso disparam coleta */
  confidence_min: z.number().min(0).max(1).default(0.7),
  /** Se true, o step gera uma pergunta para este campo quando ausente ou incerto */
  required:       z.boolean().default(true),
})
export type ResolveRequiredField = z.infer<typeof ResolveRequiredFieldSchema>

/**
 * Lookup CRM opcional executado antes de perguntar ao cliente.
 * Evita coletar dados que já estão disponíveis no CRM.
 * Erros no lookup são não-fatais — o step avança para coleta manual.
 */
export const ResolveCrmLookupSchema = z.object({
  /** mcp-server alvo (ex: "mcp-server-crm") */
  mcp_server:   z.string().min(1),
  /** Tool do mcp-server (ex: "customer_get") */
  tool:         z.string().min(1),
  /** Inputs do tool — suporta literais, $.jsonpath e @ctx.namespace.campo */
  input:        StepInputSchema.optional(),
  /** Mapeamento de campos do resultado para o ContextStore */
  context_tags: ToolContextTagsSchema.optional(),
})
export type ResolveCrmLookup = z.infer<typeof ResolveCrmLookupSchema>

/**
 * ResolveStep — coleta de contexto declarativa inline no YAML.
 *
 * Substitui a necessidade de chamar agente_contexto_ia_v1 via task step.
 * Executa um pipeline de 5 fases sem criar uma sessão de agente extra:
 *
 *   Fase 1 — Gap check:    Verifica ContextStore. Se completo → on_success imediato.
 *   Fase 2 — CRM lookup:   Chama MCP tool para preencher gaps. Erros são não-fatais.
 *   Fase 3 — LLM question: Gera pergunta consolidada via AI Gateway.
 *   Fase 4 — Input:        Envia pergunta e aguarda resposta do cliente (BLPOP).
 *   Fase 5 — LLM extract:  Extrai campos da resposta e grava no ContextStore.
 *
 * Garantias:
 *   - Nunca bloqueia o fluxo: timeout/disconnect/erros de LLM → on_success com method=skipped
 *   - on_failure apenas para falhas catastróficas (notification_send, lock roubado)
 *   - 0 chamadas LLM quando CRM resolve o contexto
 *   - Máximo 2 chamadas LLM quando é necessário perguntar ao cliente
 */
export const ResolveStepSchema = z.object({
  id:   z.string(),
  type: z.literal("resolve"),

  /** Campos que o step deve garantir no ContextStore antes de avançar */
  required_fields: z.array(ResolveRequiredFieldSchema).min(1),

  /**
   * Lookup CRM executado na Fase 2 quando há gaps após a Fase 1.
   * Ausente → pula direto para geração de pergunta (Fase 3).
   */
  crm_lookup: ResolveCrmLookupSchema.optional(),

  /**
   * Prompt ID para geração da pergunta consolidada (Fase 3).
   * O AI Gateway recebe: { gaps: string[], context: Record<string, unknown> }
   * e deve retornar: { pergunta: string }
   */
  question_prompt_id: z.string().default("resolve_generate_question_v1"),

  /**
   * Prompt ID para extração de campos da resposta do cliente (Fase 5).
   * O AI Gateway recebe: { response: string, required_fields: string[], context: Record<string, unknown> }
   * e deve retornar: { fields: Record<string, string | null> }
   */
  extract_prompt_id: z.string().default("resolve_extract_fields_v1"),

  /**
   * Tempo limite (segundos) para aguardar resposta do cliente na Fase 4.
   *  -1 ou 0 → bloqueia indefinidamente (on_success com method=timeout nunca ocorre)
   *  >0      → avança para on_success com method=timeout ao expirar
   * Default: 300 (5 minutos)
   */
  timeout_s: z.number().int().min(-1).default(300),

  /** Chave em pipeline_state.results para o relatório de saída do resolve */
  output_as: z.string().optional(),

  on_success: z.string(),
  on_failure: z.string(),
})
export type ResolveStep = z.infer<typeof ResolveStepSchema>

// ── ReceiveStep — suspends awaiting next matching stream event (no prompt sent) ──

/**
 * Filter criteria for the receive step.
 * All declared fields must match (AND semantics).
 * Omitted fields are treated as wildcard (match any).
 */
export const ReceiveFilterSchema = z.object({
  /**
   * Restrict to events authored by these roles.
   * Omitted → accept events from any role.
   * Common: ["primary", "customer"] for evaluator agents.
   * Echo suppression is always applied by the bridge — the instance's own
   * messages are never pushed to its own receive:result queue.
   */
  author_role: z.array(
    z.enum(["primary", "specialist", "supervisor", "evaluator", "reviewer", "customer"])
  ).optional(),
  /**
   * Restrict to events with matching visibility value.
   * Omitted → accept events with any visibility.
   */
  visibility: z.enum(["all", "agents_only"]).nullable().optional(),
  /**
   * Stream event types to accept.
   * Default: ["message_sent"] — normal channel messages only.
   */
  event_types: z.array(z.string().min(1)).default(["message_sent"]),
})
export type ReceiveFilter = z.infer<typeof ReceiveFilterSchema>

/**
 * ReceiveStep — suspends the skill flow until a matching stream event arrives.
 *
 * Unlike menu (which sends a prompt to the channel and awaits a customer reply),
 * receive passively listens to the session stream and unblocks when a matching
 * event is published by any participant. No message is sent to the customer.
 *
 * Primary use case: Supervisor Evaluator agent — listens to the conversation
 * in real-time, analyses each turn via a reason step, and sends targeted
 * feedback to the inviter agent only.
 *
 * Cyclic DAG support:
 *   receive → reason → notify → (back-edge) receive
 *   max_iterations bounds the cycle; on_max_iterations handles graceful exit.
 *   The engine tracks iterations in pipeline_state._receive_iterations_{id}.
 *
 * Redis mechanism (same pattern as menu):
 *   HSET receive:waiting:{sessionId}  {instanceId}  <filter JSON>
 *   BLPOP receive:result:{sessionId}:{instanceId}   session:closed:{sessionId}
 *   HDEL  receive:waiting:{sessionId} {instanceId}  (on exit)
 *
 * Bridge contract:
 *   On every stream event matching event_types, bridge checks
 *   receive:waiting:{sessionId}, applies filter, and LPUSH to matching
 *   receive:result queues. Echo suppression: bridge always skips events
 *   authored by the blocked instance itself.
 *
 * Semântica do timeout_s:
 *   -1 → blocks indefinitely (only exits on session:closed or matching event)
 *   >0 → unblocks after N seconds → on_timeout
 * Default: -1
 */
export const ReceiveStepSchema = z.object({
  id:   z.string(),
  type: z.literal("receive"),

  /**
   * Filter criteria applied by the bridge before pushing to receive:result.
   * Omitting filter (or all its fields) accepts any stream event.
   */
  filter: ReceiveFilterSchema.optional(),

  /**
   * Seconds to block awaiting a matching event.
   *   -1 → indefinite (default)
   *   >0 → timeout after N seconds → on_timeout
   */
  timeout_s: z.number().int().min(-1).default(-1),

  /**
   * Maximum loop iterations before transitioning to on_max_iterations.
   * Required (and enforced at runtime) when the step has a back-edge (cyclic flow).
   * The engine tracks the counter in pipeline_state._receive_iterations_{id}.
   */
  max_iterations: z.number().int().positive().optional(),

  /**
   * Key in pipeline_state.results where the received event payload is stored.
   * Shape: { event_type, author_id, author_role, content, received_at }
   */
  output_as: z.string().optional(),

  /**
   * Optional message sent to the session BEFORE blocking.
   * Visibility defaults to "agents_only" — does not disturb the customer.
   * Useful to signal readiness or status to the inviter agent.
   */
  notify: z.object({
    message:    z.string().min(1),
    visibility: z.union([
      z.enum(["all", "agents_only"]),
      z.array(z.string().min(1)).min(1),
    ]).default("agents_only"),
  }).optional(),

  /** Transition after a matching event is received — required */
  on_message:        z.string(),
  /** Transition on timeout; uses on_failure when absent */
  on_timeout:        z.string().optional(),
  /** Transition when the session closes while blocked; uses on_failure when absent */
  on_disconnect:     z.string().optional(),
  /** Transition when max_iterations is reached */
  on_max_iterations: z.string().optional(),
  /** Transition on unrecoverable error — required */
  on_failure:        z.string(),
})
export type ReceiveStep = z.infer<typeof ReceiveStepSchema>

// ── MentionCommand — comandos que um agente especialista reconhece via @mention ──

/**
 * Ação executada quando o agente recebe um mention_command.
 */
const MentionCommandActionSchema = z.union([
  /** Escreve campos no ContextStore (fire-and-forget) */
  z.object({ set_context:    z.record(z.string()) }),
  /** Salta para o step declarado no skill flow */
  z.object({ trigger_step:  z.string() }),
  /** Agente sai da conferência via agent_done */
  z.object({ terminate_self: z.literal(true) }),
])

/**
 * MentionCommand — declara um comando que este agente reconhece quando
 * endereçado via @alias pelo agente humano.
 *
 * Comandos não reconhecidos são ignorados silenciosamente.
 * Texto livre (sem comando estruturado) pode alimentar um reason step.
 */
export const MentionCommandSchema = z.object({
  description: z.string().optional(),
  action:      MentionCommandActionSchema,
  /**
   * Quando true, o agente responde com uma mensagem agents_only confirmando
   * o recebimento do comando. Quando false, o step/ação responde por conta própria.
   */
  acknowledge: z.boolean().default(false),
})
export type MentionCommand = z.infer<typeof MentionCommandSchema>

// ─────────────────────────────────────────────────────────────────────────────
// DelegationInput — typed parameters shown in DelegarTarefaDrawer
// ─────────────────────────────────────────────────────────────────────────────

export const DelegationFieldSchema = z.object({
  id:          z.string(),
  label:       z.string(),
  type:        z.enum(["select", "text", "number"]),
  placeholder: z.string().optional(),
  required:    z.boolean().optional(),
  options:     z.array(z.object({
    value: z.string(),
    label: z.string(),
  })).optional(),
})
export type DelegationField = z.infer<typeof DelegationFieldSchema>

export const DelegationInputSchema = z.object({
  fields: z.array(DelegationFieldSchema),
})
export type DelegationInput = z.infer<typeof DelegationInputSchema>

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
  // Masked input: transação atômica
  BeginTransactionStepSchema,
  EndTransactionStepSchema,
  // Arc 5 / Context-Aware Fase 3: coleta de contexto inline
  ResolveStepSchema,
  // Real-time stream listener (Supervisor Evaluator, cyclic flows)
  ReceiveStepSchema,
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
  // Delegate — workflow suspends and dispatches an agent to handle I/O.
  // The agent calls workflow_resume (MCP tool) when done. Only valid in
  // workflow profile (channel_type: webhook). Agents must never use delegate.
  // See: docs/arcos/delegate-workflow-io.md
  z.object({
    type:           z.literal("delegate"),
    id:             z.string(),
    /** Pool where the I/O agent will be allocated via routing engine. */
    pool:           z.string().min(1),
    /**
     * Key→value pairs written to the child session's ContextStore before
     * the agent is activated. Supports @ctx.* and $.pipeline_state.* references.
     * The engine always writes session.workflow_resume_token automatically —
     * no need to declare it here.
     */
    context:        z.record(z.string()).optional(),
    /** How long to wait for the agent to call workflow_resume. Default: 24h. */
    timeout_hours:  z.number().positive().default(24),
    business_hours: z.boolean().default(false),
    calendar_id:    z.string().uuid().optional(),
    /** Next step when agent calls workflow_resume with decision=input|approved. */
    on_resume:      z.object({ next: z.string() }),
    /** Next step when agent calls workflow_resume with decision=rejected. */
    on_reject:      z.object({ next: z.string() }).optional(),
    /** Next step when timeout_hours elapses without a resume. */
    on_timeout:     z.object({ next: z.string() }),
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
export type SuspendStep          = Extract<FlowStep, { type: "suspend" }>
// delegate step (inline schema in FlowStepSchema discriminated union)
export type DelegateStep         = Extract<FlowStep, { type: "delegate" }>
// Arc 4 extension — collect step
// CollectStep type already exported above
// Masked input — transaction steps already exported above (BeginTransactionStep, EndTransactionStep)
// Real-time stream listener — ReceiveStep and ReceiveFilter already exported above

/** Flow de orquestração — presente apenas quando type === "orchestrator" */
export const SkillFlowSchema = z.object({
  entry: z.string(),
  steps: z.array(FlowStepSchema).min(1),

  /**
   * Campos do ContextStore que este fluxo precisa para executar corretamente.
   * O engine computa @ctx.__gaps__ comparando esta lista com o ContextStore atual
   * antes de entrar no fluxo. Tags ausentes ou com confiança abaixo do threshold
   * aparecem em @ctx.__gaps__.missing e @ctx.__gaps__.low_confidence.
   *
   * Exemplo em agente_contexto_ia_v1.yaml:
   *   required_context:
   *     - tag: "caller.cpf"
   *       confidence_min: 0.8
   *     - tag: "session.motivo_contato"
   *       confidence_min: 0.6
   */
  required_context: z.array(SkillRequiredContextSchema).optional(),

  /**
   * mention_commands — comandos @mention que este especialista reconhece.
   * Conceitualmente é metadado de skill, mas viaja DENTRO do flow para
   * round-trip pela agent-registry: o modelo Skill (Prisma) não tem coluna
   * dedicada, então um campo top-level seria descartado na persistência.
   * Declarado aqui para sobreviver ao CreateSkillSchema.parse (Zod descarta
   * chaves não declaradas). RegistrySyncer._sync_skills aninha a partir do
   * top-level do YAML; a síntese deploy-driven e o mention routing leem de volta.
   */
  mention_commands: z.record(MentionCommandSchema).optional(),
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

  /**
   * System prompt instruction — obrigatório para skills verticais/horizontais (LLM-driven).
   * Opcional para skills de orquestração (classification.type === "orchestrator")
   * que definem comportamento exclusivamente via campo flow.
   */
  instruction: z.object({
    prompt_id: z.string(),
    language:  z.string().default("pt-BR"),
  }).optional(),

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

  /**
   * mention_commands — comandos que este agente especialista reconhece quando
   * endereçado via @alias pelo agente humano (protocolo @mention).
   *
   * Mapa: nome_do_comando → definição da ação.
   * Comandos não reconhecidos são ignorados silenciosamente.
   *
   * Exemplo:
   *   mention_commands:
   *     ativa:
   *       action: { set_context: { "session.copilot.mode": "active" } }
   *       acknowledge: true
   *     para:
   *       action: { terminate_self: true }
   */
  mention_commands: z.record(MentionCommandSchema).optional(),

  /**
   * delegation_input — typed parameters shown in DelegarTarefaDrawer when an
   * operator delegates a task to this agent. When defined, the drawer renders
   * form fields instead of a free-text textarea. Values are serialized into a
   * readable instruction string sent as an @mention command.
   */
  delegation_input: DelegationInputSchema.optional(),
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
