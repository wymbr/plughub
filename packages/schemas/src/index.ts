/**
 * index.ts
 * API pública do pacote @plughub/schemas.
 * Exports nomeados explícitos — sem re-export de internos.
 *
 * Organização:
 *   Nova arquitetura (v2): common, audit, message, session, stream,
 *                          channel-events, routing, ai-gateway
 *   Legado (v1, mantido para compatibilidade): context-package
 *   Compartilhados: skill, agent-registry
 */

// ── v2: Primitivos base ──────────────────────
export {
  ChannelSchema,
  MediumTypeSchema,
  SessionStatusSchema,
  CloseReasonSchema,
  SessionOutcomeSchema,
  ParticipantRoleSchema,
  SessionIdSchema,
  ParticipantIdSchema,
} from "./common"

export type {
  Channel,
  MediumType,
  SessionStatus,
  CloseReason,
  SessionOutcome,
  ParticipantRole,
} from "./common"

// ── v2: Auditoria e LGPD ─────────────────────
export {
  DataCategorySchema,
  AuditPolicySchema,
  AuditContextSchema,
  MaskingRuleSchema,
  MaskingConfigSchema,
  MaskedResultSchema,
  MaskingAccessPolicySchema,
  AuditRecordSchema,
  DEFAULT_MASKING_RULES,
} from "./audit"

export type {
  DataCategory,
  AuditPolicy,
  AuditContext,
  MaskingRule,
  MaskingConfig,
  MaskedResult,
  MaskingAccessPolicy,
  AuditRecord,
} from "./audit"

// ── v2: Mensagem ─────────────────────────────
export {
  MessageContentTypeSchema,
  MessageContentSchema,
  MessageVisibilitySchema,
  AuthorSchema,
  MessageSchema,
} from "./message"

export type {
  MessageContentType,
  MessageContent,
  MessageVisibility,
  Author,
  Message,
} from "./message"

// ── v2: Sessão ───────────────────────────────
export {
  CustomerIdentitySchema,
  SentimentEntrySchema,
  SentimentRangeSchema,
  SentimentConfigSchema,
  ParticipantSchema,
  SessionSchema,
  SessionContextSchema,
  AgentDoneV2Schema,
} from "./session"

export type {
  CustomerIdentity,
  SentimentEntry,
  SentimentRange,
  SentimentConfig,
  Participant,
  Session,
  SessionContext,
  AgentDoneV2,
} from "./session"

// ── v2: Stream canônico ──────────────────────
export {
  StreamEventTypeSchema,
  StreamAuthorSchema,
  StreamEventSchema,
  StreamPayloads,
} from "./stream"

export type {
  StreamEventType,
  StreamAuthor,
  StreamEvent,
} from "./stream"

// ── v2: Channel Gateway ──────────────────────
export {
  InboundEventSchema,
  OutboundEventSchema,
  ChannelCapabilitiesSchema,
  GatewayConfigSchema,
  GatewayHeartbeatSchema,
} from "./channel-events"

export type {
  InboundEvent,
  OutboundEvent,
  ChannelCapabilities,
  GatewayConfig,
  GatewayHeartbeat,
} from "./channel-events"

// ── v2: Routing Engine ───────────────────────
export {
  AgentStatusSchema,
  AgentStateSchema,
  RoutingScoreSchema,
  QueueEntrySchema,
  AssignmentStatusSchema,
  AssignmentTicketSchema,
} from "./routing"

export type {
  AgentStatus,
  AgentState,
  RoutingScore,
  QueueEntry,
  AssignmentStatus,
  AssignmentTicket,
} from "./routing"

// ── v2: AI Gateway ───────────────────────────
export {
  FallbackConditionSchema,
  ModelEntrySchema,
  ModelConfigSchema,
  InferMessageRoleSchema,
  InferMessageSchema,
  ToolSpecSchema,
  OutputFieldSchema,
  AIInferInputSchema,
  SentimentScoreSchema,
  AIInferOutputSchema,
} from "./ai-gateway"

export type {
  FallbackCondition,
  ModelEntry,
  ModelConfig,
  InferMessageRole,
  InferMessage,
  ToolSpec,
  OutputField,
  AIInferInput,
  SentimentScore,
  AIInferOutput,
} from "./ai-gateway"

// ── v2: Session Replayer / Evaluator ────────
export {
  EvaluationDimensionSchema,
  EvaluationResultSchema,
  ReplayEventSchema,
  ReplayContextSchema,
  EvaluationRequestSchema,
  ComparisonReportSchema,
} from "./evaluation"

