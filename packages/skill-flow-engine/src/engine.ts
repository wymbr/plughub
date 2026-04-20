/**
 * engine.ts
 * SkillFlowEngine — orquestra a execução do Skill Flow.
 * Spec: PlugHub v24.0 seções 4.7 e 9.5i
 *
 * Responsabilidades:
 * 1. Verificar se existe pipeline_state ativo → retomar ou iniciar
 * 2. Garantir execução exclusiva via lock Redis (idempotência)
 * 3. Executar steps em loop até complete, falha ou aguardando delegação
 * 4. Persistir pipeline_state a cada transição
 */

import type { Redis }          from "ioredis"
import type { SkillFlow, PipelineState, CatchStrategy } from "@plughub/schemas"
import { PipelineStateManager } from "./state"
import { executeStep }          from "./executor"
import type { StepContext, StepResult } from "./executor"

// ─────────────────────────────────────────────
// Tipos públicos
// ─────────────────────────────────────────────

export interface SkillFlowEngineConfig {
  redis:          Redis
  /** Chama tools no mcp-server-plughub */
  mcpCall:        (tool: string, input: unknown, mcpServer?: string) => Promise<unknown>
  /** Chama o AI Gateway */
  aiGatewayCall:  (payload: {
    prompt_id:     string
    input:         Record<string, unknown>
    output_schema: Record<string, unknown>
    session_id:    string
    attempt:       number
  }) => Promise<unknown>
}

export type RunResult =
  | { outcome: string; pipeline_state: PipelineState }
  | { error: "PRECONDITION_FAILED"; active_job_id: string }

// ─────────────────────────────────────────────
// SkillFlowEngine
// ─────────────────────────────────────────────

export class SkillFlowEngine {
  private readonly stateManager: PipelineStateManager

  constructor(private readonly config: SkillFlowEngineConfig) {
    this.stateManager = new PipelineStateManager(config.redis)
  }

  /**
   * Executa o flow de uma skill para uma sessão.
   *
   * Idempotência:
   *   Se outra instância do engine já está executando o mesmo pipeline
   *   (lock Redis ativo), retorna { error: "PRECONDITION_FAILED", active_job_id }.
   *
   * Retomada após falha:
   *   Se pipeline_state existe com status "in_progress", retoma do
   *   current_step_id — nunca reinicia do entry.
   */
  async run(params: {
    tenantId:       string
    sessionId:      string
    customerId:     string
    skillId:        string
    flow:           SkillFlow
    sessionContext: Record<string, unknown>
    /**
     * Identificador da instância do Routing Engine alocada para esta execução.
     * Armazenado no execution lock para que:
     *   1. O crash detector saiba que o engine ainda está vivo para esta sessão.
     *   2. O lock só seja liberado/renovado pela instância que o adquiriu.
     * Se omitido (retrocompatibilidade), usa "unknown".
     */
    instanceId?:    string
  }): Promise<RunResult> {
    const { tenantId, sessionId, customerId, skillId, flow, sessionContext } = params
    const instanceId = params.instanceId ?? "unknown"

    // ── Idempotência: tenta adquirir lock exclusivo ───────────────────────
    const lockAcquired = await this.stateManager.acquireLock(tenantId, sessionId, instanceId)
    if (!lockAcquired) {
      // Outra instância está executando — reportar o job ativo
      const activeState = await this.stateManager.get(tenantId, sessionId)
      const activeJobId = this._findActiveJobId(activeState)
      return { error: "PRECONDITION_FAILED", active_job_id: activeJobId ?? "unknown" }
    }

    try {
      return await this._execute({ tenantId, sessionId, customerId, skillId, flow, sessionContext, instanceId })
    } finally {
      // Libera apenas se ainda somos o titular do lock
      await this.stateManager.releaseLock(tenantId, sessionId, instanceId)
    }
  }

  // ─────────────────────────────────────────────
  // Execução interna
  // ─────────────────────────────────────────────

