/**
 * steps/invoke.ts
 * Executor do step type: invoke
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Chama tool MCP diretamente e persiste resultado no pipeline_state.
 * Operação atômica — não encadeia múltiplas tools nem tem loop de raciocínio.
 *
 * Idempotência (sentinel de dois estágios):
 *   O step usa uma chave sentinela em pipeline_state.results para detectar
 *   reexecuções após crash do engine:
 *
 *   1. Antes da chamada MCP: grava sentinel = "dispatched" → saveState
 *   2. Após resultado disponível: grava sentinel = "completed" + output → saveState
 *
 *   Na retomada:
 *   - sentinel = "completed" + resultado presente → retorna resultado salvo
 *     sem re-chamar o MCP. Fecha a janela principal de crash (entre o MCP
 *     retornar e a transição do engine ser persistida).
 *   - sentinel = "dispatched" sem resultado → MCP foi chamado mas o resultado
 *     não foi persistido; re-executa a chamada. Semântica at-least-once para
 *     tools não idempotentes (janela de crash residual muito curta).
 *   - sem sentinel → primeira execução normal.
 */

import { JSONPath } from "jsonpath-plus"
import type { InvokeStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeInvoke(
  step: InvokeStep,
  ctx:  StepContext
): Promise<StepResult> {
  const outputKey   = step.output_as
  const sentinelKey = `${step.id}:__invoked__`

  // ── Idempotência: checar se a chamada MCP já completou com sucesso ─────────
  if (ctx.state.results[sentinelKey] === "completed") {
    // Resultado já gravado em uma execução anterior — retornar sem re-chamar MCP
    const storedResult = outputKey !== undefined ? ctx.state.results[outputKey] : undefined
    return {
      next_step_id:      step.on_success,
      ...(outputKey !== undefined && { output_as: outputKey }),
      output_value:      storedResult,
      transition_reason: "on_success",
    }
  }

  // Resolver inputs — valores literais ou referências JSONPath
  const resolvedInput = resolveInput(step.input ?? {}, ctx)

  // step.target (external MCP) and step.tool (native plughub) are both optional;
  // at least one must be present — validated at runtime per spec 4.7.
  const toolName  = step.target?.tool        ?? step.tool  ?? ""
  const mcpServer = step.target?.mcp_server  ?? "mcp-server-plughub"

  // ── Fase 1: gravar sentinel "dispatched" antes da chamada MCP ────────────
  // Permite distinguir "nunca chamado" de "chamado mas sem resultado" na retomada.
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [sentinelKey]: "dispatched" },
  }
  await ctx.saveState(ctx.state)

  try {
    const result = await ctx.mcpCall(toolName, resolvedInput, mcpServer)

    // ── Fase 2: gravar resultado + sentinel "completed" ───────────────────
    // Fecha a janela de crash entre o MCP retornar e a transição ser persistida
    // pelo loop principal do engine. Na retomada, o sentinel "completed" garante
    // que a chamada MCP não será re-executada.
    ctx.state = {
      ...ctx.state,
      results: {
        ...ctx.state.results,
        [sentinelKey]: "completed",
        ...(outputKey !== undefined && { [outputKey]: result }),
      },
    }
    await ctx.saveState(ctx.state)

    return {
      next_step_id:      step.on_success,
      ...(outputKey !== undefined && { output_as: outputKey }),
      output_value:      result,
      transition_reason: "on_success",
    }
  } catch (error) {
    // Sentinel permanece como "dispatched" — na retomada via catch/retry,
    // o step será re-executado (at-least-once para a janela de crash residual).
    return {
      next_step_id:      step.on_failure,
      ...(outputKey !== undefined && { output_as: outputKey }),
      output_value: {
        error:      error instanceof Error ? error.message : "invoke_failed",
        mcp_server: mcpServer,
        tool:       toolName,
      },
      transition_reason: "on_failure",
    }
  }
}

/**
 * Resolve referências JSONPath nos valores de input.
 * Valores que começam com "$." são tratados como JSONPath.
 * Valores literais são passados diretamente.
 */
function resolveInput(
  input:   Record<string, string | number | boolean>,
  ctx:     StepContext
): Record<string, unknown> {
  const evalContext = {
    pipeline_state: ctx.state.results,
    session:        ctx.sessionContext,
  }

  const resolved: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === "string" && value.startsWith("$.")) {
      resolved[key] = JSONPath({ path: value, json: evalContext as object, wrap: false })
    } else {
      resolved[key] = value
    }
  }
  return resolved
}
