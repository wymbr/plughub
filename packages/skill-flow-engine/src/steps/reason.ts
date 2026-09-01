/**
 * steps/reason.ts
 * Executor do step type: reason
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Invoca o AI Gateway com prompt declarado e output_schema.
 * O AI Gateway valida o retorno contra o schema antes de retornar.
 * max_format_retries: número de tentativas de correção de formato (default: 1).
 *
 * context_tags (Opção A — declaração explícita):
 *   Após execução bem-sucedida do LLM, os campos mapeados em
 *   step.context_tags.outputs são escritos no ContextStore automaticamente.
 *   O engine passa ctx.contextStore para o step; se ausente, o step ignora silenciosamente.
 */

import { JSONPath }              from "jsonpath-plus"
import type { ReasonStep }       from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { resolveInputMap }       from "../interpolate"
import { extractOutputsToCtx }   from "../context-accumulator-util"

export async function executeReason(
  step: ReasonStep,
  ctx:  StepContext
): Promise<StepResult> {
  const resolvedInput = await resolveInputMap(
    step.input ?? {} as Record<string, unknown>,
    ctx,
    ctx.contextStore,
  )
  // T7b — JSON Schema (montado upstream do form), inline ou via json_schema_ref.
  const jsonSchema    = resolveJsonSchema(step, ctx)
  const maxRetries    = step.max_format_retries ?? 1
  // R8d — perfil de modelo (estático ou `$.pipeline_state.*`). Resolvido p/ habilitar
  // revisor heterogêneo (família ≠ avaliador) sem hardcode no YAML.
  const modelProfile  = resolveModelProfile(step, ctx)
  // LLM Accounts — lista de contas preferidas do pool (core.pool.llm_account_ids,
  // escrita pelo Routing Engine em _write_pool_context). Ausente/vazio = sem
  // restrição (AccountSelector usa o pool inteiro de contas do provider).
  const preferredConfigIds = await resolvePreferredConfigIds(ctx)

  // Fala do cliente NOMEADA — habilita a medição de sentimento no ai-gateway.
  // Resolvida uma vez, fora do laço de retry: o texto do cliente não muda entre
  // tentativas de formato, e re-resolver dispararia leitura de ContextStore por
  // tentativa sem nenhum ganho.
  const customerUtterance = await resolveCustomerUtterance(step, ctx)

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const result = await ctx.aiGatewayCall({
        prompt_id:     step.prompt_id,
        input:         resolvedInput,
        output_schema: step.output_schema,
        session_id:    ctx.sessionId,
        ...(ctx.segmentId ? { segment_id: ctx.segmentId } : {}),
        attempt,
        ...(jsonSchema ? { json_schema: jsonSchema } : {}),
        ...(modelProfile ? { model_profile: modelProfile } : {}),
        ...(customerUtterance ? { customer_utterance: customerUtterance } : {}),
        ...(preferredConfigIds && preferredConfigIds.length > 0 ? { preferred_config_ids: preferredConfigIds } : {}),
      })

      // T7b — com JSON Schema, o ai-gateway garante o shape via tool-use (validação
      // recursiva lá). Aceita o resultado direto, sem a validação estática local.
      if (jsonSchema) {
        if (step.context_tags?.outputs && ctx.contextStore) {
          await extractOutputsToCtx(
            ctx.contextStore, ctx.sessionId, ctx.customerId,
            step.context_tags.outputs, result,
            `ai_inferred:${step.id}`, ctx.segmentId, ctx.journeyId,
          )
        }
        return {
          next_step_id:      step.on_success,
          output_as:         step.output_as,
          output_value:      result,
          transition_reason: "on_success",
        }
      }

      // Validar retorno contra output_schema (caminho flat / compat)
      const validated = validateAgainstSchema(result, step.output_schema)
      if (validated.success) {
        // ── context_tags (Opção A): escrever outputs no ContextStore ─────────
        if (step.context_tags?.outputs && ctx.contextStore) {
          await extractOutputsToCtx(
            ctx.contextStore,
            ctx.sessionId,
            ctx.customerId,
            step.context_tags.outputs,
            validated.data,
            `ai_inferred:${step.id}`,
            ctx.segmentId,
            ctx.journeyId,
          )
        }

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

/**
 * T7b — resolve o JSON Schema do reason step: inline (`step.json_schema`) ou via
 * referência JSONPath (`step.json_schema_ref`) contra o pipeline_state. Retorna
 * undefined quando ausente/não resolvido → o step cai no caminho flat (output_schema).
 */
function resolveJsonSchema(
  step: ReasonStep,
  ctx:  StepContext,
): Record<string, unknown> | undefined {
  const inline = (step as { json_schema?: Record<string, unknown> }).json_schema
  if (inline && typeof inline === "object" && Object.keys(inline).length > 0) {
    return inline
  }
  const ref = (step as { json_schema_ref?: string }).json_schema_ref
  if (ref) {
    const evalContext = { pipeline_state: ctx.state.results, session: ctx.sessionContext }
    const resolved = JSONPath({ path: ref, json: evalContext as object, wrap: false })
    if (resolved && typeof resolved === "object" && !Array.isArray(resolved)) {
      return resolved as Record<string, unknown>
    }
  }
  return undefined
}


/**
 * R8d — resolve o `model_profile` do reason step. Aceita string estática
 * ("evaluation") ou referência JSONPath (`$.pipeline_state.*`) resolvida em runtime
 * (ex.: revisor lê o perfil da config da campanha injetada no pipeline_state). Retorna
 * undefined quando ausente ou quando a referência não resolve para string → o ai-gateway
 * cai no default ("balanced").
 */
export function resolveModelProfile(
  step: ReasonStep,
  ctx:  StepContext,
): string | undefined {
  const mp = (step as { model_profile?: string }).model_profile
  if (!mp) return undefined
  if (mp.startsWith("$.")) {
    const evalContext = { pipeline_state: ctx.state.results, session: ctx.sessionContext }
    const resolved = JSONPath({ path: mp, json: evalContext as object, wrap: false })
    return typeof resolved === "string" && resolved.length > 0 ? resolved : undefined
  }
  return mp
}


/**
 * LLM Accounts — resolve `core.pool.llm_account_ids` do ContextStore (escrito
 * pelo Routing Engine em `_write_pool_context` após cada alocação). Retorna
 * undefined quando ausente/ContextStore não disponível — o ai-gateway cai no
 * comportamento sem restrição (pool inteiro de contas do provider).
 */
async function resolvePreferredConfigIds(ctx: StepContext): Promise<string[] | undefined> {
  if (!ctx.contextStore) return undefined
  try {
    const value = await ctx.contextStore.getValue(ctx.sessionId, "core.pool.llm_account_ids")
    if (Array.isArray(value) && value.every(v => typeof v === "string") && value.length > 0) {
      return value as string[]
    }
    return undefined
  } catch {
    return undefined
  }
}


/**
 * Resolve `step.customer_utterance` — a referência ao texto que o CLIENTE disse.
 *
 * O ai-gateway usa esse texto para MEDIR sentimento. Ele não pode adivinhá-lo: o
 * `input` do reason é opaco por contrato, e um chute produziria score sobre
 * `pipeline_state`. Aceita `$.` (JSONPath sobre pipeline_state/session, mesma
 * máquina do `model_profile`) e `@ctx.` (ContextStore).
 *
 * Devolve undefined quando ausente ou quando a referência não resolve para string
 * não-vazia — e nesse caso **nenhum sentimento é medido**, que é o desfecho
 * honesto. Nunca inventa texto: medir a fala errada é pior que não medir.
 */
export async function resolveCustomerUtterance(
  step: ReasonStep,
  ctx:  StepContext,
): Promise<string | undefined> {
  const ref = (step as { customer_utterance?: string }).customer_utterance
  if (!ref) return undefined

  let resolved: unknown
  if (ref.startsWith("$.")) {
    const evalContext = { pipeline_state: ctx.state.results, session: ctx.sessionContext }
    resolved = JSONPath({ path: ref, json: evalContext as object, wrap: false })
  } else if (ref.startsWith("@ctx.")) {
    if (!ctx.contextStore) return undefined
    try {
      resolved = await ctx.contextStore.getValue(ctx.sessionId, ref.slice("@ctx.".length))
    } catch {
      return undefined
    }
  } else {
    // Texto literal não é aceito: seria fala fabricada pelo autor do fluxo, medida
    // como se fosse do cliente. Referência ou nada.
    return undefined
  }

  return typeof resolved === "string" && resolved.trim().length > 0 ? resolved : undefined
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

    // Treat JSON null the same as absent: Claude may return null for optional fields.
    if (value === undefined || value === null) {
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

// Keep JSONPath for backward compat — resolveInputMap handles both $. and @ctx.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function _resolveInputLegacy(
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
