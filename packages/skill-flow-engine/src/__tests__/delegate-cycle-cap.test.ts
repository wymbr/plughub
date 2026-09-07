/**
 * delegate-cycle-cap.test.ts — RET-04: o teto do ciclo de delegação.
 *
 * ⚠️ **O caso load-bearing é o da COLISÃO** (`o contador sobrevive à limpeza de
 * sentinelas`). As duas metades desta tarefa se anulariam em silêncio se o
 * contador usasse o padrão `{id}:__x__`: aquela família é limpa ao reentrar no
 * step — que é justamente o conserto que faz o ciclo girar —, então o teto
 * zeraria a cada volta e nunca dispararia. O laço seria infinito com um campo
 * `max_iterations` declarado ao lado, dando a impressão contrária.
 */

import { describe, it, expect, vi } from "vitest"
import { executeDelegate } from "../steps/delegate"
import { PipelineStateManager } from "../state"
import type { DelegateStep } from "@plughub/schemas"
import type { PipelineState } from "@plughub/schemas"

function estado(results: Record<string, unknown>): PipelineState {
  return {
    session_id: "s1", skill_id: "sk", current_step_id: "delegar",
    status: "in_progress", results, retry_counts: {}, transitions: [],
    started_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  } as unknown as PipelineState
}

const passo = (extra: Partial<DelegateStep> = {}): DelegateStep => ({
  type: "delegate", id: "delegar", pool: "sac_ia",
  timeout_hours: 4,
  on_resume:  { next: "continuar" },
  on_timeout: { next: "escalar_humano" },
  ...extra,
} as DelegateStep)

/** ctx mínimo: o teto decide ANTES de qualquer efeito colateral. */
function ctx(state: PipelineState) {
  const salvos: PipelineState[] = []
  return {
    ctx: {
      state,
      sessionId: "s1",
      saveState: vi.fn(async (s: PipelineState) => { salvos.push(s) }),
      resumeContext: undefined,
    } as never,
    salvos,
  }
}

describe("delegate — teto do ciclo (RET-04)", () => {
  it("sem `max_iterations` não conta nada: o comportamento de sempre", async () => {
    const { ctx: c, salvos } = ctx(estado({}))
    // Sem teto declarado, o step nem toca no contador — a esmagadora maioria
    // dos `delegate` do parque não cicla, e não deve pagar por este mecanismo.
    await executeDelegate(passo(), c).catch(() => undefined)
    expect(salvos.some(s => "_delegate_iterations_delegar" in s.results)).toBe(false)
  })

  it("ao atingir o teto, sai por `on_max_iterations` e ZERA o contador", async () => {
    const { ctx: c, salvos } = ctx(estado({ "_delegate_iterations_delegar": 3 }))
    const r = await executeDelegate(
      passo({ max_iterations: 3, on_max_iterations: "encerrar_ciclo" }), c,
    )
    expect(r.next_step_id).toBe("encerrar_ciclo")
    // Zerar é o que permite a uma NOVA navegação no mesmo contato contar do
    // começo, em vez de nascer com o orçamento do atendimento anterior gasto.
    expect(salvos.at(-1)!.results["_delegate_iterations_delegar"]).toBe(0)
  })

  it("sem `on_max_iterations`, cai no destino do timeout — nunca em lugar nenhum", async () => {
    const { ctx: c } = ctx(estado({ "_delegate_iterations_delegar": 2 }))
    const r = await executeDelegate(passo({ max_iterations: 2 }), c)
    expect(r.next_step_id).toBe("escalar_humano")
  })

  it("abaixo do teto, INCREMENTA antes de suspender", async () => {
    const { ctx: c, salvos } = ctx(estado({ "_delegate_iterations_delegar": 1 }))
    // Falha adiante (sem mcpCall) é esperada: o que importa é que o incremento
    // já aconteceu ANTES — contar no retorno deixaria de fora o especialista que
    // nunca devolve, e o laço poderia girar pelo `on_timeout`.
    await executeDelegate(passo({ max_iterations: 5 }), c).catch(() => undefined)
    expect(salvos[0]!.results["_delegate_iterations_delegar"]).toBe(2)
  })

  // ── O CASO QUE CARREGA PESO ────────────────────────────────────────────────
  it("o contador SOBREVIVE à limpeza de sentinelas — senão as duas metades se anulam", () => {
    const antes = estado({
      "_delegate_iterations_delegar": 2,
      "delegar:__delegated__":        "delegated",
      "delegar:__resume_decision__":  "input",
    })
    const depois = PipelineStateManager.addTransition(
      antes, "nivel_continuacao", "delegar", "on_success",
    )
    // As sentinelas somem (é o que faz o ciclo girar)…
    expect(depois.results["delegar:__delegated__"]).toBeUndefined()
    expect(depois.results["delegar:__resume_decision__"]).toBeUndefined()
    // …e o CONTADOR fica. Se ele usasse o padrão `{id}:__x__`, seria zerado
    // junto e o teto nunca dispararia: laço infinito com `max_iterations`
    // declarado ao lado, dando a impressão contrária.
    expect(depois.results["_delegate_iterations_delegar"]).toBe(2)
  })
})
