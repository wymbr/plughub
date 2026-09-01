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
  execution_model:         string
  pools:                   string[]
  /** Permissões MCP autorizadas — ex: ["mcp-server-crm:customer_get"]. Spec 4.6k. */
  permissions:             string[]
  /**
   * Propósito DECLARADO NO REGISTRY: "executor" | "orchestrator" | "evaluator".
   *
   * Entrada de autorização — por isso vem daqui e nunca do input do `agent_login`.
   * Um agente declarar "sou evaluator" seria asserção, não autorização.
   * Não confundir com o papel de participação na sessão (primary/specialist/
   * supervisor), que é fato de (participante, sessão) e vive no escopo da sessão.
   *
   * Opcional no tipo para não obrigar fixtures/stubs antigos a declará-lo — os
   * consumidores aplicam `?? "executor"`, que fecha por omissão.
   */
}

export interface RegistryClient {
  getAgentType(tenantId: string, agentTypeId: string): Promise<AgentTypeInfo | null>
}

// ─── Cliente de produção (HTTP) ───────────────────────────────────────────────

export function createRegistryClient(baseUrl: string): RegistryClient {
  return {
    async getAgentType(tenantId, agentTypeId) {
      // AgentType entity retired. In the deploy-driven model an agent's identity
      // IS its deployed skill_id, so agent_login validates against the skill.
      // Per-agent config (capacity, pools, permissions) now lives on the pool's
      // deploy slot, not the agent identity — external login gets the permissive
      // default (empty permissions ⇒ no MCP tool filtering, the deploy-driven norm).
      const url = `${baseUrl}/v1/skills/${encodeURIComponent(agentTypeId)}`
      const res = await fetch(url, {
        headers: {
          "X-Tenant-Id": tenantId,
          "Accept":      "application/json",
        },
      })
      if (res.status === 404) return null
      if (!res.ok) {
        throw new Error(`Agent Registry retornou ${res.status} para skill '${agentTypeId}'`)
      }
      const data = await res.json() as Record<string, unknown>

      return {
        agent_type_id:           (data["skill_id"] as string | undefined) ?? agentTypeId,
        max_concurrent_sessions: 1,
        execution_model:         "stateless",
        pools:                   [],
        permissions:             [],
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
      // Garante que campos adicionados retroativamente existem mesmo em stubs antigos
      if (!found.permissions)    found.permissions    = []
      if (!found.execution_model) found.execution_model = "stateless"
      return found
    },
  }
}
