/**
 * config-hooks.ts
 * React hooks for the Config API (packages/config-api — port 3600).
 *
 * Two-level model: global defaults (tenant_id = '__global__') + tenant overrides.
 * The GET /config?tenant_id=xxx endpoint returns resolved values (tenant wins over global).
 * The GET /config/{ns}/raw?tenant_id=xxx returns only explicit overrides for that tenant.
 */

import { useState, useEffect, useCallback } from 'react'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ConfigEntry {
  namespace:   string
  key:         string
  value:       unknown
  tenant_id:   string | null   // null = global default
  description: string
  updated_at:  string
}

/** GET /config?tenant_id={t} returns namespace → key → ConfigEntry */
export type ConfigAllResponse = Record<string, Record<string, ConfigEntry>>

// ─── Base URL (can be overridden via env var) ─────────────────────────────────

const CONFIG_BASE = import.meta.env.VITE_CONFIG_API_BASE_URL ?? ''

// ─── fetch helpers ────────────────────────────────────────────────────────────

async function cfetch(path: string): Promise<unknown> {
  const res = await fetch(`${CONFIG_BASE}${path}`)
  if (!res.ok) throw new Error(`Config API ${path} → ${res.status}`)
  return res.json()
}

async function cput(
  path: string,
  body: unknown,
  adminToken: string,
): Promise<unknown> {
  const res = await fetch(`${CONFIG_BASE}${path}`, {
    method:  'PUT',
    headers: {
      'Content-Type':  'application/json',
      'X-Admin-Token': adminToken,
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Config PUT ${path} → ${res.status}: ${text}`)
  }
  return res.json()
}

async function cdel(path: string, adminToken: string): Promise<void> {
  const res = await fetch(`${CONFIG_BASE}${path}`, {
    method:  'DELETE',
    headers: { 'X-Admin-Token': adminToken },
  })
  if (!res.ok && res.status !== 404) {
    const text = await res.text()
    throw new Error(`Config DELETE ${path} → ${res.status}: ${text}`)
  }
}

// ─── Hook: useConfigAll ───────────────────────────────────────────────────────

/**
 * Fetches all resolved config for a tenant.
 * Refetches whenever tenantId changes or refresh() is called.
 */
export function useConfigAll(tenantId: string): {
  config:   ConfigAllResponse | null
  loading:  boolean
  error:    string | null
  refresh:  () => Promise<void>
} {
  const [config,  setConfig]  = useState<ConfigAllResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const fetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const raw  = await cfetch(`/config?tenant_id=${encodeURIComponent(tenantId)}`)
      // API returns { tenant_id, config: { namespace: { key: ConfigEntry } } }
      const data = (raw as { config?: ConfigAllResponse }).config ?? (raw as ConfigAllResponse)
      setConfig(data)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => { void fetch() }, [fetch])

  return { config, loading, error, refresh: fetch }
}

// ─── Hook: useConfigNamespace ─────────────────────────────────────────────────

/**
 * Fetches all resolved entries for a single namespace.
 */
export function useConfigNamespace(tenantId: string, namespace: string): {
  entries:  Record<string, ConfigEntry> | null
  loading:  boolean
  error:    string | null
  refresh:  () => Promise<void>
} {
  const [entries, setEntries] = useState<Record<string, ConfigEntry> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const fetch = useCallback(async () => {
    if (!namespace) return
    setLoading(true)
    setError(null)
    try {
      const raw  = await cfetch(
        `/config/${encodeURIComponent(namespace)}?tenant_id=${encodeURIComponent(tenantId)}`
      )
      // API returns { tenant_id, namespace, entries: { key: ConfigEntry } }
      const data = (raw as { entries?: Record<string, ConfigEntry> }).entries
             ?? (raw as Record<string, ConfigEntry>)
      setEntries(data)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [tenantId, namespace])

  useEffect(() => { void fetch() }, [fetch])

  return { entries, loading, error, refresh: fetch }
}

// ─── Mutation helpers (not hooks — called imperatively) ───────────────────────

/**
 * Upserts a config value.
 * tenantId = null → sets global default; tenantId = string → tenant override.
 */
export async function putConfig(
  namespace:   string,
  key:         string,
  value:       unknown,
  tenantId:    string | null,
  description: string,
  adminToken:  string,
): Promise<void> {
  await cput(
    `/config/${encodeURIComponent(namespace)}/${encodeURIComponent(key)}`,
    { tenant_id: tenantId, value, description },
    adminToken,
  )
}

/**
 * Deletes a config override for a tenant (or the global default if tenantId = null).
 */
export async function deleteConfig(
  namespace:  string,
  key:        string,
  tenantId:   string | null,
  adminToken: string,
): Promise<void> {
  const qs = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  await cdel(
    `/config/${encodeURIComponent(namespace)}/${encodeURIComponent(key)}${qs}`,
    adminToken,
  )
}
