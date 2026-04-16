/**
 * infra/registry-client.ts
 * Cliente HTTP para o Agent Registry.
 * Usado por agent_login para validar agent_type_id e obter max_concurrent_sessions + pools.
 *
 * Consome: GET /v1/agent-types/:agent_type_id  (header X-Tenant-Id: {tenant_id})
 * Spec: 4.5
 */

// ─── Tipos ────────────────────────────────────────────────────────────────────

export interface AgentTypeInfo {
  agent_type_id:           string
  max_concurrent_sessions: number
  pools:                   string[]
  /** Permissões MCP autorizadas — ex: ["mcp-server-crm:customer_get"]. Spec 4.6k. */
  permissions:             string[]
}

export interface RegistryClient {
  getAgentType(tenantId: string, agentTypeId: string): Promise<AgentTypeInfo | null>
}

// ─── Cliente de produção (HTTP) ───────────────────────────────────────────────

export function createRegistryClient(baseUrl: string): RegistryClient {
  return {
    async getAgentType(tenantId, agentTypeId) {
      const url = `${baseUrl}/v1/agent-types/${encodeURIComponent(agentTypeId)}`
      const res = await fetch(url, {
        headers: {
          "X-Tenant-Id": tenantId,
          "Accept":      "application/json",
        },
      })
      if (res.status === 404) return null
      if (!res.ok) {
        throw new Error(`Agent Registry retornou ${res.status} para agent_type_id '${agentTypeId}'`)
      }
      const data = await res.json() as Record<string, unknown>

      // Extrair pools: o endpoint retorna { pools: [{ pool_id, ... }] }
      const rawPools = data["pools"] as Array<Record<string, unknown>> | undefined
      const pools = rawPools
        ? rawPools.map(p => (p["pool_id"] as string | undefined) ?? "").filter(Boolean)
        : []

      const rawPerms  = data["permissions"] as string[] | undefined
      const permissions = Array.isArray(rawPerms) ? rawPerms : []

      return {
        agent_type_id:           data["agent_type_id"] as string,
        max_concurrent_sessions: (data["max_concurrent_sessions"] as number | undefined) ?? 1,
        pools,
        permissions,
      }
    },
  }
}

// ─── Stub para testes ─────────────────────────────────────────────────────────

export function createStubRegistryClient(agentTypes: AgentTypeInfo[]): RegistryClient {
  return {
    async getAgentType(_tenantId, agentTypeId) {
      const found = agentTypes.find(a => a.agent_type_id === agentTypeId)
      if (!found) return null
      // Garante que permissions existe mesmo em stubs criados antes de 4.6k
      if (!found.permissions) found.permissions = []
      return found
    },
  }
}
