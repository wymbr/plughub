import {
  Pool, AgentType, Skill, Instance,
  CreatePoolInput, UpdatePoolInput, CreateAgentTypeInput, CreateSkillInput,
  GatewayConfig, CreateGatewayConfigInput, UpdateGatewayConfigInput,
  AgentInstance, CreateHumanAgentInput, UpdateHumanAgentInput,
} from '@/types'

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

// Headers for routes that also require a user identity (e.g. channels, human-agent actions)
const operatorHeaders = (tenantId: string) => ({
  'Content-Type': 'application/json',
  'x-tenant-id': tenantId,
  'x-user-id': 'operator',
})

// Pools
export const listPools = async (tenantId: string): Promise<ListResponse<Pool>> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch pools')
  const data = await response.json()
  // API returns { pools: [...], total: n }
  return { items: data.pools ?? data.items ?? [], total: data.total ?? 0 }
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
  const data = await response.json()
  // API returns { agent_types: [...], total: n }
  return { items: data.agent_types ?? data.items ?? [], total: data.total ?? 0 }
}

export const getAgentType = async (agentTypeId: string, tenantId: string): Promise<AgentType> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types/${agentTypeId}`, {
    headers: headers(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch agent type')
  return response.json()
}

export const createAgentType = async (data: CreateAgentTypeInput, tenantId: string): Promise<AgentType> => {
  const payload = {
    ...data,
    pools:  data.pools.map(id => ({ pool_id: id })),
    skills: data.skills ?? [],
  }
  const response = await fetch(`${getBaseUrl()}/v1/agent-types`, {
    method: 'POST',
    headers: operatorHeaders(tenantId),
    body: JSON.stringify(payload)
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
  const data = await response.json()
  // API returns { skills: [...], total: n }
  return { items: data.skills ?? data.items ?? [], total: data.total ?? 0 }
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
  const data = await response.json()
  // API returns { instances: [...], total: n }
  return { items: data.instances ?? data.items ?? [], total: data.total ?? 0 }
}

// Human Agent Instances (framework=human)
export const listHumanInstances = async (tenantId: string, status?: string): Promise<ListResponse<AgentInstance>> => {
  const params = new URLSearchParams({ framework: 'human' })
  if (status) params.append('status', status)
  const response = await fetch(`${getBaseUrl()}/v1/instances?${params}`, {
    headers: operatorHeaders(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch human instances')
  const data = await response.json()
  return { items: data.instances ?? data.items ?? [], total: data.total ?? 0 }
}

export const instanceAction = async (
  instanceId: string,
  action: 'pause' | 'resume' | 'force_logout',
  tenantId: string,
): Promise<void> => {
  const response = await fetch(`${getBaseUrl()}/v1/instances/${instanceId}`, {
    method: 'PATCH',
    headers: operatorHeaders(tenantId),
    body: JSON.stringify({ action }),
  })
  if (!response.ok) throw new Error('Failed to perform instance action')
}

// Human Agent Types (framework=human)
export const listHumanAgentTypes = async (tenantId: string): Promise<ListResponse<AgentType>> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types?framework=human`, {
    headers: operatorHeaders(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch human agent types')
  const data = await response.json()
  return { items: data.agent_types ?? data.items ?? [], total: data.total ?? 0 }
}

export const createHumanAgentType = async (data: CreateHumanAgentInput, tenantId: string): Promise<AgentType> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types`, {
    method: 'POST',
    headers: operatorHeaders(tenantId),
    body: JSON.stringify({ ...data, framework: 'human', execution_model: 'stateful' }),
  })
  if (!response.ok) throw new Error('Failed to create human agent type')
  return response.json()
}

export const updateHumanAgentType = async (agentTypeId: string, data: UpdateHumanAgentInput, tenantId: string): Promise<AgentType> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types/${agentTypeId}`, {
    method: 'PUT',
    headers: operatorHeaders(tenantId),
    body: JSON.stringify(data),
  })
  if (!response.ok) throw new Error('Failed to update human agent type')
  return response.json()
}

export const deleteAgentType = async (agentTypeId: string, tenantId: string): Promise<void> => {
  const response = await fetch(`${getBaseUrl()}/v1/agent-types/${agentTypeId}`, {
    method: 'DELETE',
    headers: operatorHeaders(tenantId),
  })
  if (!response.ok) throw new Error('Failed to delete agent type')
}

// Channels (GatewayConfig)
export const listChannels = async (tenantId: string): Promise<ListResponse<GatewayConfig>> => {
  const response = await fetch(`${getBaseUrl()}/v1/channels`, {
    headers: operatorHeaders(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch channels')
  return response.json()
}

export const createChannel = async (data: CreateGatewayConfigInput, tenantId: string): Promise<GatewayConfig> => {
  const response = await fetch(`${getBaseUrl()}/v1/channels`, {
    method: 'POST',
    headers: operatorHeaders(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to create channel config')
  return response.json()
}

export const updateChannel = async (id: string, data: UpdateGatewayConfigInput, tenantId: string): Promise<GatewayConfig> => {
  const response = await fetch(`${getBaseUrl()}/v1/channels/${id}`, {
    method: 'PUT',
    headers: operatorHeaders(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw new Error('Failed to update channel config')
  return response.json()
}

export const deleteChannel = async (id: string, tenantId: string): Promise<void> => {
  const response = await fetch(`${getBaseUrl()}/v1/channels/${id}`, {
    method: 'DELETE',
    headers: operatorHeaders(tenantId)
  })
  if (!response.ok) throw new Error('Failed to delete channel config')
}
