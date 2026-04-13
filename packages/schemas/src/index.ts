/**
 * index.ts
 * API pública do pacote @plughub/schemas.
 * Exports nomeados explícitos — sem re-export de internos.
 */

// ── Context Package ──────────────────────────
export {
  ChannelSchema,
  OutcomeSchema,
  ExecutionModelSchema,
  CustomerTierSchema,
  CustomerProfileSchema,
  InsightStatusSchema,
  InsightConfidenceSchema,
  SessionItemSchema,
  // Aliases do modelo unificado (spec 3.4a)
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
  Channel,
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
