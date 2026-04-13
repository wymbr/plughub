/**
 * index.ts
 * API pública do @plughub/skill-flow-engine.
 */

export { SkillFlowEngine }             from "./engine"
export type { SkillFlowEngineConfig }  from "./engine"

export { PipelineStateManager }        from "./state"

export { executeStep }                 from "./executor"
export type { StepContext, StepResult } from "./executor"
