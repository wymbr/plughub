import { apiFetch } from '@/api/apiFetch'
/**
 * outbound/api.ts
 * Types + REST client for the Outbound module (mailings + campaigns + deliveries).
 *
 * Backend: mailing-api (port 3660) proxied under /v1/mailings and /v1/campaigns
 * (header X-Tenant-ID). Webhook pools come from agent-registry (/v1/pools, header
 * x-tenant-id); calendars from calendar-api (/v1/calendars). Mirrors the Zod contract
 * in @plughub/schemas/outbound.ts. Shared by OutboundPage's tabs.
 */

const ORG_ID = import.meta.env.VITE_CALENDAR_ORG_ID ?? 'org-default'

// ── Types (mirror @plughub/schemas/outbound.ts) ──────────────────────────────

export type DedupPolicy = 'customer' | 'customer_context' | 'none'
export type EntryStatus = 'active' | 'expired' | 'unsubscribed' | 'invalid'
export type CampaignStatus = 'active' | 'paused' | 'completed' | 'archived'
export type OrderDir = 'asc' | 'desc'
export type OrderFieldType = 'text' | 'number'
export type DeliveryResult =
  | 'claimed' | 'pending' | 'contacted' | 'responded'
  | 'failed' | 'skipped_ineligible' | 'suppressed'

/** column_map — file import PARSING config on the mailing (Fase 4). */
export interface ColumnMap {
  customer_id_column?: string | null
  anchors: Array<{ kind: string; column: string }>
  contacts: Record<string, string>   // channel → column
  metadata_columns?: string[]
}

export interface Mailing {
  id: string
  tenant_id: string
  name: string
  description: string | null
  dedup_policy: DedupPolicy
  metadata_contract: string | null
  entry_ttl_seconds: number | null
  column_map: ColumnMap | null
  entry_count?: number   // active entries (list endpoint only)
  created_at: string
  updated_at: string
}

export interface MailingEntry {
  id: string
  mailing_id: string
  customer_id: string | null
  contacts: Record<string, string>
  metadata: Record<string, unknown>
  dedup_key: string
  source: string | null
  status: EntryStatus
  added_at: string
  expires_at: string | null
}

export interface OrderField {
  path: string
  dir: OrderDir
  type: OrderFieldType
}

export interface Campaign {
  id: string
  tenant_id: string
  name: string
  mailing_id: string
  pool_id: string
  selection: Record<string, unknown> | null
  ordering: OrderField[]
  channel_policy: Record<string, unknown>
  contact_calendar_id: string | null
  transactional: boolean
  batch_size: number
  retry: Record<string, unknown>
  agenda_id: string | null
  status: CampaignStatus
  created_at: string
  updated_at: string
}

export interface CampaignDelivery {
  id: string
  campaign_id: string
  mailing_entry_id: string
  claimed_at: string | null
  contacted_at: string | null
  session_id: string | null
  root_session_id: string | null
  result: DeliveryResult
  attempts: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface ImportReport {
  import_id: string
  total: number
  added: number
  deduped: number
  resolved: number
  unresolved: number
  rejected: Array<{ row: number; reason: string }>
}

export interface WebhookPool {
  pool_id: string
  channel_types: string[]
}

// ── Fetch helpers ────────────────────────────────────────────────────────────

// ⚠️ RENOMEADO em 2026-08-31 (AUT-19). Este helper JA se chamava `apiFetch`, e a
// varredura da AUT-18 (2026-08-30) trocou o `fetch(` do corpo dele por `apiFetch(`,
// criando **auto-recursao infinita**, mais um `import { apiFetch }` no topo que colide
// com o nome. Hoje ele DELEGA ao apiFetch compartilhado — que e o que a AUT-18 queria —
// e mantem o que so ele fazia: Content-Type e levantar em nao-2xx.
async function jsonFetch(path: string, opts?: RequestInit) {
  const res = await apiFetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts?.headers ?? {}) },
    ...opts,
  })
  if (!res.ok) {
    const msg = await res.text().catch(() => String(res.status))
    throw new Error(msg)
  }
  if (res.status === 204) return null
  return res.json()
}

/** mailing-api client. Tenant read from X-Tenant-ID. */
export function makeOutboundApi(tenantId: string) {
  const th = { 'X-Tenant-ID': tenantId }
  return {
    // ── Mailings ──
    listMailings: (): Promise<{ mailings: Mailing[]; total: number }> =>
      jsonFetch('/v1/mailings', { headers: th }),
    createMailing: (body: object): Promise<Mailing> =>
      jsonFetch('/v1/mailings', { method: 'POST', headers: th, body: JSON.stringify(body) }),
    updateMailing: (id: string, body: object): Promise<Mailing> =>
      jsonFetch(`/v1/mailings/${id}`, { method: 'PATCH', headers: th, body: JSON.stringify(body) }),
    removeMailing: (id: string) =>
      jsonFetch(`/v1/mailings/${id}`, { method: 'DELETE', headers: th }),
    listEntries: (id: string, status?: string): Promise<{ entries: MailingEntry[]; total: number }> =>
      jsonFetch(`/v1/mailings/${id}/entries${status ? `?status=${status}` : ''}`, { headers: th }),
    /** File import (multipart) — parses via the mailing's column_map (Fase 4). */
    importFile: async (id: string, file: File): Promise<ImportReport> => {
      const fd = new FormData()
      fd.append('file', file)
      const res = await apiFetch(`/v1/mailings/${id}/import`, { method: 'POST', headers: th, body: fd })
      if (!res.ok) throw new Error(await res.text().catch(() => String(res.status)))
      return res.json()
    },

    // ── Campaigns ──
    listCampaigns: (): Promise<{ campaigns: Campaign[]; total: number }> =>
      jsonFetch('/v1/campaigns', { headers: th }),
    createCampaign: (body: object): Promise<Campaign> =>
      jsonFetch('/v1/campaigns', { method: 'POST', headers: th, body: JSON.stringify(body) }),
    updateCampaign: (id: string, body: object): Promise<Campaign> =>
      jsonFetch(`/v1/campaigns/${id}`, { method: 'PATCH', headers: th, body: JSON.stringify(body) }),
    listDeliveries: (id: string, limit = 200): Promise<{ deliveries: CampaignDelivery[]; total: number }> =>
      jsonFetch(`/v1/campaigns/${id}/deliveries?limit=${limit}`, { headers: th }),
  }
}

/** Webhook-only pools from agent-registry (campaign target). */
export async function fetchWebhookPools(tenantId: string): Promise<WebhookPool[]> {
  const data = await jsonFetch('/v1/pools', { headers: { 'x-tenant-id': tenantId } })
  const pools = (data?.pools ?? []) as Array<{ pool_id: string; channel_types?: string[] }>
  return pools
    .filter(p => (p.channel_types ?? []).includes('webhook'))
    .map(p => ({ pool_id: p.pool_id, channel_types: p.channel_types ?? [] }))
}

/** Calendars from calendar-api (contact-window dropdown). */
export async function fetchCalendars(tenantId: string): Promise<Array<{ id: string; name: string }>> {
  try {
    const data = await jsonFetch(`/v1/calendars?organization_id=${ORG_ID}&tenant_id=${tenantId}`)
    return (data ?? []).map((c: { id: string; name: string }) => ({ id: c.id, name: c.name }))
  } catch {
    return []
  }
}

// ── formatting ───────────────────────────────────────────────────────────────

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}
