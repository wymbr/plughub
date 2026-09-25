/**
 * pinned-version.test.ts — SFE-03: a sessão retomada executa a versão em que NASCEU.
 *
 * O lançador resolve o fluxo lendo o slot `current` do pool a cada ativação. Numa
 * retomada depois de um promote, isso continuava o `current_step_id` gravado numa versão
 * que não o gravou. Medido no ledger (2026-09-25): 3 de 1 140 sessões suspensas
 * atravessaram um promote; `ff98077b` nasceu na versão de 12/08 e terminou `resolved`
 * na de 13/08, sem erro nenhum.
 *
 * A "queda" é simulada com uma tool que nunca responde: o estado fica `in_progress` no
 * step, com o pin gravado, exatamente como fica quando o processo morre ali. O lock é
 * apagado à mão, como o TTL faria.
 *
 * Cada teste diz o que o faria reprovar:
 *   · step renomeado          → retomada lê o fluxo recebido (volta o "Step não encontrado")
 *   · step com outro sentido  → idem, pela metade SILENCIOSA do defeito
 *   · pin ausente com marca   → cair no `current` em vez de recusar
 *   · legado sem marca        → recusar sessão anterior ao rollout
 *   · nascer de novo          → pin eterno (nunca mais pega versão nova)
 *   · TTL                     → `save()` sem renovar o pin
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine } from "../engine"
import type { SkillFlow, PipelineState } from "@plughub/schemas"

// ── Redis em memória: chaves separadas e TTL de verdade, que o mock de `vi.fn` não tem ──
class FakeRedis {
  store = new Map<string, string>()
  ttl   = new Map<string, number>()
  async get(k: string) { return this.store.get(k) ?? null }
  async set(k: string, v: string, ...args: unknown[]) {
    if (args.includes("NX") && this.store.has(k)) return null
    this.store.set(k, v)
    const i = args.indexOf("EX")
    if (i >= 0) this.ttl.set(k, Number(args[i + 1]))
    return "OK"
  }
  async del(...keys: string[]) { let n = 0; for (const k of keys) { if (this.store.delete(k)) n++ } return n }
  async expire(k: string, s: number) { if (!this.store.has(k)) return 0; this.ttl.set(k, s); return 1 }
  async eval(lua: string, _n: number, key: string, owner: string) {
    if (this.store.get(key) !== owner) return 0
    if (lua.includes('"del"')) this.store.delete(key)
    return 1
  }
}

const T   = "tenant-pin"
const SID = "sess-pin-1"
const PIPE = `${T}:pipeline:${SID}`
const PIN  = `${PIPE}:pinned`
const LOCK = `${PIPE}:running`

const invoke = (id: string, tool: string, next: string) => ({
  id, type: "invoke" as const,
  target: { mcp_server: "mcp-server-crm", tool },
  input: {}, output_as: id, on_success: next, on_failure: next,
})

// v1 — a versão em que a sessão nasce: `coletar` (a queda acontece aqui) → `ferramenta_v1`.
const V1: SkillFlow = {
  entry: "coletar",
  steps: [
    invoke("coletar", "coleta", "seguir"),
    invoke("seguir", "ferramenta_v1", "fim"),
    { id: "fim", type: "complete", outcome: "resolved" },
  ],
} as SkillFlow

// v2 com o step RENOMEADO — a metade barulhenta.
const V2_RENOMEADO: SkillFlow = {
  entry: "coletar_dados",
  steps: [
    invoke("coletar_dados", "coleta", "fim"),
    { id: "fim", type: "complete", outcome: "resolved" },
  ],
} as SkillFlow

// v2 com o MESMO id e outro sentido — a metade silenciosa.
const V2_OUTRO_SENTIDO: SkillFlow = {
  entry: "coletar",
  steps: [
    invoke("coletar", "coleta", "seguir"),
    invoke("seguir", "ferramenta_v2", "fim"),
    { id: "fim", type: "complete", outcome: "resolved" },
  ],
} as SkillFlow

let redis: FakeRedis
let tools: string[]
let hang: boolean

function engine() {
  return new SkillFlowEngine({
    redis: redis as never,
    mcpCall: async (tool: string) => {
      tools.push(tool)
      if (hang && tool === "coleta") return new Promise(() => { /* a queda */ })
      return { ok: true }
    },
    aiGatewayCall: vi.fn() as never,
  })
}

const run = (flow: SkillFlow, deployVersion: string, skillId = "skill_pin") =>
  engine().run({
    tenantId: T, sessionId: SID, customerId: "c1", skillId, flow,
    sessionContext: {}, instanceId: "inst-1", deployVersion,
  })

/** Nasce em v1 e "cai" dentro de `coletar`; o lock expira. */
async function nascerECair() {
  hang = true
  void run(V1, "v1")
  await vi.waitFor(() => expect(tools).toContain("coleta"))
  const state = JSON.parse(redis.store.get(PIPE)!) as PipelineState
  expect(state.status).toBe("in_progress")
  expect(state.current_step_id).toBe("coletar")
  redis.store.delete(LOCK)
  hang = false
  tools = []
}

