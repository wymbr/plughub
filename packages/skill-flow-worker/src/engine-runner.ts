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
  // redis is stored so each runInstance can create a per-run SkillFlowEngine
  // whose persistSuspend/persistCollect closures capture the workflow instance UUID.
  // A shared engine cannot capture per-run instance IDs because the engine uses
  // origin_session_id (not instance.id) as its sessionId — they differ when a
  // workflow is triggered from a real contact session.
  private redis: Redis
  private workflowClient: WorkflowClient
  private settings: WorkerSettings

  constructor(config: EngineRunnerConfig) {
    this.redis          = config.redis
    this.workflowClient = config.workflowClient
    this.settings       = config.settings
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
      // Use origin_session_id as the ContextStore key when available so that
      // @ctx.* reads/writes target the originating customer session's hash
      // ({tenant}:ctx:{origin_session_id}) rather than the workflow UUID.
      // Falls back to instance.id for headless workflows with no session origin.
      const contextSessionId = instance.origin_session_id ?? instance.id

      // Create a per-run engine so that persistSuspend / persistCollect closures
      // capture the workflow instance UUID (instance.id).  A shared engine cannot
      // do this because the engine's sessionId is contextSessionId (= origin_session_id
      // when triggered from a contact), which differs from the workflow instance UUID
      // that workflow-api requires in the URL path.
      const capturedInstanceId = instance.id
      const engine = new SkillFlowEngine({
        redis:          this.redis,
        mcpCall:        this.mcpCall.bind(this),
        aiGatewayCall:  this.aiGatewayCall.bind(this),
        persistSuspend: (params) => this._persistSuspend(capturedInstanceId, params),
        persistCollect: (params) => this._persistCollect(capturedInstanceId, params),
      })

      const result = await engine.run({
        tenantId:    instance.tenant_id,
        sessionId:   contextSessionId,
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
    json_schema?:  Record<string, unknown>   // T7b — tool-use nativo quando presente
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
        ...(payload.json_schema ? { json_schema: payload.json_schema } : {}),
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

  // ── _persistSuspend — POST /v1/workflow/instances/{id}/persist-suspend ──────
  // instanceId is captured from runInstance — always the workflow UUID, never
  // origin_session_id or any other session identifier.

  private async _persistSuspend(
    instanceId: string,
    params: {
      tenant_id:     string
      session_id:    string
      step_id:       string
      resume_token:  string
      reason:        string
      timeout_hours: number
      business_hours: boolean
      calendar_id?:  string
      metadata?:     Record<string, unknown>
    },
  ): Promise<{ resume_expires_at: string }> {
    const persistParams: PersistSuspendParams = {
      step_id:        params.step_id,
      resume_token:   params.resume_token,
      reason:         params.reason,
      timeout_hours:  params.timeout_hours,
      business_hours: params.business_hours,
      ...(params.calendar_id ? { calendar_id: params.calendar_id } : {}),
      ...(params.metadata    ? { metadata:    params.metadata    } : {}),
    }
    return this.workflowClient.persistSuspend(instanceId, persistParams)
  }

  // ── _persistCollect — POST /v1/workflow/instances/{id}/collect/persist ──────
  // instanceId is captured from runInstance — always the workflow UUID, never
  // origin_session_id or any other session identifier.

  private async _persistCollect(
    instanceId: string,
    params: {
      tenant_id:      string
      session_id:     string
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
    },
  ): Promise<{ send_at: string; expires_at: string }> {
    const collectParams: PersistCollectParams = {
      step_id:        params.step_id,
      collect_token:  params.collect_token,
      target:         params.target,
      ...(params.channel ? { channel: params.channel } : {}),
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
    return this.workflowClient.persistCollect(instanceId, collectParams)
  }
}
