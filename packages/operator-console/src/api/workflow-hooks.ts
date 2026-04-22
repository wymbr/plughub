/**
 * workflow-hooks.ts
 * React hooks for workflow instance API.
 */
import { useCallback, useEffect, useState } from 'react'
import type { WorkflowInstance } from '../types'

const WORKFLOW_API_BASE = import.meta.env.VITE_WORKFLOW_API_BASE_URL ?? 'http://localhost:3800'

export function useWorkflowInstances(
  tenantId: string,
  status?: string,
  intervalMs = 10_000,
): { instances: WorkflowInstance[]; loading: boolean; refresh: () => Promise<void> } {
  const [instances, setInstances] = useState<WorkflowInstance[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId })
      if (status) params.append('status', status)
      params.append('limit', '100')
      const res = await fetch(`${WORKFLOW_API_BASE}/v1/workflow/instances?${params}`)
      if (res.ok) {
        const data = (await res.json()) as { instances: WorkflowInstance[] }
        setInstances(data.instances ?? [])
      }
    } catch {
      // stale data acceptable
    } finally {
      setLoading(false)
    }
  }, [tenantId, status])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instances, loading, refresh }
}

export function useWorkflowInstance(
  instanceId: string | null,
  intervalMs = 10_000,
): { instance: WorkflowInstance | null; loading: boolean; refresh: () => Promise<void> } {
  const [instance, setInstance] = useState<WorkflowInstance | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!instanceId) return
    setLoading(true)
    try {
      const res = await fetch(`${WORKFLOW_API_BASE}/v1/workflow/instances/${encodeURIComponent(instanceId)}`)
      if (res.ok) {
        setInstance((await res.json()) as WorkflowInstance)
      }
    } catch {
      // stale data acceptable
    } finally {
      setLoading(false)
    }
  }, [instanceId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instance, loading, refresh }
}

export async function cancelInstance(instanceId: string, tenantId: string): Promise<void> {
  const res = await fetch(`${WORKFLOW_API_BASE}/v1/workflow/instances/${encodeURIComponent(instanceId)}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id: tenantId }),
  })
  if (!res.ok) {
    throw new Error(`Failed to cancel instance: HTTP ${res.status}`)
  }
}
