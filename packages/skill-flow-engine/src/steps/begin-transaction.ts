/**
 * steps/begin-transaction.ts
 * Executor do step type: begin_transaction
 *
 * Abre um bloco atômico de captura de dados sensíveis.
 * Inicializa o masked_scope no contexto de execução e registra o on_failure
 * da transação para uso posterior pelo engine quando ocorrer falha interna.
 *
 * Invariantes:
 * - O masked_scope nunca é escrito em pipeline_state, Redis ou stream
 * - Falha em qualquer step dentro do bloco → engine executa on_failure
 * - retry nunca reutiliza valores do masked_scope
 *
 * Spec: docs/guias/masked-input.md
 */

import type { BeginTransactionStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeBeginTransaction(
  step: BeginTransactionStep,
  ctx:  StepContext,
): Promise<StepResult> {
  // Inicializa (ou limpa) o masked_scope no contexto de execução.
  // Qualquer tentativa anterior é descartada — cada begin_transaction
  // começa com um scope limpo.
  ctx.maskedScope = {}

  // Limpa sentinelas de idempotência de invocações anteriores neste bloco.
  // Sentinelas `{stepId}:__invoked__` ficam em pipeline_state.results após
  // um invoke ter completado. Se o fluxo faz retry da transação (ex: PIN inválido
  // → pin_invalido → tx_inicio), precisamos garantir que o invoke será
  // re-executado com o novo valor mascarado — não retornar o resultado cacheado.
  const clearedResults = Object.fromEntries(
    Object.entries(ctx.state.results).filter(([k]) => !k.endsWith(":__invoked__")),
  )
  ctx.state = { ...ctx.state, results: clearedResults }

  // Registra o on_failure da transação no contexto para que o engine
  // saiba para onde fazer rewind quando ocorrer falha dentro do bloco.
  ctx.transactionOnFailure = step.on_failure  // string — nunca null aqui

  // begin_transaction é um step de controle — avança imediatamente para
  // o próximo step na declaração do fluxo (determinado pelo engine via
  // índice de posição no array, não via on_success explícito).
  return {
    next_step_id:      "__transaction_begin__",
    transition_reason: "on_success",
  }
}
