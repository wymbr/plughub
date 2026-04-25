/**
 * engine-runner.ts
 * Wraps SkillFlowEngine and wires it with real HTTP callbacks:
 *   - mcpCall        → POST {mcpServerUrl}/mcp (JSON-RPC tools/call)
 *   - aiGatewayCall  → POST {aiGatewayUrl}/v1/reason
 *   - persistSuspend → POST {workflowApiUrl}/v1/workflow/instances/{id}/persist-suspend
 *   - persistCollect → POST {workflowApiUrl}/v1/workflow/instances/{id}/collect/persist
 */

import Redis from 'ioredis'
import { SkillFlowEngine } from '@plughub/skill-flow-engine'
import type { SkillFlowEngineConfig, ResumeContext } from '@plughub/skill-flow-engine'
import type { WorkerSettings } from './config'
import {
  WorkflowClient,
  type WorkflowInstance,
  type PersistSuspendParams,
  type PersistCollectParams,
} from './workflow-client'

export interface EngineRunnerConfig {
  settings:       WorkerSettings
  redis:          Redis
  workflowClient: WorkflowClient
}

// ── JSON-RPC request ID counter (monotonic, per-process) ─────────────────────
let _rpcId = 0
function nextRpcId(): number {
  return ++_rpcId
}

export class EngineRunner {
  private engine: SkillFlowEngine
  private workflowClient: WorkflowClient
  private settings: WorkerSettings

  constructor(config: EngineRunnerConfig) {
    this.workflowClient = config.workflowClient
    this.settings = config.settings

    const engineConfig: SkillFlowEngineConfig = {
      redis:           config.redis,
      mcpCall:         this.mcpCall.bind(this),
      aiGatewayCall:   this.aiGatewayCall.bind(this),
      persistSuspend:  this.persistSuspend.bind(this),
      persistCollect:  this.persistCollect.bind(this),
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
        tenantId:    instance.tenant_id,
        sessionId:   instance.id,
        customerId:  'workflow',
        skillId:     instance.flow_id,
        flow:        flowDefinition as never,
        sessionContext:
          ((instance.pipeline_state as Record<string, unknown>)['contact_context'] as Record<string, unknown>) ?? {},
        instanceId:  instance.id,
        ...(resumeContext ? { resumeContext } : {}),
      })

      if ('error' in result) {
        console.error(`Instance ${instance.id}: engine returned precondition error`, result)
        return
      }

      const { outcome, pipeline_state } = result

      // If outcome is 'suspended' or 'collected', the respective persist callback
      // has already handled the workflow-api state transition — nothing more to do.
      if (outcome === 'suspended' || outcome === 'collected') {
        console.log(`Instance ${instance.id}: ${outcome} (handled by callback)`)
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

  // ── MCP call — JSON-RPC tools/call ────────────────────────────────────────

  private async mcpCall(
    tool: string,
    input: unknown,
    _mcpServer?: string,
  ): Promise<unknown> {
    const url = `${this.settings.mcpServerUrl}/mcp`

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (this.settings.mcpSessionToken) {
      headers['Authorization'] = `Bearer ${this.settings.mcpSessionToken}`
    }

    const rpcBody = {
      jsonrpc: '2.0',
      id:      nextRpcId(),
      method:  'tools/call',
      params:  {
        name:      tool,
        arguments: input ?? {},
      },
    }

    const res = await fetch(url, {
      method:  'POST',
      headers,
      body:    JSON.stringify(rpcBody),
    })

    if (!res.ok) {
      const text = await res.text()
      throw new Error(`MCP call "${tool}" failed: HTTP ${res.status} — ${text}`)
    }

    const json = (await res.json()) as {
      jsonrpc: string
      id:      number
      result?: unknown
      error?:  { code: number; message: string; data?: unknown }
    }

    if (json.error) {
      throw new Error(`MCP RPC error for "${tool}": [${json.error.code}] ${json.error.message}`)
    }

    // MCP SDK wraps tool output in result.content[0].text (when text content)
    // or result directly for structured outputs.
    const result = json.result as Record<string, unknown> | undefined
    if (result && Array.isArray(result['content'])) {
      const first = (result['content'] as Array<{ type: string; text?: string }>)[0]
      if (first?.type === 'text' && typeof first.text === 'string') {
        try {
          return JSON.parse(first.text)
        } catch {
          return first.text
        }
      }
    }
    return result ?? json.result
  }

  // ── AI Gateway call — POST /v1/reason ─────────────────────────────────────

  private async aiGatewayCall(payload: {
    prompt_id:     string
    input:         Record<string, unknown>
    output_schema: Record<string, unknown>
    session_id:    string
    attempt:       number
  }): Promise<unknown> {
    const url = `${this.settings.aiGatewayUrl}/v1/reason`

    const res = await fetch(url, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        session_id:    payload.session_id,
        prompt_id:     payload.prompt_id,
        input:         payload.input,
        output_schema: payload.output_schema,
        attempt:       payload.attempt,
      }),
    })

    if (!res.ok) {
      const text = await res.text()
      throw new Error(`AI Gateway reason call failed: HTTP ${res.status} — ${text}`)
    }

    const body = (await res.json()) as { result: unknown }
    // The reason endpoint returns { session_id, result, model_used, ... }
    // The engine only consumes the 'result' field.
    return body.result ?? body
  }

  // ── persistSuspend — POST /v1/workflow/instances/{id}/persist-suspend ──────

  private async persistSuspend(params: {
    tenant_id:     string
    session_id:    string
    step_id:       string
    resume_token:  string
    reason:        string
    timeout_hours: number
    business_hours: boolean
    calendar_id?:  string
    metadata?:     Record<string, unknown>
  }): Promise<{ resume_expires_at: string }> {
    const persistParams: PersistSuspendParams = {
      step_id:        params.step_id,
      resume_token:   params.resume_token,
      reason:         params.reason,
      timeout_hours:  params.timeout_hours,
      business_hours: params.business_hours,
      ...(params.calendar_id ? { calendar_id: params.calendar_id } : {}),
      ...(params.metadata    ? { metadata:    params.metadata    } : {}),
    }
    return this.workflowClient.persistSuspend(params.session_id, persistParams)
  }

  // ── persistCollect — POST /v1/workflow/instances/{id}/collect/persist ──────

  private async persistCollect(params: {
    tenant_id:      string
    session_id:     string
    step_id:        string
    collect_token:  string
    target:         { type: string; id: string }
    channel:        string
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
  }): Promise<{ send_at: string; expires_at: string }> {
    const collectParams: PersistCollectParams = {
      step_id:        params.step_id,
      collect_token:  params.collect_token,
      target:         params.target,
      channel:        params.channel,
      interaction:    params.interaction,
      prompt:         params.prompt,
      timeout_hours:  params.timeout_hours,
      business_hours: params.business_hours,
      ...(params.options      ? { options:      params.options      } : {}),
      ...(params.fields       ? { fields:        params.fields       } : {}),
      ...(params.scheduled_at ? { scheduled_at: params.scheduled_at } : {}),
      ...(params.delay_hours !== undefined ? { delay_hours: params.delay_hours } : {}),
      ...(params.calendar_id  ? { calendar_id:  params.calendar_id  } : {}),
      ...(params.campaign_id  ? { campaign_id:  params.campaign_id  } : {}),
    }
    return this.workflowClient.persistCollect(params.session_id, collectParams)
  }
}
