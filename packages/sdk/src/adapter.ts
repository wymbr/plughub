/**
 * adapter.ts
 * PlugHubAdapter — interface única de portabilidade do SDK.
 * Spec: PlugHub v24.0 seção 4.6d
 *
 * Opera nas duas direções com o mesmo contrato:
 *   Entrada: context_package → schema do agente
 *   Saída:   schema do agente → context_package / ambiente externo
 */

import { z } from "zod"
import {
  type ContextPackage,
  type AgentDone,
  OutcomeSchema,
} from "@plughub/schemas"

// ─────────────────────────────────────────────
// Tipos do adapter
// ─────────────────────────────────────────────

/** Referência JSONPath — $.campo.subcampo */
type JsonPath = `$.${string}`

/** Valor do mapeamento: literal ou JSONPath */
type MappingValue = string | number | boolean | JsonPath

function isJsonPath(v: MappingValue): v is JsonPath {
  return typeof v === "string" && v.startsWith("$.")
}

/** Resolve um JSONPath simples sobre um objeto */
function resolvePath(obj: unknown, path: JsonPath): unknown {
  const parts = path.slice(2).split(".")
  let current: unknown = obj
  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

/** Define o mapeamento de campos do context_package para o schema do agente */
export type ContextMap = Record<string, string>

/** Define o mapeamento de resultado do agente para o contrato da plataforma */
export type ResultMap = Record<string, string>

/** Mapeamento de outcome semântico do agente para valores da plataforma */
export type OutcomeMap = Partial<Record<string, z.infer<typeof OutcomeSchema>>>

export interface PlugHubAdapterConfig {
  /**
   * Mapeamento bidirecional de contexto.
   * Chave: campo no context_package da plataforma (ex: "customer_data.tier")
   * Valor: campo no schema do agente (ex: "case.account_tier")
   */
  context_map: ContextMap

  /**
   * Mapeamento bidirecional de resultado.
   * Chave: campo no contrato de conclusão da plataforma (ex: "outcome")
   * Valor: campo no resultado do agente (ex: "resolution_status")
   */
  result_map: ResultMap

  /**
   * Mapeamento de outcome semântico.
   * Chave: valor de outcome no agente (ex: "needs_escalation")
   * Valor: valor de outcome na plataforma (ex: "escalated_human")
   */
  outcome_map?: OutcomeMap
}

// ─────────────────────────────────────────────
// PlugHubAdapter
// ─────────────────────────────────────────────

export class PlugHubAdapter {
  readonly config: PlugHubAdapterConfig

  constructor(config: PlugHubAdapterConfig) {
    this.config = config
    this._validateConfig()
  }

  /**
   * Direção ENTRADA — context_package → schema do agente
   * Chamado antes de passar contexto para o handler do agente.
   */
  fromPlatform(pkg: ContextPackage): Record<string, unknown> {
    const result: Record<string, unknown> = {}
    for (const [platformField, agentField] of Object.entries(this.config.context_map)) {
      const value = resolvePath(pkg, `$.${platformField}` as JsonPath)
      if (value !== undefined) {
        this._setNestedField(result, agentField, value)
      }
    }
    return result
  }

  /**
   * Direção SAÍDA — resultado do agente → contrato de conclusão da plataforma
   * Chamado após o handler do agente retornar.
   */
  toPlatform(agentResult: Record<string, unknown>): Partial<AgentDone> {
    const result: Record<string, unknown> = {}
    for (const [platformField, agentField] of Object.entries(this.config.result_map)) {
      const value = resolvePath(agentResult, `$.${agentField}` as JsonPath)
      if (value !== undefined) {
        result[platformField] = value
      }
    }

    // Mapear outcome semântico
    if (result["outcome"] !== undefined && this.config.outcome_map) {
      const mappedOutcome = this.config.outcome_map[result["outcome"] as string]
      if (mappedOutcome !== undefined) {
        result["outcome"] = mappedOutcome
      }
    }

    return result as Partial<AgentDone>
  }

  /**
   * Valida que os campos obrigatórios do contrato estão mapeados.
   * Chamado no construtor — erros de configuração falham na inicialização.
   */
  private _validateConfig(): void {
    const requiredResultFields = ["outcome", "issue_status"]
    const mappedResultFields = Object.keys(this.config.result_map)
    const missing = requiredResultFields.filter(f => !mappedResultFields.includes(f))
    if (missing.length > 0) {
      throw new Error(
        `PlugHubAdapter: campos obrigatórios sem mapeamento em result_map: ${missing.join(", ")}\n` +
        `Spec 4.6d: issue_status e outcome são obrigatórios para o Agent Quality Score.`
      )
    }
  }

  private _setNestedField(
    obj: Record<string, unknown>,
    path: string,
    value: unknown
  ): void {
    const parts = path.split(".")
    let current = obj
    for (let i = 0; i < parts.length - 1; i++) {
      const part = parts[i]!
      if (current[part] == null || typeof current[part] !== "object") {
        current[part] = {}
      }
      current = current[part] as Record<string, unknown>
    }
    current[parts[parts.length - 1]!] = value
  }
}

// ─────────────────────────────────────────────
// Drivers — o que é específico de cada ambiente
// Spec 4.6d: o adapter é agnóstico, os drivers traduzem
// ─────────────────────────────────────────────

export interface PlugHubDriver {
  readonly name: string
  /**
   * Traduz o output do adapter para o formato do ambiente externo.
   * Usado na direção SAÍDA (agente nativo sendo portado).
   */
  toExternal(adapterOutput: Record<string, unknown>): unknown
  /**
   * Traduz o output do ambiente externo para o formato do adapter.
   * Usado na direção ENTRADA (agente externo chegando na plataforma).
   */
  fromExternal(externalOutput: unknown): Record<string, unknown>
}

export { GenericMCPDriver } from "./drivers/generic-mcp"
export { BedrockDriver }    from "./drivers/bedrock"
