/**
 * agent-registry.test.ts
 * Tests for PoolRegistration, AgentTypeRegistration and PipelineState schemas.
 * PlugHub spec v24.0 section 4.5
 */

import { describe, it, expect } from "vitest"
import {
  PoolRegistrationSchema,
  AgentTypeRegistrationSchema,
  AgentTypeSchema,
  PipelineStateSchema,
  SupervisorConfigSchema,
  RoutingDecisionSchema,
  TenantConfigSchema,
} from "./agent-registry"

// ─────────────────────────────────────────────
// PoolRegistrationSchema
// ─────────────────────────────────────────────

describe("PoolRegistrationSchema", () => {
  const basePool = {
    pool_id:       "retencao_humano",
    channel_types: ["chat", "whatsapp"],
    sla_target_ms: 480000,
  }

  it("validates minimal pool without supervisor_config", () => {
    expect(() => PoolRegistrationSchema.parse(basePool)).not.toThrow()
  })

  it("rejects pool_id with invalid characters", () => {
    expect(() =>
      PoolRegistrationSchema.parse({ ...basePool, pool_id: "retencao-humano" })
    ).toThrow()
  })

  it("rejects pool without channel_types", () => {
    expect(() =>
      PoolRegistrationSchema.parse({ ...basePool, channel_types: [] })
    ).toThrow()
  })

  it("rejects invalid channel", () => {
    expect(() =>
      PoolRegistrationSchema.parse({ ...basePool, channel_types: ["telegram"] })
    ).toThrow()
  })

  it("validates pool with complete supervisor_config", () => {
    const poolWithSupervisor = {
      ...basePool,
      evaluation_template_id: "template_retencao_v2",
      supervisor_config: {
        enabled: true,
        history_window_days: 30,
        insight_categories: [
          "insight.historico.atendimento.*",
          "insight.historico.servico.*",
        ],
        intent_capability_map: {
          portability_check: [
            {
              capability:        "mcp-server-telco:portability_check",
              interaction_model: "background" as const,
            },
          ],
          churn_signal: [
            {
              agent_type_id:     "agente_retencao_v3",
              interaction_model: "conference" as const,
              channel_identity:  { text: "Especialista", voice_profile: "specialist_pt_br" },
              auto_join:         false,
            },
          ],
        },
        sentiment_alert_threshold: -0.30,
        proactive_delegation: {
          enabled:         true,
          min_relevance:   "high" as const,
          delegation_mode: "silent" as const,
          version_policy:  "stable" as const,
        },
      },
    }
    expect(() => PoolRegistrationSchema.parse(poolWithSupervisor)).not.toThrow()
  })

  it("validates routing_expression with custom weights", () => {
    expect(() =>
      PoolRegistrationSchema.parse({
        ...basePool,
        routing_expression: {
          weight_sla:      1.5,
          weight_wait:     1.0,
          weight_tier:     0.5,
          weight_churn:    0.8,
          weight_business: 0.3,
        },
      })
    ).not.toThrow()
  })
})

// ─────────────────────────────────────────────
// SupervisorConfigSchema
// ─────────────────────────────────────────────

