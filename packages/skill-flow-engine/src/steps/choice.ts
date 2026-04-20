/**
 * steps/choice.ts
 * Executor do step type: choice
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Avalia condições JSONPath sobre o pipeline_state.
 * Retorna o next do primeiro match, ou default se nenhuma satisfeita.
 */

import { JSONPath } from "jsonpath-plus"
import type { ChoiceStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export function executeChoice(
  step: ChoiceStep,
  ctx:  StepContext
): StepResult {
  // Contexto de avaliação — pipeline_state + session
  const evalContext = {
    pipeline_state: ctx.state.results,
    session:        ctx.sessionContext,
  }

  for (const condition of step.conditions) {
    const fieldValue = resolveJsonPath(condition.field, evalContext)
    if (evaluateCondition(fieldValue, condition.operator, condition.value)) {
      return {
        next_step_id:      condition.next,
        transition_reason: "condition_match",
      }
    }
  }

  return {
    next_step_id:      step.default,
    transition_reason: "default",
  }
}

function resolveJsonPath(path: string, context: unknown): unknown {
  try {
    // Remove o prefixo "$." e avalia sobre o contexto
    const results = JSONPath({ path, json: context as object, wrap: false })
    return results
  } catch {
    return undefined
  }
}

function evaluateCondition(
  fieldValue: unknown,
  operator:   string,
  expected:   unknown
): boolean {
  switch (operator) {
    case "eq":       return fieldValue === expected
    case "neq":      return fieldValue !== expected
    case "gt":       return typeof fieldValue === "number" && fieldValue > (expected as number)
    case "gte":      return typeof fieldValue === "number" && fieldValue >= (expected as number)
    case "lt":       return typeof fieldValue === "number" && fieldValue < (expected as number)
    case "lte":      return typeof fieldValue === "number" && fieldValue <= (expected as number)
    case "contains":
      if (typeof fieldValue === "string") return fieldValue.includes(String(expected))
      if (Array.isArray(fieldValue))      return fieldValue.includes(expected)
      return false
    default:
      return false
  }
}
