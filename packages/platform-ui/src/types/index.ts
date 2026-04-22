export type UserRole = 'operator' | 'supervisor' | 'admin' | 'developer' | 'business'

export interface Session {
  userId: string
  name: string
  role: UserRole
  tenantId: string
  installationId: string
  locale: 'pt-BR' | 'en'
}

export interface Pool {
  pool_id: string
  tenant_id: string
  description?: string
  channel_types: string[]
  sla_target_ms: number
  status: string
  created_at: string
  updated_at: string
}

export interface CreatePoolInput {
  pool_id: string
  description?: string
  channel_types: string[]
  sla_target_ms: number
}

export interface UpdatePoolInput {
  description?: string
  channel_types?: string[]
  sla_target_ms?: number
}

export interface AgentType {
  agent_type_id: string
  tenant_id: string
  framework: string
  execution_model: string
  pools: string[]
  skills: string[]
  status: string
  created_at: string
}

export interface CreateAgentTypeInput {
  agent_type_id: string
  framework: string
  execution_model: string
  pools: string[]
  skills: string[]
}

export interface Skill {
  skill_id: string
  tenant_id: string
  name: string
  version: string
  description?: string
  classification?: {
    type?: string
    vertical?: string
    domain?: string
  }
  status: string
  created_at: string
}

export interface CreateSkillInput {
  skill_id: string
  name: string
  version: string
  description?: string
  classification?: {
    type?: string
    vertical?: string
    domain?: string
  }
}

export interface Instance {
  instance_id: string
  agent_type_id: string
  pool_id: string
  tenant_id: string
  status: string
  updated_at: string
}
