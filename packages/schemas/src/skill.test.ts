/**
 * skill.test.ts
 * Testes dos schemas de SkillRegistration, SkillFlow e FlowStep.
 * PlugHub spec v24.0 seção 4.7
 */

import { describe, it, expect } from "vitest"
import {
  SkillSchema,
  SkillRegistrationSchema,
  SkillRefSchema,
  SkillFlowSchema,
  TaskStepSchema,
  ChoiceStepSchema,
  CatchStepSchema,
  EscalateStepSchema,
  CompleteStepSchema,
  InvokeStepSchema,
  ReasonStepSchema,
  NotifyStepSchema,
} from "./skill"

// ─────────────────────────────────────────────
// SkillRegistrationSchema (alias de SkillSchema)
// ─────────────────────────────────────────────

const baseSkill = {
  skill_id:    "skill_portabilidade_telco_v2",
  name:        "Portabilidade Telco",
  version:     "2.0",
  description: "Conduz portabilidade de número — elegibilidade, coleta, confirmação.",
  classification: {
    type:     "vertical" as const,
    vertical: "telco",
    domain:   "portabilidade",
  },
  instruction: {
    prompt_id: "prompt_portabilidade_telco_v2",
    language:  "pt-BR",
  },
}

describe("SkillRegistrationSchema", () => {
  it("valida skill mínima (apenas campos obrigatórios)", () => {
    expect(() => SkillRegistrationSchema.parse(baseSkill)).not.toThrow()
  })

  it("é o mesmo schema que SkillSchema", () => {
    const r1 = SkillRegistrationSchema.safeParse(baseSkill)
    const r2 = SkillSchema.safeParse(baseSkill)
    expect(r1.success).toBe(r2.success)
  })

  it("skill_id deve seguir a convenção skill_{nome}_v{n}", () => {
    expect(() =>
      SkillRegistrationSchema.parse({ ...baseSkill, skill_id: "portabilidade_telco_v2" })
    ).toThrow()
    expect(() =>
      SkillRegistrationSchema.parse({ ...baseSkill, skill_id: "skill_portabilidade_telco" })
    ).toThrow()
    expect(() =>
      SkillRegistrationSchema.parse({ ...baseSkill, skill_id: "Skill_portabilidade_v1" })
    ).toThrow()
  })

  it("valida skill com tools e interface", () => {
    expect(() =>
      SkillRegistrationSchema.parse({
        ...baseSkill,
        tools: [
          { mcp_server: "mcp-server-telco", tool: "portability_check",   required: true },
          { mcp_server: "mcp-server-telco", tool: "portability_request", required: true },
          { mcp_server: "mcp-server-crm",   tool: "interaction_log",     required: false },
        ],
        interface: {
          input_schema:  { customer_id: "string", phone_number: "string" },
          output_schema: { portability_status: "eligible | ineligible | pending", protocol_number: "string | null" },
        },
        evaluation: {
          template_id:            "eval_portabilidade_v1",
          criteria: [
            { name: "verificacao_elegibilidade", weight: 0.30 },
            { name: "coleta_dados_completa",     weight: 0.25 },
            { name: "confirmacao_protocolo",     weight: 0.25 },
            { name: "instrucao_proximos_passos", weight: 0.20 },
          ],
          evaluate_independently: true,
        },
        knowledge_domains: ["kb_telco_portabilidade", "kb_telco_regulatorio"],
      })
    ).not.toThrow()
  })

  it("valida skill horizontal reutilizável entre verticais", () => {
    expect(() =>
      SkillRegistrationSchema.parse({
        ...baseSkill,
        skill_id:    "skill_extracao_documento_v3",
        name:        "Extração de Documento",
        description: "Extrai dados estruturados de documentos",
        classification: { type: "horizontal" as const },
      })
    ).not.toThrow()
  })

  it("rejeita skill orchestrator sem campo flow", () => {
    expect(() =>
      SkillRegistrationSchema.parse({
        ...baseSkill,
        classification: { type: "orchestrator" as const },
        // flow ausente — deve falhar o .refine()
      })
    ).toThrow(/flow/)
  })

  it("valida skill orchestrator com flow válido", () => {
    expect(() =>
      SkillRegistrationSchema.parse({
        ...baseSkill,
        skill_id:       "skill_onboarding_finserv_v1",
        classification: { type: "orchestrator" as const },
        flow: {
          entry: "verificar_identidade",
          steps: [
            {
              id:         "verificar_identidade",
              type:       "task" as const,
              target:     { skill_id: "skill_verificacao_identidade_v2" },
              on_success: "concluir",
              on_failure: "escalar_humano",
            },
            {
              id:      "concluir",
              type:    "complete" as const,
              outcome: "resolved" as const,
            },
            {
              id:      "escalar_humano",
              type:    "escalate" as const,
              target:  { pool: "especialista_onboarding" },
              context: "pipeline_state" as const,
            },
          ],
        },
      })
    ).not.toThrow()
  })
})

