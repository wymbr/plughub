/**
 * engine-runner.ts
 * Wraps SkillFlowEngine and wires it with workflow-api callbacks.
 */

import Redis from 'ioredis'
import { SkillFlowEngine } from '@plughub/skill-flow-engine'
import type { SkillFlowEngineConfig, ResumeContext } from '@plughub/skill-flow-engine'
import type { WorkerSettings } from './config'
import { WorkflowClient, type WorkflowInstance, type PersistSuspendParams } from './workflow-client'

export interface EngineRunnerConfig {
  settings: WorkerSettings
  redis: Redis
  workflowClient: WorkflowClient
}

export class EngineRunner {
  private engine: SkillFlowEngine
  private workflowClient: WorkflowClient
  private settings: WorkerSettings

  constructor(config: EngineRunnerConfig) {
    this.workflowClient = config.workflowClient
    this.settings = config.settings

    const engineConfig: SkillFlowEngineConfig = {
      redis: config.redis,
      mcpCall: this.mcpCall.bind(this),
      aiGatewayCall: this.aiGatewayCall.bind(this),
      persistSuspend: this.persistSuspend.bind(this),
    }
    this.engine = new SkillFlowEngine(engineConfig)
  }

  async runInstance(
    instance: WorkflowInstance,
    resumeContext?: ResumeContext,
  ): Promise<void> {
    // Validate that flow_definition exists in metadata
    const flowDefinition = (instance.metadata as Record<string, unknown>)['flow_definition']
    if (!flowDefinition) {
      console.warn(
        `Instance ${instance.id}: no flow_definition in metadata, failing`,
      )
      await this.workflowClient.fail(instance.id, 'Missing flow_definition in metadata')
      return
    }

    try {
      const result = await this.engine.run({
        tenantId: instance.tenant_id,
        sessionId: instance.id,
        customerId: 'workflow',
        skillId: instance.flow_id,
        flow: flowDefinition as never,
        sessionContext: ((instance.pipeline_state as Record<string, unknown>)['contact_context'] as Record<string, unknown>) ?? {},
        instanceId: instance.id,
        ...(resumeContext ? { resumeContext } : {}),
      })

      if ('error' in result) {
        console.error(
          `Instance ${instance.id}: engine returned precondition error`,
          result,
        )
        return
      }

      const { outcome, pipeline_state } = result

      // If outcome is 'suspended', persistSuspend already handled it
      if (outcome === 'suspended') {
        console.log(`Instance ${instance.id}: suspended (handled by persistSuspend callback)`)
        return
      }

      // Otherwise, mark as completed
      await this.workflowClient.complete(instance.id, outcome, pipeline_state)
      console.log(`Instance ${instance.id}: completed with outcome "${outcome}"`)
    } catch (err) {
      const error = String(err instanceof Error ? err.message : err)
      console.error(`Instance ${instance.id}: error during execution: ${error}`)
      try {
        await this.workflowClient.fail(instance.id, error)
      } catch (failErr) {
        console.error(`Instance ${instance.id}: failed to report error: ${failErr}`)
      }
    }
  }

  private async mcpCall(
    tool: string,
    input: unknown,
    _mcpServer?: string,
  ): Promise<unknown> {
    // Stub: forward to MCP server HTTP endpoint
    const url = `${this.settings.mcpServerUrl}/mcp`
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool, input }),
    })
    if (!res.ok) {
      throw new Error(`MCP call failed: HTTP ${res.status}`)
    }
    return res.json()
  }

  private async aiGatewayCall(payload: {
    prompt_id: string
    input: Record<string, unknown>
    output_schema: Record<string, unknown>
    session_id: string
    attempt: number
  }): Promise<unknown> {
    // Stub: forward to AI Gateway HTTP endpoint
    const url = `${this.settings.aiGatewayUrl}/infer`
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      throw new Error(`AI Gateway call failed: HTTP ${res.status}`)
    }
    return res.json()
  }

  private async persistSuspend(params: {
    tenant_id: string
    session_id: string
    step_id: string
    resume_token: string
    reason: string
    timeout_hours: number
    business_hours: boolean
    calendar_id?: string
    metadata?: Record<string, unknown>
  }): Promise<{ resume_expires_at: string }> {
    const persistParams: PersistSuspendParams = {
      step_id: params.step_id,
      resume_token: params.resume_token,
      reason: params.reason,
      timeout_hours: params.timeout_hours,
      business_hours: params.business_hours,
      ...(params.calendar_id ? { calendar_id: params.calendar_id } : {}),
      ...(params.metadata ? { metadata: params.metadata } : {}),
    }
    return this.workflowClient.persistSuspend(params.session_id, persistParams)
  }
}
