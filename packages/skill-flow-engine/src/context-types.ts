/**
 * context-types.ts
 * Interfaces mínimas de ContextStore e ContextAccumulator para o Skill Flow Engine.
 *
 * O engine define apenas o contrato que precisa — não importa a implementação
 * concreta do @plughub/sdk. O chamador (skill-flow-worker) injeta o objeto real.
 *
 * Isso mantém o skill-flow-engine dependendo apenas de @plughub/schemas,
 * sem acrescentar @plughub/sdk na cadeia de dependências.
 */

import type {
  ContextEntry,
  ContextSnapshot,
  ContextMergeStrategy,
  SkillRequiredContext,
  ContextGapsReport,
  ContextTagEntry,
} from "@plughub/schemas"

// ── ContextStore mínimo ────────────────────────────────────────────────────────

export interface IContextStore {
  get(sessionId: string, tag: string, customerId?: string): Promise<ContextEntry | null>
  getValue(sessionId: string, tag: string, customerId?: string): Promise<unknown>
  getAll(sessionId: string): Promise<ContextSnapshot>
  getByPrefix(sessionId: string, prefixes: string[], customerId?: string): Promise<ContextSnapshot>
  getMissing(sessionId: string, requiredContext: SkillRequiredContext[], customerId?: string): Promise<ContextGapsReport>
  set(sessionId: string, tag: string, entry: Omit<ContextEntry, "updated_at">, merge?: ContextMergeStrategy, customerId?: string): Promise<void>
  delete(sessionId: string, tag: string, customerId?: string): Promise<void>
  clearSession(sessionId: string): Promise<void>
}

// ── ContextAccumulator mínimo ─────────────────────────────────────────────────

export interface IContextAccumulator {
  extractFromInputs(
    contextTagsInputs: Record<string, ContextTagEntry> | undefined,
    toolArgs:          Record<string, unknown>,
    source:            string,
  ): Promise<void>

  extractFromOutputs(
    contextTagsOutputs: Record<string, ContextTagEntry> | undefined,
    toolResult:         unknown,
    source:             string,
  ): Promise<void>
}
