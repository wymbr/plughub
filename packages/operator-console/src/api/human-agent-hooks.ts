/**
 * human-agent-hooks.ts
 * React hooks and API helpers for the Human Agent Management panel.
 *
 * Uses the existing agent-registry endpoints:
 *   GET  /v1/instances?framework=human   — live human agent instances
 *   PATCH /v1/instances/:id              — operator actions (pause/resume/force_logout)
 *   GET  /v1/agent-types?framework=human — human agent type profiles (via RegistryPanel hooks)
 *   POST /v1/agent-types                 — create human agent profile
 *   PUT  /v1/agent-types/:id             — update profile
 *   DELETE /v1/agent-types/:id           — deprecate profile
 */
import { useCallback, useEffect, useState } from 'react'
import type { RegistryInstance, RegistryAgentType } from '../types'

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

// ── Human agent instances (live status) ───────────────────────────────────

export function useHumanInstances(
  tenantId:  string,
  intervalMs = 10_000,
): { instances: RegistryInstance[]; total: number; loading: boolean; refresh: () => Promise<void> } {
  const [instances, setInstances] = useState<RegistryInstance[]>([])
  const [total,     setTotal]     = useState(0)
  const [loading,   setLoading]   = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const data = await apiFetch<{ instances: RegistryInstance[]; total: number }>(
        `/v1/instances?framework=human&limit=200`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      setInstances(data.instances ?? [])
      setTotal(data.total ?? 0)
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instances, total, loading, refresh }
}

export type InstanceAction = 'pause' | 'resume' | 'force_logout'

export async function instanceAction(
  tenantId:   string,
  instanceId: string,
  action:     InstanceAction,
): Promise<RegistryInstance> {
  return apiFetch<RegistryInstance>(`/v1/instances/${encodeURIComponent(instanceId)}`, {
    method:  'PATCH',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify({ action }),
  })
}

// ── Human agent type profiles ──────────────────────────────────────────────

export function useHumanAgentTypes(
  tenantId:  string,
  intervalMs = 30_000,
): { agentTypes: RegistryAgentType[]; loading: boolean; refresh: () => Promise<void> } {
  const [agentTypes, setAgentTypes] = useState<RegistryAgentType[]>([])
  const [loading,    setLoading]    = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const data = await apiFetch<{ agent_types: RegistryAgentType[] }>(
        `/v1/agent-types`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      // Filter client-side for human framework
      const all = data.agent_types ?? []
      setAgentTypes(all.filter(a => a.framework === 'human'))
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

export async function createHumanAgent(
  tenantId: string,
  body: {
    agent_type_id:            string
    role?:                    string
    pools:                    string[]
    max_concurrent_sessions?: number
    permissions?:             string[]
    prompt_id?:               string
  },
): Promise<RegistryAgentType> {
  return apiFetch<RegistryAgentType>('/v1/agent-types', {
    method:  'POST',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify({
      ...body,
      framework:       'human',
      execution_model: 'stateful',
      pools:           body.pools.map(id => ({ pool_id: id })),
      skills:          [],
    }),
  })
}

export async function updateHumanAgent(
  tenantId:     string,
  agentTypeId:  string,
  updates: {
    role?:                    string
    max_concurrent_sessions?: number
    permissions?:             string[]
    prompt_id?:               string
  },
): Promise<RegistryAgentType> {
  return apiFetch<RegistryAgentType>(`/v1/agent-types/${encodeURIComponent(agentTypeId)}`, {
    method:  'PUT',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify(updates),
  })
}

export async function deprecateHumanAgent(
  tenantId:    string,
  agentTypeId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/agent-types/${encodeURIComponent(agentTypeId)}`, {
    method:  'DELETE',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
  })
}
