/**
 * context-accumulator-util.ts
 * Utilitário leve para extração de outputs de LLM para o ContextStore.
 *
 * Usa apenas @plughub/schemas e IContextStore local — sem depender de @plughub/sdk.
 * Equivalente ao ContextAccumulator.extractFromOutputs do SDK, internalizado no engine.
 */

import type { ContextTagEntry, ContextMergeStrategy } from "@plughub/schemas"
import type { IContextStore } from "./context-types"

/**
 * Extrai campos de um objeto de saída e os escreve no ContextStore.
 *
 * @param store        ContextStore onde escrever
 * @param sessionId    ID da sessão atual
 * @param customerId   ID do cliente (para tags de longa duração)
 * @param outputTags   Mapeamento dotPath → ContextTagEntry da anotação context_tags.outputs
 * @param outputObj    Objeto de saída do LLM (ou outro step)
 * @param source       Origem da escrita (ex: "ai_inferred:step_id")
 */
export async function extractOutputsToCtx(
  store:      IContextStore,
  sessionId:  string,
  customerId: string | undefined,
  outputTags: Record<string, ContextTagEntry>,
  outputObj:  unknown,
  source:     string,
): Promise<void> {
  if (!outputObj || typeof outputObj !== "object") return

  for (const [dotPath, tagEntry] of Object.entries(outputTags)) {
    const value = resolveDotPath(outputObj, dotPath)
    if (value === undefined || value === null) continue

    const merge = (tagEntry.merge ?? "highest_confidence") as ContextMergeStrategy
    await store.set(
      sessionId,
      tagEntry.tag,
      {
        value,
        confidence:     tagEntry.confidence,
        source,
        visibility:     tagEntry.visibility ?? "agents_only",
        ttl_override_s: tagEntry.ttl_override_s,
      },
      merge,
      customerId,
    )
  }
}

/** Navega em `obj` pelo caminho dot-notation. */
function resolveDotPath(obj: unknown, dotPath: string): unknown {
  const segments = dotPath.split(".")
  let current: unknown = obj
  for (const seg of segments) {
    if (current === null || current === undefined) return undefined
    if (typeof current !== "object") return undefined
    current = (current as Record<string, unknown>)[seg]
  }
  return current
}
