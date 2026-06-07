/**
 * steps/notify.test.ts
 * F1.4b (bancada de agentes): context_tags no notify — extração do pipeline_state
 * para o ContextStore (o notify nunca havia implementado a extração; os YAMLs
 * wrap-up/NPS declaravam e o engine ignorava em silêncio).
 */

import { describe, it, expect, vi } from "vitest"
import { executeNotify }            from "../../steps/notify"
import type { StepContext }         from "../../executor"
import { NotifyStepSchema }         from "@plughub/schemas"
import type { PipelineState }       from "@plughub/schemas"

function makeCtx(
  results: Record<string, unknown>,
  contextStore?: unknown,
): StepContext {
  return {
    sessionId:      "s1",
    customerId:     "c1",
    sessionContext: {},
    state: {
      results, retry_counters: {}, transitions: [], status: "in_progress",
      flow_id: "test", current_step_id: "registrar",
      started_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    } as PipelineState,
    tenantId:        "tenant1",
    mcpCall:         vi.fn().mockResolvedValue({}),
    aiGatewayCall:   async () => ({}),
    saveState:       async () => {},
    retryStep:       async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    executeFallback: async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    getJobId:        async () => null,
    setJobId:        async () => {},
    redis:                {} as any,
    clearJobId:           async () => {},
    maskedScope:          {},
    transactionOnFailure: null,
    ...(contextStore ? { contextStore: contextStore as any } : {}),
  }
}

function makeStore() {
  return {
    get:          vi.fn().mockResolvedValue(null),
    getValue:     vi.fn().mockResolvedValue(null),
    getAll:       vi.fn().mockResolvedValue({}),
    getByPrefix:  vi.fn().mockResolvedValue({}),
    getMissing:   vi.fn().mockResolvedValue({ missing: [], low_confidence: [], complete: true }),
    set:          vi.fn().mockResolvedValue(undefined),
    delete:       vi.fn().mockResolvedValue(undefined),
    clearSession: vi.fn().mockResolvedValue(undefined),
  }
}

// Step espelhando o registrar_classificacao do agente_wrapup_v1
const stepWithTags = NotifyStepSchema.parse({
  id:      "registrar_classificacao",
  type:    "notify",
  message: "✅ Classificação registrada.",
  context_tags: {
    outputs: {
      wrapup_classificacao: {
        tag:        "session.wrapup.classificacao",
        confidence: 1.0,
        merge:      "overwrite",
        scope:      "session",
      },
    },
  },
  on_success: "proximo",
  on_failure: "falha",
})

describe("executeNotify — context_tags (F1.4b)", () => {
  it("extrai o valor do pipeline_state e escreve no ContextStore (scope session)", async () => {
    const store  = makeStore()
    const ctx    = makeCtx({ wrapup_classificacao: "escalado" }, store)
    const result = await executeNotify(stepWithTags, ctx)

    expect(result.next_step_id).toBe("proximo")
    // extração é fire-and-forget — aguarda o microtask queue esvaziar
    await new Promise((r) => setImmediate(r))

    expect(store.set).toHaveBeenCalledTimes(1)
    const [sid, tag, entry] = store.set.mock.calls[0]
    expect(sid).toBe("s1")
    expect(tag).toBe("session.wrapup.classificacao")   // scope session → tag sem prefixo
    expect((entry as { value: unknown }).value).toBe("escalado")
  })

  it("scope segment prefixa a tag com segment.{segmentId}.", async () => {
    const store = makeStore()
    const step  = NotifyStepSchema.parse({
      ...JSON.parse(JSON.stringify(stepWithTags)),
      context_tags: {
        outputs: {
          wrapup_classificacao: {
            tag: "session.wrapup.classificacao", confidence: 1.0,
            merge: "overwrite", scope: "segment",
          },
        },
      },
    })
    const ctx = makeCtx({ wrapup_classificacao: "resolvido" }, store)
    ctx.segmentId = "seg-123"
    await executeNotify(step, ctx)
    await new Promise((r) => setImmediate(r))

    expect(store.set.mock.calls[0][1]).toBe("segment.seg-123.session.wrapup.classificacao")
  })

  it("sem context_tags não escreve no ContextStore", async () => {
    const store = makeStore()
    const step  = NotifyStepSchema.parse({
      id: "n1", type: "notify", message: "oi",
      on_success: "proximo", on_failure: "falha",
    })
    await executeNotify(step, makeCtx({}, store))
    await new Promise((r) => setImmediate(r))

    expect(store.set).not.toHaveBeenCalled()
  })

  it("valor ausente no pipeline_state → não escreve a tag", async () => {
    const store = makeStore()
    const ctx   = makeCtx({}, store)   // sem wrapup_classificacao
    await executeNotify(stepWithTags, ctx)
    await new Promise((r) => setImmediate(r))

    expect(store.set).not.toHaveBeenCalled()
  })
})
