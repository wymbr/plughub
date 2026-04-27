/**
 * registry-hooks.ts
 * React hooks and API helpers for the Registry Management panel.
 *
 * Agent Registry runs on port 3300. All routes are proxied via Vite:
 *   /v1/pools       → agent-registry
 *   /v1/agent-types → agent-registry
 *   /v1/skills      → agent-registry
 *   /v1/instances   → agent-registry
 */
import { useCallback, useEffect, useState } from 'react'
import type {
  RegistryPool,
  RegistryAgentType,
  RegistrySkill,
  RegistryInstance,
} from '../types'

const REGISTRY_BASE = import.meta.env.VITE_REGISTRY_API_BASE_URL ?? ''

// ── Shared fetch helper ────────────────────────────────────────────────────

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${REGISTRY_BASE}${path}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts?.headers ?? {}) },
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}: ${body}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as T
}

// ── Pools ──────────────────────────────────────────────────────────────────

export function usePools(
  tenantId: string,
  intervalMs = 30_000,
): { pools: RegistryPool[]; loading: boolean; refresh: () => Promise<void> } {
  const [pools, setPools]     = useState<RegistryPool[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const data = await apiFetch<{ pools: RegistryPool[] }>(
        `/v1/pools?status=active`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      setPools(data.pools ?? [])
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { pools, loading, refresh }
}

export async function createPool(
  tenantId: string,
  body: {
    pool_id:       string
    channel_types: string[]
    sla_target_ms: number
    description?:  string
  },
): Promise<RegistryPool> {
  return apiFetch<RegistryPool>('/v1/pools', {
    method:  'POST',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify(body),
  })
}

export async function updatePool(
  tenantId: string,
  poolId:   string,
  updates:  Partial<Pick<RegistryPool, 'description' | 'channel_types' | 'sla_target_ms' | 'status'>>,
): Promise<RegistryPool> {
  return apiFetch<RegistryPool>(`/v1/pools/${encodeURIComponent(poolId)}`, {
    method:  'PUT',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify(updates),
  })
}

// ── Agent Types ────────────────────────────────────────────────────────────

export function useAgentTypes(
  tenantId:  string,
  intervalMs = 30_000,
): { agentTypes: RegistryAgentType[]; loading: boolean; refresh: () => Promise<void> } {
  const [agentTypes, setAgentTypes] = useState<RegistryAgentType[]>([])
  const [loading, setLoading]       = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const data = await apiFetch<{ agent_types: RegistryAgentType[] }>(
        `/v1/agent-types`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      setAgentTypes(data.agent_types ?? [])
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { agentTypes, loading, refresh }
}

export async function createAgentType(
  tenantId: string,
  body: {
    agent_type_id:           string
    framework:               string
    execution_model:         'stateless' | 'stateful'
    role?:                   string
    pools:                   string[]          // pool_id list
    skills?:                 Array<{ skill_id: string; version_policy: string }>
    max_concurrent_sessions?: number
    permissions?:            string[]
    prompt_id?:              string
  },
): Promise<RegistryAgentType> {
  return apiFetch<RegistryAgentType>('/v1/agent-types', {
    method:  'POST',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify({
      ...body,
      pools:  body.pools.map(id => ({ pool_id: id })),
      skills: body.skills ?? [],
    }),
  })
}

export async function deleteAgentType(
  tenantId:     string,
  agentTypeId:  string,
): Promise<void> {
  return apiFetch<void>(`/v1/agent-types/${encodeURIComponent(agentTypeId)}`, {
    method:  'DELETE',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
  })
}

// ── Skills ─────────────────────────────────────────────────────────────────

export function useSkills(
  tenantId:  string,
  intervalMs = 30_000,
): { skills: RegistrySkill[]; loading: boolean; refresh: () => Promise<void> } {
  const [skills, setSkills]   = useState<RegistrySkill[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const data = await apiFetch<{ skills: RegistrySkill[] }>(
        `/v1/skills`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      setSkills(data.skills ?? [])
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { skills, loading, refresh }
}

export async function fetchSkill(
  tenantId: string,
  skillId:  string,
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/v1/skills/${encodeURIComponent(skillId)}`, {
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
  })
}

export async function upsertSkill(
  tenantId: string,
  skillId:  string,
  body:     Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/v1/skills/${encodeURIComponent(skillId)}`, {
    method:  'PUT',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify(body),
  })
}

export async function deleteSkill(
  tenantId: string,
  skillId:  string,
): Promise<void> {
  return apiFetch<void>(`/v1/skills/${encodeURIComponent(skillId)}`, {
    method:  'DELETE',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
  })
}

// ── Instances (read-only) ──────────────────────────────────────────────────

export function useInstances(
  tenantId:  string,
  poolId?:   string,
  intervalMs = 15_000,
): { instances: RegistryInstance[]; total: number; loading: boolean; refresh: () => Promise<void> } {
  const [instances, setInstances] = useState<RegistryInstance[]>([])
  const [total, setTotal]         = useState(0)
  const [loading, setLoading]     = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '100' })
      if (poolId) params.set('pool_id', poolId)
      const data = await apiFetch<{ instances: RegistryInstance[]; total: number }>(
        `/v1/instances?${params}`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      setInstances(data.instances ?? [])
      setTotal(data.total ?? 0)
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId, poolId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instances, total, loading, refresh }
}
