/**
 * WCH-02 (Console recarregado) — a projeção do stream para o histórico do Console.
 *
 * Proposições, cada uma com o controle ao lado:
 *   - chamada começou e não terminou → ativa; terminou → não; nunca houve → não; contato fechou → não;
 *   - a nota do supervisor entra (nas duas formas de visibilidade que o stream carrega) e a fala de
 *     outros autores NÃO (já está na lista — entraria duplicada);
 *   - nota dirigida (lista de participantes) fica de fora;
 *   - a intercalação pela hora respeita a ordem, e hora ilegível não inventa posição.
 */
import { describe, it, expect } from "vitest"
import { projectStreamForConsole, mergeByTimestamp } from "../lib/console-history"
import type { RawStreamEntry } from "../lib/console-history"

let n = 0
const e = (f: Record<string, string>): RawStreamEntry =>
  [`${++n}-0`, Object.entries(f).flat()]

const call = (state: string) => e({ type: "media.call", state, payload: JSON.stringify({ state }) })
const nota = (text: string, visibility = "agents_only", ts = "2026-09-22T10:00:05+00:00") => e({
  type: "message", visibility,
  author: JSON.stringify({ role: "supervisor", participant_id: "sup-1" }),
  payload: JSON.stringify({ message_id: `m-${text}`, content: { type: "text", text } }),
  event_id: `ev-${text}`, timestamp: ts,
})

describe("WCH-02 — projectStreamForConsole", () => {
  it("chamada iniciada e não encerrada → ativa", () => {
    expect(projectStreamForConsole([call("started")]).callActive).toBe(true)
  })

  it("controles: encerrada, nunca houve, contato fechado → inativa", () => {
    expect(projectStreamForConsole([call("started"), call("ended")]).callActive).toBe(false)
    expect(projectStreamForConsole([]).callActive).toBe(false)
    expect(projectStreamForConsole([call("started"), e({ type: "session_closed" })]).callActive).toBe(false)
  })

  it("segunda chamada no mesmo contato: vale a ÚLTIMA", () => {
    expect(projectStreamForConsole([call("started"), call("ended"), call("started")]).callActive).toBe(true)
  })

  it("state só no payload (sem campo plano) também é lido", () => {
    const só = e({ type: "media.call", payload: JSON.stringify({ state: "started" }) })
    expect(projectStreamForConsole([só]).callActive).toBe(true)
  })

  it("a nota do supervisor entra, com o id do payload e a visibilidade, nas duas formas", () => {
    const r = projectStreamForConsole([nota("a"), nota("b", JSON.stringify("all"))])
    expect(r.supervisorNotes).toEqual([
      { id: "m-a", author: "supervisor", text: "a", timestamp: "2026-09-22T10:00:05+00:00", visibility: "agents_only" },
      { id: "m-b", author: "supervisor", text: "b", timestamp: "2026-09-22T10:00:05+00:00", visibility: "all" },
    ])
  })

  it("controle: fala de agente e de cliente NÃO entra (já está na lista)", () => {
    const agente = e({ type: "message", author_role: "primary", visibility: JSON.stringify("all"),
                       payload: JSON.stringify({ content: { type: "text", text: "oi" } }) })
    const cliente = e({ type: "message", author: JSON.stringify({ role: "customer" }),
                        payload: JSON.stringify({ content: { type: "text", text: "olá" } }) })
    expect(projectStreamForConsole([agente, cliente]).supervisorNotes).toEqual([])
  })

  it("nota dirigida (lista de participantes) fica de fora", () => {
    expect(projectStreamForConsole([nota("x", JSON.stringify(["part_1"]))]).supervisorNotes).toEqual([])
  })

  it("entrada malformada não derruba a projeção", () => {
    const lixo = e({ type: "message", author: "{", payload: "não é json" })
    expect(projectStreamForConsole([lixo, call("started")])).toEqual({ callActive: true, supervisorNotes: [] })
  })
})

describe("WCH-02 — mergeByTimestamp", () => {
  it("intercala pela hora, e empate mantém a ordem de chegada", () => {
    const base = [{ id: "1", timestamp: "2026-09-22T10:00:00Z" }, { id: "3", timestamp: "2026-09-22T10:00:10Z" }]
    const extra = [{ id: "2", timestamp: "2026-09-22T10:00:05+00:00" }, { id: "4", timestamp: "2026-09-22T10:00:10Z" }]
    expect(mergeByTimestamp(base, extra).map(m => m.id)).toEqual(["1", "2", "3", "4"])
  })

  it("hora ilegível: acrescenta ao fim em vez de inventar posição", () => {
    const base = [{ id: "1", timestamp: "" }]
    expect(mergeByTimestamp(base, [{ id: "2", timestamp: "2026-09-22T10:00:05Z" }]).map(m => m.id)).toEqual(["1", "2"])
  })
})