// ─────────────────────────────────────────────
// SkillRefSchema
// ─────────────────────────────────────────────

describe("SkillRefSchema", () => {
  it("valida ref com version_policy stable (default)", () => {
    expect(() =>
      SkillRefSchema.parse({ skill_id: "skill_portabilidade_telco_v2" })
    ).not.toThrow()
  })

  it("valida ref com version_policy latest", () => {
    expect(() =>
      SkillRefSchema.parse({
        skill_id:       "skill_diagnostico_rede_v1",
        version_policy: "latest" as const,
      })
    ).not.toThrow()
  })

  it("valida ref com version_policy exact e exact_version", () => {
    expect(() =>
      SkillRefSchema.parse({
        skill_id:       "skill_portabilidade_telco_v2",
        version_policy: "exact" as const,
        exact_version:  "2.1",
      })
    ).not.toThrow()
  })

  it(".refine(): rejeita version_policy exact sem exact_version", () => {
    expect(() =>
      SkillRefSchema.parse({
        skill_id:       "skill_portabilidade_telco_v2",
        version_policy: "exact" as const,
        // exact_version ausente
      })
    ).toThrow(/exact_version/)
  })
})

// ─────────────────────────────────────────────
// SkillFlowSchema
// ─────────────────────────────────────────────

describe("SkillFlowSchema", () => {
  it(".refine(): rejeita flow quando entry não existe nos steps", () => {
    expect(() =>
      SkillFlowSchema.parse({
        entry: "step_inexistente",
        steps: [
          {
            id:      "concluir",
            type:    "complete" as const,
            outcome: "resolved" as const,
          },
        ],
      })
    ).toThrow(/entry/)
  })

  it(".refine(): rejeita flow sem step complete ou escalate", () => {
    expect(() =>
      SkillFlowSchema.parse({
        entry: "tarefa_a",
        steps: [
          {
            id:         "tarefa_a",
            type:       "task" as const,
            target:     { skill_id: "skill_qualquer_v1" },
            on_success: "tarefa_a",
            on_failure: "tarefa_a",
          },
        ],
      })
    ).toThrow(/complete|escalate/)
  })

  it("valida flow com todos os 8 tipos de step", () => {
    expect(() =>
      SkillFlowSchema.parse({
        entry: "invocar_cliente",
        steps: [
          {
            id: "invocar_cliente",
            type: "invoke" as const,
            target: { mcp_server: "mcp-server-crm", tool: "customer_get" },
            input: { customer_id: "$.session.customer_id" },
            output_as: "cliente",
            on_success: "razao_intent",
            on_failure: "escalar",
          },
          {
            id: "razao_intent",
            type: "reason" as const,
            prompt_id: "prompt_classificacao_v1",
            output_schema: {
              intencao: { type: "string" as const, enum: ["cancelamento", "suporte"] },
            },
            output_as: "classificacao",
            on_success: "decidir",
            on_failure: "escalar",
          },
          {
            id: "decidir",
            type: "choice" as const,
            conditions: [
              {
                field:    "$.pipeline_state.classificacao.intencao",
                operator: "eq" as const,
                value:    "cancelamento",
                next:     "notificar",
              },
            ],
            default: "tarefa_suporte",
          },
          {
            id:         "tarefa_suporte",
            type:       "task" as const,
            target:     { skill_id: "skill_suporte_tecnico_v1" },
            on_success: "concluir",
            on_failure: "tratar_falha",
          },
          {
            id:            "tratar_falha",
            type:          "catch" as const,
            error_context: "tarefa_suporte",
            strategies: [
              {
                type:         "retry" as const,
                max_attempts: 2,
                delay_ms:     2000,
                on_exhausted: "escalar",
              },
            ],
            on_failure: "escalar",
          },
          {
            id:      "notificar",
            type:    "notify" as const,
            message: "Sua solicitação foi registrada com protocolo {{$.pipeline_state.protocolo}}.",
            channel: "session" as const,
            on_success: "concluir",
            on_failure: "concluir",
          },
          {
            id:      "concluir",
            type:    "complete" as const,
            outcome: "resolved" as const,
          },
          {
            id:      "escalar",
            type:    "escalate" as const,
            target:  { pool: "suporte_humano" },
            context: "pipeline_state" as const,
          },
        ],
      })
    ).not.toThrow()
  })
})

