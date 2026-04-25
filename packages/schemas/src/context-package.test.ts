/**
 * context-package.test.ts
 * Tests for ContextPackage, AgentDonePayload,
 * ConversationInsight and PendingDelivery schemas.
 * PlugHub spec v24.0 sections 3.4, 3.4a, 4.2
 */

import { describe, it, expect } from "vitest"
import {
  ContextPackageSchema,
  AgentDonePayloadSchema,
  AgentDoneSchema,
  ConversationInsightSchema,
  PendingDeliverySchema,
  SessionItemSchema,
} from "./context-package"

// ─────────────────────────────────────────────
// ConversationInsight and PendingDelivery — unified model (spec 3.4a)
// ─────────────────────────────────────────────

const baseItem = {
  item_id:     "550e8400-e29b-41d4-a716-446655440000",
  customer_id: "660e8400-e29b-41d4-a716-446655440001",
  tenant_id:   "tenant_acme",
  category:    "insight.conversa.servico.falha",
  content:     { descricao: "Cliente relatou falha técnica" },
  source:      "agente_anterior",
  status:      "pending" as const,
  priority:    70,
}

describe("ConversationInsightSchema", () => {
  it("validates minimal conversation insight", () => {
    expect(() => ConversationInsightSchema.parse(baseItem)).not.toThrow()
  })

  it("validates historical insight with expires_at and source_session_id", () => {
    expect(() =>
      ConversationInsightSchema.parse({
        ...baseItem,
        category:          "insight.historico.atendimento.cancelamento",
        source_session_id: "770e8400-e29b-41d4-a716-446655440002",
        expires_at:        "2026-06-01T00:00:00Z",
        status:            "consumed" as const,
        confidence:        "confirmed" as const,
      })
    ).not.toThrow()
  })

  it("rejects invalid status", () => {
    expect(() =>
      ConversationInsightSchema.parse({ ...baseItem, status: "arquivado" })
    ).toThrow()
  })

  it("rejects invalid item_id (non-UUID)", () => {
    expect(() =>
      ConversationInsightSchema.parse({ ...baseItem, item_id: "nao-um-uuid" })
    ).toThrow()
  })

  it("validates all valid status enum values", () => {
    const statuses = [
      "pending", "offered", "accepted", "delivered",
      "consumed", "expired", "replaced",
    ] as const
    for (const status of statuses) {
      expect(() =>
        ConversationInsightSchema.parse({ ...baseItem, status })
      ).not.toThrow()
    }
  })

  it("rejects priority outside 0-100 range", () => {
    expect(() =>
      ConversationInsightSchema.parse({ ...baseItem, priority: 101 })
    ).toThrow()
    expect(() =>
      ConversationInsightSchema.parse({ ...baseItem, priority: -1 })
    ).toThrow()
  })
})

describe("PendingDeliverySchema", () => {
  it("is the same schema as ConversationInsightSchema (unified model)", () => {
    // Both must accept exactly the same input
    const pendingItem = {
      ...baseItem,
      category: "outbound.retencao.oferta",
      content:  { oferta: "20% desconto", validade: "2026-04-01" },
      source:   "bpm",
    }
    expect(() => PendingDeliverySchema.parse(pendingItem)).not.toThrow()
    expect(() => SessionItemSchema.parse(pendingItem)).not.toThrow()
  })
})

// ─────────────────────────────────────────────
// ContextPackageSchema
// ─────────────────────────────────────────────

const baseConversationHistory = [
  {
    role:      "customer" as const,
    content:   "Quero cancelar meu plano",
    timestamp: "2026-03-16T14:00:00Z",
  },
]

const baseContextPackage = {
  session_id:   "880e8400-e29b-41d4-a716-446655440003",
  tenant_id:    "tenant_acme",
  channel:      "chat" as const,
  customer_data: {
    customer_id: "990e8400-e29b-41d4-a716-446655440004",
    tenant_id:   "tenant_acme",
    tier:        "gold" as const,
  },
  channel_context: {
    turn_count: 1,
    started_at: "2026-03-16T14:00:00Z",
  },
  conversation_history: baseConversationHistory,
}

