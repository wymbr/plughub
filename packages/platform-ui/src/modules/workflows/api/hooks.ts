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
