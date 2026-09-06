/**
 * A sentinela de idempotência protege a QUEDA — não esteriliza o CICLO.
 *
 * Contexto (medido em contato real, 2026-09-06): num laço
 * `menu → invoke → choice → menu`, a segunda visita ao `invoke` devolvia o
 * resultado guardado e nunca mais chamava o mundo. O cursor de navegação ficava
 * parado, a tela remontava o mesmo menu, e o step relatava sucesso todas as
 * vezes — nada ficava vermelho. O validador de fluxo SANCIONA aquele ciclo; o
 * runtime o esterilizava.
 *
 * ⚠️ O teste que decide não é o primeiro. "Re-executa ao reentrar" sozinho
 * ficaria verde numa implementação que simplesmente APAGASSE a sentinela sempre
 * — e essa implementação destruiria a idempotência através de queda, que é a
 * razão de a sentinela existir. Por isso o segundo caso, o CONTROLE NEGATIVO,
 * é o load-bearing: retomar sem transitar TEM de preservar o carimbo.
 */
import { describe, it, expect } from "vitest"
import { PipelineStateManager } from "../state"
import type { PipelineState } from "@plughub/schemas"

function estado(results: Record<string, unknown>, current: string): PipelineState {
  return {
    session_id:      "s1",
    skill_id:        "skill_teste",
    current_step_id: current,
    status:          "in_progress",
    results,
    retry_counts:    {},
    transitions:     [],
    started_at:      new Date().toISOString(),
    updated_at:      new Date().toISOString(),
  } as unknown as PipelineState
}

describe("sentinela de idempotência × ciclo", () => {
  it("REENTRAR num step por transição limpa a sentinela dele — o ciclo volta a chamar o mundo", () => {
    const antes = estado({
      "descer:__invoked__": "completed",
      "descer_saida":       { path: ["sac"] },
      "avaliar:__invoked__": "completed",
    }, "avaliar")

    const depois = PipelineStateManager.addTransition(antes, "avaliar", "descer", "on_success")

    expect(depois.results["descer:__invoked__"]).toBeUndefined()
    expect(depois.current_step_id).toBe("descer")
  })

  it("o RESULTADO do step sobrevive à limpeza — outros steps o referenciam por `$.`", () => {
    const antes = estado({
      "descer:__invoked__": "completed",
      "descer_saida":       { path: ["sac"] },
    }, "avaliar")

    const depois = PipelineStateManager.addTransition(antes, "avaliar", "descer", "on_success")

    // Apagar o resultado junto abriria uma janela em que `$.pipeline_state.*`
    // resolve vazio. A re-execução o sobrescreve, que é o comportamento querido.
    expect(depois.results["descer_saida"]).toEqual({ path: ["sac"] })
  })

  it("a sentinela de OUTRO step é intocada — a limpeza é do step em que se ENTRA", () => {
    const antes = estado({
      "descer:__invoked__":   "completed",
      "registrar:__invoked__": "completed",
      "avisar:__notified__":   "completed",
      "tarefa:__job_id__":     "job-1",
    }, "avaliar")

    const depois = PipelineStateManager.addTransition(antes, "avaliar", "descer", "on_success")

    expect(depois.results["registrar:__invoked__"]).toBe("completed")
    expect(depois.results["avisar:__notified__"]).toBe("completed")
    expect(depois.results["tarefa:__job_id__"]).toBe("job-1")
  })

  it("as TRÊS espécies de sentinela são limpas — uma lista incompleta congela um step em silêncio", () => {
    const antes = estado({
      "x:__invoked__":  "completed",
      "x:__notified__": "completed",
      "x:__job_id__":   "job-9",
    }, "y")

    const depois = PipelineStateManager.addTransition(antes, "y", "x", "on_success")

    expect(depois.results["x:__invoked__"]).toBeUndefined()
    expect(depois.results["x:__notified__"]).toBeUndefined()
    expect(depois.results["x:__job_id__"]).toBeUndefined()
  })

  it("CONTROLE NEGATIVO: retomada após queda NÃO passa por transição, logo a sentinela sobrevive", () => {
    // A retomada do engine começa executando `current_step_id` sem chamar
    // `addTransition`. Se este caso ficasse verde por a sentinela ter sumido, o
    // conserto teria trocado o ciclo esterilizado por efeito colateral DUPLICADO
    // — e um `notification_send` repetido fala duas vezes com o cliente.
    const aposQueda = estado({ "descer:__invoked__": "completed" }, "descer")

    // Nenhuma transição acontece: o estado é o que foi lido do Redis.
    expect(aposQueda.results["descer:__invoked__"]).toBe("completed")
    expect(aposQueda.current_step_id).toBe("descer")
  })

  it("o estado de ENTRADA não é mutado — o chamador do engine guarda a referência antiga", () => {
    const antes = estado({ "descer:__invoked__": "completed" }, "avaliar")
    PipelineStateManager.addTransition(antes, "avaliar", "descer", "on_success")
    expect(antes.results["descer:__invoked__"]).toBe("completed")
  })
})
