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
import type { IContextStore } from "./context-types"
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
  /**
   * ContextStore unificado — acesso a @ctx.namespace.campo para steps que
   * referenciam contexto via @ctx.*.
   * Opcional — quando ausente, @ctx.* retorna undefined (não quebra o fluxo).
   */
  contextStore?: IContextStore

  /**
   * Arc 4 — Optional. Wired by workflow-api to persist WorkflowInstance to PostgreSQL
   * and calculate the business-hours deadline for a suspend step.
   * If absent, the suspend step falls back to wall-clock hours.
   */
  persistSuspend?: (params: {
    tenant_id:     string
    session_id:    string
    step_id:       string
    resume_token:  string
    reason:        string
    timeout_hours: number
    business_hours: boolean
    calendar_id?:  string
    metadata?:     Record<string, unknown>
  }) => Promise<{ resume_expires_at: string }>

  /**
   * Arc 4 — Optional. Wired by the Skill Flow worker to persist a collect_instance
   * in PostgreSQL, calculate send_at/expires_at via calendar-api, and publish
   * collect.requested to Kafka.
   * If absent, the collect step falls back to wall-clock times.
   */
  persistCollect?: (params: {
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
  }) => Promise<{ send_at: string; expires_at: string }>
}

export type RunResult =
  | { outcome: string; pipeline_state: PipelineState }
  | { error: "PRECONDITION_FAILED"; active_job_id: string }

