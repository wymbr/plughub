/**
 * hooks.ts — Workflows module
 * Wraps workflow-api (port 3800) via Vite proxy /v1/workflow
 */
import { useCallback, useEffect, useState } from 'react'

async function safeJson<T>(res: Response): Promise<T> {
  const ct = res.headers.get('content-type') ?? ''
  if (!ct.includes('application/json') && !ct.includes('text/json')) {
    throw new Error(`API indisponível (HTTP ${res.status})`)
  }
  return res.json() as Promise<T>
}

export type WorkflowStatus = 'active' | 'suspended' | 'completed' | 'failed' | 'timed_out' | 'cancelled'
export type SuspendReason  = 'approval' | 'input' | 'webhook' | 'timer'

export interface WorkflowInstance {
  id:               string
  installation_id:  string
  organization_id:  string
  tenant_id:        string
  flow_id:          string
  session_id?:      string
  pool_id?:         string
  status:           WorkflowStatus
  current_step?:    string
  pipeline_state:   Record<string, unknown>
  suspend_reason?:  SuspendReason
  resume_token?:    string
  resume_expires_at?: string
  suspended_at?:    string
  resumed_at?:      string
  completed_at?:    string
  outcome?:         string
  created_at:       string
  metadata:         Record<string, unknown>
}

// ─── useWorkflowInstances ─────────────────────────────────────────────────────

export function useWorkflowInstances(
  tenantId:  string,
  status?:   WorkflowStatus | undefined,
  intervalMs = 10_000,
): { instances: WorkflowInstance[]; loading: boolean; refresh: () => void } {
  const [instances, setInstances] = useState<WorkflowInstance[]>([])
  const [loading,   setLoading]   = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '200' })
      if (status) params.set('status', status)
      const res = await fetch(`/v1/workflow/instances?${params.toString()}`)
      if (res.ok) {
        const data = await safeJson<{ instances?: WorkflowInstance[] }>(res)
        setInstances(data.instances ?? [])
      }
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId, status])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instances, loading, refresh }
}

// ─── useWorkflowInstance ──────────────────────────────────────────────────────

export function useWorkflowInstance(
  instanceId: string | null,
  intervalMs  = 10_000,
): { instance: WorkflowInstance | null; loading: boolean; refresh: () => void } {
  const [instance, setInstance] = useState<WorkflowInstance | null>(null)
  const [loading,  setLoading]  = useState(false)

  const refresh = useCallback(async () => {
    if (!instanceId) return
    setLoading(true)
    try {
      const res = await fetch(`/v1/workflow/instances/${encodeURIComponent(instanceId)}`)
      if (res.ok) setInstance(await safeJson<WorkflowInstance>(res))
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [instanceId])

  useEffect(() => {
    setInstance(null)
    if (!instanceId) return
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, instanceId, intervalMs])

  return { instance, loading, refresh }
}

// ─── triggerWorkflow ──────────────────────────────────────────────────────────

export async function triggerWorkflow(payload: {
  tenant_id:       string
  installation_id: string
  organization_id: string
  flow_id:         string
  metadata?:       Record<string, unknown>
  pipeline_state?: Record<string, unknown>
}): Promise<WorkflowInstance> {
  const res = await fetch('/v1/workflow/trigger', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  })
  if (!res.ok) {
    const body = await safeJson<{ detail?: string }>(res).catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return safeJson(res)
}

// ─── cancelWorkflow ───────────────────────────────────────────────────────────

export async function cancelWorkflow(instanceId: string, tenantId: string): Promise<void> {
  const res = await fetch(`/v1/workflow/instances/${encodeURIComponent(instanceId)}/cancel`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ tenant_id: tenantId }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

// ─── Webhook types ────────────────────────────────────────────────────────────

export interface Webhook {
  id:                string
  tenant_id:         string
  flow_id:           string
  description:       string
  token_prefix:      string
  active:            boolean
  trigger_count:     number
  last_triggered_at: string | null
  context_override:  Record<string, unknown>
  created_at:        string
  updated_at:        string
}

export interface WebhookDelivery {
  id:           string
  webhook_id:   string
  tenant_id:    string
  triggered_at: string
  status_code:  number
  payload_hash: string
  instance_id:  string | null
  error:        string | null
  latency_ms:   number | null
}

// ─── useWebhooks ──────────────────────────────────────────────────────────────

export function useWebhooks(
  tenantId:   string,
  adminToken: string,
  intervalMs  = 15_000,
): { webhooks: Webhook[]; loading: boolean; refresh: () => void } {
  const [webhooks, setWebhooks] = useState<Webhook[]>([])
  const [loading,  setLoading]  = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId || !adminToken) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '100' })
      const res = await fetch(`/v1/workflow/webhooks?${params}`, {
        headers: { 'X-Admin-Token': adminToken },
      })
      if (res.ok) setWebhooks(await safeJson<Webhook[]>(res))
    } catch { /* stale */ }
    finally { setLoading(false) }
  }, [tenantId, adminToken])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { webhooks, loading, refresh }
}