export type {
  EvaluationDimension,
  EvaluationResult,
  ReplayEvent,
  ReplayContext,
  EvaluationRequest,
  ComparisonReport,
} from "./evaluation"

// ── Legado v1 (mantido para compatibilidade) ─
// Context Package — schemas da spec anterior; mantidos para consumidores existentes.
// ChannelSchema e Channel são exportados com prefixo "Legacy" para evitar conflito
// com os equivalentes v2 (que têm canais adicionais: instagram, telegram, webchat).
export {
  ChannelSchema         as LegacyChannelSchema,
  OutcomeSchema,
  ExecutionModelSchema,
  CustomerTierSchema,
  CustomerProfileSchema,
  InsightStatusSchema,
  InsightConfidenceSchema,
  SessionItemSchema,
  ConversationInsightSchema,
  PendingDeliverySchema,
  ProcessContextSchema,
  IssueStatusValueSchema,
  IssueSchema,
  ContextPackageSchema,
  AgentDoneSchema,
  AgentDonePayloadSchema,
} from "./context-package"

export type {
  Channel               as LegacyChannel,
  Outcome,
  ExecutionModel,
  CustomerTier,
  CustomerProfile,
  InsightConfidence,
  SessionItem,
  ConversationInsight,
  PendingDelivery,
  ProcessContext,
  Issue,
  ContextPackage,
  AgentDone,
  AgentDonePayload,
} from "./context-package"

// ── Skill Registry ───────────────────────────
export {
  SkillTypeSchema,
  SkillClassificationSchema,
  SkillToolSchema,
  SkillInterfaceSchema,
  SkillEvaluationSchema,
  TaskStepSchema,
  ChoiceStepSchema,
  CatchStepSchema,
  EscalateStepSchema,
  CompleteStepSchema,
  InvokeStepSchema,
  ReasonStepSchema,
  NotifyStepSchema,
  MenuStepSchema,
  FlowStepSchema,
  SkillFlowSchema,
  SkillSchema,
  SkillRegistrationSchema,
  VersionPolicySchema,
  SkillRefSchema,
} from "./skill"

export type {
  SkillType,
  SkillTool,
  FlowStep,
  SkillFlow,
  Skill,
  SkillRegistration,
  SkillRef,
  TaskStep,
  ChoiceStep,
  CatchStrategy,
  CatchStep,
  EscalateStep,
  CompleteStep,
  InvokeStep,
  ReasonStep,
  NotifyStep,
  MenuStep,
} from "./skill"

// ── Usage Metering ───────────────────────────
export {
  UsageDimensionSchema,
  SessionUsageMetaSchema,
  MessageUsageMetaSchema,
  LlmTokenUsageMetaSchema,
  WhatsappConversationMetaSchema,
  VoiceMinutesMetaSchema,
  SmsSegmentsMetaSchema,
  EmailMessageMetaSchema,
  UsageSourceComponentSchema,
  UsageEventSchema,
  UsageCounterSchema,
  UsageHourlySchema,
  QuotaLimitSchema,
  UsageCycleResetSchema,
} from "./usage"

export type {
  UsageDimension,
  SessionUsageMeta,
  MessageUsageMeta,
  LlmTokenUsageMeta,
  WhatsappConversationMeta,
  VoiceMinutesMeta,
  SmsSegmentsMeta,
  EmailMessageMeta,
  UsageSourceComponent,
  UsageEvent,
  UsageCounter,
  UsageHourly,
  QuotaLimit,
  UsageCycleReset,
} from "./usage"

// ── Agent Registry ───────────────────────────
export {
  RoutingExpressionSchema,
  InteractionModelSchema,
  RelevanceModelSchema,
  SupervisorConfigSchema,
  PoolEvaluationConfigSchema,
  PoolRegistrationSchema,
  AgentFrameworkSchema,
  AgentRoleSchema,
  AgentClassificationSchema,
  AgentTypeRegistrationSchema,
  AgentTypeSchema,
  PipelineStateSchema,
  RoutingModeSchema,
  RoutingDecisionSchema,
  TenantTierSchema,
  TenantConfigSchema,
} from "./agent-registry"

export type {
  RelevanceModel,
  SupervisorConfig,
  PoolEvaluationConfig,
  PoolRegistration,
  AgentFramework,
  AgentRole,
  AgentTypeRegistration,
  AgentType,
  PipelineState,
  RoutingMode,
  RoutingDecision,
  TenantTier,
  TenantConfig,
} from "./agent-registry"
