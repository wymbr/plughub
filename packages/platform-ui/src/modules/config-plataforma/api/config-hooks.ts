/**
 * config-hooks.ts — wraps config-api (port 3600)
 *
 * GET /config?tenant_id=...  → all namespaces
 * GET /config/{ns}?tenant_id=...  → one namespace
 * PUT /config/{ns}/{key}  → upsert (requires X-Admin-Token)
 * DELETE /config/{ns}/{key}?tenant_id=...  → remove override
 */
import { useCallback, useEffect, useState } from 'react'

/** Safely parse a Response as JSON, surfacing clear errors when the backend
 *  is unavailable (proxy returns HTML) rather than crashing with SyntaxError. */
async function safeJson<T>(res: Response): Promise<T> {
  const ct = res.headers.get('content-type') ?? ''
  if (!ct.includes('application/json') && !ct.includes('text/json')) {
    throw new Error(`API indisponível (HTTP ${res.status})`)
  }
  return res.json() as Promise<T>
}

export interface ConfigEntry {
  key:         string
  value:       unknown
  description: string
  tenant_id:   string | null   // null or "__global__" = platform default; real tenantId = override
  namespace?:  string
  updated_at?: string
}

export interface AllConfig {
  tenant_id: string
  config:    Record<string, Record<string, ConfigEntry>>
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
      .then(r => safeJson<AllConfig>(r).then(j => r.ok ? j : Promise.reject((j as {detail?: string}).detail ?? `HTTP ${r.status}`)))
      .then(j => { setData(j); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [tenantId, tick])

  return { data, loading, error, reload }
}

// ─── useNamespace ─────────────────────────────────────────────────────────────

export function useNamespace(tenantId: string, ns: string): {
  entries: Record<string, ConfigEntry>
  loading: boolean
  error:   string | null
  reload:  () => void
} {
  const [entries, setEntries] = useState<Record<string, ConfigEntry>>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [tick,    setTick]    = useState(0)

  const reload = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    if (!tenantId || !ns) return
    setLoading(true)
    setError(null)
    fetch(`/config/${ns}?tenant_id=${encodeURIComponent(tenantId)}`)
      .then(r => safeJson<{entries?: Record<string, ConfigEntry>; detail?: string}>(r)
        .then(j => r.ok ? j : Promise.reject(j.detail ?? `HTTP ${r.status}`)))
      .then(j => {
        // Normalise: if the API returned plain values instead of ConfigEntry objects,
        // wrap them so downstream code can safely access .value, .tenant_id, .description.
        const raw = j.entries ?? {}
        const normalised: Record<string, ConfigEntry> = {}
        for (const [k, v] of Object.entries(raw)) {
          if (v !== null && typeof v === 'object' && 'value' in (v as object)) {
            normalised[k] = v as ConfigEntry
          } else {
            normalised[k] = { key: k, value: v, description: '', tenant_id: '__global__' }
          }
        }
        setEntries(normalised)
        setLoading(false)
      })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [tenantId, ns, tick])

  return { entries, loading, error, reload }
}

// ─── useMultiNamespace ───────────────────────────────────────────────────────
// Fetches multiple namespaces in parallel and merges them.
// Each ConfigEntry gets entry.namespace set to its source namespace.

export function useMultiNamespace(tenantId: string, namespaceIds: string[]): {
  entriesByNs: Record<string, Record<string, ConfigEntry>>
  loading: boolean
  error:   string | null
  reload:  () => void
} {
  const [entriesByNs, setEntriesByNs] = useState<Record<string, Record<string, ConfigEntry>>>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [tick,    setTick]    = useState(0)

  const reload = useCallback(() => setTick(t => t + 1), [])
  const nsKey = namespaceIds.join(',')

  useEffect(() => {
    if (!tenantId || namespaceIds.length === 0) return
    setLoading(true)
    setError(null)

    const fetchNs = (ns: string) =>
      fetch(`/config/${ns}?tenant_id=${encodeURIComponent(tenantId)}`)
        .then(r => safeJson<{entries?: Record<string, ConfigEntry>; detail?: string}>(r)
          .then(j => r.ok ? j : Promise.reject(j.detail ?? `HTTP ${r.status}`)))
        .then(j => {
          const raw = j.entries ?? {}
          const normalised: Record<string, ConfigEntry> = {}
          for (const [k, v] of Object.entries(raw)) {
            if (v !== null && typeof v === 'object' && 'value' in (v as object)) {
              normalised[k] = { ...(v as ConfigEntry), namespace: ns }
            } else {
              normalised[k] = { key: k, value: v, description: '', tenant_id: '__global__', namespace: ns }
            }
          }
          return { ns, entries: normalised }
        })

    Promise.all(namespaceIds.map(fetchNs))
      .then(results => {
        const merged: Record<string, Record<string, ConfigEntry>> = {}
        for (const { ns, entries } of results) merged[ns] = entries
        setEntriesByNs(merged)
        setLoading(false)
      })
      .catch(e => { setError(String(e)); setLoading(false) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, nsKey, tick])

  return { entriesByNs, loading, error, reload }
}

// ─── putConfig ────────────────────────────────────────────────────────────────

// G-PROBE platform-wide: config-api aceita Bearer+ABAC (telas migradas) OU X-Admin-Token
// (legado). Quando `accessToken` é passado, manda `Authorization: Bearer`; senão cai no
// admin-token (compat com as telas ainda não migradas).
function _writeHeaders(adminToken: string, accessToken?: string): Record<string, string> {
  if (accessToken) return { 'Authorization': `Bearer ${accessToken}` }
  return adminToken ? { 'X-Admin-Token': adminToken } : {}
}

export async function putConfig(
  ns:          string,
  key:         string,
  value:       unknown,
  tenantId:    string | null,
  adminToken:  string,
  accessToken?: string,
): Promise<void> {
  const res = await fetch(`/config/${ns}/${key}`, {
    method:  'PUT',
    headers: {
      'Content-Type': 'application/json',
      ..._writeHeaders(adminToken, accessToken),
    },
    body: JSON.stringify({ value, tenant_id: tenantId || null }),
  })
  if (!res.ok) {
    const body = await safeJson<{ detail?: string }>(res).catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
}

// ─── deleteConfig ─────────────────────────────────────────────────────────────

export async function deleteConfig(
  ns:          string,
  key:         string,
  tenantId:    string | null,
  adminToken:  string,
  accessToken?: string,
): Promise<void> {
  const params = new URLSearchParams()
  if (tenantId) params.set('tenant_id', tenantId)
  const res = await fetch(`/config/${ns}/${key}?${params.toString()}`, {
    method:  'DELETE',
    headers: _writeHeaders(adminToken, accessToken),
  })
  if (!res.ok) {
    const body = await safeJson<{ detail?: string }>(res).catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
}