beforeEach(() => {
  redis = new FakeRedis()
  tools = []
  hang  = false
  vi.restoreAllMocks()
})

describe("SFE-03 — nascimento fixa a versão", () => {
  it("grava o pin e a marca no pipeline_state", async () => {
    await nascerECair()
    const pin = JSON.parse(redis.store.get(PIN)!)
    expect(pin).toMatchObject({ skill_id: "skill_pin", deploy_version: "v1" })
    expect(pin.flow.entry).toBe("coletar")
    expect(JSON.parse(redis.store.get(PIPE)!).pinned_version).toBe("v1")
  })
})

describe("SFE-03 — retomada depois de um promote executa a versão do NASCIMENTO", () => {
  it("step renomeado na versão nova: retoma em v1 em vez de falhar", async () => {
    await nascerECair()
    const r = await run(V2_RENOMEADO, "v2")
    expect(r).toMatchObject({ outcome: "resolved", deploy_version: "v1" })
    expect(tools).toEqual(["coleta", "ferramenta_v1"])
  })

  it("step com o mesmo id e outro sentido: segue o caminho de v1, nunca o de v2", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {})
    await nascerECair()
    const r = await run(V2_OUTRO_SENTIDO, "v2")
    expect(r).toMatchObject({ outcome: "resolved", deploy_version: "v1" })
    expect(tools).not.toContain("ferramenta_v2")
    expect(tools).toContain("ferramenta_v1")
    // e diz que o pool já está noutra versão
    expect(info.mock.calls.flat().join(" ")).toMatch(/deploy=v1.*deploy=v2/)
  })

  it("pin AUSENTE numa sessão marcada: recusa nomeada, nunca a versão atual", async () => {
    await nascerECair()
    redis.store.delete(PIN)
    await expect(run(V2_OUTRO_SENTIDO, "v2")).rejects.toThrow(/SFE-03.*RECUSADA/)
    expect(tools).toEqual([])
    expect(JSON.parse(redis.store.get(PIPE)!).status).toBe("failed")
  })
})

describe("SFE-03 — transição e renovação", () => {
  it("pipeline anterior ao pin (sem marca): retoma na versão recebida, fixa-a e AVISA", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    await nascerECair()
    // Estado como o de antes do rollout: sem marca, sem pin, com uma transição feita.
    const s = JSON.parse(redis.store.get(PIPE)!)
    delete s.pinned_version
    s.transitions = [{ from_step: "x", to_step: "coletar", reason: "on_success", timestamp: new Date().toISOString() }]
    redis.store.set(PIPE, JSON.stringify(s))
    redis.store.delete(PIN)

    const r = await run(V2_OUTRO_SENTIDO, "v2")
    expect(r).toMatchObject({ outcome: "resolved", deploy_version: "v2" })
    expect(tools).toContain("ferramenta_v2")
    expect(warn.mock.calls.flat().join(" ")).toMatch(/anterior ao pin/)
  })

  it("nascer DE NOVO depois de concluir pega a versão nova (o pin não é eterno)", async () => {
    await run(V1, "v1")
    expect(JSON.parse(redis.store.get(PIPE)!).status).toBe("completed")
    tools = []
    const r = await run(V2_OUTRO_SENTIDO, "v2")
    expect(r).toMatchObject({ deploy_version: "v2" })
    expect(tools).toContain("ferramenta_v2")
    expect(JSON.parse(redis.store.get(PIN)!).deploy_version).toBe("v2")
  })

  it("save() renova o TTL do pin junto com o do estado", async () => {
    await nascerECair()
    redis.ttl.set(PIN, 5)
    await run(V2_RENOMEADO, "v2")
    expect(redis.ttl.get(PIN)).toBe(redis.ttl.get(PIPE))
    expect(redis.ttl.get(PIN)).toBeGreaterThan(5)
  })
})

describe("SFE-03 — UMA resposta para 'isto é retomada?'", () => {
  const base = { flow_id: "f", current_step_id: "s", started_at: "", updated_at: "", results: {}, retry_counters: {}, transitions: [] }
  it.each([
    ["in_progress", undefined, true],
    ["suspended",   { step_id: "s" }, true],
    ["suspended",   undefined, false],
    ["completed",   { step_id: "s" }, false],
    ["failed",      undefined, false],
  ])("status=%s resume=%o → %s", (status, rc, esperado) => {
    const st = { ...base, status } as unknown as PipelineState
    expect(SkillFlowEngine.isResumption(st, rc as never)).toBe(esperado)
  })
  it("sem estado → nascimento", () => {
    expect(SkillFlowEngine.isResumption(null)).toBe(false)
  })
})
