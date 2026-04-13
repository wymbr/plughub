/**
 * index.ts
 * API pública do pacote @plughub/sdk.
 * Exports nomeados explícitos.
 */

// ── Core ─────────────────────────────────────
export { definePlugHubAgent }         from "./agent"
export type {
  PlugHubAgentConfig,
  PlugHubAgentInstance,
  AgentHandler,
  AgentHandlerContext,
  AgentHandlerResult,
}                                      from "./agent"

// ── Adapter ──────────────────────────────────
export { PlugHubAdapter }              from "./adapter"
export type {
  PlugHubAdapterConfig,
  ContextMap,
  ResultMap,
  OutcomeMap,
  PlugHubDriver,
}                                      from "./adapter"

// ── Drivers ──────────────────────────────────
export { GenericMCPDriver }            from "./drivers/generic-mcp"
export { BedrockDriver }               from "./drivers/bedrock"
export type { BedrockDriverConfig }    from "./drivers/bedrock"
export { AgentBuilderDriver }          from "./drivers/agent-builder"
export type { AgentBuilderDriverConfig } from "./drivers/agent-builder"
export { CopilotDriver }               from "./drivers/copilot"
export type { CopilotDriverConfig }    from "./drivers/copilot"

// ── Lifecycle ────────────────────────────────
export { LifecycleManager }            from "./lifecycle"
export type { LifecycleManagerConfig } from "./lifecycle"

// ── Observabilidade ──────────────────────────
export { observability, ObservabilityManager } from "./observability"
export type {
  PlugHubTraceAttributes,
  TraceSpan,
  TracerBackend,
}                                      from "./observability"

// ── Certificação ─────────────────────────────
export { certifyAgent }                from "./certify"
export type {
  CertifyConfig,
  CertifyReport,
  CertifyCheck,
  CertifyStatus,
}                                      from "./certify"

// ── Portabilidade ────────────────────────────
export { verifyPortability }           from "./portability"
export type {
  PortabilityVerifyConfig,
  PortabilityReport,
  PortabilityCheck,
}                                      from "./portability"
