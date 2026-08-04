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
  PrincipalTypeSchema,
  VerificationClassSchema,
  MaskingRuleSchema,
  MaskingConfigSchema,
  MaskedResultSchema,
  MaskingAccessPolicySchema,
  AuditRecordSchema,
  DEFAULT_MASKING_RULES,
  // ContextStore field-level masking (dynamic rules — Arc 11 Fase D)
  ContextMaskingTypeSchema,
  ContextMaskingRuleSchema,
  ContextMaskingConfigSchema,
  DEFAULT_CONTEXT_MASKING_CONFIG,
} from "./audit"

export type {
  DataCategory,
  AuditPolicy,
  AuditContext,
  PrincipalType,
  VerificationClass,
  MaskingRule,
  MaskingConfig,
  MaskedResult,
  MaskingAccessPolicy,
  AuditRecord,
  // ContextStore field-level masking
  ContextMaskingType,
  ContextMaskingRule,
  ContextMaskingConfig,
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
  // A5 — approval decision audit block on the `message` payload
  ApprovalFieldEditSchema,
  ApprovalDecisionMetaSchema,
} from "./stream"

export type {
  StreamEventType,
  StreamAuthor,
  StreamEvent,
  ApprovalFieldEdit,
  ApprovalDecisionMeta,
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
  // Arc 6 — EvaluationForm + Criterion
  EvaluationCriterionTypeSchema,
  EvaluationDimensionDefSchema,
  EvaluationCriterionSchema,
  EvaluationFormSchema,
  EvidenceRefSchema,
  EvaluationCriterionResponseSchema,
  // T6 — criterion field derivation helpers
  deriveContestable,
  deriveEvidenceRequired,
  // Arc 6 — Campaign + Instance
  SamplingRulesSchema,
  ReviewerRulesSchema,
  CampaignScheduleSchema,
  EvaluationCampaignStatusSchema,
  EvaluationCampaignSchema,
  EvaluationInstanceStatusSchema,
  ReviewResultSchema,
  EvaluationInstanceSchema,
  // Arc 6 — Kafka events
  KnowledgeSnippetSchema,
  EvalInstanceCreatedSchema,
  EvalSubmittedSchema,
  EvalReviewRequestedSchema,
  EvalReviewCompletedSchema,
  EvalContestedSchema,
  EvalLockedSchema,
  EvalCampaignStatusChangedSchema,
  EvaluationLifecycleEventSchema,
} from "./evaluation"

export type {
  EvaluationDimension,
  EvaluationResult,
  ReplayEvent,
  ReplayContext,
  EvaluationRequest,
  ComparisonReport,
  // Arc 6 — EvaluationForm + Criterion
  EvaluationCriterionType,
  EvaluationDimensionDef,
  EvaluationCriterion,
  EvaluationForm,
  EvidenceRef,
  EvaluationCriterionResponse,
  // Arc 6 — Campaign + Instance
  SamplingRules,
  ReviewerRules,
  CampaignSchedule,
  EvaluationCampaignStatus,
  EvaluationCampaign,
  EvaluationInstanceStatus,
  ReviewResult,
  EvaluationInstance,
  // Arc 6 — Kafka events
  KnowledgeSnippet,
  EvalInstanceCreated,
  EvalSubmitted,
  EvalReviewRequested,
  EvalReviewCompleted,
  EvalContested,
  EvalLocked,
  EvalCampaignStatusChanged,
  EvaluationLifecycleEvent,
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
  SkillConfigParamSchema,
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
  ChannelCapabilitySchema,
  CollectChannelPolicySchema,
  CollectTargetSchema,
  CollectStepSchema,
  BeginTransactionStepSchema,
  EndTransactionStepSchema,
  ResolveRequiredFieldSchema,
  ResolveCrmLookupSchema,
  ResolveStepSchema,
  MentionCommandSchema,
  ReceiveFilterSchema,
  ReceiveStepSchema,
  FlowStepSchema,
  SkillFlowSchema,
  SkillSchema,
  SkillRegistrationSchema,
  VersionPolicySchema,
  SkillRefSchema,
  DelegationFieldSchema,
  DelegationInputSchema,
} from "./skill"

