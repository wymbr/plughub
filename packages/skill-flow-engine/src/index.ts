/**
 * index.ts
 * API pública do @plughub/skill-flow-engine.
 */

export { SkillFlowEngine }                        from "./engine"
export type { SkillFlowEngineConfig, RunResult, ResumeContext } from "./engine"

export { PipelineStateManager }        from "./state"

export { executeStep }                 from "./executor"
export type { StepContext, StepResult } from "./executor"

export { handleMentionCommand, parseCommandName } from "./mention-commands"
export type { MentionCommandContext, MentionCommandResult } from "./mention-commands"

export { isFieldMasked, computeMaskedFieldIds } from "./masking-policy"
export type { MaskedFieldDef } from "./masking-policy"

export { redisKeys } from "./redis-keys"
