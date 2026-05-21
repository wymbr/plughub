/**
 * workflow-client.ts
 * HTTP client for the workflow-api.
 */

import type { WorkerSettings } from './config'

export interface WorkflowInstance {
  id: string
  installation_id: string
  organization_id: string
  tenant_id: string
  flow_id: string
  session_id?: string
  /**
   * The real customer session that originated this workflow.
   * When present, the engine uses this as the ContextStore sessionId
   * so that @ctx.* reads/writes target {tenant}:ctx:{origin_session_id}.
   */
  origin_session_id?: string
  pool_id?: string
  status: 'active' | 'suspended' | 'completed' | 'failed' | 'timed_out' | 'cancelled'
  current_step?: string
  pipeline_state: Record<string, unknown>
  suspend_reason?: 'approval' | 'input' | 'webhook' | 'timer'
  resume_token?: string
  resume_expires_at?: string
  suspended_at?: string
  resumed_at?: string
  completed_at?: string
  outcome?: string
  created_at: string
  metadata: Record<string, unknown>
  /** Arc 10: set when this instance was initiated via creates_journey:true or journey_start MCP tool */
  journey_id?: string
}

export interface PersistSuspendParams {
  step_id: string
  resume_token: string
  reason: string
  timeout_hours: number
  business_hours: boolean
  calendar_id?: string
  metadata?: Record<string, unknown>
}

export interface PersistCollectParams {
  step_id:        string
  collect_token:  string
  target:         { type: string; id: string }
  channel?:       string   // optional — channel-gateway selects by requires[] when absent
  interaction:    string
  prompt:         string
  options?:       Array<{ id: string; label: string }>
  fields?:        Array<{ id: string; label: string; type: string }>
  scheduled_at?:  string
  delay_hours?:   number
  timeout_hours:  number
  business_hours: boolean
  calendar_id?:   string
  campaign_id?:   string
}

export class WorkflowClient {
  private baseUrl: string

  constructor(settings: WorkerSettings) {
    this.baseUrl = settings.workflowApiUrl
  }

  async getInstance(instanceId: string): Promise<WorkflowInstance> {
    const res = await fetch(`${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}`)
    if (!res.ok) {
      throw new Error(`Failed to fetch instance ${instanceId}: HTTP ${res.status}`)
    }
    return (await res.json()) as WorkflowInstance
  }

  async complete(instanceId: string, outcome: string, pipelineState: Record<string, unknown>): Promise<void> {
    const res = await fetch(`${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outcome, pipeline_state: pipelineState }),
    })
    if (!res.ok) {
      throw new Error(`Failed to complete instance ${instanceId}: HTTP ${res.status}`)
    }
  }

  async fail(instanceId: string, error: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/fail`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error }),
    })
    if (!res.ok) {
      throw new Error(`Failed to fail instance ${instanceId}: HTTP ${res.status}`)
    }
  }

  async persistSuspend(
    instanceId: string,
    params: PersistSuspendParams,
  ): Promise<{ resume_expires_at: string }> {
    const res = await fetch(`${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/persist-suspend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`Failed to persist suspend for instance ${instanceId}: HTTP ${res.status} — ${text}`)
    }
    return (await res.json()) as { resume_expires_at: string }
  }

  async persistCollect(
    instanceId: string,
    params: PersistCollectParams,
  ): Promise<{ send_at: string; expires_at: string }> {
    const res = await fetch(`${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/collect/persist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`Failed to persist collect for instance ${instanceId}: HTTP ${res.status} — ${text}`)
    }
    const body = (await res.json()) as { send_at: string; expires_at: string }
    return { send_at: body.send_at, expires_at: body.expires_at }
  }

  /**
   * Arc 10 Phase B — creates_journey: true support.
   *
   * Called by EngineRunner when the skill YAML has creates_journey:true and the
   * running instance does not yet have a journey_id.  Idempotent on the server
   * side — safe to call even if a race created the journey first.
   *
   * Returns the journey_id of the created (or existing) Journey.
   */
  async createJourneyForInstance(
    instanceId: string,
    tenantId: string,
  ): Promise<{ journey_id: string }> {
    const res = await fetch(`${this.baseUrl}/v1/journeys/from-instance/${encodeURIComponent(instanceId)}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-tenant-id': tenantId,
        'x-internal': '1',
      },
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`Failed to create journey for instance ${instanceId}: HTTP ${res.status} — ${text}`)
    }
    const body = (await res.json()) as { journey_id: string }
    return { journey_id: body.journey_id }
  }
}
