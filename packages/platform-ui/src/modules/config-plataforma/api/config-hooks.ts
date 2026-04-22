/**
 * config-hooks.ts — wraps config-api (port 3600)
 *
 * GET /config?tenant_id=...  → all namespaces
 * GET /config/{ns}?tenant_id=...  → one namespace
 * PUT /config/{ns}/{key}  → upsert (requires X-Admin-Token)
 * DELETE /config/{ns}/{key}?tenant_id=...  → remove override
 */
import { useCallback, useEffect, useState } from 'react'

export interface ConfigEntry {
  key:         string
  value:       unknown
  description: string
}

export interface AllConfig {
  tenant_id: string
  config:    Record<string, Record<string, unknown>>
}

// ─── useAllConfig ─────────────────────────────────────────────────────────────

export function useAllConfig(tenantId: string): {
  data:    AllConfig | null
  loading: boolean
  error:   string | null
  reload:  () => void
} {
  const [data,    setData]    = useState<AllConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [tick,    setTick]    = useState(0)

  const reload = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    fetch(`/config?tenant_id=${encodeURIComponent(tenantId)}`)
      .then(r => r.ok ? r.json() : r.json().then(b => Promise.reject(b?.detail ?? `HTTP ${r.status}`)))
      .then(j => { setData(j); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [tenantId, tick])

  return { data, loading, error, reload }
}

// ─── useNamespace ─────────────────────────────────────────────────────────────

export function useNamespace(tenantId: string, ns: string): {
  entries: Record<string, unknown>
  loading: boolean
  error:   string | null
  reload:  () => void
} {
  const [entries, setEntries] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [tick,    setTick]    = useState(0)

  const reload = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    if (!tenantId || !ns) return
    setLoading(true)
    setError(null)
    fetch(`/config/${ns}?tenant_id=${encodeURIComponent(tenantId)}`)
      .then(r => r.ok ? r.json() : r.json().then(b => Promise.reject(b?.detail ?? `HTTP ${r.status}`)))
      .then(j => { setEntries(j.entries ?? {}); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [tenantId, ns, tick])

  return { entries, loading, error, reload }
}

// ─── putConfig ────────────────────────────────────────────────────────────────

export async function putConfig(
  ns:         string,
  key:        string,
  value:      unknown,
  tenantId:   string | null,
  adminToken: string,
): Promise<void> {
  const res = await fetch(`/config/${ns}/${key}`, {
    method:  'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...(adminToken ? { 'X-Admin-Token': adminToken } : {}),
    },
    body: JSON.stringify({ value, tenant_id: tenantId || null }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
}

// ─── deleteConfig ─────────────────────────────────────────────────────────────

export async function deleteConfig(
  ns:         string,
  key:        string,
  tenantId:   string | null,
  adminToken: string,
): Promise<void> {
  const params = new URLSearchParams()
  if (tenantId) params.set('tenant_id', tenantId)
  const res = await fetch(`/config/${ns}/${key}?${params.toString()}`, {
    method:  'DELETE',
    headers: adminToken ? { 'X-Admin-Token': adminToken } : {},
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
}
