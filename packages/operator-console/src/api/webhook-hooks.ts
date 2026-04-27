/**
 * webhook-hooks.ts
 * React hooks and API helpers for the Webhook Trigger management panel.
 *
 * All admin mutations require an X-Admin-Token header.
 * The plain token is returned ONCE from create/rotate and must be captured immediately.
 */
import { useCallback, useEffect, useState } from 'react'
import type { Webhook, WebhookDelivery } from '../types'

const WORKFLOW_API_BASE = import.meta.env.VITE_WORKFLOW_API_BASE_URL ?? ''

// ── List ─────────────────────────────────────────────────────────────────────

export function useWebhooks(
  tenantId:   string,
  adminToken: string,
  intervalMs  = 15_000,
): { webhooks: Webhook[]; loading: boolean; refresh: () => Promise<void> } {
  const [webhooks, setWebhooks] = useState<Webhook[]>([])
  const [loading, setLoading]   = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId || !adminToken) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '100' })
      const res = await fetch(`${WORKFLOW_API_BASE}/v1/workflow/webhooks?${params}`, {
        headers: { 'X-Admin-Token': adminToken },
      })
      if (res.ok) setWebhooks((await res.json()) as Webhook[])
    } catch { /* stale data acceptable */ }
    finally { setLoading(false) }
  }, [tenantId, adminToken])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { webhooks, loading, refresh }
}

// ── Deliveries ────────────────────────────────────────────────────────────────

export function useWebhookDeliveries(
  webhookId:  string | null,
  adminToken: string,
  limit       = 20,
): { deliveries: WebhookDelivery[]; loading: boolean; refresh: () => Promise<void> } {
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([])
  const [loading, setLoading]       = useState(false)

  const refresh = useCallback(async () => {
    if (!webhookId || !adminToken) return
    setLoading(true)
    try {
      const res = await fetch(
        `${WORKFLOW_API_BASE}/v1/workflow/webhooks/${encodeURIComponent(webhookId)}/deliveries?limit=${limit}`,
        { headers: { 'X-Admin-Token': adminToken } },
      )
      if (res.ok) setDeliveries((await res.json()) as WebhookDelivery[])
    } catch { /* stale */ }
    finally { setLoading(false) }
  }, [webhookId, adminToken, limit])

  useEffect(() => { refresh() }, [refresh])

  return { deliveries, loading, refresh }
}

// ── Mutations ─────────────────────────────────────────────────────────────────

export async function createWebhook(
  tenantId:        string,
  flowId:          string,
  description:     string,
  contextOverride: Record<string, unknown>,
  adminToken:      string,
): Promise<{ webhook: Webhook; token: string }> {
  const res = await fetch(`${WORKFLOW_API_BASE}/v1/workflow/webhooks`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': adminToken },
    body: JSON.stringify({
      tenant_id:        tenantId,
      flow_id:          flowId,
      description,
      context_override: contextOverride,
    }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  const { token, ...webhook } = data
  return { webhook: webhook as Webhook, token: token as string }
}

export async function patchWebhook(
  webhookId:  string,
  updates: { active?: boolean; description?: string; context_override?: Record<string, unknown> },
  adminToken: string,
): Promise<Webhook> {
  const res = await fetch(`${WORKFLOW_API_BASE}/v1/workflow/webhooks/${encodeURIComponent(webhookId)}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': adminToken },
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as Webhook
}

export async function rotateWebhookToken(
  webhookId:  string,
  adminToken: string,
): Promise<{ webhook: Webhook; token: string }> {
  const res = await fetch(
    `${WORKFLOW_API_BASE}/v1/workflow/webhooks/${encodeURIComponent(webhookId)}/rotate`,
    { method: 'POST', headers: { 'X-Admin-Token': adminToken } },
  )
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  const { token, ...webhook } = data
  return { webhook: webhook as Webhook, token: token as string }
}

export async function deleteWebhook(
  webhookId:  string,
  adminToken: string,
): Promise<void> {
  const res = await fetch(
    `${WORKFLOW_API_BASE}/v1/workflow/webhooks/${encodeURIComponent(webhookId)}`,
    { method: 'DELETE', headers: { 'X-Admin-Token': adminToken } },
  )
  if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
}
