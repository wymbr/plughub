/**
 * interpolate.ts
 * Helper compartilhado para interpolação de templates em steps.
 *
 * Suporta: {{$.pipeline_state.campo}} e {{$.session.campo}}
 * Usado por: notify, menu (e qualquer step que envie texto ao cliente)
 */

import type { StepContext } from "./executor"

/** Regex para interpolação dinâmica: {{$.pipeline_state.campo}} */
const INTERPOLATION_REGEX = /\{\{([\$\.][^}]+)\}\}/g

/**
 * Interpola referências dinâmicas em um template de string.
 *
 * @param template  - string com placeholders {{$.pipeline_state.xxx}}
 * @param ctx       - contexto do step (fornece pipeline_state e sessionContext)
 * @returns string com valores resolvidos; placeholder substituído por "" se ausente
 *
 * @example
 *   interpolate("Olá {{$.pipeline_state.nome}}!", ctx)
 *   // → "Olá João!" (se pipeline_state.nome = "João")
 *   // → "Olá !"    (se pipeline_state.nome está ausente)
 */
export function interpolate(template: string, ctx: StepContext): string {
  return template.replace(INTERPOLATION_REGEX, (_, path: string) => {
    const parts = path.replace(/^\$\./, "").split(".")
    let current: unknown = {
      pipeline_state: ctx.state.results,
      session:        ctx.sessionContext,
    }
    for (const part of parts) {
      if (current == null || typeof current !== "object") return ""
      current = (current as Record<string, unknown>)[part]
    }
    return current != null ? String(current) : ""
  })
}
