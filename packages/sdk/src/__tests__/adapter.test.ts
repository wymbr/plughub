/**
 * adapter.test.ts
 * Testes do PlugHubAdapter — spec 4.6d
 */

import { describe, it, expect } from "vitest"
import { PlugHubAdapter }       from "../adapter"
import { ContextPackageSchema } from "@plughub/schemas"

const validPkg = ContextPackageSchema.parse({
  session_id:   "550e8400-e29b-41d4-a716-446655440000",
  tenant_id:    "tenant_telco",
  channel:      "chat",
  customer_data: {
    customer_id: "660e8400-e29b-41d4-a716-446655440001",
    tenant_id:   "tenant_telco",
    tier:        "gold",
    churn_risk:  0.72,
  },
  channel_context: {
    turn_count: 3,
    started_at: "2026-03-16T14:00:00Z",
    handoff_reason: "churn_signal",
  },
  conversation_history: [
    { role: "customer", content: "Quero cancelar", timestamp: "2026-03-16T14:01:00Z" },
  ],
})

describe("PlugHubAdapter — construção", () => {
  it("instancia com mapeamentos válidos", () => {
    expect(() => new PlugHubAdapter({
      context_map: { "customer_data.tier": "cliente.tier" },
      result_map:  { "outcome": "status", "issue_status": "issues" },
    })).not.toThrow()
  })

  it("rejeita adapter sem outcome no result_map", () => {
    expect(() => new PlugHubAdapter({
      context_map: {},
      result_map:  { "issue_status": "issues" },  // falta outcome
    })).toThrow(/outcome/)
  })

  it("rejeita adapter sem issue_status no result_map", () => {
    expect(() => new PlugHubAdapter({
      context_map: {},
      result_map:  { "outcome": "status" },  // falta issue_status
    })).toThrow(/issue_status/)
  })
})

describe("PlugHubAdapter — fromPlatform (direção entrada)", () => {
  const adapter = new PlugHubAdapter({
    context_map: {
      "customer_data.tier":          "case.account_tier",
      "customer_data.churn_risk":    "case.churn_score",
      "channel_context.handoff_reason": "case.handoff_reason",
      "conversation_history":        "case.history",
    },
    result_map: {
      "outcome":       "resolution_status",
      "issue_status":  "issues",
    },
  })

  it("mapeia campos do context_package para schema do agente", () => {
    const ctx = adapter.fromPlatform(validPkg)
    expect((ctx["case"] as Record<string, unknown>)?.["account_tier"]).toBe("gold")
    expect((ctx["case"] as Record<string, unknown>)?.["churn_score"]).toBe(0.72)
    expect((ctx["case"] as Record<string, unknown>)?.["handoff_reason"]).toBe("churn_signal")
  })

  it("ignora campos ausentes no context_package sem erro", () => {
    const pkgSemChurn = ContextPackageSchema.parse({
      ...validPkg,
      customer_data: { ...validPkg.customer_data, churn_risk: undefined },
    })
    expect(() => adapter.fromPlatform(pkgSemChurn)).not.toThrow()
  })
})

describe("PlugHubAdapter — toPlatform (direção saída)", () => {
  const adapter = new PlugHubAdapter({
    context_map: {},
    result_map: {
      "outcome":      "resolution_status",
      "issue_status": "issues",
    },
    outcome_map: {
      "needs_escalation": "escalated_human",
      "transferred":      "transferred_agent",
    },
  })

  it("mapeia resultado do agente para campos da plataforma", () => {
    const result = adapter.toPlatform({
      resolution_status: "resolved",
      issues:            [{ issue_id: "1", description: "ok", status: "resolved" }],
    })
    expect(result.outcome).toBe("resolved")
  })

  it("aplica outcome_map para traduzir outcome semântico", () => {
    const result = adapter.toPlatform({
      resolution_status: "needs_escalation",
      issues: [],
    })
    expect(result.outcome).toBe("escalated_human")
  })

  it("mantém outcome sem mapeamento inalterado", () => {
    const result = adapter.toPlatform({
      resolution_status: "resolved",
      issues: [],
    })
    expect(result.outcome).toBe("resolved")
  })
})