// ─────────────────────────────────────────────
// Tipos de step individuais
// ─────────────────────────────────────────────

describe("TaskStepSchema", () => {
  it("valida step task mínimo", () => {
    expect(() =>
      TaskStepSchema.parse({
        id:         "verificar",
        type:       "task" as const,
        target:     { skill_id: "skill_verificacao_v1" },
        on_success: "proximo",
        on_failure: "escalar",
      })
    ).not.toThrow()
  })

  it("rejeita type errado", () => {
    expect(() =>
      TaskStepSchema.parse({
        id: "verificar", type: "invoke",
        target: { skill_id: "skill_v1" }, on_success: "a", on_failure: "b",
      })
    ).toThrow()
  })
})

describe("ChoiceStepSchema", () => {
  it("rejeita conditions vazio", () => {
    expect(() =>
      ChoiceStepSchema.parse({
        id:         "decidir",
        type:       "choice" as const,
        conditions: [],
        default:    "fallback",
      })
    ).toThrow()
  })
})

describe("CatchStepSchema", () => {
  it("valida catch com retry e fallback em sequência", () => {
    expect(() =>
      CatchStepSchema.parse({
        id:            "tratar",
        type:          "catch" as const,
        error_context: "step_falhou",
        strategies: [
          {
            type: "retry" as const, max_attempts: 2, delay_ms: 1000, on_exhausted: "fallback_step",
          },
          {
            type: "fallback" as const, id: "fallback_step",
            target: { skill_id: "skill_backup_v1" },
            on_success: "concluir", on_failure: "escalar",
          },
        ],
        on_failure: "escalar",
      })
    ).not.toThrow()
  })

  it("rejeita strategies vazio", () => {
    expect(() =>
      CatchStepSchema.parse({
        id: "t", type: "catch" as const,
        error_context: "s", strategies: [], on_failure: "e",
      })
    ).toThrow()
  })
})

describe("ReasonStepSchema", () => {
  it("valida step reason com output_schema enum", () => {
    expect(() =>
      ReasonStepSchema.parse({
        id:        "classificar",
        type:      "reason" as const,
        prompt_id: "prompt_v1",
        output_schema: {
          intencao:  { type: "string" as const, enum: ["cancelamento", "suporte"] },
          confianca: { type: "number" as const, minimum: 0, maximum: 1 },
        },
        output_as:   "classificacao",
        on_success:  "proximo",
        on_failure:  "escalar",
      })
    ).not.toThrow()
  })
})

describe("NotifyStepSchema", () => {
  it("rejeita message vazia", () => {
    expect(() =>
      NotifyStepSchema.parse({
        id: "notif", type: "notify" as const,
        message: "", channel: "session" as const,
        on_success: "a", on_failure: "b",
      })
    ).toThrow()
  })
})

describe("CompleteStepSchema", () => {
  it("rejeita outcome inválido", () => {
    expect(() =>
      CompleteStepSchema.parse({
        id: "fin", type: "complete" as const, outcome: "abandoned",
      })
    ).toThrow()
  })
})

describe("EscalateStepSchema", () => {
  it("rejeita context diferente de pipeline_state", () => {
    expect(() =>
      EscalateStepSchema.parse({
        id: "esc", type: "escalate" as const,
        target: { pool: "humano" },
        context: "session_state",  // inválido
      })
    ).toThrow()
  })
})
