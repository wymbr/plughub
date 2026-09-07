/**
 * steps/choice.ts
 * Executor do step type: choice
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Avalia condições sobre pipeline_state ($.) ou ContextStore (@ctx.*, @segment.*).
 * Retorna o next do primeiro match, ou default se nenhuma satisfeita.
 *
 * Operadores suportados:
 *   eq, neq, gt, gte, lt, lte, contains  — comparação de valor
 *   exists                                — tag @ctx presente com qualquer valor
 *   confidence_gte                        — confiança da entry @ctx ≥ value
 */

import { JSONPath }          from "jsonpath-plus"
import type { ChoiceStep, ContextEntry } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeChoice(
  step: ChoiceStep,
  ctx:  StepContext
): Promise<StepResult> {
  // Contexto de avaliação para referências $.
  const evalContext = {
    pipeline_state: ctx.state.results,
    session:        ctx.sessionContext,
  }

  for (const condition of step.conditions) {
    const isCtxRef     = condition.field.startsWith("@ctx.")
    const isSegmentRef = condition.field.startsWith("@segment.")

    let matched: boolean

    if (isCtxRef || isSegmentRef) {
      // Resolve the tag — segment refs get prefixed with segment.{segmentId}.
      let tag: string
      if (isSegmentRef) {
        const localTag = condition.field.replace(/^@segment\./, "")
        tag = ctx.segmentId ? `segment.${ctx.segmentId}.${localTag}` : localTag
      } else {
        tag = condition.field.replace(/^@ctx\./, "")
      }

      // Lê a ContextEntry completa para poder avaliar exists e confidence_gte
      const entry = ctx.contextStore
        ? await ctx.contextStore.get(ctx.sessionId, tag, ctx.customerId)
        : null

      matched = evaluateCtxCondition(entry, condition.operator, condition.value)
    } else {
      const fieldValue = resolveJsonPath(condition.field, evalContext)
      matched = evaluateCondition(fieldValue, condition.operator, condition.value)
    }

    if (matched) {
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

// ── Resolvers ─────────────────────────────────────────────────────────────────

function resolveJsonPath(path: string, context: unknown): unknown {
  try {
    const results = JSONPath({ path, json: context as object, wrap: false })
    return results
  } catch {
    return undefined
  }
}

// ── Evaluators ────────────────────────────────────────────────────────────────

/**
 * Avalia condições sobre uma ContextEntry do ContextStore.
 * Suporta os operadores novos (exists, confidence_gte) além dos padrão.
 */
function evaluateCtxCondition(
  entry:    ContextEntry | null,
  operator: string,
  expected: unknown,
): boolean {
  switch (operator) {
    case "exists":
      // Tag presente com qualquer valor (incluindo null/false/0)
      return entry !== null

    case "confidence_gte": {
      // Confiança da entry >= value numérico
      if (!entry) return false
      const threshold = typeof expected === "number" ? expected : parseFloat(String(expected))
      return !isNaN(threshold) && entry.confidence >= threshold
    }

    default:
      // Para os demais operadores, avalia sobre o value da entry
      return entry !== null
        ? evaluateCondition(entry.value, operator, expected)
        : false
  }
}

/**
 * Avalia condições de comparação de valor (operadores padrão).
 *
 * ⚠️ **Este avaliador serve DOIS ramos**: o de `$.` (direto) e o de `@ctx.`
 * (através do `evaluateCtxCondition`, para os operadores que não são dele). Um
 * operador que exista só no outro ramo NÃO cai num erro aqui — cai no `default`,
 * que devolve `false`. E `false` numa condição de `choice` não é um erro: é o
 * `default` do step, que quase sempre é um caminho legítimo.
 *
 * Foi assim que `exists` sobre `$.` passou de RET-05 a 2026-09-07 sempre falso:
 * o `continuar` do orquestrador testava `$.pipeline_state.nivel.on_return` com
 * o ponteiro PRESENTE no estado, caía no `default: finalizar`, e o contato
 * terminava parecendo um fim normal — medido em contato real, com o
 * `pipeline_state` mostrando `continuar -> finalizar default` ao lado de
 * `"on_return": "pos_atendimento"`. Nada ficou vermelho em lugar nenhum.
 *
 * **Ao acrescentar um operador, acrescente nos DOIS ramos ou recuse no schema.**
 * Quem impõe isso é `infra/test/probe_choice_operator_parity.sh`, não este
 * comentário — a restrição já vivia como prosa (o docstring do `ConditionSchema`
 * dizia *"tag presente no ContextStore"*) e prosa não impediu nada.
 */
function evaluateCondition(
  fieldValue: unknown,
  operator:   string,
  expected:   unknown
): boolean {
  switch (operator) {
    // ── `exists` sobre `$.` — o CRITÉRIO é `undefined`, nunca truthiness ─────
    //
    // `resolveJsonPath` usa `wrap: false`, então caminho ausente resolve para
    // `undefined`. `null`, `0`, `false` e `""` são valores PRESENTES, e é isso
    // que mantém a semântica igual à do ramo `@ctx.`, onde `exists` é
    // "entry presente com qualquer valor". Usar truthiness aqui faria um
    // contador zerado desaparecer — a família de defeito que o CLAUDE.md
    // cataloga em `if not x` × `is None`, na direção inversa.
    case "exists":   return fieldValue !== undefined
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
