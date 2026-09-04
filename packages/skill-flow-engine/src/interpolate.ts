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
import { filtrarLeituraCtx, type SitioInterpolacao } from "./ctx-audit"

// ── Regex ─────────────────────────────────────────────────────────────────────

/** Interpola {{$.path}}, {{@ctx.path}}, {{@segment.path}} ou {{@masked.field}} numa string template */
const INTERPOLATION_REGEX = /\{\{((?:\$\.|@ctx\.|@segment\.|@masked\.)[^}]+)\}\}/g

/** Detecta se uma string inteira é uma referência única (sem texto ao redor) */
const SINGLE_REF_REGEX = /^(?:\$\.|@ctx\.|@segment\.|@masked\.)/

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
  sitio?:       SitioInterpolacao,
): Promise<unknown> {
  if (ref.startsWith("@masked.")) {
    return resolveMaskedRef(ref, ctx)
  }
  if (ref.startsWith("@segment.")) {
    return resolveSegmentRef(ref, ctx, contextStore)
  }
  if (ref.startsWith("@ctx.")) {
    return resolveCtxRef(ref, ctx, contextStore, sitio)
  }
  // `$.pipeline_state.*` NÃO é auditado aqui: o mapa tipa tag de ContextStore,
  // não chave de pipeline_state. São 225 interpolações e é a F5 (§D7) — auditar
  // sem tipo produziria 225 linhas de `unknown`, que é ruído, não medição.
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
  /**
   * CTX-02 — de onde esta interpolação parte. É o SÍTIO que decide a plateia
   * (§D2 do `adr-context-read-audience-policy`), e sem ele a auditoria não tem
   * o que julgar.
   *
   * Opcional de propósito: os chamadores que não o passam continuam
   * funcionando, e a auditoria simplesmente não roda para eles. Torná-lo
   * obrigatório agora quebraria 5 call sites por uma fase que ainda só observa
   * — e um parâmetro obrigatório preenchido às pressas com um valor plausível
   * é pior que a ausência dele.
   */
  sitio?:       SitioInterpolacao,
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
    matches.map(({ ref }) => resolveRef(ref, ctx, contextStore, sitio))
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
  if (typeof value === "string") {
    // Pure reference → resolve as raw value
    if (SINGLE_REF_REGEX.test(value)) {
      return resolveRef(value, ctx, contextStore)
    }
    // Template string with {{...}} placeholders → interpolate (returns string)
    // Enables invoke step inputs like:
    //   context_json: '{"session.foo": "{{$.pipeline_state.foo}}"}'
    if (value.includes("{{")) {
      return interpolate(value, ctx, contextStore)
    }
    return value
  }
  // Recursively resolve nested ARRAYS so that invoke step inputs like
  //   signals: [{ metric: "nps", value: "$.pipeline_state.coletar_nps.nps" }]
  // have refs inside array elements resolved (not left as literal strings).
  if (Array.isArray(value)) {
    return Promise.all(value.map(v => resolveInputValue(v, ctx, contextStore)))
  }
  // Recursively resolve nested plain objects so that invoke step inputs like
  //   context: { "session.foo": "$.pipeline_state.foo" }
  // have their values resolved just like top-level string inputs.
  if (value !== null && typeof value === "object") {
    return resolveInputMap(value as Record<string, unknown>, ctx, contextStore)
  }
  return value
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

// ── resolveVisibility — resolve visibility arrays with @ctx.* / @segment.* ──

/**
 * Resolve visibility arrays that may contain @ctx.* or @segment.* references.
 * String values ("all", "agents_only") pass through unchanged.
 * Array values have each element resolved; empty result falls back to "all".
 */
export async function resolveVisibility(
  visibility: string | string[],
  ctx:        StepContext,
  contextStore?: IContextStore,
): Promise<string | string[]> {
  if (typeof visibility === "string") return visibility
  if (!Array.isArray(visibility)) return visibility
  const resolved: string[] = []
  for (const item of visibility) {
    if (item.startsWith("@ctx.") || item.startsWith("@segment.")) {
      const val = await resolveRef(item, ctx, contextStore)
      if (val != null && val !== "") resolved.push(String(val))
    } else {
      resolved.push(item)
    }
  }
  if (resolved.length > 0) return resolved
  // Original array had items but all resolved to empty/null — the intent was to
  // restrict visibility to specific participants. Falling back to "all" would
  // broadcast to everyone (including the customer), which is the opposite of the
  // YAML author's intention.  Fall back to "agents_only" as a safe default.
  // This prevents NPS messages from leaking to the Agent Assist UI when
  // @ctx.core.contact.customer_participant_id is not yet in the ContextStore.
  return visibility.length > 0 ? "agents_only" : "all"
}

// ── Implementações internas ──────────────────────────────────────────────────

/** Resolve um @segment.local_tag → value do ContextStore com segment prefix */
async function resolveSegmentRef(
  ref:          string,
  ctx:          StepContext,
  contextStore:  IContextStore | undefined,
): Promise<unknown> {
  if (!contextStore) return undefined
  if (!ctx.segmentId) return undefined

  // "@segment.nps_score" → "segment.{segmentId}.nps_score"
  const localTag = ref.replace(/^@segment\./, "")
  const fullTag  = `segment.${ctx.segmentId}.${localTag}`
  return contextStore.getValue(ctx.sessionId, fullTag, ctx.customerId)
}

/** Resolve um @ctx.namespace.campo → value do  IContextStore */
async function resolveCtxRef(
  ref:          string,
  ctx:          StepContext,
  contextStore:  IContextStore | undefined,
  sitio?:       SitioInterpolacao,
): Promise<unknown> {
  if (!contextStore) return undefined

  // "@ctx.caller.cpf" → "caller.cpf"
  const tag = ref.replace(/^@ctx\./, "")

  // Arc 16: @ctx.journey.* reads from the journey Redis hash
  // The SDK maps getValue("journey:{journeyId}", tag) → {tenant}:ctx:journey:{journeyId}
  //
  // CNS-03: `core.journey.*` entra aqui pela mesma porta. O escopo de uma tag do core é
  // o SEGUNDO segmento (a CNS-02 deu o primeiro à propriedade), e as três casas que
  // roteiam por prefixo têm de concordar — esta, o `ttlFor`/`isJourneyTag` do SDK e o
  // `writeContextTag` do mcp-server. Duas concordando e uma não é escrita indo para um
  // hash e leitura vindo de outro, que degrada como "a tag não existe".
  const bruto = (tag.startsWith("journey.") || tag.startsWith("core.journey.")) && ctx.journeyId
    ? await contextStore.getValue(`journey:${ctx.journeyId}`, tag, ctx.customerId)
    : await contextStore.getValue(ctx.sessionId, tag, ctx.customerId)

  // CTX-04 (F3) — O FILTRO POR PLATEIA. Ele SUBSTITUI o valor; não existe uma
  // segunda porta que devolva o cru (§D1).
  //
  // ⚠️ Aqui é AGUARDADO, ao contrário da auditoria da CTX-02, que era
  // fire-and-forget de propósito. Observar podia ficar fora do caminho crítico;
  // filtrar não pode — um filtro que a resposta não espera não filtra nada.
  //
  // ⚠️ Sem `sitio` não há plateia derivável, e sem plateia não há decisão a tomar.
  // O parâmetro é opcional porque torná-lo obrigatório quebraria call sites que
  // não renderizam para ninguém; os três sítios de plateia (`notify`, `menu`,
  // `receive.notify`) o declaram.
  if (!sitio) return bruto
  return filtrarLeituraCtx(bruto, tag, sitio, ctx.tenantId)
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
      // Dialog primitive §17.3-1 — deploy-time skill params from the slot's
      // config_json, accessible as $.config.* (e.g. $.config.form_id).
      // Own namespace, does not collide with session.*.
      config:         ctx.config ?? {},
      // Always-available built-in fields — accessible as $.session_id, $.tenant_id etc.
      // Useful in invoke steps that need the current session or tenant identifier
      // without requiring the caller to put them in sessionContext explicitly.
      session_id:     ctx.sessionId,
      tenant_id:      ctx.tenantId,
      customer_id:    ctx.customerId,
      instance_id:    ctx.instanceId,
      // Segment UUID of the running agent — lets a skill pass its OWN segment to
      // survey_record(grain=segment) for a "signal about itself" without the bridge
      // having to inject it (see analytics-agents-workbench §14, TODO follow-ups A item 1).
      segment_id:     ctx.segmentId,
    }
    return JSONPath({ path: ref, json: evalContext as object, wrap: false })
  } catch {
    return undefined
  }
}
