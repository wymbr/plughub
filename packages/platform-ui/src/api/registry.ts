import {
  Pool, Skill, Instance,
  CreatePoolInput, UpdatePoolInput, CreateSkillInput,
  GatewayConfig, CreateGatewayConfigInput, UpdateGatewayConfigInput,
  AgentInstance,
  ChannelEndpoint, CreateChannelEndpointInput, UpdateChannelEndpointInput,
  ChannelEndpointChannel,
} from '@/types'

import { getAccessToken } from '@/auth/token-store'

const getBaseUrl = () => {
  return import.meta.env.VITE_REGISTRY_URL || 'http://localhost:3300'
}

interface ListResponse<T> {
  items: T[]
  total: number
}

// G-PROBE platform-wide: o agent-registry gateia as mutações de config (pools/skills/
// channels/channel-endpoints) em Bearer+ABAC `config.resources`. Anexa o Bearer do
// operador (do token-store) quando presente; GETs são abertos (header ignorado).
const bearer = (): Record<string, string> => {
  const t = getAccessToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

const headers = (tenantId: string) => ({
  'Content-Type': 'application/json',
  'x-tenant-id': tenantId,
  ...bearer(),
})

// Headers for routes that also require a user identity (e.g. channels, human-agent actions)
const operatorHeaders = (tenantId: string) => ({
  'Content-Type': 'application/json',
  'x-tenant-id': tenantId,
  'x-user-id': 'operator',
  ...bearer(),
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

// Extract a human-readable error from a non-OK pool response. The registry
// returns { error, details? } (e.g. 422 when Σ session_reservation > contracted C).
const poolError = async (response: Response, fallback: string): Promise<Error> => {
  const body = await response.json().catch(() => ({})) as { error?: string }
  return new Error(body.error || fallback)
}

export const createPool = async (data: CreatePoolInput, tenantId: string): Promise<Pool> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools`, {
    method: 'POST',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw await poolError(response, 'Failed to create pool')
  return response.json()
}

export const updatePool = async (poolId: string, data: UpdatePoolInput, tenantId: string): Promise<Pool> => {
  const response = await fetch(`${getBaseUrl()}/v1/pools/${poolId}`, {
    method: 'PUT',
    headers: headers(tenantId),
    body: JSON.stringify(data)
  })
  if (!response.ok) throw await poolError(response, 'Failed to update pool')
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

// (Human Agent Types CRUD removed — AgentType entity retired. Human agents are
// login-driven; live instances are managed via listHumanInstances/instanceAction.)

// Channels (GatewayConfig)
export const listChannels = async (tenantId: string): Promise<ListResponse<GatewayConfig>> => {
  const response = await fetch(`${getBaseUrl()}/v1/channels`, {
    headers: operatorHeaders(tenantId)
  })
  if (!response.ok) throw new Error('Failed to fetch channels')
  const data = await response.json()
  // API returns { channels: [...], total: n } — normalise to ListResponse like
  // listPools does. Without this the callers read `.items` (undefined) and the
  // integrations list renders empty even though the records exist.
  return { items: data.channels ?? data.items ?? [], total: data.total ?? 0 }
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

// ── Channel Endpoints ─────────────────────────────────────────────────────────

export const listChannelEndpoints = async (
  tenantId:         string,
  channel?:         ChannelEndpointChannel | string,
  gatewayConfigId?: string,
): Promise<ChannelEndpoint[]> => {
  const params = new URLSearchParams()
  if (channel)         params.set('channel',           channel)
  if (gatewayConfigId) params.set('gateway_config_id', gatewayConfigId)
  const qs = params.toString() ? `?${params.toString()}` : ''
  const response = await fetch(`${getBaseUrl()}/v1/channel-endpoints${qs}`, {
    headers: headers(tenantId),
  })
  if (!response.ok) throw new Error('Failed to fetch channel endpoints')
  const data = await response.json() as { endpoints: ChannelEndpoint[] }
  return data.endpoints
}

export const createChannelEndpoint = async (
  data: CreateChannelEndpointInput,
  tenantId: string,
): Promise<ChannelEndpoint> => {
  const response = await fetch(`${getBaseUrl()}/v1/channel-endpoints`, {
    method: 'POST',
    headers: headers(tenantId),
    body: JSON.stringify(data),
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({})) as { error?: string }
    throw new Error(err.error ?? 'Failed to create channel endpoint')
  }
  return response.json()
}

export const updateChannelEndpoint = async (
  id: string,
  data: UpdateChannelEndpointInput,
  tenantId: string,
): Promise<ChannelEndpoint> => {
  const response = await fetch(`${getBaseUrl()}/v1/channel-endpoints/${id}`, {
    method: 'PUT',
    headers: headers(tenantId),
    body: JSON.stringify(data),
  })
  if (!response.ok) throw new Error('Failed to update channel endpoint')
  return response.json()
}

export const deleteChannelEndpoint = async (id: string, tenantId: string): Promise<void> => {
  const response = await fetch(`${getBaseUrl()}/v1/channel-endpoints/${id}`, {
    method: 'DELETE',
    headers: headers(tenantId),
  })
  if (!response.ok) throw new Error('Failed to delete channel endpoint')
}

