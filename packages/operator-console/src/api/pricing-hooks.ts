/**
 * pricing-hooks.ts
 * React hooks for the Pricing API (packages/pricing-api — port 3900).
 */
import { useCallback, useEffect, useState } from 'react'

const PRICING_BASE = import.meta.env.VITE_PRICING_API_BASE_URL ?? ''

// ─── Types ────────────────────────────────────────────────────────────────────

export interface InvoiceLineItem {
  resource_type: string
  label:         string
  quantity:      number
  unit_price:    number
  days_active:   number | null
  billing_days:  number
  subtotal:      number
}

export interface ReserveGroup {
  pool_id:      string
  label:        string
  active:       boolean
  days_active:  number
  billing_days: number
  items:        InvoiceLineItem[]
  subtotal:     number
}

export interface Invoice {
  tenant_id:       string
  installation_id: string
  cycle_start:     string
  cycle_end:       string
  billing_days:    number
  currency:        string
  base_items:      InvoiceLineItem[]
  reserve_groups:  ReserveGroup[]
  base_total:      number
  reserve_total:   number
  grand_total:     number
  generated_at:    string
}

export interface InstallationResource {
  id:              string
  tenant_id:       string
  installation_id: string
  resource_type:   string
  quantity:        number
  pool_type:       'base' | 'reserve'
  reserve_pool_id: string | null
  active:          boolean
  billing_unit:    string
  label:           string
  updated_at:      string
}

export interface ActivationLogEntry {
  id:                string
  tenant_id:         string
  reserve_pool_id:   string
  activation_date:   string
  deactivation_date: string | null
  activated_by:      string
}

// ─── useInvoice ───────────────────────────────────────────────────────────────

export function useInvoice(
  tenantId:       string,
  installationId: string = 'default',
  cycleStart?:    string,
  cycleEnd?:      string,
): {
  invoice:  Invoice | null
  loading:  boolean
  error:    string | null
  refresh:  () => Promise<void>
} {
  const [invoice,  setInvoice]  = useState<Invoice | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ installation_id: installationId })
      if (cycleStart) params.set('cycle_start', cycleStart)
      if (cycleEnd)   params.set('cycle_end',   cycleEnd)
      const res = await fetch(`${PRICING_BASE}/v1/pricing/invoice/${encodeURIComponent(tenantId)}?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setInvoice(await res.json() as Invoice)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [tenantId, installationId, cycleStart, cycleEnd])

  useEffect(() => { void refresh() }, [refresh])

  return { invoice, loading, error, refresh }
}

// ─── useResources ─────────────────────────────────────────────────────────────

export function useResources(
  tenantId:       string,
  installationId: string = 'default',
): {
  resources: InstallationResource[]
  loading:   boolean
  error:     string | null
  refresh:   () => Promise<void>
} {
  const [resources, setResources] = useState<InstallationResource[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `${PRICING_BASE}/v1/pricing/resources/${encodeURIComponent(tenantId)}?installation_id=${encodeURIComponent(installationId)}`
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { resources: InstallationResource[] }
      setResources(data.resources)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [tenantId, installationId])

  useEffect(() => { void refresh() }, [refresh])

  return { resources, loading, error, refresh }
}

// ─── useActivationLog ─────────────────────────────────────────────────────────

export function useActivationLog(
  tenantId:      string,
  reservePoolId?: string,
): {
  logs:    ActivationLogEntry[]
  loading: boolean
  refresh: () => Promise<void>
} {
  const [logs,    setLogs]    = useState<ActivationLogEntry[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (reservePoolId) params.set('reserve_pool_id', reservePoolId)
      const res = await fetch(
        `${PRICING_BASE}/v1/pricing/reserve/${encodeURIComponent(tenantId)}/activity?${params}`
      )
      if (res.ok) {
        const data = await res.json() as { logs: ActivationLogEntry[] }
        setLogs(data.logs)
      }
    } catch { /* stale data ok */ }
    finally { setLoading(false) }
  }, [tenantId, reservePoolId])

  useEffect(() => { void refresh() }, [refresh])

  return { logs, loading, refresh }
}

// ─── Mutations ────────────────────────────────────────────────────────────────

export async function activateReservePool(
  tenantId:    string,
  poolId:      string,
  adminToken:  string,
): Promise<void> {
  const res = await fetch(
    `${PRICING_BASE}/v1/pricing/reserve/${encodeURIComponent(tenantId)}/${encodeURIComponent(poolId)}/activate`,
    { method: 'POST', headers: { 'X-Admin-Token': adminToken } },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string }
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
}

export async function deactivateReservePool(
  tenantId:    string,
  poolId:      string,
  adminToken:  string,
): Promise<void> {
  const res = await fetch(
    `${PRICING_BASE}/v1/pricing/reserve/${encodeURIComponent(tenantId)}/${encodeURIComponent(poolId)}/deactivate`,
    { method: 'POST', headers: { 'X-Admin-Token': adminToken } },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string }
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
}