  private async _execute(params: {
    tenantId:       string
    sessionId:      string
    customerId:     string
    skillId:        string
    flow:           SkillFlow
    sessionContext: Record<string, unknown>
    instanceId:     string
  }): Promise<RunResult> {
    const { tenantId, sessionId, customerId, skillId, flow, sessionContext, instanceId } = params

    // 1. Retomar ou iniciar pipeline
    let state = await this.stateManager.get(tenantId, sessionId)

    if (state?.status === "in_progress") {
      // Retomada após falha do orquestrador — continua do current_step_id
    } else {
      // Novo pipeline — inicia do entry
      state = PipelineStateManager.create(skillId, flow.entry)
      await this.stateManager.save(tenantId, sessionId, state)
    }

    // Construir mapa de steps para lookup O(1)
    const stepMap = new Map(flow.steps.map(s => [s.id, s]))

    // 2. Loop de execução
    while (true) {
      const currentStep = stepMap.get(state.current_step_id)
      if (!currentStep) {
        await this.stateManager.fail(tenantId, sessionId, state)
        throw new Error(`Step não encontrado: ${state.current_step_id}`)
      }

      // Construir contexto de execução
      const ctx = this._buildContext(tenantId, sessionId, customerId, sessionContext, state, stepMap, instanceId)

      // Executar step
      const result = await executeStep(currentStep, ctx)
      // Sincronizar state — o step executor pode ter chamado ctx.saveState
      state = ctx.state

      // Persistir output do step no pipeline_state
      if (result.output_as && result.output_value !== undefined) {
        state = PipelineStateManager.setResult(state, result.output_as, result.output_value)
      }

      // Verificar encerramento
      if (result.next_step_id === "__complete__") {
        state = PipelineStateManager.addTransition(
          state, currentStep.id, "__complete__", result.transition_reason
        )
        const completedState = { ...state, status: "completed" as const }
        await this.stateManager.complete(tenantId, sessionId, state)
        return { outcome: result.outcome ?? "resolved", pipeline_state: completedState }
      }

      // Aguardando task assíncrona (execution_mode: async)
      if (result.next_step_id === "__awaiting_task__") {
        await this.stateManager.save(tenantId, sessionId, state)
        return { outcome: "awaiting_task", pipeline_state: state }
      }

      // Aguardando escalação para pool humano
      if (result.next_step_id === "__awaiting_escalation__") {
        await this.stateManager.save(tenantId, sessionId, state)
        return { outcome: "escalated_human", pipeline_state: state }
      }

      // Transitar para próximo step
      state = PipelineStateManager.addTransition(
        state, currentStep.id, result.next_step_id, result.transition_reason
      )

      // Persistir ANTES de executar o próximo step (garante retomada correta)
      await this.stateManager.save(tenantId, sessionId, state)
    }
  }

  // ─────────────────────────────────────────────
  // Construção de contexto de execução
  // ─────────────────────────────────────────────

  private _buildContext(
    tenantId:       string,
    sessionId:      string,
    customerId:     string,
    sessionContext: Record<string, unknown>,
    state:          PipelineState,
    stepMap:        Map<string, SkillFlow["steps"][number]>,
    instanceId:     string,
  ): StepContext {
    const self = this

    const ctx: StepContext = {
      tenantId,
      sessionId,
      customerId,
      sessionContext,
      state,
      redis: self.config.redis,

      mcpCall: (tool, input, mcpServer) =>
        self.config.mcpCall(tool, input, mcpServer),

      aiGatewayCall: (payload) =>
        self.config.aiGatewayCall(payload),

      saveState: async (s) => {
        ctx.state = s
        await self.stateManager.save(tenantId, sessionId, s)
      },

      retryStep: async (stepId) => {
        const step = stepMap.get(stepId)
        if (!step) throw new Error(`Step para retry não encontrado: ${stepId}`)
        return executeStep(step, ctx)
      },

      executeFallback: async (strategy: CatchStrategy & { type: "fallback" }) => {
        const fallbackResult = await self.config.mcpCall("agent_delegate", {
          session_id:    sessionId,
          target_skill:  "skill_id" in strategy.target ? strategy.target.skill_id : undefined,
          target_pool:   "pool" in strategy.target ? strategy.target.pool : undefined,
          payload: {
            customer_id:      customerId,
            pipeline_step:    strategy.id,
            pipeline_context: state.results,
          },
          delegation_mode: "silent",
        }) as { status: string; outcome?: string; result?: unknown }

        return {
          next_step_id:      fallbackResult.outcome === "resolved"
            ? strategy.on_success
            : strategy.on_failure,
          output_as:         strategy.id,
          output_value:      fallbackResult.result ?? null,
          transition_reason: fallbackResult.outcome === "resolved"
            ? "on_success"
            : "on_failure",
        } satisfies StepResult
      },

      getJobId: (stepId) =>
        self.stateManager.getJobId(tenantId, sessionId, stepId),

      setJobId: (stepId, jobId) =>
        self.stateManager.setJobId(tenantId, sessionId, stepId, jobId),

      clearJobId: (stepId) =>
        self.stateManager.clearJobId(tenantId, sessionId, stepId),

      renewLock: (ttlSeconds) =>
        self.stateManager.renewLock(tenantId, sessionId, instanceId, ttlSeconds),
    }

    return ctx
  }

  /** Procura job_id ativo no pipeline_state para reportar no PRECONDITION_FAILED. */
  private _findActiveJobId(state: PipelineState | null): string | undefined {
    if (!state) return undefined
    for (const [key, value] of Object.entries(state.results)) {
      if (key.endsWith(":__job_id__") && typeof value === "string") {
        return value
      }
    }
    return undefined
  }
}
