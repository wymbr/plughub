/**
 * generic-mcp.ts
 * GenericMCPDriver — portabilidade para qualquer sistema que fale MCP.
 * Spec 4.6d: driver padrão, base do argumento de portabilidade.
 */

import type { PlugHubDriver } from "../adapter"

export class GenericMCPDriver implements PlugHubDriver {
  readonly name = "generic-mcp"

  toExternal(adapterOutput: Record<string, unknown>): unknown {
    // MCP usa JSON puro — passagem direta
    return adapterOutput
  }

  fromExternal(externalOutput: unknown): Record<string, unknown> {
    if (typeof externalOutput !== "object" || externalOutput === null) {
      return { raw: externalOutput }
    }
    return externalOutput as Record<string, unknown>
  }
}
