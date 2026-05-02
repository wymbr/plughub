/**
 * steps/catch.ts
 * Executor do step type: catch
 * Spec: PlugHub v24.0 seção 4.7 + 9.5i
 *
 * Executa internamente — sem delegação A2A.
 * Estratégias em sequência: retry → fallback → on_failure.
 * Contadores de retry persistidos no pipeline_state a cada tentativa.
 */

import type { CatchStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { PipelineStateManager } from "../state"

const RETRY_DELAY_DEFAULT_MS = 1_000

export async function executeCatch(
  step: CatchStep,
  ctx:  StepContext
): Promise<StepResult> {

  // Lê o resultado de falha do step referenciado
  const failedResult = ctx.state.results[step.error_context]

  for (const strategy of step.strategies) {
    if (strategy.type === "retry") {
      const retryKey    = `${step.id}.retry`
      const attempts    = ctx.state.retry_counters[retryKey] ?? 0
      const maxAttempts = strategy.max_attempts

      if (attempts < maxAttempts) {
        // Incrementar contador e persistir antes de tentar
        ctx.state = PipelineStateManager.incrementRetry(ctx.state, retryKey)
        await ctx.saveState(ctx.state)

        // Aguardar delay
        await sleep(strategy.delay_ms ?? RETRY_DELAY_DEFAULT_MS)

        // Reexecutar o step que falhou (delegar novamente)
        const retryResult = await ctx.retryStep(step.error_context)

        if (retryResult.transition_reason === "on_success") {
          return retryResult
        }
        // Falhou novamente — continuar para próxima strategy
        if (strategy.on_exhausted) {
          // ir para fallback declarado
          continue
        }
      }
      // Tentativas esgotadas — continuar para próxima strategy
    }

    if (strategy.type === "fallback") {
      // Executar agente alternativo com target diferente
      const fallbackResult = await ctx.executeFallback(strategy)

      if (fallbackResult.transition_reason === "on_success") {
        return {
          next_step_id:      strategy.on_success,
          output_as:         step.id,
          output_value:      fallbackResult.output_value,
          transition_reason: "on_success",
        }
      }
      // Fallback falhou — continuar para próxima strategy
    }
  }

  // Todas as strategies esgotadas — ir para on_failure
  return {
    next_step_id:      step.on_failure,
    output_as:         step.id,
    output_value:      { error: "all_strategies_exhausted", failed_step: step.error_context, failed_result: failedResult },
    transition_reason: "on_failure",
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
