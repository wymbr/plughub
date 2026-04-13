/**
 * steps/reason.ts
 * Executor do step type: reason
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Invoca o AI Gateway com prompt declarado e output_schema.
 * O AI Gateway valida o retorno contra o schema antes de retornar.
 * max_format_retries: número de tentativas de correção de formato (default: 1).
 */

import { JSONPath } from "jsonpath-plus"
import { z }        from "zod"
import type { ReasonStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeReason(
  step: ReasonStep,
  ctx:  StepContext
): Promise<StepResult> {
  const resolvedInput = resolveInput(step.input ?? {}, ctx)
  const maxRetries    = step.max_format_retries ?? 1

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const result = await ctx.aiGatewayCall({
        prompt_id:     step.prompt_id,
        input:         resolvedInput,
        output_schema: step.output_schema,
        session_id:    ctx.sessionId,
        attempt,
      })

      // Validar retorno contra output_schema
      const validated = validateAgainstSchema(result, step.output_schema)
      if (validated.success) {
        return {
          next_step_id:      step.on_success,
          output_as:         step.output_as,
          output_value:      validated.data,
          transition_reason: "on_success",
        }
      }

      // Formato inválido — tentar novamente se há retries disponíveis
      if (attempt < maxRetries) continue

      // Esgotou retries — on_failure
      return {
        next_step_id:      step.on_failure,
        output_as:         step.output_as,
        output_value:      { error: "invalid_output_schema", details: validated.error },
        transition_reason: "on_failure",
      }

    } catch (error) {
      if (attempt < maxRetries) continue
      return {
        next_step_id:      step.on_failure,
        output_as:         step.output_as,
        output_value:      { error: error instanceof Error ? error.message : "ai_gateway_error" },
        transition_reason: "on_failure",
      }
    }
  }

  return {
    next_step_id:      step.on_failure,
    output_as:         step.output_as,
    output_value:      { error: "max_retries_exceeded" },
    transition_reason: "on_failure",
  }
}

/** Valida o retorno do AI Gateway contra o output_schema declarado no step */
function validateAgainstSchema(
  data:   unknown,
  schema: ReasonStep["output_schema"]
): { success: true; data: unknown } | { success: false; error: string } {
  if (typeof data !== "object" || data === null) {
    return { success: false, error: "response is not an object" }
  }

  type OutputFieldDef = ReasonStep["output_schema"][string]
  const obj = data as Record<string, unknown>
  for (const [field, def] of Object.entries(schema) as Array<[string, OutputFieldDef]>) {
    const value = obj[field]

    if (value === undefined) {
      if (def.required !== false) {
        return { success: false, error: `required field missing: ${field}` }
      }
      continue
    }

    // Validar tipo
    if (def.type === "string" && typeof value !== "string") {
      return { success: false, error: `field ${field}: expected string` }
    }
    if (def.type === "number" && typeof value !== "number") {
      return { success: false, error: `field ${field}: expected number` }
    }
    if (def.type === "boolean" && typeof value !== "boolean") {
      return { success: false, error: `field ${field}: expected boolean` }
    }

    // Validar enum
    if (def.enum && !def.enum.includes(String(value))) {
      return { success: false, error: `field ${field}: "${value}" not in enum [${def.enum.join(", ")}]` }
    }

    // Validar range numérico
    if (def.type === "number" && typeof value === "number") {
      if (def.minimum !== undefined && value < def.minimum) {
        return { success: false, error: `field ${field}: ${value} < minimum ${def.minimum}` }
      }
      if (def.maximum !== undefined && value > def.maximum) {
        return { success: false, error: `field ${field}: ${value} > maximum ${def.maximum}` }
      }
    }
  }

  return { success: true, data }
}

function resolveInput(
  input:   Record<string, string | number | boolean>,
  ctx:     StepContext
): Record<string, unknown> {
  const evalContext = { pipeline_state: ctx.state.results, session: ctx.sessionContext }
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
