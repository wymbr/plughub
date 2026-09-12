/**
 * form-task-close.test.ts — PUL-07: fechar tarefa de formulário pendente devolve
 * à fila; nunca fecha.
 *
 * Os dois casos que motivaram são de 2026-09-11: wrap-ups reivindicados cujo
 * formulário abriu vazio foram ENCERRADOS pela barra de atendimento, e o
 * fechamento apagou o `resume_token`. A tabulação dos dois se perdeu.
 */

import { describe, it, expect } from "vitest"
import { decideFormTaskClose, type FormTaskState } from "../lib/form-task-close"

const ME     = "human-25d9df99"
const THEIRS = "human-u-admin"

const item = (over: Partial<FormTaskState> = {}): FormTaskState => ({
  ledgerPresent:  true,
  tokenAlive:     true,
  holderInstance: ME,
  ...over,
})

describe("decideFormTaskClose", () => {
  it("contato NORMAL (sem item no ledger) fecha como sempre", () => {
    const v = decideFormTaskClose(item({ ledgerPresent: false, holderInstance: null }), ME)
    expect(v.action).toBe("close")
    expect(v.reason).toBe("no_work_item")
  })

  it("⚠️ tarefa VIVA detida por mim vira DEVOLUÇÃO — o caso do 5120fe90", () => {
    const v = decideFormTaskClose(item(), ME)
    expect(v.action).toBe("return_to_queue")
    expect(v.reason).toBe("form_task_pending")
  })

  it("token NÃO conferido conta como vivo — desconhecido não é morto", () => {
    const v = decideFormTaskClose(item({ tokenAlive: null }), ME)
    expect(v.action).toBe("return_to_queue")
    expect(v.reason).toBe("form_task_pending:token_unverified")
  })

  it("tarefa já MORTA (token cancelado) fecha — não há o que preservar", () => {
    const v = decideFormTaskClose(item({ tokenAlive: false }), ME)
    expect(v.action).toBe("close")
    expect(v.reason).toBe("token_gone")
  })

  it("tarefa de OUTRO agente é RECUSADA — o defeito com outro dono", () => {
    const v = decideFormTaskClose(item({ holderInstance: THEIRS }), ME)
    expect(v.action).toBe("refuse")
    expect(v.reason).toBe(`held_by_other:${THEIRS}`)
  })

  it("tarefa viva SEM dono é recusada — devolver exige posse", () => {
    const v = decideFormTaskClose(item({ holderInstance: null }), ME)
    expect(v.action).toBe("refuse")
    expect(v.reason).toBe("no_holder")
  })

  it("chamador sem identidade é recusado — sem instância não há release", () => {
    const v = decideFormTaskClose(item(), "")
    expect(v.action).toBe("refuse")
    expect(v.reason).toBe("caller_unknown")
  })

  it("todo veredicto nomeia o motivo, inclusive o caminho normal", () => {
    const casos: [FormTaskState, string][] = [
      [item({ ledgerPresent: false }), ME],
      [item(), ME],
      [item({ tokenAlive: false }), ME],
      [item({ holderInstance: THEIRS }), ME],
    ]
    for (const [st, caller] of casos) expect(decideFormTaskClose(st, caller).reason.length).toBeGreaterThan(0)
  })
})
