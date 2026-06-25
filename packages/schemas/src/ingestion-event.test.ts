/**
 * ingestion-event.test.ts
 * Tests for the ingestion_event_v1 family (R13a-1) — mandatory/optional fields
 * per docs/arcos/quality-ingest.md §3, defaults, and discriminated-union parse.
 */

import { describe, it, expect } from "vitest"
import {
  INGESTION_EVENT_SCHEMA_VERSION,
  IngestionContactOpenedSchema,
  IngestionParticipantJoinedSchema,
  IngestionMessageSentSchema,
  IngestionParticipantLeftSchema,
  IngestionContactClosedSchema,
  IngestionEventSchema,
  deriveIngestionEventId,
} from "./ingestion-event"

const ISO = "2026-06-24T12:00:00.000Z"

// ─────────────────────────────────────────────
// Version tag
// ─────────────────────────────────────────────

describe("INGESTION_EVENT_SCHEMA_VERSION", () => {
  it("is ingestion_event_v1", () => {
    expect(INGESTION_EVENT_SCHEMA_VERSION).toBe("ingestion_event_v1")
  })
})

// ─────────────────────────────────────────────
// contact.opened
// ─────────────────────────────────────────────

describe("IngestionContactOpenedSchema", () => {
  const base = {
    event_type: "contact.opened",
    external_contact_id: "c-1",
    source: "ccaas:genesys",
    channel: "voice",
    opened_at: ISO,
  }

  it("accepts the mandatory minimum", () => {
    expect(() => IngestionContactOpenedSchema.parse(base)).not.toThrow()
  })

  it("accepts optional medium + customer_ref", () => {
    const parsed = IngestionContactOpenedSchema.parse({
      ...base,
      medium: "voice",
      customer_ref: "cust-9",
      event_id: "e1",
    })
    expect(parsed.customer_ref).toBe("cust-9")
  })

  it.each(["source", "channel", "opened_at", "external_contact_id"])(
    "rejects when mandatory %s is missing",
    (field) => {
      const bad: Record<string, unknown> = { ...base }
      delete bad[field]
      expect(() => IngestionContactOpenedSchema.parse(bad)).toThrow()
    },
  )

  it("rejects non-ISO opened_at", () => {
    expect(() =>
      IngestionContactOpenedSchema.parse({ ...base, opened_at: "yesterday" }),
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// participant.joined
// ─────────────────────────────────────────────

describe("IngestionParticipantJoinedSchema", () => {
  const base = {
    event_type: "participant.joined",
    external_contact_id: "c-1",
    segment_ref: "seg-a",
    external_agent_id: "agt-7",
    agent_kind: "ai",
    pool_id: "retencao_humano",
    started_at: ISO,
  }

  it("accepts the mandatory minimum and defaults role=primary", () => {
    const parsed = IngestionParticipantJoinedSchema.parse(base)
    expect(parsed.role).toBe("primary")
  })

  it("accepts AI-only skill_id + deploy_version", () => {
    const parsed = IngestionParticipantJoinedSchema.parse({
      ...base,
      skill_id: "skill_portabilidade_telco",
      deploy_version: "2026-06-01T00:00:00.000Z",
      role: "specialist",
    })
    expect(parsed.skill_id).toBe("skill_portabilidade_telco")
    expect(parsed.role).toBe("specialist")
  })

  it.each([
    "segment_ref",
    "external_agent_id",
    "agent_kind",
    "pool_id",
    "started_at",
  ])("rejects when mandatory %s is missing", (field) => {
    const bad: Record<string, unknown> = { ...base }
    delete bad[field]
    expect(() => IngestionParticipantJoinedSchema.parse(bad)).toThrow()
  })

  it("rejects invalid agent_kind", () => {
    expect(() =>
      IngestionParticipantJoinedSchema.parse({ ...base, agent_kind: "bot" }),
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// message.sent
// ─────────────────────────────────────────────

describe("IngestionMessageSentSchema", () => {
  const base = {
    event_type: "message.sent",
    external_contact_id: "c-1",
    ts: ISO,
    author_role: "customer",
    content: "olá",
    masked: true,
  }

  it("accepts the mandatory minimum with content_type/visibility defaults", () => {
    const parsed = IngestionMessageSentSchema.parse(base)
    expect(parsed.content_type).toBe("text")
    expect(parsed.visibility).toBe("all")
    expect(parsed.masked_categories).toEqual([])
  })

  it("accepts optional author_id, segment_ref, masked_categories", () => {
    const parsed = IngestionMessageSentSchema.parse({
      ...base,
      author_id: "agt-7",
      segment_ref: "seg-a",
      visibility: "agents_only",
      masked_categories: ["cpf", "phone"],
    })
    expect(parsed.masked_categories).toEqual(["cpf", "phone"])
    expect(parsed.visibility).toBe("agents_only")
  })

  it.each(["ts", "author_role", "content", "masked"])(
    "rejects when mandatory %s is missing",
    (field) => {
      const bad: Record<string, unknown> = { ...base }
      delete bad[field]
      expect(() => IngestionMessageSentSchema.parse(bad)).toThrow()
    },
  )

  it("rejects invalid author_role", () => {
    expect(() =>
      IngestionMessageSentSchema.parse({ ...base, author_role: "primary" }),
    ).toThrow()
  })

  it("rejects non-boolean masked", () => {
    expect(() =>
      IngestionMessageSentSchema.parse({ ...base, masked: "yes" }),
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// participant.left
// ─────────────────────────────────────────────

describe("IngestionParticipantLeftSchema", () => {
  const base = {
    event_type: "participant.left",
    external_contact_id: "c-1",
    segment_ref: "seg-a",
    ended_at: ISO,
  }

  it("accepts the mandatory minimum (outcome optional)", () => {
    expect(() => IngestionParticipantLeftSchema.parse(base)).not.toThrow()
  })

  it("accepts optional outcome, tool_trace, precomputed_metrics", () => {
    const parsed = IngestionParticipantLeftSchema.parse({
      ...base,
      outcome: "resolved",
      tool_trace: [{ tool: "customer_get", ok: true }],
      precomputed_metrics: { agent_response_latency_s: 4.2 },
    })
    expect(parsed.outcome).toBe("resolved")
    expect(parsed.precomputed_metrics?.agent_response_latency_s).toBe(4.2)
  })

  it.each(["segment_ref", "ended_at"])(
    "rejects when mandatory %s is missing",
    (field) => {
      const bad: Record<string, unknown> = { ...base }
      delete bad[field]
      expect(() => IngestionParticipantLeftSchema.parse(bad)).toThrow()
    },
  )

  it("rejects non-numeric precomputed_metrics value", () => {
    expect(() =>
      IngestionParticipantLeftSchema.parse({
        ...base,
        precomputed_metrics: { latency: "fast" },
      }),
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// contact.closed
// ─────────────────────────────────────────────

describe("IngestionContactClosedSchema", () => {
  const base = {
    event_type: "contact.closed",
    external_contact_id: "c-1",
    outcome: "resolved",
    closed_at: ISO,
  }

  it("accepts the mandatory minimum", () => {
    expect(() => IngestionContactClosedSchema.parse(base)).not.toThrow()
  })

  it("accepts optional close_reason", () => {
    const parsed = IngestionContactClosedSchema.parse({
      ...base,
      close_reason: "flow_complete",
    })
    expect(parsed.close_reason).toBe("flow_complete")
  })

  it.each(["outcome", "closed_at", "external_contact_id"])(
    "rejects when mandatory %s is missing",
    (field) => {
      const bad: Record<string, unknown> = { ...base }
      delete bad[field]
      expect(() => IngestionContactClosedSchema.parse(bad)).toThrow()
    },
  )

  it("rejects empty outcome", () => {
    expect(() =>
      IngestionContactClosedSchema.parse({ ...base, outcome: "" }),
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// Discriminated union
// ─────────────────────────────────────────────

describe("IngestionEventSchema (discriminated union)", () => {
  it("routes each event_type to its variant", () => {
    const events = [
      { event_type: "contact.opened", external_contact_id: "c", source: "s", channel: "voice", opened_at: ISO },
      { event_type: "participant.joined", external_contact_id: "c", segment_ref: "s", external_agent_id: "a", agent_kind: "human", pool_id: "p", started_at: ISO },
      { event_type: "message.sent", external_contact_id: "c", ts: ISO, author_role: "agent", content: "x", masked: false },
      { event_type: "participant.left", external_contact_id: "c", segment_ref: "s", ended_at: ISO },
      { event_type: "contact.closed", external_contact_id: "c", outcome: "resolved", closed_at: ISO },
    ]
    for (const e of events) {
      expect(() => IngestionEventSchema.parse(e)).not.toThrow()
    }
  })

  it("rejects an unknown event_type", () => {
    expect(() =>
      IngestionEventSchema.parse({ event_type: "contact.paused", external_contact_id: "c" }),
    ).toThrow()
  })
})

// ─────────────────────────────────────────────
// Idempotency helper
// ─────────────────────────────────────────────

describe("deriveIngestionEventId", () => {
  it("is stable for the same contact/type/index", () => {
    const a = deriveIngestionEventId("c-1", "message.sent", 3)
    const b = deriveIngestionEventId("c-1", "message.sent", 3)
    expect(a).toBe(b)
    expect(a).toBe("ext:c-1:message.sent:3")
  })

  it("differs across index or type", () => {
    expect(deriveIngestionEventId("c-1", "message.sent", 3)).not.toBe(
      deriveIngestionEventId("c-1", "message.sent", 4),
    )
    expect(deriveIngestionEventId("c-1", "message.sent", 3)).not.toBe(
      deriveIngestionEventId("c-1", "contact.closed", 3),
    )
  })
})