/** Arc 4: resume context passed from workflow-api when resuming a suspended step. */
export interface ResumeContext {
  decision:  "approved" | "rejected" | "input" | "timeout"
  step_id:   string
  payload:   Record<string, unknown>
}

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
   *
   * pipelineSessionId (opcional — assist mode):
   *   Quando um agente especialista é invocado via task step (mode: assist),
   *   ele usa o session_id do pai para comunicações (notify/menu vão ao canal
   *   correto) mas um pipeline state isolado para evitar conflito de lock com
   *   o agente primário que está em polling.
   *   Se omitido, pipeline state e comms usam o mesmo session_id.
   */
  async run(params: {
    tenantId:          string
    sessionId:         string
    customerId:        string
    skillId:           string
    flow:              SkillFlow
    sessionContext:    Record<string, unknown>
    /**
     * Identificador da instância do Routing Engine alocada para esta execução.
     * Armazenado no execution lock para que:
     *   1. O crash detector saiba que o engine ainda está vivo para esta sessão.
     *   2. O lock só seja liberado/renovado pela instância que o adquiriu.
     * Se omitido (retrocompatibilidade), usa "unknown".
     */
    instanceId?:       string
    /**
     * Override para o pipeline state key — usado no modo assist para que o
     * especialista use o session_id do pai para comms mas tenha pipeline
     * state isolado. Padrão: igual a sessionId.
     */
    pipelineSessionId?: string
    /**
     * Arc 4 — Resume context. When set, the engine is resuming a suspended workflow.
     * The suspend step reads this instead of suspending again.
     */
    resumeContext?: ResumeContext
    /** Segment UUID for segment-scoped ContextStore writes. */
    segmentId?:        string
  }): Promise<RunResult> {
    const { tenantId, sessionId, customerId, skillId, flow, sessionContext } = params
    const instanceId        = params.instanceId        ?? "unknown"
    const pipelineSessionId = params.pipelineSessionId ?? sessionId
    const resumeContext     = params.resumeContext
    const segmentId         = params.segmentId

    // ── Idempotência: tenta adquirir lock exclusivo ───────────────────────
    const lockAcquired = await this.stateManager.acquireLock(tenantId, pipelineSessionId, instanceId)
    if (!lockAcquired) {
      // Outra instância está executando — reportar o job ativo
      const activeState = await this.stateManager.get(tenantId, pipelineSessionId)
      const activeJobId = this._findActiveJobId(activeState)
      return { error: "PRECONDITION_FAILED", active_job_id: activeJobId ?? "unknown" }
    }

    try {
      return await this._execute({
        tenantId, sessionId, pipelineSessionId, customerId, skillId, flow, sessionContext, instanceId,
        ...(resumeContext ? { resumeContext } : {}),
        ...(segmentId ? { segmentId } : {}),
      })
    } finally {
      // Libera apenas se ainda somos o titular do lock
      await this.stateManager.releaseLock(tenantId, pipelineSessionId, instanceId)
    }
  }

  // ─────────────────────────────────────────────
  // Execução interna
  // ─────────────────────────────────────────────

  private async _execute(params: {
    tenantId:          string
    sessionId:         string
    pipelineSessionId: string
    customerId:        string
    skillId:           string
    flow:              SkillFlow
    sessionContext:    Record<string, unknown>
    instanceId:        string
    resumeContext?:    ResumeContext
    segmentId?:        string
  }): Promise<RunResult> {
    const { tenantId, sessionId, pipelineSessionId, customerId, skillId, flow, sessionContext, instanceId, resumeContext, segmentId } = params

    // 1. Retomar ou iniciar pipeline (usa pipelineSessionId para state isolation)
    let state = await this.stateManager.get(tenantId, pipelineSessionId)

    if (state?.status === "in_progress") {
      // Retomada após falha do orquestrador — continua do current_step_id
    } else {
      // Novo pipeline — inicia do entry
      state = PipelineStateManager.create(skillId, flow.entry)

      // ── required_context: computar @ctx.__gaps__ antes do primeiro step ──
      // Se o skill declara required_context e há um ContextStore disponível,
      // computa o GapsReport e armazena em pipeline_state.results["@ctx.__gaps__"].
      // Steps de choice e reason podem referenciar esse valor para decidir se
      // devem coletar informações faltantes antes de prosseguir.
      if (flow.required_context?.length && this.config.contextStore) {
        const gapsReport = await this.config.contextStore.getMissing(
          sessionId,
          flow.required_context,
          customerId,
        )
        state = PipelineStateManager.setResult(state, "@ctx.__gaps__", gapsReport)
      }

      await this.stateManager.save(tenantId, pipelineSessionId, state)
    }

    // Construir mapa de steps para lookup O(1)
    const stepMap     = new Map(flow.steps.map(s => [s.id, s]))
    const stepsArray  = flow.steps

    // ── Masked input — estado in-memory da transação ───────────────────────
    // Estes valores existem apenas em memória e nunca são persistidos.
    let maskedScope:          Record<string, string> = {}
    let transactionOnFailure: string | null          = null

    // 2. Loop de execução
    while (true) {
      const currentStep = stepMap.get(state.current_step_id)
      if (!currentStep) {
        await this.stateManager.fail(tenantId, pipelineSessionId, state)
        throw new Error(`Step não encontrado: ${state.current_step_id}`)
      }

      // Construir contexto de execução
      // sessionId → usado para comms (notify, menu, MCP calls)
      // pipelineSessionId → usado para state/lock
      const ctx = this._buildContext(
        tenantId, sessionId, pipelineSessionId, customerId, sessionContext, state, stepMap, instanceId,
        maskedScope, transactionOnFailure ?? null, resumeContext, segmentId,
      )

      // Executar step
      const result = await executeStep(currentStep, ctx)

      // Sincronizar state — o step executor pode ter chamado ctx.saveState
      state = ctx.state

      // Sincronizar estado in-memory da transação (mutados pelos executores)
      maskedScope          = ctx.maskedScope
      transactionOnFailure = ctx.transactionOnFailure

      // Persistir output do step no pipeline_state
      if (result.output_as && result.output_value !== undefined) {
        state = PipelineStateManager.setResult(state, result.output_as, result.output_value)
      }

      // ── Marcadores internos de transação ────────────────────────────────

      // begin_transaction: avança para o step seguinte na ordem do array
      if (result.next_step_id === "__transaction_begin__") {
        const currentIdx  = stepsArray.findIndex(s => s.id === currentStep.id)
        const nextStep    = stepsArray[currentIdx + 1]
        if (!nextStep) {
          await this.stateManager.fail(tenantId, pipelineSessionId, state)
          throw new Error(`begin_transaction sem step seguinte: ${currentStep.id}`)
        }
        state = PipelineStateManager.addTransition(
          state, currentStep.id, nextStep.id, "on_success"
        )
        await this.stateManager.save(tenantId, pipelineSessionId, state)
        continue
      }

      // end_transaction: limpa transactionOnFailure (já feito no executor);
      // se on_success não foi declarado, avança para o step seguinte
      if (result.next_step_id === "__transaction_end__") {
        transactionOnFailure = null
        const currentIdx  = stepsArray.findIndex(s => s.id === currentStep.id)
        const nextStep    = stepsArray[currentIdx + 1]
        if (nextStep) {
          state = PipelineStateManager.addTransition(
            state, currentStep.id, nextStep.id, "on_success"
          )
          await this.stateManager.save(tenantId, pipelineSessionId, state)
          continue
        }
        // sem step seguinte → encerrar (equivale a complete)
        await this.stateManager.complete(tenantId, pipelineSessionId, state)
        return { outcome: "resolved", pipeline_state: { ...state, status: "completed" as const } }
      }

      // ── Detectar falha dentro de bloco de transação ──────────────────────
      // Se estamos dentro de um begin_transaction (transactionOnFailure definido)
      // e o step retornou on_failure, fazemos rewind para on_failure da transação.
      if (
        transactionOnFailure !== null &&
        result.transition_reason === "on_failure"
      ) {
        const rewindTarget = transactionOnFailure
        // Descartar masked_scope — nunca reutilizar valores sensíveis
        maskedScope          = {}
        transactionOnFailure = null
        ctx.maskedScope          = {}
        ctx.transactionOnFailure = null

        state = PipelineStateManager.addTransition(
          state, currentStep.id, rewindTarget, "on_failure"
        )
        await this.stateManager.save(tenantId, pipelineSessionId, state)
        continue
      }

      // ── Verificar encerramento ───────────────────────────────────────────

      if (result.next_step_id === "__complete__") {
        state = PipelineStateManager.addTransition(
          state, currentStep.id, "__complete__", result.transition_reason
        )
        const completedState = { ...state, status: "completed" as const }
        await this.stateManager.complete(tenantId, pipelineSessionId, state)
        return { outcome: result.outcome ?? "resolved", pipeline_state: completedState }
      }

      // Aguardando task assíncrona (execution_mode: async)
      if (result.next_step_id === "__awaiting_task__") {
        const awaitingState = { ...state, status: "completed" as const }
        await this.stateManager.complete(tenantId, pipelineSessionId, state)
        return { outcome: "awaiting_task", pipeline_state: awaitingState }
      }

      // Aguardando escalação para pool humano.
      // Marcamos como "completed" para que novas conexões do mesmo session_id
      // iniciem um novo pipeline em vez de retomar do step escalar.
      if (result.next_step_id === "__awaiting_escalation__") {
        const escalatedState = { ...state, status: "completed" as const }
        await this.stateManager.complete(tenantId, pipelineSessionId, state)
        return { outcome: "escalated_human", pipeline_state: escalatedState }
      }

      // Arc 4: fluxo suspenso aguardando sinal externo
      if (result.next_step_id === "__suspended__") {
        const suspendedState = { ...state, status: "suspended" as const }
        await this.stateManager.save(tenantId, pipelineSessionId, suspendedState)
        return { outcome: "suspended", pipeline_state: suspendedState }
      }

      // Transitar para próximo step
      state = PipelineStateManager.addTransition(
        state, currentStep.id, result.next_step_id, result.transition_reason
      )

      // Persistir ANTES de executar o próximo step (garante retomada correta)
      await this.stateManager.save(tenantId, pipelineSessionId, state)
    }
  }

  // ─────────────────────────────────────────────
  // Construção de contexto de execução
  // ─────────────────────────────────────────────

  private _buildContext(
    tenantId:             string,
    sessionId:            string,
    pipelineSessionId:    string,
    customerId:           string,
    sessionContext:       Record<string, unknown>,
    state:                PipelineState,
    stepMap:              Map<string, SkillFlow["steps"][number]>,
    instanceId:           string,
    maskedScope:          Record<string, string>,
    transactionOnFailure: string | null,
    resumeContext?:       ResumeContext,
    segmentId?:           string,
  ): StepContext {
    const self = this

    const ctx: StepContext = {
      tenantId,
      sessionId,         // used by notify/menu/MCP calls — always the comms session
      customerId,
      sessionContext,
      state,
      redis: self.config.redis,
      instanceId,
      ...(segmentId ? { segmentId } : {}),
      // Masked input — in-memory transaction scope (mutable, never persisted)
      maskedScope:          maskedScope,
      transactionOnFailure: transactionOnFailure,

      ...(self.config.contextStore
        ? { contextStore: self.config.contextStore }
        : {}),

      mcpCall: (tool, input, mcpServer) =>
        self.config.mcpCall(tool, input, mcpServer),

      aiGatewayCall: (payload) =>
        self.config.aiGatewayCall(payload),

      saveState: async (s) => {
        ctx.state = s
        // state persisted under pipelineSessionId (may differ from sessionId in assist mode)
        await self.stateManager.save(tenantId, pipelineSessionId, s)
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

      // Job ID operations use pipelineSessionId for isolation in assist mode
      getJobId: (stepId) =>
        self.stateManager.getJobId(tenantId, pipelineSessionId, stepId),

      setJobId: (stepId, jobId) =>
        self.stateManager.setJobId(tenantId, pipelineSessionId, stepId, jobId),

      clearJobId: (stepId) =>
        self.stateManager.clearJobId(tenantId, pipelineSessionId, stepId),

      renewLock: (ttlSeconds) =>
        self.stateManager.renewLock(tenantId, pipelineSessionId, instanceId, ttlSeconds),

      // Arc 4 — wired only when caller provides persistSuspend
      ...(self.config.persistSuspend
        ? { persistSuspend: (params: Parameters<NonNullable<StepContext["persistSuspend"]>>[0]) =>
              self.config.persistSuspend!({ tenant_id: tenantId, session_id: sessionId, ...params }) }
        : {}),

      // Arc 4 — wired only when caller provides persistCollect
      ...(self.config.persistCollect
        ? { persistCollect: (params: Parameters<NonNullable<StepContext["persistCollect"]>>[0]) =>
              self.config.persistCollect!({ tenant_id: tenantId, session_id: sessionId, ...params }) }
        : {}),

      // Arc 4 — resume context forwarded only when present
      ...(resumeContext ? { resumeContext } : {}),
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
