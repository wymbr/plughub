/**
 * context-accumulator.ts
 * ContextAccumulator — extração de contexto a partir de chamadas de MCP tools.
 *
 * Responsabilidade:
 *   Lê as anotações `context_tags` de uma tool definition e escreve automaticamente
 *   no ContextStore as entradas correspondentes, sem exigir código explícito do
 *   desenvolvedor em cada chamada.
 *
 * Dois pontos de extração:
 *   1. extractFromInputs  — antes de chamar a tool (parâmetros de entrada)
 *   2. extractFromOutputs — depois de receber o retorno da tool
 *
 * Uso típico (via McpInterceptor):
 *
 *   const acc = new ContextAccumulator({ store, sessionId: "sess_abc" })
 *   // antes da chamada:
 *   await acc.extractFromInputs(toolDef.context_tags?.inputs, toolArgs, "mcp_call:customer_get")
 *   // depois da chamada:
 *   await acc.extractFromOutputs(toolDef.context_tags?.outputs, result, "mcp_call:customer_get")
 *
 * Dot-notation para campos aninhados no retorno:
 *   "customer.nome" → acessa result.customer.nome
 */

import type { ContextStore } from "./context-store"
import type {
  ContextTagEntry,
  ContextMergeStrategy,
} from "@plughub/schemas"

// ── Config ────────────────────────────────────────────────────────────────────

export interface ContextAccumulatorConfig {
  store:      ContextStore
  sessionId:  string
  /** Necessário apenas para tags de longa duração (pricing, insight.historico) */
  customerId?: string
}

// ── ContextAccumulator ────────────────────────────────────────────────────────

export class ContextAccumulator {
  private readonly store:      ContextStore
  private readonly sessionId:  string
  private readonly customerId: string | undefined

  constructor(config: ContextAccumulatorConfig) {
    this.store      = config.store
    this.sessionId  = config.sessionId
    this.customerId = config.customerId
  }

  // ── extractFromInputs ───────────────────────────────────────────────────────

  /**
   * Extrai valores dos argumentos de entrada de uma tool e persiste no ContextStore.
   *
   * @param contextTagsInputs  Mapa { paramName → ContextTagEntry } da anotação da tool
   * @param toolArgs           Argumentos passados para a tool call
   * @param source             Origem da escrita (ex: "mcp_call:customer_get")
   */
  async extractFromInputs(
    contextTagsInputs: Record<string, ContextTagEntry> | undefined,
    toolArgs:          Record<string, unknown>,
    source:            string,
  ): Promise<void> {
    if (!contextTagsInputs) return

    for (const [paramName, tagEntry] of Object.entries(contextTagsInputs)) {
      const value = toolArgs[paramName]
      if (value === undefined || value === null) continue

      await this._writeEntry(tagEntry, value, source)
    }
  }

  // ── extractFromOutputs ──────────────────────────────────────────────────────

  /**
   * Extrai valores do retorno de uma tool e persiste no ContextStore.
   *
   * Suporta dot-notation para campos aninhados:
   *   "customer.nome" → acessa result.customer.nome
   *
   * @param contextTagsOutputs  Mapa { dotPath → ContextTagEntry } da anotação da tool
   * @param toolResult          Retorno da tool call
   * @param source              Origem da escrita (ex: "mcp_call:customer_get")
   */
  async extractFromOutputs(
    contextTagsOutputs: Record<string, ContextTagEntry> | undefined,
    toolResult:         unknown,
    source:             string,
  ): Promise<void> {
    if (!contextTagsOutputs) return
    if (toolResult === null || toolResult === undefined) return

    for (const [dotPath, tagEntry] of Object.entries(contextTagsOutputs)) {
      const value = this._resolveDotPath(toolResult, dotPath)
      if (value === undefined || value === null) continue

      await this._writeEntry(tagEntry, value, source)
    }
  }

  // ── _writeEntry ─────────────────────────────────────────────────────────────

  private async _writeEntry(
    tagEntry: ContextTagEntry,
    value:    unknown,
    source:   string,
  ): Promise<void> {
    const merge = (tagEntry.merge ?? "highest_confidence") as ContextMergeStrategy

    await this.store.set(
      this.sessionId,
      tagEntry.tag,
      {
        value,
        confidence:     tagEntry.confidence,
        source,
        visibility:     tagEntry.visibility ?? "agents_only",
        ttl_override_s: tagEntry.ttl_override_s,
      },
      merge,
      this.customerId,
    )
  }

  // ── _resolveDotPath ─────────────────────────────────────────────────────────

  /**
   * Navega em `obj` pelo caminho dot-notation.
   * Retorna `undefined` se qualquer segmento não existir ou obj não for objeto.
   *
   * Ex: _resolveDotPath({ customer: { nome: "João" } }, "customer.nome") → "João"
   */
  private _resolveDotPath(obj: unknown, dotPath: string): unknown {
    const segments = dotPath.split(".")
    let current: unknown = obj

    for (const seg of segments) {
      if (current === null || current === undefined) return undefined
      if (typeof current !== "object") return undefined
      current = (current as Record<string, unknown>)[seg]
    }

    return current
  }
}
