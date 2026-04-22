import { Pool, AgentType, Skill, Instance, CreatePoolInput, UpdatePoolInput, CreateAgentTypeInput, CreateSkillInput } from '@/types'

const getBaseUrl = () => {
  return import.meta.env.VITE_REGISTRY_URL || 'http://localhost:3300'
}

interface ListResponse<T> {
  items: T[]
  total: number
}

const headers = (tenantId: string) => ({
  'Content-Type': 'application/json',
  'x-tenant-id': tenantId
})

// Pools
export const listPools = async (tenantId: string): Promise<ListResponse<Pool>> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch pools')
  return response.json()
}

export const getPool = async (poolId: string, tenantId: string): Promise<Pool> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools/${poolId}`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch pool')
  return response.json()
}

export const createPool = async (data: CreatePoolInput, tenantId: string): Promise<Pool> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools`, {
    method: 'POST',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to create pool')
  return response.json()
}

export const updatePool = async (poolId: string, data: UpdatePoolInput, tenantId: string): Promise<Pool> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools/${poolId}`, {
    method: 'PUT',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to update pool')
  return response.json()
}

// Agent Types
export const listAgentTypes = async (tenantId: string, poolId?: string): Promise<ListResponse<AgentType>> => {
  const params = poolId ? `?pool_id=${poolId}` : ''
  const response = await fetch(`${getBaseUrl()}/v1/agent-types${params}`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch agent types')
  return response.json()
}

export const getAgentType = async (agentTypeId: string, tenantId: string): Promise<AgentType> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types/${agentTypeId}`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch agent type')
  return response.json()
}

export const createAgentType = async (data: CreateAgentTypeInput, tenantId: string): Promise<AgentType> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types`, {
    method: 'POST',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to create agent type')
  return response.json()
}

// Skills
export const listSkills = async (tenantId: string): Promise<ListResponse<Skill>> => {
  const response = await fetch(`${getBaseUrl()}/v1/skills`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch skills')
  return response.json()
}

export const getSkill = async (skillId: string, tenantId: string): Promise<Skill> => {
  const response = await fetch(`${getBaseUrl()}/v1/skills/${skillId}`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch skill')
  return response.json()
}

export const createSkill = async (data: CreateSkillInput, tenantId: string): Promise<Skill> => {
  const response = await fetch(`${getBaseUrl()}/v1/skills`, {
    method: 'POST',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to create skill')
  return response.json()
}

export const upsertSkill = async (skillId: string, data: CreateSkillInput, tenantId: string): Promise<Skill> => {
  const response = await fetch(`${getBaseUrl()}/v1/skills/${skillId}`, {
    method: 'PUT',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to upsert skill')
  return response.json()
}

export const deleteSkill = async (skillId: string, tenantId: string): Promise<void> => {
  const response = await fetch(`${getBaseUrl()}/v1/skills/${skillId}`, {
    method: 'DELETE',
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to delete skill')
}

// Instances
export const listInstances = async (tenantId: string, poolId?: string, status?: string): Promise<ListResponse<Instance>> => {
  const params = new URLSearchParams()
  if (poolId) params.append('pool_id', poolId)
  if (status) params.append('status', status)

  const response = await fetch(`${getBaseUrl()}/v1/instances?${params}`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch instances')
  return response.json()
}
