/**
 * caller-token-chain.test.ts — CTR-06: a cadeia `delegate → delegate`.
 *
 * `core.workflow.delegate_resume_token` é tag ÚNICA da sessão, e o token que ela
 * carrega é fato da ARESTA (chamador → chamado). Numa cadeia
 * `A → B → C`, a delegação de B para C SOBRESCREVE o token que retomaria A, e
 * quando B tenta devolver ele encontra o próprio token — A nunca volta e o
 * contato fica pendurado até o `timeout_hours`, **sem nada ficar vermelho**.
 *
 * ⚠️ **Os dois casos que carregam peso são os NEGATIVOS**, e não a captura:
 *
 *   1. `não restaura quando a tag já é a certa` — uma implementação que gravasse
 *      sempre passaria em todos os testes positivos e transformaria o log de
 *      reparo em ruído de todo delegate do parque. É esse log que denuncia uma
 *      colisão real; se ele imprime sempre, ele não diz nada.
 *
 *   2. `pipeline sem chamador não inventa token` — capturar de um contato que
 *      nunca foi delegado escreveria na tag do orquestrador um token velho
 *      (consumido) que ele não pediu.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine }      from "../engine"
import { PipelineStateManager } from "../state"
import type { SkillFlow }       from "@plughub/schemas"

const TAG    = "core.workflow.delegate_resume_token"
const CHAVE  = "_caller_resume_token"
const TENANT = "tenant-test"

const mockRedis = {
  get:  vi.fn().mockResolvedValue(null),
  set:  vi.fn().mockResolvedValue("OK"),
  del:  vi.fn().mockResolvedValue(1),
  eval: vi.fn().mockResolvedValue(1),
}

/** ContextStore mínimo, com um hash de verdade — a colisão é observável nele. */
function lojaFake(inicial: Record<string, unknown> = {}) {
  const hash: Record<string, unknown> = { ...inicial }
  return {
    hash,
    get:          vi.fn(async () => null),
    getValue:     vi.fn(async (_sid: string, tag: string) => hash[tag]),
    getAll:       vi.fn(async () => ({})),
    getByPrefix:  vi.fn(async () => ({})),
    getMissing:   vi.fn(async () => ({})),
    set:          vi.fn(async (_sid: string, tag: string, entry: { value: unknown }) => {
      hash[tag] = entry.value
    }),
    delete:       vi.fn(async () => undefined),
    clearSession: vi.fn(async () => undefined),
  }
}

/** Fluxo trivial: um `complete`. O que se mede é o que acontece ANTES do 1º step. */
const fluxo: SkillFlow = {
  entry: "fim",
  steps: [{ id: "fim", type: "complete", outcome: "resolved" }],
}

function motor(store: ReturnType<typeof lojaFake>) {
  return new SkillFlowEngine({
    redis:        mockRedis as never,
    mcpCall:      vi.fn(),
    contextStore: store as never,
  } as never)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRedis.get.mockResolvedValue(null)
  mockRedis.set.mockResolvedValue("OK")
})

describe("CTR-06 — token do chamador", () => {
  it("captura a tag no NASCIMENTO do pipeline", async () => {
    const store = lojaFake({ [TAG]: "T_A" })
    const r = await motor(store).run({
      tenantId: TENANT, sessionId: "s1", customerId: "c1",
      skillId: "sk", flow: fluxo, sessionContext: {},
    })
    expect("outcome" in r && r.pipeline_state.results[CHAVE]).toBe("T_A")
  })

  // ── NEGATIVO 1 ────────────────────────────────────────────────────────────
  it("pipeline sem chamador não inventa token", async () => {
    const store = lojaFake({})   // contato de entrada: ninguém delegou para cá
    const r = await motor(store).run({
      tenantId: TENANT, sessionId: "s1", customerId: "c1",
      skillId: "sk", flow: fluxo, sessionContext: {},
    })
    expect("outcome" in r && CHAVE in r.pipeline_state.results).toBe(false)
    expect(store.set).not.toHaveBeenCalled()
  })

  it("A CADEIA: ao retomar, a tag volta a apontar para o MEU chamador", async () => {
    // Estado de B: nasceu com T_A guardado, delegou a C e suspendeu.
    const suspenso = {
      ...PipelineStateManager.create("sk_b", "fim"),
      status:          "suspended" as const,
      current_step_id: "fim",
      results:         { [CHAVE]: "T_A" },
    }
    mockRedis.get.mockImplementation(async (k: string) =>
      k.includes(":pipeline:") ? JSON.stringify(suspenso) : null,
    )
    // A tag ficou apontando para a delegação MAIS FUNDA (B → C).
    const store = lojaFake({ [TAG]: "T_B" })

    await motor(store).run({
      tenantId: TENANT, sessionId: "s1", customerId: "c1",
      skillId: "sk_b", flow: fluxo, sessionContext: {},
      resumeContext: { step_id: "fim", decision: "approved" } as never,
    })

    expect(store.hash[TAG]).toBe("T_A")
    // `overwrite`: quem escreveu por cima gravou com a MESMA confiança, então
    // `highest_confidence` faria da restauração um no-op silencioso.
    expect(store.set).toHaveBeenCalledWith(
      "s1", TAG, expect.objectContaining({ value: "T_A" }), "overwrite", "c1",
    )
  })

  // ── NEGATIVO 2 — o que impede o log de reparo de virar ruído ──────────────
  it("não restaura quando a tag já é a certa (delegate sem cadeia)", async () => {
    const suspenso = {
      ...PipelineStateManager.create("sk_b", "fim"),
      status:          "suspended" as const,
      current_step_id: "fim",
      results:         { [CHAVE]: "T_A" },
    }
    mockRedis.get.mockImplementation(async (k: string) =>
      k.includes(":pipeline:") ? JSON.stringify(suspenso) : null,
    )
    const store = lojaFake({ [TAG]: "T_A" })   // ninguém delegou por dentro

    await motor(store).run({
      tenantId: TENANT, sessionId: "s1", customerId: "c1",
      skillId: "sk_b", flow: fluxo, sessionContext: {},
      resumeContext: { step_id: "fim", decision: "approved" } as never,
    })

    expect(store.set).not.toHaveBeenCalled()
  })

  it("falha de leitura degrada BARULHENTO e não derruba o pipeline", async () => {
    const store = lojaFake({})
    store.getValue.mockRejectedValue(new Error("redis fora"))
    const aviso = vi.spyOn(console, "warn").mockImplementation(() => undefined)

    const r = await motor(store).run({
      tenantId: TENANT, sessionId: "s1", customerId: "c1",
      skillId: "sk", flow: fluxo, sessionContext: {},
    })

    expect("outcome" in r).toBe(true)
    expect(aviso.mock.calls.some(c => String(c[0]).includes("CTR-06"))).toBe(true)
    aviso.mockRestore()
  })
})