export type {
  SkillType,
  SkillTool,
  SkillConfigParam,
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
  CollectChannelPolicy,
  CollectTarget,
  CollectStep,
  BeginTransactionStep,
  EndTransactionStep,
  ResolveRequiredField,
  ResolveCrmLookup,
  ResolveStep,
  MentionCommand,
  ReceiveFilter,
  ReceiveStep,
  DelegationField,
  DelegationInput,
  SuspendStep,
  DelegateStep,
  LoopStep,
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

// ── Platform / Installation context ─────────
export {
  InstallationContextSchema,
  ResourceScopeSchema,
  PlatformConfigSchema,
} from "./platform"

export type {
  InstallationContext,
  ResourceScope,
  PlatformConfig,
} from "./platform"

// ── Journey J3 — merge edge (topic journey.merges) ─────────
export {
  JourneyMergedEventSchema,
} from "./journey-merges"

export type {
  JourneyMergedEvent,
} from "./journey-merges"

// ── Calendar ─────────────────────────────────
export {
  DayOfWeekSchema,
  TimeSlotSchema,
  DayScheduleSchema,
  HolidaySchema,
  HolidaySetSchema,
  CalendarExceptionSchema,
  CalendarSchema,
  CalendarOperatorSchema,
  CalendarEntityTypeSchema,
  CalendarAssociationSchema,
  CalendarQuerySchema,
  IsOpenStatusSchema,
  IsOpenResultSchema,
  BusinessDurationResultSchema,
  CalendarWindowOpenedSchema,
  CalendarWindowClosedSchema,
  CalendarEventSchema,
} from "./calendar"

export type {
  DayOfWeek,
  TimeSlot,
  DaySchedule,
  Holiday,
  HolidaySet,
  CalendarException,
  Calendar,
  CalendarOperator,
  CalendarEntityType,
  CalendarAssociation,
  CalendarQuery,
  IsOpenStatus,
  IsOpenResult,
  BusinessDurationResult,
  CalendarWindowOpened,
  CalendarWindowClosed,
  CalendarEvent,
} from "./calendar"

// ── Workflow ──────────────────────────────────
export {
  SuspendReasonSchema,
  SuspendNotifySchema,
  SuspendStepSchema,
  WorkflowStatusSchema,
  WorkflowInstanceSchema,
  WorkflowTriggerTypeSchema,
  WorkflowTriggerSchema,
  WorkflowDecisionSchema,
  WorkflowResumeSchema,
  WorkflowStartedSchema,
  WorkflowSuspendedSchema,
  WorkflowResumedSchema,
  WorkflowCompletedSchema,
  WorkflowTimedOutSchema,
  WorkflowFailedSchema,
  WorkflowCancelledSchema,
  WorkflowEventSchema,
  CollectStatusSchema,
  CollectRequestedSchema,
  CollectSentSchema,
  CollectRespondedSchema,
  CollectTimedOutSchema,
  CollectEventSchema,
} from "./workflow"

export type {
  SuspendReason,
  SuspendNotify,
  // SuspendStep exported from ./skill — removed here to avoid duplicate identifier
  WorkflowStatus,
  WorkflowInstance,
  WorkflowTriggerType,
  WorkflowTrigger,
  WorkflowDecision,
  WorkflowResume,
  WorkflowStarted,
  WorkflowSuspended,
  WorkflowResumed,
  WorkflowCompleted,
  WorkflowTimedOut,
  WorkflowFailed,
  WorkflowCancelled,
  WorkflowEvent,
  CollectStatus,
  CollectRequested,
  CollectSent,
  CollectResponded,
  CollectTimedOut,
  CollectEvent,
} from "./workflow"

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

// ── ContactContext — context-aware progressive resolution ────────────────────
export {
  ContactContextSourceSchema,
  ContactContextFieldSchema,
  ContactContextCrmDataSchema,
  ContactContextSchema,
  ContextRequirementSchema,
  ContextResolutionRequestSchema,
} from "./contact-context"

export type {
  ContactContextSource,
  ContactContextField,
  ContactContextCrmData,
  ContactContext,
  ContextRequirement,
  ContextResolutionRequest,
} from "./contact-context"

// ── ContactSegment — Arc 5 segment-level analytics ──────────────────────────
export {
  SegmentOutcomeSchema,
  ContactSegmentSchema,
  ConversationParticipantEventSchema,
} from "./contact-segment"

export type {
  SegmentOutcome,
  ContactSegment,
  ConversationParticipantEvent,
} from "./contact-segment"

// ── Rules Engine events ───────────────────────────────────────────────────────
export {
  RulesEvaluationContextSchema,
  RulesEscalationEventSchema,
  RulesActiveEventSchema,
  RulesShadowEventSchema,
  RulesEventSchema,
} from "./rules-events"

export type {
  RulesEvaluationContext,
  RulesEscalationEvent,
  RulesActiveEvent,
  RulesShadowEvent,
  RulesEvent,
} from "./rules-events"

// ── Platform / cross-cutting Kafka events ────────────────────────────────────
export {
  RegistryChangedEventSchema,
  ConfigChangedEventSchema,
  SentimentUpdatedEventSchema,
  QueuePositionUpdatedEventSchema,
  RoutingResultEventSchema,
  ConversationRoutedEventSchema,
  AgentLoginEventSchema,
  AgentReadyEventSchema,
  AgentBusyEventSchema,
  AgentDoneEventSchema,
  AgentReleasedEventSchema,
  AgentPauseEventSchema,
  AgentLogoutEventSchema,
  AgentHeartbeatEventSchema,
  AgentLifecycleEventSchema,
  ConversationContactOpenSchema,
  ConversationContactClosedSchema,
  ConversationMessageSentSchema,
  ConversationsEventSchema,
} from "./platform-events"

export type {
  RegistryChangedEvent,
  ConfigChangedEvent,
  SentimentUpdatedEvent,
  QueuePositionUpdatedEvent,
  RoutingResultEvent,
  ConversationRoutedEvent,
  AgentLoginEvent,
  AgentReadyEvent,
  AgentBusyEvent,
  AgentDoneEvent,
  AgentReleasedEvent,
  AgentPauseEvent,
  AgentLogoutEvent,
  AgentHeartbeatEvent,
  AgentLifecycleEvent,
  ConversationContactOpen,
  ConversationContactClosed,
  ConversationMessageSent,
  ConversationsEvent,
} from "./platform-events"

// ── ChannelEndpoint — external address → pool mapping ────────────────────────
export {
  ChannelEndpointChannelSchema,
  ChannelEndpointSchema,
  CreateChannelEndpointSchema,
  UpdateChannelEndpointSchema,
  ChannelEndpointQuerySchema,
} from "./channel-endpoint"

export type {
  ChannelEndpointChannel,
  ChannelEndpoint,
  CreateChannelEndpoint,
  UpdateChannelEndpoint,
  ChannelEndpointQuery,
} from "./channel-endpoint"

// ── Agent Business Events (Arc 12) ───────────────────────────────────────────
export {
  AGENT_EVENT_CATEGORY_REGEX,
  AGENT_EVENT_PII_TAG_KEYS,
  AgentBusinessEventSchema,
  AgentEventInputSchema,
  decomposeCategoryLevels,
} from "./agent-events"

export type {
  AgentBusinessEvent,
  AgentEventInput,
} from "./agent-events"

// ── Session Signals — survey grão session/workflow/journey (F10) ──────────────
export {
  SignalGrainSchema,
  SESSION_SIGNAL_GRAINS,
  SurveySignalSchema,
  SurveyRecordInputSchema,
  SessionSignalEventSchema,
} from "./survey"

export type {
  SignalGrain,
  SurveySignal,
  SurveyRecordInput,
  SessionSignalEvent,
} from "./survey"

// ── Scoring — shared score-composition primitive (survey dimensions + Quality) ─
export {
  ScoreScaleSchema,
  ScoreAggregationSchema,
  composeScore,
} from "./scoring"

export type {
  ScoreScale,
  ScoreAggregation,
  ScoredItem,
} from "./scoring"

// ── Dialog primitive — generic scripted-dialog form (survey + OTP) ────────────
export {
  LocaleCodeSchema,
  LocalizedTextSchema,
  DialogValidationSchema,
  DialogCaptureSchema,
  DialogDimensionSchema,
  DialogOptionSchema,
  DialogFieldSchema,
  DialogVisibilitySchema,
  StatementNodeSchema,
  DialogInteractionSchema,
  AskWhenOpSchema,
  AskWhenSchema,
  DialogRetrySchema,
  QuestionNodeSchema,
  DialogNodeSchema,
  DialogFormStatusSchema,
  DialogFormSchema,
  resolveLocalizedText,
  evaluateAskWhen,
  askWhenForwardRefErrors,
} from "./dialog"

export type {
  LocaleCode,
  LocalizedText,
  DialogValidation,
  DialogCapture,
  DialogDimension,
  DialogOption,
  DialogField,
  DialogVisibility,
  StatementNode,
  DialogInteraction,
  AskWhenOp,
  AskWhen,
  DialogRetry,
  QuestionNode,
  DialogNode,
  DialogFormStatus,
  DialogForm,
} from "./dialog"

// ── ContextStore — unified contact context store ──────────────────────────────
export {
  ContextVisibilitySchema,
  ContextMergeStrategySchema,
  ContextEntrySchema,
  ContextSnapshotSchema,
  ContextTagScopeSchema,
  ContextTagEntrySchema,
  ToolContextTagsSchema,
  SkillRequiredContextSchema,
  ReasonStepContextTagsSchema,
  ContextGapsReportSchema,
} from "./context-store"

export type {
  ContextVisibility,
  ContextMergeStrategy,
  ContextEntry,
  ContextSnapshot,
  ContextTagScope,
  ContextTagEntry,
  ToolContextTags,
  SkillRequiredContext,
  ReasonStepContextTags,
  ContextGapsReport,
} from "./context-store"

// ── Quality Ingest — ingestion_event_v1 (R13a) ───────────────────────────────
export {
  INGESTION_EVENT_SCHEMA_VERSION,
  IngestionAgentKindSchema,
  IngestionAuthorRoleSchema,
  IngestionContentTypeSchema,
  IngestionVisibilitySchema,
  IngestionSegmentRoleSchema,
  IngestionContactOpenedSchema,
  IngestionParticipantJoinedSchema,
  IngestionMessageSentSchema,
  IngestionParticipantLeftSchema,
  IngestionContactClosedSchema,
  IngestionEventSchema,
  deriveIngestionEventId,
} from "./ingestion-event"

export type {
  IngestionAgentKind,
  IngestionAuthorRole,
  IngestionContentType,
  IngestionVisibility,
  IngestionSegmentRole,
  IngestionContactOpened,
  IngestionParticipantJoined,
  IngestionMessageSent,
  IngestionParticipantLeft,
  IngestionContactClosed,
  IngestionEvent,
} from "./ingestion-event"

// ── Scheduler / Agenda (scheduler-api — fire a pool via webhook at a time) ────
export {
  FireTimeSchema,
  FrequencySchema,
  BusinessDayPolicySchema,
  MonthOverflowSchema,
  MisfirePolicySchema,
  MonthDaySchema,
  MonthByDateSchema,
  MonthByPositionSchema,
  MonthBySchema,
  RecurrenceRuleSchema,
  AgendaValiditySchema,
  OnceScheduleSchema,
  RecurringScheduleSchema,
  AgendaScheduleSchema,
  AgendaStatusSchema,
  AgendaSchema,
  DispatchResultSchema,
  AgendaDispatchSchema,
  CreateAgendaSchema,
  UpdateAgendaSchema,
} from "./scheduler"

export type {
  FireTime,
  Frequency,
  BusinessDayPolicy,
  MonthOverflow,
  MisfirePolicy,
  MonthDay,
  MonthBy,
  RecurrenceRule,
  AgendaValidity,
  AgendaSchedule,
  AgendaStatus,
  Agenda,
  DispatchResult,
  AgendaDispatch,
  CreateAgenda,
  UpdateAgenda,
} from "./scheduler"

// ── Outbound — Mailing + Campaign + Delivery (mailing-api, schema `outbound`) ──
export {
  DedupPolicySchema,
  MailingSchema,
  EntryStatusSchema,
  EntryContactsSchema,
  MailingEntrySchema,
  CampaignStatusSchema,
  ChannelPolicySchema,
  OrderDirSchema,
  OrderFieldTypeSchema,
  CampaignOrderFieldSchema,
  CampaignOrderingSchema,
  CampaignRetrySchema,
  CampaignSchema,
  DeliveryResultSchema,
  CampaignDeliverySchema,
  CreateMailingSchema,
  UpdateMailingSchema,
  AddEntrySchema,
  AddEntryResultSchema,
  CreateCampaignSchema,
  UpdateCampaignSchema,
  DrainRequestSchema,
  DrainedEntrySchema,
  DrainResponseSchema,
  DeliveryResultInputSchema,
  // Fase 2 — contact governance
  ContactWindowSchema,
  FrequencyCapSchema,
  ChannelCapSchema,
  ContactPolicyScopeSchema,
  ContactPolicySchema,
  ContactLogSchema,
  CreateContactPolicySchema,
  UpdateContactPolicySchema,
  EligibilityRequestSchema,
  EligibilityResultSchema,
  UnsubscribeScopeSchema,
  UnsubscribeInputSchema,
  UnsubscribeResultSchema,
} from "./outbound"

export type {
  DedupPolicy,
  Mailing,
  EntryStatus,
  EntryContacts,
  MailingEntry,
  CampaignStatus,
  ChannelPolicy,
  OrderDir,
  OrderFieldType,
  CampaignOrderField,
  CampaignOrdering,
  CampaignRetry,
  Campaign,
  DeliveryResult,
  CampaignDelivery,
  CreateMailing,
  UpdateMailing,
  AddEntry,
  AddEntryResult,
  CreateCampaign,
  UpdateCampaign,
  DrainRequest,
  DrainedEntry,
  DrainResponse,
  DeliveryResultInput,
  // Fase 2 — contact governance
  ContactWindow,
  FrequencyCap,
  ChannelCap,
  ContactPolicyScope,
  ContactPolicy,
  ContactLog,
  CreateContactPolicy,
  UpdateContactPolicy,
  EligibilityRequest,
  EligibilityResult,
  UnsubscribeScope,
  UnsubscribeInput,
  UnsubscribeResult,
} from "./outbound"