// ─── useWebhookDeliveries ─────────────────────────────────────────────────────

export function useWebhookDeliveries(
  webhookId:  string | null,
  adminToken: string,
  limit       = 20,
): { deliveries: WebhookDelivery[]; loading: boolean; refresh: () => void } {
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([])
  const [loading,    setLoading]    = useState(false)

  const refresh = useCallback(async () => {
    if (!webhookId || !adminToken) return
    setLoading(true)
    try {
      const res = await fetch(
        `/v1/workflow/webhooks/${encodeURIComponent(webhookId)}/deliveries?limit=${limit}`,
        { headers: { 'X-Admin-Token': adminToken } },
      )
      if (res.ok) setDeliveries(await safeJson<WebhookDelivery[]>(res))
    } catch { /* stale */ }
    finally { setLoading(false) }
  }, [webhookId, adminToken, limit])

  useEffect(() => { refresh() }, [refresh])

  return { deliveries, loading, refresh }
}

// ─── Webhook mutations ────────────────────────────────────────────────────────

export async function createWebhookApi(
  tenantId:        string,
  flowId:          string,
  description:     string,
  contextOverride: Record<string, unknown>,
  adminToken:      string,
): Promise<{ webhook: Webhook; token: string }> {
  const res = await fetch('/v1/workflow/webhooks', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': adminToken },
    body: JSON.stringify({ tenant_id: tenantId, flow_id: flowId, description, context_override: contextOverride }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await safeJson<Webhook & { token: string }>(res)
  const { token, ...webhook } = data
  return { webhook: webhook as Webhook, token }
}

export async function patchWebhookApi(
  webhookId:  string,
  updates:    { active?: boolean; description?: string; context_override?: Record<string, unknown> },
  adminToken: string,
): Promise<Webhook> {
  const res = await fetch(`/v1/workflow/webhooks/${encodeURIComponent(webhookId)}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': adminToken },
    body:    JSON.stringify(updates),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return safeJson<Webhook>(res)
}

export async function rotateWebhookTokenApi(
  webhookId:  string,
  adminToken: string,
): Promise<{ webhook: Webhook; token: string }> {
  const res = await fetch(`/v1/workflow/webhooks/${encodeURIComponent(webhookId)}/rotate`, {
    method: 'POST', headers: { 'X-Admin-Token': adminToken },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await safeJson<Webhook & { token: string }>(res)
  const { token, ...webhook } = data
  return { webhook: webhook as Webhook, token }
}

export async function deleteWebhookApi(webhookId: string, adminToken: string): Promise<void> {
  const res = await fetch(`/v1/workflow/webhooks/${encodeURIComponent(webhookId)}`, {
    method: 'DELETE', headers: { 'X-Admin-Token': adminToken },
  })
  if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
}
