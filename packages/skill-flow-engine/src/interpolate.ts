/**
 * interpolate.ts
 * Helper compartilhado para interpolação de templates em steps.
 *
 * Suporta dois tipos de referência:
 *   {{$.pipeline_state.campo}}  — JSONPath sobre pipeline_state / sessionContext
 *   {{@ctx.namespace.campo}}    — referência ao  IContextStore unificado
 *
 * Também resolve referências simples (não em template):
 *   "$.pipeline_state.campo"    → valor raw (sem string wrapping)
 *   "@ctx.caller.cpf"           → valor raw do ContextStore
 *
 * Usado por: notify, menu, reason (input), suspend, e qualquer step que
 * envie texto ao cliente ou resolva parâmetros de entrada.
 */

import type { IContextStore } from "./context-types"
import type { StepContext }   from "./executor"

// ── Regex ─────────────────────────────────────────────────────────────────────

/** Interpola {{$.path}}, {{@ctx.path}} ou {{@masked.field}} numa string template */
const INTERPOLATION_REGEX = /\{\{((?:\$\.|@ctx\.|@masked\.)[^}]+)\}\}/g

/** Detecta se uma string inteira é uma referência única (sem texto ao redor) */
const SINGLE_REF_REGEX = /^(?:\$\.|@ctx\.|@masked\.)/

// ── resolveRef — resolve uma única referência ────────────────────────────────

/**
 * Resolve uma referência individual.
 * Retorna `undefined` se o caminho não existir.
 *
 * @param ref          Referência: "$.pipeline_state.foo" ou "@ctx.caller.cpf"
 * @param ctx          Contexto do step (para pipeline_state e sessionContext)
 * @param contextStore  IContextStore instância (para @ctx.*)
 */
export async function resolveRef(
  ref:          string,
  ctx:          StepContext,
  contextStore:  IContextStore | undefined,
): Promise<unknown> {
  if (ref.startsWith("@masked.")) {
    return resolveMaskedRef(ref, ctx)
  }
  if (ref.startsWith("@ctx.")) {
    return resolveCtxRef(ref, ctx, contextStore)
  }
  return resolveJsonPathRef(ref, ctx)
}

// ── interpolate — interpola um template de string ────────────────────────────

/**
 * Interpola referências dinâmicas em um template de string.
 * Async porque @ctx.* requer leitura do Redis.
 *
 * @param template     String com placeholders {{$.pipeline_state.xxx}} ou {{@ctx.xxx}}
 * @param ctx          Contexto do step
 * @param contextStore  IContextStore para resolução @ctx.* (opcional — steps sem  IContextStore usam só $.)
 * @returns            String com valores resolvidos; placeholder → "" se ausente
 *
 * @example
 *   await interpolate("Olá {{$.pipeline_state.nome}}!", ctx)
 *   // → "Olá João!"
 *   await interpolate("CPF: {{@ctx.caller.cpf}}", ctx, store)
 *   // → "CPF: 123.456.789-00"
 */
export async function interpolate(
  template:     string,
  ctx:          StepContext,
  contextStore?: IContextStore,
): Promise<string> {
  // Coleta todos os matches e resolve em paralelo
  const matches: Array<{ placeholder: string; ref: string }> = []
  let m: RegExpExecArray | null
  const re = new RegExp(INTERPOLATION_REGEX.source, "g")
  while ((m = re.exec(template)) !== null) {
    const ref = m[1]
    if (ref === undefined) continue
    matches.push({ placeholder: m[0] ?? "", ref })
  }

  // Resolve em paralelo para eficiência
  const resolved = await Promise.all(
    matches.map(({ ref }) => resolveRef(ref, ctx, contextStore))
  )

  // Substitui na string
  let result = template
  for (let i = 0; i < matches.length; i++) {
    const match = matches[i]
    if (!match) continue
    const value = resolved[i]
    result = result.replace(
      match.placeholder,
      value != null ? String(value) : ""
    )
  }
  return result
}

// ── resolveInputValue — resolve um valor de input de step ────────────────────

/**
 * Resolve um único valor de input de step.
 * Se o valor for uma string que começa com "$." ou "@ctx.", resolve como referência.
 * Caso contrário, retorna o valor literal.
 *
 * @param value        Valor declarado no YAML (string, number, boolean ou referência)
 * @param ctx          Contexto do step
 * @param contextStore  IContextStore para @ctx.* (opcional)
 */
export async function resolveInputValue(
  value:        unknown,
  ctx:          StepContext,
  contextStore?: IContextStore,
): Promise<unknown> {
  if (typeof value !== "string") return value
  if (!SINGLE_REF_REGEX.test(value)) return value
  return resolveRef(value, ctx, contextStore)
}

// ── resolveInputMap — resolve mapa de inputs de step ────────────────────────

/**
 * Resolve um mapa de inputs declarativos de step.
 * Referências ($. e @ctx.) são resolvidas; literais são mantidos.
 *
 * @param input        Mapa key→value do step YAML
 * @param ctx          Contexto do step
 * @param contextStore  IContextStore para @ctx.* (opcional)
 */
export async function resolveInputMap(
  input:         Record<string, unknown>,
  ctx:           StepContext,
  contextStore?: IContextStore,
): Promise<Record<string, unknown>> {
  const resolved: Record<string, unknown> = {}
  await Promise.all(
    Object.entries(input).map(async ([key, value]) => {
      resolved[key] = await resolveInputValue(value, ctx, contextStore)
    })
  )
  return resolved
}

// ── Implementações internas ──────────────────────────────────────────────────

/** Resolve um @ctx.namespace.campo → value do  IContextStore */
async function resolveCtxRef(
  ref:          string,
  ctx:          StepContext,
  contextStore:  IContextStore | undefined,
): Promise<unknown> {
  if (!contextStore) return undefined

  // "@ctx.caller.cpf" → "caller.cpf"
  const tag = ref.replace(/^@ctx\./, "")
  return contextStore.getValue(ctx.sessionId, tag, ctx.customerId)
}

/**
 * Resolve um @masked.field_id → valor do maskedScope em memória.
 * Retorna string vazia se o campo não existe no scope
 * (scope limpo ou fora de bloco de transação).
 * Nunca lança exceção — ausência é silenciosa.
 */
function resolveMaskedRef(ref: string, ctx: StepContext): string {
  // "@masked.senha_atual" → "senha_atual"
  const fieldId = ref.replace(/^@masked\./, "")
  return ctx.maskedScope?.[fieldId] ?? ""
}

/** Resolve um $.path sobre pipeline_state / sessionContext */
function resolveJsonPathRef(ref: string, ctx: StepContext): unknown {
  try {
    // Lazy import para não criar dependência circular em ambientes sem jsonpath-plus
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { JSONPath } = require("jsonpath-plus") as typeof import("jsonpath-plus")
    const evalContext = {
      pipeline_state: ctx.state.results,
      session:        ctx.sessionContext,
    }
    return JSONPath({ path: ref, json: evalContext as object, wrap: false })
  } catch {
    return undefined
  }
}