describe("SupervisorConfigSchema", () => {
  it("validates disabled supervisor", () => {
    expect(() =>
      SupervisorConfigSchema.parse({ enabled: false })
    ).not.toThrow()
  })

  it("rejects positive sentiment_alert_threshold", () => {
    expect(() =>
      SupervisorConfigSchema.parse({
        enabled:                   true,
        sentiment_alert_threshold: 0.5,  // must be negative
      })
    ).toThrow()
  })

  it("validates history_window_days at maximum limit", () => {
    expect(() =>
      SupervisorConfigSchema.parse({
        enabled:              true,
        history_window_days:  365,
      })
    ).not.toThrow()
  })

  it("rejects history_window_days above limit", () => {
    expect(() =>
      SupervisorConfigSchema.parse({
        enabled:             true,
        history_window_days: 366,
      })
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// AgentTypeRegistrationSchema
// ─────────────────────────────────────────────

describe("AgentTypeRegistrationSchema", () => {
  const baseAgent = {
    agent_type_id:   "agente_retencao_v1",
    framework:       "langgraph" as const,
    execution_model: "stateless" as const,
    pools:           ["retencao_humano"],
  }

  it("validates minimal executor agent", () => {
    expect(() => AgentTypeRegistrationSchema.parse(baseAgent)).not.toThrow()
  })

  it("rejects agent_type_id with invalid format", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        agent_type_id: "agente-retencao-v1",  // hyphen not allowed
      })
    ).toThrow()
  })

  it("rejects agent_type_id without version", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        agent_type_id: "agente_retencao",  // missing _v{n}
      })
    ).toThrow()
  })

  it("rejects empty pools", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({ ...baseAgent, pools: [] })
    ).toThrow()
  })

  it("validates human agent with null prompt_id", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        agent_type_id:           "agente_humano_retencao_v1",
        framework:               "human" as const,
        max_concurrent_sessions: 5,
        prompt_id:               null,
      })
    ).not.toThrow()
  })

  it("validates agent with skills and permissions", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        skills: [
          { skill_id: "skill_portabilidade_telco_v2", version_policy: "stable" as const },
          { skill_id: "skill_diagnostico_rede_v1",    version_policy: "stable" as const },
        ],
        permissions: [
          "mcp-server-crm:retention_offer",
        ],
        capabilities: {
          portabilidade:       "2.0",
          diagnostico_tecnico: "1.0",
          retencao:            "1.0",
        },
      })
    ).not.toThrow()
  })

  it("rejects permission with invalid format", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        permissions: ["mcp-server-crm/retention_offer"],  // slash instead of colon
      })
    ).toThrow()
  })

  it("validates orchestrator agent with skill", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        agent_type_id: "agente_orquestrador_onboarding_v1",
        role:          "orchestrator" as const,
        skills: [
          { skill_id: "skill_onboarding_finserv_v1", version_policy: "stable" as const },
        ],
      })
    ).not.toThrow()
  })

  it("rejects orchestrator agent without skills", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        agent_type_id: "agente_orquestrador_v1",
        role:          "orchestrator" as const,
        skills:        [],
      })
    ).toThrow()
  })

  it("validates version_policy exact with declared exact_version", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        skills: [
          {
            skill_id:       "skill_portabilidade_telco_v2",
            version_policy: "exact" as const,
            exact_version:  "2.0",
          },
        ],
      })
    ).not.toThrow()
  })

  it("rejects version_policy exact without exact_version", () => {
    expect(() =>
      AgentTypeRegistrationSchema.parse({
        ...baseAgent,
        skills: [
          {
            skill_id:       "skill_portabilidade_telco_v2",
            version_policy: "exact" as const,
            // exact_version absent — must fail
          },
        ],
      })
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// PipelineStateSchema
// ─────────────────────────────────────────────

describe("PipelineStateSchema", () => {
  const basePipeline = {
    flow_id:         "flow_onboarding_finserv_v1",
    current_step_id: "verificar_identidade",
    status:          "in_progress" as const,
    started_at:      "2026-03-16T14:00:00Z",
    updated_at:      "2026-03-16T14:02:00Z",
  }

  it("validates minimal pipeline_state", () => {
    expect(() => PipelineStateSchema.parse(basePipeline)).not.toThrow()
  })

  it("validates pipeline_state with results and retry_counters", () => {
    expect(() =>
      PipelineStateSchema.parse({
        ...basePipeline,
        results: {
          cliente:       { customer_id: "uuid", tier: "gold" },
          classificacao: { intencao: "portabilidade", confianca: 0.92 },
        },
        retry_counters: {
          tratar_falha_credito: 1,
        },
        transitions: [
          {
            from_step:  "verificar_identidade",
            to_step:    "analisar_credito",
            reason:     "on_success" as const,
            timestamp:  "2026-03-16T14:01:00Z",
          },
          {
            from_step:  "analisar_credito",
            to_step:    "tratar_falha_credito",
            reason:     "on_failure" as const,
            timestamp:  "2026-03-16T14:01:30Z",
          },
        ],
      })
    ).not.toThrow()
  })

  it("rejects invalid status", () => {
    expect(() =>
      PipelineStateSchema.parse({ ...basePipeline, status: "running" })
    ).toThrow()
  })

  it("rejects transition with invalid reason", () => {
    expect(() =>
      PipelineStateSchema.parse({
        ...basePipeline,
        transitions: [
          {
            from_step: "step_a",
            to_step:   "step_b",
            reason:    "manual_override",  // not a valid value
            timestamp: "2026-03-16T14:01:00Z",
          },
        ],
      })
    ).toThrow()
  })

  it("validates pipeline_state with error_context", () => {
    expect(() =>
      PipelineStateSchema.parse({
        ...basePipeline,
        status: "failed" as const,
        error_context: {
          step_id:   "analisar_credito",
          error:     "MCP timeout após 5000ms",
          timestamp: "2026-03-16T14:01:30Z",
        },
      })
    ).not.toThrow()
  })

  it("rejects error_context without step_id", () => {
    expect(() =>
      PipelineStateSchema.parse({
        ...basePipeline,
        error_context: { error: "timeout" },
      })
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// AgentTypeSchema (alias for AgentTypeRegistrationSchema)
// ─────────────────────────────────────────────

describe("AgentTypeSchema (alias)", () => {
  it("behaves identically to AgentTypeRegistrationSchema", () => {
    expect(() =>
      AgentTypeSchema.parse({
        agent_type_id:   "agente_suporte_v1",
        framework:       "anthropic_sdk" as const,
        execution_model: "stateless" as const,
        pools:           ["suporte_telco"],
      })
    ).not.toThrow()
  })
})

// ─────────────────────────────────────────────
// RoutingDecisionSchema
// ─────────────────────────────────────────────

describe("RoutingDecisionSchema", () => {
  it("validates autonomous decision without fallback", () => {
    expect(() =>
      RoutingDecisionSchema.parse({
        agent_type_id: "agente_atendimento_telco_v3",
        mode:          "autonomous" as const,
      })
    ).not.toThrow()
  })

  it("validates hybrid decision with fallback and reevaluation", () => {
    expect(() =>
      RoutingDecisionSchema.parse({
        agent_type_id:     "agente_atendimento_telco_v3",
        fallback:          "agente_humano_retencao_v2",
        mode:              "hybrid" as const,
        reevaluation_turn: 3,
      })
    ).not.toThrow()
  })

  it("rejects agent_type_id with invalid format", () => {
    expect(() =>
      RoutingDecisionSchema.parse({
        agent_type_id: "agente-telco-v3",  // hyphen not allowed
        mode:          "autonomous" as const,
      })
    ).toThrow()
  })

  it("rejects invalid mode", () => {
    expect(() =>
      RoutingDecisionSchema.parse({
        agent_type_id: "agente_atendimento_v1",
        mode:          "manual" as const,  // does not exist
      })
    ).toThrow()
  })

  it("rejects non-positive reevaluation_turn", () => {
    expect(() =>
      RoutingDecisionSchema.parse({
        agent_type_id:     "agente_atendimento_v1",
        mode:              "supervised" as const,
        reevaluation_turn: 0,  // must be positive
      })
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// TenantConfigSchema
// ─────────────────────────────────────────────

describe("TenantConfigSchema", () => {
  const baseTenant = {
    tenant_id:    "tenant_acme",
    tier:         "standard" as const,
    workspace_id: "ws_acme_prod",
  }

  it("validates standard tenant with defaults", () => {
    expect(() => TenantConfigSchema.parse(baseTenant)).not.toThrow()
  })

  it("validates enterprise tenant with custom rate_limits", () => {
    expect(() =>
      TenantConfigSchema.parse({
        ...baseTenant,
        tier: "enterprise" as const,
        rate_limits: {
          requests_per_minute:     5000,
          max_concurrent_sessions: 2000,
          mcp_calls_per_second:    500,
        },
      })
    ).not.toThrow()
  })

  it("rejects invalid tier", () => {
    expect(() =>
      TenantConfigSchema.parse({ ...baseTenant, tier: "premium" })
    ).toThrow()
  })

  it("rejects empty tenant_id", () => {
    expect(() =>
      TenantConfigSchema.parse({ ...baseTenant, tenant_id: "" })
    ).toThrow()
  })

  it("rejects rate_limits with zero requests_per_minute", () => {
    expect(() =>
      TenantConfigSchema.parse({
        ...baseTenant,
        rate_limits: { requests_per_minute: 0 },
      })
    ).toThrow()
  })
})