describe("ContextPackageSchema", () => {
  it("validates minimal context_package with default schema_version", () => {
    const result = ContextPackageSchema.parse(baseContextPackage)
    expect(result.schema_version).toBe(1)
  })

  it("validates context_package with explicit schema_version", () => {
    const result = ContextPackageSchema.parse({
      ...baseContextPackage,
      schema_version: 3,
    })
    expect(result.schema_version).toBe(3)
  })

  it("rejects negative schema_version", () => {
    expect(() =>
      ContextPackageSchema.parse({ ...baseContextPackage, schema_version: -1 })
    ).toThrow()
  })

  it("validates full context_package with insights and pending_deliveries", () => {
    expect(() =>
      ContextPackageSchema.parse({
        ...baseContextPackage,
        conversation_summary:  "Cliente quer cancelar plano por insatisfação com sinal",
        sentiment_trajectory:  [0.1, -0.3, -0.5],
        attempted_resolutions: ["oferta_desconto_10pct"],
        intent_history: [
          {
            intent:     "cancelamento",
            confidence: 0.92,
            turn:       1,
            timestamp:  "2026-03-16T14:00:05Z",
          },
        ],
        conversation_insights: [baseItem],
        pending_deliveries: [
          {
            ...baseItem,
            item_id:  "aa0e8400-e29b-41d4-a716-446655440005",
            category: "outbound.retencao.oferta",
            status:   "pending" as const,
          },
        ],
        schema_version: 1,
      })
    ).not.toThrow()
  })

  it("rejects invalid channel", () => {
    expect(() =>
      ContextPackageSchema.parse({ ...baseContextPackage, channel: "telegram" })
    ).toThrow()
  })

  it("rejects invalid session_id (non-UUID)", () => {
    expect(() =>
      ContextPackageSchema.parse({ ...baseContextPackage, session_id: "sessao-123" })
    ).toThrow()
  })

  it("rejects negative turn_count", () => {
    expect(() =>
      ContextPackageSchema.parse({
        ...baseContextPackage,
        channel_context: { ...baseContextPackage.channel_context, turn_count: -1 },
      })
    ).toThrow()
  })

  it("rejects sentiment_trajectory value outside -1 to 1", () => {
    expect(() =>
      ContextPackageSchema.parse({
        ...baseContextPackage,
        sentiment_trajectory: [0.5, 1.5],  // 1.5 is out of range
      })
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// AgentDonePayloadSchema — spec 4.2
// Critical rule: handoff_reason required when outcome !== "resolved"
// ─────────────────────────────────────────────

const baseAgentDone = {
  session_id:   "bb0e8400-e29b-41d4-a716-446655440006",
  agent_id:     "inst_agente_retencao_001",
  outcome:      "resolved" as const,
  issue_status: [
    {
      issue_id:    "issue_001",
      description: "Cancelamento revertido com desconto",
      status:      "resolved" as const,
    },
  ],
  completed_at: "2026-03-16T14:30:00Z",
}

describe("AgentDonePayloadSchema", () => {
  it("validates resolved outcome without handoff_reason", () => {
    expect(() => AgentDonePayloadSchema.parse(baseAgentDone)).not.toThrow()
  })

  it("validates resolved outcome with handoff_reason (optional for resolved)", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        handoff_reason: "Incluído por precaução",
      })
    ).not.toThrow()
  })

  it(".refine(): rejects escalated_human without handoff_reason", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        outcome: "escalated_human" as const,
        // handoff_reason absent — must fail .refine()
      })
    ).toThrow(/handoff_reason/)
  })

  it(".refine(): rejects transferred_agent without handoff_reason", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        outcome: "transferred_agent" as const,
      })
    ).toThrow(/handoff_reason/)
  })

  it(".refine(): rejects callback without handoff_reason", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        outcome: "callback" as const,
      })
    ).toThrow(/handoff_reason/)
  })

  it(".refine(): accepts escalated_human with handoff_reason", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        outcome:        "escalated_human" as const,
        handoff_reason: "Churn risk alto — especialista humano necessário",
      })
    ).not.toThrow()
  })

  it(".refine(): accepts transferred_agent with handoff_reason", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        outcome:        "transferred_agent" as const,
        handoff_reason: "Skill de portabilidade necessária",
      })
    ).not.toThrow()
  })

  it("rejects empty issue_status (required and non-empty)", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({ ...baseAgentDone, issue_status: [] })
    ).toThrow()
  })

  it("rejects invalid outcome: 'abandoned'", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({ ...baseAgentDone, outcome: "abandoned" })
    ).toThrow()
  })

  it("rejects all outcome values outside the valid enum", () => {
    // All values that are NOT the 4 valid outcomes must fail
    const invalids = ["completed", "failed", "error", "pending", "in_progress",
                      "resolved_partial", "cancelled", "timeout", "unknown"]
    for (const outcome of invalids) {
      const result = AgentDonePayloadSchema.safeParse({ ...baseAgentDone, outcome })
      expect(result.success).toBe(false)
    }
  })

  it("accepts all 4 valid outcomes with handoff_reason when required", () => {
    const cases = [
      { outcome: "resolved" },
      { outcome: "escalated_human",   handoff_reason: "cliente com churn alto" },
      { outcome: "transferred_agent", handoff_reason: "skill de portabilidade necessária" },
      { outcome: "callback",          handoff_reason: "retornar em 30 minutos" },
    ]
    for (const { outcome, handoff_reason } of cases) {
      const result = AgentDonePayloadSchema.safeParse({
        ...baseAgentDone,
        outcome,
        ...(handoff_reason ? { handoff_reason } : {}),
      })
      expect(result.success).toBe(true)
    }
  })

  it(".refine(): accepts callback with handoff_reason", () => {
    expect(() =>
      AgentDonePayloadSchema.parse({
        ...baseAgentDone,
        outcome:        "callback" as const,
        handoff_reason: "cliente solicitou retorno após 1h",
      })
    ).not.toThrow()
  })

  it("AgentDonePayloadSchema is identical to AgentDoneSchema", () => {
    // Ensures the alias does not change behaviour
    const input = { ...baseAgentDone }
    const r1 = AgentDonePayloadSchema.safeParse(input)
    const r2 = AgentDoneSchema.safeParse(input)
    expect(r1.success).toBe(r2.success)
  })
})
