/**
 * evaluation.test.ts
 * Unit tests for evaluation_context_get and evaluation_submit MCP tools.
 * Covers Arc 3 (Session Replayer baseline) and Arc 6 (form-aware + campaign context).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerEvaluationTools, type EvaluationDeps } from "../tools/evaluation"

// ─── Mock infrastructure ──────────────────────────────────────────────────────

const mockKafka = { publish: vi.fn().mockResolvedValue(undefined) }
const mockPostgres = { fetchTranscript: vi.fn().mockResolvedValue([]) }
const mockRedis = {
  hget:   vi.fn(),
  get:    vi.fn(),
  expire: vi.fn().mockResolvedValue(1),
}

vi.mock("../infra/jwt", () => ({
  verifySessionToken: (_token: string) => ({ tenant_id: "tenant_test" }),
  InvalidTokenError:  class InvalidTokenError extends Error {},
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

function makeServer(): { server: McpServer; callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>; rawTool: (name: string, args: Record<string, unknown>) => Promise<ToolResponse> } {
  const server = new McpServer({ name: "test", version: "0.0.1" })
  const deps: EvaluationDeps = {
    kafka:            mockKafka as any,
    postgres:         mockPostgres as any,
    redis:            mockRedis as any,
    proxyUrl:         "http://localhost:7422",
    skillRegistryUrl: "http://localhost:3300/v1",
  }
  registerEvaluationTools(server, deps)

  async function rawTool(name: string, args: Record<string, unknown>): Promise<ToolResponse> {
    // Matches pattern from runtime.test.ts: _registeredTools[name].handler(input)
    const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
      ._registeredTools?.[name]
    if (!reg) throw new Error(`Tool '${name}' not registered`)
    return reg.handler(args)
  }

  async function callTool(name: string, args: Record<string, unknown>) {
    const result = await rawTool(name, args)
    const text = result?.content?.[0]?.text
    return text ? JSON.parse(text) : result
  }

  return { server, callTool, rawTool }
}

function makeReplayContext(overrides: Record<string, unknown> = {}) {
  return {
    session_id:   "sess_abc",
    tenant_id:    "tenant_test",
    replay_id:    "replay_001",
    session_meta: { channel: "webchat", opened_at: "2026-01-01T00:00:00Z", outcome: "resolved" },
    events:       [],
    sentiment:    [{ score: 0.5, timestamp: "2026-01-01T00:01:00Z" }],
    participants: [
      { participant_id: "agent_001", role: "primary",   agent_type_id: "agente_sac_v1" },
      { participant_id: "eval_001",  role: "evaluator", agent_type_id: "agente_avaliacao_v1" },
    ],
    speed_factor:    10.0,
    source:          "redis",
    created_at:      "2026-01-01T00:05:00Z",
    comparison_mode: false,
    ...overrides,
  }
}

const VALID_TOKEN   = "valid.session.token"
const SESSION_ID    = "sess_abc"
const PARTICIPANT_ID = "00000000-0000-0000-0000-000000000001"
const EVALUATION_ID  = "11111111-1111-1111-1111-111111111111"

// ─── evaluation_context_get ───────────────────────────────────────────────────

describe("evaluation_context_get", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Default: participant role is "evaluator"
    mockRedis.hget.mockResolvedValue("evaluator")
  })

  it("returns context with participant_summary for Arc 3 ReplayContext", async () => {
    const ctx = makeReplayContext()
    mockRedis.get.mockResolvedValue(JSON.stringify(ctx))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    }) as Record<string, unknown>

    expect(result.session_id).toBe(SESSION_ID)
    expect(result.participant_id).toBe(PARTICIPANT_ID)
    expect(result.context).toMatchObject({ session_id: SESSION_ID })
    expect(result.retrieved_at).toBeDefined()
    // Arc 6 fields absent — should not be present
    expect(result.evaluation_form).toBeUndefined()
    expect(result.campaign_id).toBeUndefined()
    expect(result.instance_id).toBeUndefined()
    // Participant summary always present
    expect(Array.isArray(result.participant_summary)).toBe(true)
    expect((result.participant_summary as unknown[]).length).toBe(2)
  })

  it("surfaces evaluation_form, campaign_id, instance_id from Arc 6 ReplayContext", async () => {
    const ctx = makeReplayContext({
      evaluation_form: { form_id: "form_abc", name: "Avaliação SAC", version: 1, criteria: [] },
      campaign_id:     "camp_jan_2026",
      instance_id:     "inst_xyz",
    })
    mockRedis.get.mockResolvedValue(JSON.stringify(ctx))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    }) as Record<string, unknown>

    expect(result.evaluation_form).toMatchObject({ form_id: "form_abc" })
    expect(result.campaign_id).toBe("camp_jan_2026")
    expect(result.instance_id).toBe("inst_xyz")
  })

  it("surfaces comparison_mode=true from ReplayContext", async () => {
    const ctx = makeReplayContext({ comparison_mode: true })
    mockRedis.get.mockResolvedValue(JSON.stringify(ctx))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    }) as Record<string, unknown>

    expect(result.comparison_mode).toBe(true)
  })

  it("returns error when ReplayContext not found", async () => {
    mockRedis.get.mockResolvedValue(null)

    const { rawTool } = makeServer()
    const raw = await rawTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    })

    expect(raw.isError).toBe(true)
    const text = JSON.parse(raw.content[0]!.text)
    expect(text.error).toBe("replay_not_ready")
  })

  it("rejects participant with role 'primary'", async () => {
    mockRedis.hget.mockResolvedValue("primary")
    mockRedis.get.mockResolvedValue(JSON.stringify(makeReplayContext()))

    const { rawTool } = makeServer()
    const raw = await rawTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    })

    expect(raw.isError).toBe(true)
    const text = JSON.parse(raw.content[0]!.text)
    expect(text.error).toBe("unauthorized")
  })

  it("rejects participant with agent_role 'reviewer'", async () => {
    // `reviewer` saiu do vocabulário aceito: a revisão humana do Arc 13 é o
    // contrato REST (contestation_router) com Bearer JWT + ABAC, não MCP.
    // `AgentRoleSchema` (registry) nem sequer tem esse valor.
    mockRedis.hget.mockResolvedValue("reviewer")
    mockRedis.get.mockResolvedValue(JSON.stringify(makeReplayContext()))

    const { rawTool } = makeServer()
    const raw = await rawTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    })

    expect(raw.isError).toBe(true)
    expect(JSON.parse(raw.content[0]!.text).error).toBe("unauthorized")
  })

  it("resolves the instance via the participant→instance map", async () => {
    // O hash é indexado por `instance_id`; o caller passa `participant_id`. Os dois
    // só coincidem por acidente no avaliador do Arc 6. O mapa
    // `{tenant}:participant:{id}:instance` (escrito pelo agent_busy) é a via correta
    // — a mesma que o message_send já usa.
    const MAPPED_INSTANCE = "inst_evaluator_42"
    mockRedis.get.mockImplementation((key: string) =>
      Promise.resolve(
        key.includes(":participant:")
          ? MAPPED_INSTANCE
          : JSON.stringify(makeReplayContext()),
      ))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    }) as Record<string, unknown>

    expect(result.session_id).toBe(SESSION_ID)
    expect(mockRedis.hget).toHaveBeenCalledWith(
      `tenant_test:agent:instance:${MAPPED_INSTANCE}`,
      "agent_role",
    )
  })

  it("rejects when agent_role cannot be read (fails closed)", async () => {
    // Regressão do buraco corrigido: o gate antigo tinha default "" e a condição
    // `if (role && ...)` curto-circuitava — a verificação NÃO acontecia e o
    // ReplayContext (com original_content DESMASCARADO) saía para qualquer um.
    mockRedis.hget.mockResolvedValue(null)
    mockRedis.get.mockResolvedValue(JSON.stringify(makeReplayContext()))

    const { rawTool } = makeServer()
    const raw = await rawTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    })

    expect(raw.isError).toBe(true)
    expect(JSON.parse(raw.content[0]!.text).error).toBe("unauthorized")
  })

  it("extracts participant_summary fields correctly", async () => {
    const ctx = makeReplayContext()
    mockRedis.get.mockResolvedValue(JSON.stringify(ctx))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_context_get", {
      session_token:  VALID_TOKEN,
      session_id:     SESSION_ID,
      participant_id: PARTICIPANT_ID,
    }) as Record<string, unknown>

    const summary = result.participant_summary as Array<Record<string, unknown>>
    const primary = summary.find(p => p.role === "primary")
    expect(primary).toBeDefined()
    expect(primary?.participant_id).toBe("agent_001")
    expect(primary?.agent_type_id).toBe("agente_sac_v1")
  })
})

// ─── evaluation_submit ────────────────────────────────────────────────────────

describe("evaluation_submit", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // O hash da instância responde por CAMPO: `agent_role` é a entrada de
    // autorização do gate, `agent_type_id` é a identidade carimbada no resultado.
    // Um `mockResolvedValue` único devolveria o agent_type_id também para
    // `agent_role` e o gate negaria tudo.
    mockRedis.hget.mockImplementation((_key: string, field: string) =>
      Promise.resolve(field === "agent_role" ? "evaluator" : "agente_avaliacao_v1"))
    mockRedis.get.mockResolvedValue(JSON.stringify(makeReplayContext()))
    mockKafka.publish.mockResolvedValue(undefined)
  })

  const baseInput = {
    session_token:      VALID_TOKEN,
    session_id:         SESSION_ID,
    participant_id:     PARTICIPANT_ID,
    evaluation_id:      EVALUATION_ID,
    composite_score:    8.5,
    dimensions:         [{ dimension_id: "communication", name: "Comunicação", score: 9, weight: 1, flags: [] }],
    summary:            "Atendimento satisfatório.",
    highlights:         ["Resposta ágil"],
    improvement_points: [],
    compliance_flags:   [],
    is_benchmark:       false,
  }

  it("rejects submit when agent_role is not evaluator (fails closed)", async () => {
    mockRedis.hget.mockImplementation((_key: string, field: string) =>
      Promise.resolve(field === "agent_role" ? "executor" : "agente_retencao_v1"))

    const { rawTool } = makeServer()
    const raw = await rawTool("evaluation_submit", baseInput)

    expect(raw.isError).toBe(true)
    expect(JSON.parse(raw.content[0]!.text).error).toBe("unauthorized")
    expect(mockKafka.publish).not.toHaveBeenCalled()
  })

  it("publishes evaluation.completed with eval_status=submitted (Arc 3 baseline)", async () => {
    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", baseInput) as Record<string, unknown>

    expect(result.submitted).toBe(true)
    expect(result.evaluation_id).toBe(EVALUATION_ID)
    expect(result.composite_score).toBe(8.5)
    expect(mockKafka.publish).toHaveBeenCalledTimes(1)

    const [topic, event] = mockKafka.publish.mock.calls[0] as [string, Record<string, unknown>]
    expect(topic).toBe("evaluation.events")
    expect(event.event_type).toBe("evaluation.completed")
    expect(event.eval_status).toBe("submitted")
    expect(event.tenant_id).toBe("tenant_test")
    expect(event.session_outcome).toBe("resolved")   // from ReplayContext session_meta
  })

  it("includes criterion_responses in published event (Arc 6)", async () => {
    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", {
      ...baseInput,
      criterion_responses: [
        {
          criterion_id:  "crit_greeting",
          na:            false,
          score:         9.0,
          notes:         "Saudação correta",
          evidence:      [{ event_id: "evt_001", turn_index: 0, category: "positive" }],
        },
        {
          criterion_id:  "crit_empathy",
          na:            false,
          boolean_value: true,
          evidence:      [],
        },
      ],
    }) as Record<string, unknown>

    expect(result.criterion_responses_included).toBe(true)

    const [, event] = mockKafka.publish.mock.calls[0] as [string, Record<string, unknown>]
    expect(Array.isArray(event.criterion_responses)).toBe(true)
    expect((event.criterion_responses as unknown[]).length).toBe(2)
  })

  it("preserves form-driven justification + evidence.stream_entry_id (T9-C.fix)", async () => {
    const { callTool } = makeServer()
    await callTool("evaluation_submit", {
      ...baseInput,
      criterion_responses: [
        {
          criterion_id:  "crit_greeting",
          na:            false,
          score:         8.0,
          // saída form-driven: `justification` (não `notes`) + evidência por stream_entry_id
          justification: "A saudação seguiu o protocolo e o agente se identificou no início.",
          evidence:      [{ stream_entry_id: "mfix1", excerpt: "bom dia, joao", relevance_note: "abertura cordial" }],
        },
      ],
    })

    const [, event] = mockKafka.publish.mock.calls[0] as [string, Record<string, unknown>]
    const crs = event.criterion_responses as Array<Record<string, unknown>>
    expect(crs).toHaveLength(1)
    // justification NÃO pode ser descartado pelo Zod (era o bug)
    expect(crs[0]?.justification).toBe("A saudação seguiu o protocolo e o agente se identificou no início.")
    // evidência com stream_entry_id deve sobreviver (alinha a evidência clicável do nível 3)
    const ev = crs[0]?.evidence as Array<Record<string, unknown>>
    expect(ev?.[0]?.stream_entry_id).toBe("mfix1")
    expect(ev?.[0]?.relevance_note).toBe("abertura cordial")
  })

  it("includes knowledge_snippets in published event (Arc 6)", async () => {
    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", {
      ...baseInput,
      knowledge_snippets: [
        { snippet_id: "snip_001", content: "Protocolo de atendimento X", score: 0.92 },
      ],
    }) as Record<string, unknown>

    expect(result.knowledge_snippets_included).toBe(true)
    const [, event] = mockKafka.publish.mock.calls[0] as [string, Record<string, unknown>]
    const snips = event.knowledge_snippets as Array<Record<string, unknown>>
    expect(snips[0]?.score).toBe(0.92)
  })

  it("publishes eval.instance.submitted when instance_id provided (Arc 6)", async () => {
    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", {
      ...baseInput,
      form_id:     "form_abc",
      campaign_id: "camp_jan",
      instance_id: "inst_xyz",
    }) as Record<string, unknown>

    expect(result.instance_lifecycle_published).toBe(true)
    // Two Kafka publishes: evaluation.completed + eval.instance.submitted
    expect(mockKafka.publish).toHaveBeenCalledTimes(2)

    const calls = mockKafka.publish.mock.calls as Array<[string, Record<string, unknown>]>
    const lifecycleEvent = calls.find(([, e]) => e.event_type === "eval.instance.submitted")
    expect(lifecycleEvent).toBeDefined()
    expect(lifecycleEvent![1].instance_id).toBe("inst_xyz")
    expect(lifecycleEvent![1].campaign_id).toBe("camp_jan")
    expect(lifecycleEvent![1].form_id).toBe("form_abc")
  })

  it("reads campaign_id + instance_id from ReplayContext as fallback", async () => {
    mockRedis.get.mockResolvedValue(JSON.stringify(makeReplayContext({
      campaign_id: "camp_from_ctx",
      instance_id: "inst_from_ctx",
    })))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", baseInput) as Record<string, unknown>

    // Should have published lifecycle event using values from ReplayContext
    expect(result.instance_lifecycle_published).toBe(true)
    const calls = mockKafka.publish.mock.calls as Array<[string, Record<string, unknown>]>
    const lifecycle = calls.find(([, e]) => e.event_type === "eval.instance.submitted")
    expect(lifecycle![1].campaign_id).toBe("camp_from_ctx")
    expect(lifecycle![1].instance_id).toBe("inst_from_ctx")
  })

  it("does not publish lifecycle event when no instance_id", async () => {
    // ReplayContext has no instance_id
    mockRedis.get.mockResolvedValue(JSON.stringify(makeReplayContext()))

    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", baseInput) as Record<string, unknown>

    expect(result.instance_lifecycle_published).toBe(false)
    expect(mockKafka.publish).toHaveBeenCalledTimes(1)   // only evaluation.completed
  })

  it("includes comparison report when comparison_turns provided", async () => {
    const { callTool } = makeServer()
    const result = await callTool("evaluation_submit", {
      ...baseInput,
      comparison_turns: [
        { turn_index: 0, production_text: "Olá, como posso ajudar?", replay_text: "Olá, em que posso ajudar?" },
        { turn_index: 1, production_text: "Problema resolvido.", replay_text: "Tudo resolvido." },
      ],
    }) as Record<string, unknown>

    expect(result.comparison_included).toBe(true)
    const [, event] = mockKafka.publish.mock.calls[0] as [string, Record<string, unknown>]
    expect(event.comparison).toBeDefined()
    const cmp = event.comparison as Record<string, unknown>
    expect(typeof cmp.similarity_score).toBe("number")
  })

  it("expires ReplayContext TTL after submission", async () => {
    const { callTool } = makeServer()
    await callTool("evaluation_submit", baseInput)
    expect(mockRedis.expire).toHaveBeenCalledWith(
      "tenant_test:replay:sess_abc:context",
      60
    )
  })
})
