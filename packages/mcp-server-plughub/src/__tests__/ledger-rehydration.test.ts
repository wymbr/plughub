/**
 * ledger-rehydration.test.ts — PUL-05: o item em posse volta à tela do DONO pelo
 * ledger, e só ele.
 *
 * O caso que motivou: `0596f383` (operator@) em posse, token vivo, formulário
 * suspenso — invisível no Console porque a única fonte da reconexão era uma chave
 * de 300 s por pool. Os casos de descarte são os de 2026-09-11 também: `5120fe90` e
 * `4841c60d`, em posse no ledger mas com a sessão fechada e o token cancelado
 * (PUL-06). Reentregá-los traria de volta uma tela sem saída.
 */

import { describe, it, expect } from "vitest"
import { decideLedgerRehydration, type LedgerCandidate } from "../lib/ledger-rehydration"

const ME     = "human-25d9df99"
const THEIRS = "human-u-admin"

function cand(over: Partial<LedgerCandidate["item"]> = {}, extra: Partial<LedgerCandidate> = {}): LedgerCandidate {
  return {
    item: {
      session_id:       "0596f383",
      queue_session_id: "0596f383",
      pool_id:          "retencao_humano-int",
      state:            "claimed",
      claimed_by:       ME,
      claimed_at:       "2026-09-11T14:29:52.310Z",
      ...over,
    },
    tokenAlive:   true,
    closedMarker: null,
    ...extra,
  }
}

const NONE = new Set<string>()

describe("decideLedgerRehydration — entrega", () => {
  it("item em posse MINHA, token vivo, sessão aberta → entrega (o caso do 0596f383)", () => {
    const [d] = decideLedgerRehydration([cand()], ME, NONE)
    expect(d!.deliver).toBe(true)
    expect(d!.reason).toBe("held_by_me")
  })

  it("o evento tem a forma que o Console já consome, com o CLAIM como âncora do relógio", () => {
    const [d] = decideLedgerRehydration([cand()], ME, NONE)
    expect(d!.event).toEqual({
      type:        "conversation.assigned",
      session_id:  "0596f383",
      pool_id:     "retencao_humano-int",
      instance_id: ME,
      assigned_at: "2026-09-11T14:29:52.310Z",
      source:      "work_ledger",
    })
  })

  it("usa o id que está NA FILA, não a chave do ledger", () => {
    const [d] = decideLedgerRehydration([cand({ session_id: "ledger-key", queue_session_id: "na-fila" })], ME, NONE)
    expect(d!.event!.session_id).toBe("na-fila")
  })

  it("sem claimed_at, OMITE assigned_at (o Console degrada barulhento) — nunca inventa um", () => {
    const [d] = decideLedgerRehydration([cand({ claimed_at: null })], ME, NONE)
    expect(d!.deliver).toBe(true)
    expect(d!.event).not.toHaveProperty("assigned_at")
  })

  it("token NÃO conferido entrega com motivo próprio — desconhecido não é morto", () => {
    const [d] = decideLedgerRehydration([cand({}, { tokenAlive: null })], ME, NONE)
    expect(d!.deliver).toBe(true)
    expect(d!.reason).toBe("held_by_me:token_unverified")
  })
})

describe("decideLedgerRehydration — descarte, sempre com motivo", () => {
  it("⚠️ sessão FECHADA → descarta (5120fe90 / 4841c60d)", () => {
    const [d] = decideLedgerRehydration([cand({}, { closedMarker: "agent_done", tokenAlive: false })], ME, NONE)
    expect(d!.deliver).toBe(false)
    expect(d!.reason).toBe("session_closed:agent_done")
  })

  it("⚠️ token cancelado, mesmo sem marcador de fechamento → descarta", () => {
    const [d] = decideLedgerRehydration([cand({}, { tokenAlive: false })], ME, NONE)
    expect(d!.deliver).toBe(false)
    expect(d!.reason).toBe("token_gone")
  })

  it("item de OUTRO agente → descarta", () => {
    const [d] = decideLedgerRehydration([cand({ claimed_by: THEIRS })], ME, NONE)
    expect(d!.deliver).toBe(false)
    expect(d!.reason).toBe(`held_by_other:${THEIRS}`)
  })

  it("item na FILA (unclaimed) → descarta: ele mora na inbox, não em CONTACTS", () => {
    const [d] = decideLedgerRehydration([cand({ state: "unclaimed", claimed_by: null })], ME, NONE)
    expect(d!.deliver).toBe(false)
    expect(d!.reason).toBe("not_claimed:unclaimed")
  })

  it("já entregue pela chave por pool → não duplica", () => {
    const [d] = decideLedgerRehydration([cand()], ME, new Set(["0596f383"]))
    expect(d!.deliver).toBe(false)
    expect(d!.reason).toBe("already_delivered")
  })

  it("cliente sem identidade → nada é reentregue", () => {
    const ds = decideLedgerRehydration([cand()], "", NONE)
    expect(ds.every(d => !d.deliver)).toBe(true)
    expect(ds[0]!.reason).toBe("legacy_client_no_identity")
  })
})

describe("decideLedgerRehydration — população", () => {
  it("os três de 2026-09-11 juntos: só o vivo volta", () => {
    const ds = decideLedgerRehydration([
      cand({ session_id: "5120fe90", queue_session_id: "5120fe90" }, { closedMarker: "agent_done", tokenAlive: false }),
      cand({ session_id: "4841c60d", queue_session_id: "4841c60d" }, { closedMarker: "agent_done", tokenAlive: false }),
      cand(),
    ], ME, NONE)
    expect(ds.filter(d => d.deliver).map(d => d.session_id)).toEqual(["0596f383"])
    expect(ds.every(d => d.reason.length > 0)).toBe(true)
  })
})
