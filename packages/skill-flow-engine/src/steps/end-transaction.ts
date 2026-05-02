/**
 * steps/end-transaction.ts
 * Executor do step type: end_transaction
 *
 * Fecha o bloco atômico no caminho de sucesso:
 * 1. Escreve o status da operação em pipeline_state.results[result_as]
 * 2. Limpa o masked_scope da memória (valores sensíveis são descartados)
 * 3. Limpa transactionOnFailure do contexto
 *
 * Invariantes:
 * - end_transaction é exclusivamente o caminho de sucesso
 * - rollback é sempre implícito e automático (via on_failure do begin_transaction)
 * - após end_transaction, @masked.* retorna string vazia (scope foi limpo)
 *
 * Spec: docs/guias/masked-input.md
 */

import type { EndTransactionStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeEndTransaction(
  step: EndTransactionStep,
  ctx:  StepContext,
): Promise<StepResult> {
  // Determinar quais campos foram coletados nesta transação
  const fieldsCollected = Object.keys(ctx.maskedScope ?? {})

  // Status a persistir em pipeline_state (nunca os valores — apenas metadados)
  const transactionStatus = {
    status:          "ok" as const,
    fields_collected: fieldsCollected,
    completed_at:    new Date().toISOString(),
  }

  // Limpar masked_scope — valores sensíveis são descartados da memória
  ctx.maskedScope          = {}
  ctx.transactionOnFailure = null  // null = fora de bloco de transação

  const result: import("../executor").StepResult = {
    next_step_id:      step.on_success ?? "__transaction_end__",
    transition_reason: "on_success",
  }
  if (step.result_as) {
    result.output_as    = step.result_as
    result.output_value = transactionStatus
  }
  return result
}
