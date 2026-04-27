/**
 * channel-hooks.ts
 * React hooks and API helpers for the Channel Configuration panel.
 *
 * Agent Registry runs on port 3300. Channel routes are proxied via Vite:
 *   /v1/channels → agent-registry
 */
import { useCallback, useEffect, useState } from 'react'
import type { GatewayConfig, ChannelType }  from '../types'

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

// ── Channels ───────────────────────────────────────────────────────────────

export function useChannels(
  tenantId:  string,
  intervalMs = 30_000,
): { channels: GatewayConfig[]; loading: boolean; refresh: () => Promise<void> } {
  const [channels, setChannels] = useState<GatewayConfig[]>([])
  const [loading, setLoading]   = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const data = await apiFetch<{ channels: GatewayConfig[] }>(
        `/v1/channels`,
        { headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' } },
      )
      setChannels(data.channels ?? [])
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { channels, loading, refresh }
}

export async function createChannel(
  tenantId: string,
  body: {
    channel:       ChannelType
    display_name:  string
    active?:       boolean
    credentials?:  Record<string, string>
    settings?:     Record<string, unknown>
  },
): Promise<GatewayConfig> {
  return apiFetch<GatewayConfig>('/v1/channels', {
    method:  'POST',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify(body),
  })
}

export async function updateChannel(
  tenantId:  string,
  configId:  string,
  updates: {
    display_name?:  string
    active?:        boolean
    credentials?:   Record<string, string>
    settings?:      Record<string, unknown>
  },
): Promise<GatewayConfig> {
  return apiFetch<GatewayConfig>(`/v1/channels/${encodeURIComponent(configId)}`, {
    method:  'PUT',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
    body:    JSON.stringify(updates),
  })
}

export async function deleteChannel(
  tenantId:  string,
  configId:  string,
): Promise<void> {
  return apiFetch<void>(`/v1/channels/${encodeURIComponent(configId)}`, {
    method:  'DELETE',
    headers: { 'x-tenant-id': tenantId, 'x-user-id': 'operator' },
  })
}
