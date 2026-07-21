/**
 * steps/loop.ts
 * Executor do step type: loop (dialog primitive Fatia 2 — N perguntas sequenciais).
 *
 * Caminha uma sub-flow (body) sobre um array, uma iteração por elemento. Modelado
 * no padrão cíclico do `receive` (contador em pipeline_state), mas em vez de
 * bloquear num evento de stream:
 *   - expõe o elemento ATUAL num path FIXO (item_as) — sem índice variável em ref;
 *   - transita para o body (que faz 1 turno de I/O, tipicamente um menu, e volta
 *     para este loop via on_success: <loop id>);
 *   - acumula a resposta do body (collect) num array (results_as);
 *   - ao esgotar o array, transita para on_complete.
 *
 * Estado interno em pipeline_state.results (não colide com item_as/results_as):
 *   _loop_idx_{id}      — próximo índice a renderizar (default 0)
 *   _loop_started_{id}  — o body já rodou ao menos uma vez
 *   _loop_acc_{id}      — acumulador interno
 *
 * Guarda de ciclo: o menu do body bloqueia por input a cada iteração (validateFlow),
 * e max_iterations é o teto duro. A cada iteração limpamos os sentinels
 * `:__notified__` (como o receive) para que notify no body re-execute.
 */

import type { LoopStep } from "@plughub/schemas"
import { evaluateAskWhen } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { resolveInputValue } from "../interpolate"

interface CollectedEntry {
  value:       unknown
  output_key?: unknown
  metric?:     unknown
}

export async function executeLoop(
  step: LoopStep,
  ctx:  StepContext,
): Promise<StepResult> {
  const idxKey     = `_loop_idx_${step.id}`
  const startedKey = `_loop_started_${step.id}`
  const accKey     = `_loop_acc_${step.id}`

  const results = ctx.state.results ?? {}
  const idx     = (results[idxKey]     as number | undefined)  ?? 0
  const started = (results[startedKey] as boolean | undefined) ?? false
  let   acc     = (results[accKey]     as CollectedEntry[] | undefined) ?? []

  // Resolve o array a iterar.
  const resolved = await resolveInputValue(step.over, ctx, ctx.contextStore)
  const items    = Array.isArray(resolved) ? resolved : []
  const n        = items.length
  const cap      = step.max_iterations ?? 100

  // ── Coleta a resposta do body da iteração ANTERIOR (re-entrada) ──────────────
  // No re-entry, pipeline_state.<item_as> ainda contém o elemento anterior (que o
  // body acabou de responder), então pareamos output_key/metric dele com o valor.
  if (started && step.collect) {
    const prevItem = results[step.item_as] as Record<string, unknown> | undefined
    const entry: CollectedEntry = { value: results[step.collect] }
    if (prevItem) {
      if (prevItem["output_key"] !== undefined) entry.output_key = prevItem["output_key"]
      const cap0 = prevItem["capture"] as Record<string, unknown> | undefined
      if (cap0 && cap0["metric"] !== undefined) entry.metric = cap0["metric"]
    }
    acc = [...acc, entry]
  }

  // ── Skip-logic condicional (ask_when) ────────────────────────────────────────
  // Antes de expor o elemento atual, avança sobre os itens cuja guarda `ask_when`
  // for FALSA (declarativa, ADR adr-dialog-conditional-skip-logic). Item pulado
  // não é exposto nem coletado (fica NA na composição, re-normalizado). As
  // respostas até aqui vêm do acumulador (output_key → value).
  const answered: Record<string, unknown> = {}
  for (const e of acc) {
    if (e.output_key !== undefined && e.output_key !== null) answered[String(e.output_key)] = e.value
  }
  let curIdx = idx
  while (curIdx < n && curIdx < cap) {
    const it = items[curIdx] as { ask_when?: unknown } | undefined
    const guard = it && typeof it === "object" ? it.ask_when : undefined
    if (evaluateAskWhen(guard as never, answered)) break
    curIdx++
  }

  // ── Terminação: array esgotado ou teto atingido ─────────────────────────────
  if (curIdx >= n || curIdx >= cap) {
    const cleaned: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(results)) {
      if (k === idxKey || k === startedKey || k === accKey) continue
      cleaned[k] = v
    }
    cleaned[step.results_as] = acc
    await ctx.saveState({ ...ctx.state, results: cleaned })
    return { next_step_id: step.on_complete, transition_reason: "on_success" }
  }

  // ── Expõe o elemento atual, avança, entra no body ───────────────────────────
  // Limpa os sentinels de conclusão de step a cada volta, para que o body
  // RE-EXECUTE por iteração (senão o step é pulado a partir da 2ª volta):
  //   :__notified__ — notify (já era limpo);
  //   :__invoked__  — invoke: o sentinel "completed" faz o invoke retornar o
  //                   resultado cacheado sem re-chamar a tool. Sem limpar, um
  //                   invoke no body do loop só executa na 1ª iteração (bug:
  //                   drena N, mas só contabiliza 1). Cada iteração é uma chamada
  //                   lógica distinta (item diferente) → re-executar é o correto.
  const next: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(results)) {
    if (k.endsWith(":__notified__")) continue
    if (k.endsWith(":__invoked__"))  continue
    next[k] = v
  }
  next[step.item_as] = items[curIdx]
  if (step.index_as) next[step.index_as] = curIdx
  next[idxKey]     = curIdx + 1
  next[startedKey] = true
  next[accKey]     = acc
  await ctx.saveState({ ...ctx.state, results: next })

  return { next_step_id: step.body, transition_reason: "on_success" }
}
