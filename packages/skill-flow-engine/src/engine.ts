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
    json_schema?:  Record<string, unknown>   // T7b — tool-use nativo quando presente
    model_profile?: string                    // R8d — perfil de modelo (revisor heterogêneo)
    /** T2/D1 — chave de atribuição de custo. Ver `executor.ts`. */
    segment_id?:   string
    preferred_config_ids?: string[]           // LLM Accounts — core.pool.llm_account_ids
  }) => Promise<unknown>
  /**
   * ContextStore unificado — acesso a @ctx.namespace.campo para steps que
   * referenciam contexto via @ctx.*.
   * Opcional — quando ausente, @ctx.* retorna undefined (não quebra o fluxo).
   */
  contextStore?: IContextStore

  /**
   * Arc 16 — Journey ContextStore namespace.
   * When provided, @ctx.journey.* reads/writes target the journey Redis hash
   * {tenant}:ctx:journey:{journeyId} instead of the session hash.
   * The skill-flow-worker sets this when the workflow instance has a journey_id.
   * Optional — when absent, @ctx.journey.* resolves normally from the session hash.
   */
  journeyId?: string

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
   * Arc 19 — Optional. Wired by orchestrator-bridge for webhook (workflow) sessions.
   * Replaces the wall-clock fallback for webhook pools.
   * See StepContext.persistSuspendWebhook for full contract.
   * Priority: persistSuspend (Arc 4) → persistSuspendWebhook (Arc 19) → wall-clock.
   */
  persistSuspendWebhook?: (params: {
    tenant_id:     string
    session_id:    string
    step_id:       string
    resume_token:  string
    timeout_hours: number
    /** Arc 19 Fase D: forwarded from suspend step for business-hours deadline calculation. */
    business_hours?: boolean
    calendar_id?:    string
    /**
     * Fase 1 do arco de workflow — motivo da suspensão, portador do fato que a
     * transição (D4) precisa. ⚠️ Este tipo e o `StepContext.persistSuspendWebhook`
     * em `executor.ts` são DUAS declarações do mesmo contrato: alargar só uma
     * compila no call site do step e quebra no implementador (ou vice-versa).
     */
    reason?:         string
  }) => Promise<{ resume_expires_at: string }>

  /**
   * Arc 19 delegate step — Optional. Wired by skill-flow-service for webhook sessions.
   * Called when a delegate step executes for the first time.
   * Creates a child session in the target pool and writes context to its ContextStore.
   * tenant_id and session_id are injected by _buildContext (same pattern as above).
   * If absent, the delegate step falls back to on_timeout (config error).
   */
  persistDelegate?: (params: {
    tenant_id:          string
    session_id:         string   // parent (workflow) session
    step_id:            string
    resume_token:       string
    pool:               string
    context:            Record<string, string>
    timeout_hours:      number
    origin_session_id:  string
    /** Identity Resolver (nível b) — index pending under native customer_id for cross-channel resume. */
    customer_resumable?: boolean
    /** How a discovered cross-channel pending is offered on reconnect. */
    resume_policy?:      "offer" | "auto"
    /** Camada B (pull direcionado) — reserva do work item ao recurso + transbordo. */
    assigned_to?:              string
    fallback_to_pool_after_s?: number
    /** Wrap-up unificado (Camada E2) — auto-atendimento no Console (inline). */
    auto_attend?:              boolean
  }) => Promise<{ child_session_id: string }>

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
    channel?:       string   // optional — channel-gateway selects by requires[] when absent
    /** Arc 16 capability requirements (channel must be a superset). */
    requires?:      string[]
    /** Journey J4c — declarative channel policy (N2 input); N3 never names the channel. */
    // `| undefined` on each member: the Zod-inferred CollectStep type carries
    // `string[] | undefined`, and exactOptionalPropertyTypes rejects assigning that
    // to a plain optional `string[]`.
    channel_policy?: {
      /** canal → pool que atende. As chaves são os canais permitidos. */
      channels?:         Record<string, string> | undefined
      preferred_order?:  string[] | undefined
      exclude?:          string[] | undefined
      urgency?:          "low" | "normal" | "high" | undefined
    }
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
    /** Journey J4c — DialogForm rendered on engagement (survey collect). */
    dialog_form_id?: string
    /** S2 — grão do sinal, já resolvido (journey | session | workflow | segment). */
    signal_grain?:  string
    /** Identity Resolver (nível b) — index pending under native customer_id for cross-channel resume. */
    customer_resumable?: boolean
    /** How a discovered cross-channel pending is offered on reconnect. */
    resume_policy?:      "offer" | "auto"
  }) => Promise<{ send_at: string; expires_at: string }>
}

export type RunResult =
  | { outcome: string; pipeline_state: PipelineState }
  | { error: "PRECONDITION_FAILED"; active_job_id: string }

// ─────────────────────────────────────────────
// DAG Cycle Validation
// ─────────────────────────────────────────────

/**
 * Returns the set of step IDs that can follow this step.
 * Covers all next-step fields across every step type.
 * Special engine sentinels (__complete__, __suspended__, etc.) are excluded
 * because they are not real step IDs.
 */
function _getSuccessors(step: SkillFlow["steps"][number]): string[] {
  const s = step as Record<string, unknown>
  const targets: string[] = []

  // Standard fields present across most step types. Fields like on_timeout /
  // on_resume / on_reject / on_response are STRINGS in some step types and
  // OBJECTS `{ next }` in others (collect/suspend) — both shapes handled.
  for (const field of [
    "next", "on_success", "on_failure",
    "on_message", "on_timeout", "on_disconnect", "on_max_iterations",
    "on_exhausted", "on_invite", "on_escalate",
    "on_response", "on_resume", "on_reject",
    // loop step — body (loop entry) + on_complete (exit)
    "body", "on_complete",
    // choice step — default branch (2026-06-04: era ponto cego, ciclos via
    // choice escapavam da validação)
    "default",
  ]) {
    const v = s[field]
    if (typeof v === "string") targets.push(v)
    else if (v && typeof v === "object" && typeof (v as Record<string, unknown>)["next"] === "string") {
      targets.push((v as Record<string, unknown>)["next"] as string)
    }
  }

  // choice step — conditions[].next (formato real dos YAMLs; 2026-06-04: era
  // ponto cego junto com `default`)
  if (Array.isArray(s["conditions"])) {
    for (const cond of s["conditions"] as Array<Record<string, unknown>>) {
      if (typeof cond["next"] === "string") targets.push(cond["next"] as string)
    }
  }

  // choice step — branches[].next (formato legado)
  if (Array.isArray(s["branches"])) {
    for (const branch of s["branches"] as Array<Record<string, unknown>>) {
      if (typeof branch["next"] === "string") targets.push(branch["next"] as string)
    }
  }

  // catch step — strategies[] (array real) e strategy (legado singular)
  if (s["type"] === "catch") {
    const strategyList = Array.isArray(s["strategies"])
      ? (s["strategies"] as Array<Record<string, unknown>>)
      : []
    const legacy = s["strategy"] as Record<string, unknown> | undefined
    if (legacy) strategyList.push(legacy)
    for (const strategy of strategyList) {
      for (const field of ["on_success", "on_failure", "on_exhausted"]) {
        if (typeof strategy[field] === "string") targets.push(strategy[field] as string)
      }
    }
  }

  // Filter out engine sentinels (not real step IDs)
  return targets.filter(t =>
    t !== "__complete__" &&
    t !== "__awaiting_task__" &&
    t !== "__awaiting_escalation__" &&
    t !== "__suspended__" &&
    t !== "__transaction_begin__" &&
    t !== "__transaction_end__"
  )
}

/**
 * validateFlow
 *
 * Validates that every cycle in the flow step graph is controlled:
 * a cycle is valid if it passes through a BLOCKING guard step — `receive`
 * with `max_iterations`, any `menu` (blocks on external I/O; includes the
 * mention-protocol standby), `suspend` or `collect` (block on external
 * signals, bounded by the gateway timeout scanner).
 *
 * Uncontrolled cycles (reason/notify/invoke/choice only) would run
 * indefinitely, burning LLM calls with no natural exit.
 *
 * Algorithm: DFS with three-colour marking (white → gray → black).
 * When a back-edge is found (gray → gray), the cycle is extracted from
 * the current DFS path and checked for a guarding receive step.
 *
 * Throws on the first unguarded cycle found.
 * Typically called once per flow before execution begins.
 */
export function validateFlow(flow: SkillFlow): void {
  const stepMap = new Map(flow.steps.map(s => [s.id, s]))

  // Build adjacency list
  const adj = new Map<string, string[]>()
  for (const step of flow.steps) {
    adj.set(step.id, _getSuccessors(step).filter(id => stepMap.has(id)))
  }

  const WHITE = 0, GRAY = 1, BLACK = 2
  const color  = new Map<string, number>()
  const violations: string[] = []

  function dfs(id: string, path: string[]): void {
    const c = color.get(id) ?? WHITE
    if (c === BLACK) return
    if (c === GRAY) {
      // Back-edge: extract cycle and check for guard
      const cycleStart = path.indexOf(id)
      const cycleNodes = path.slice(cycleStart)
      // Guarda de ciclo (política 2026-06-04): um ciclo é controlado quando
      // passa por um step que BLOQUEIA aguardando o mundo externo — cada
      // iteração exige input humano/externo, então não há runaway (loops de
      // reason/notify/invoke queimando LLM sem freio):
      //   receive + max_iterations — freio explícito por contagem;
      //   menu                     — bloqueia em I/O do cliente/agente
      //                              (inclui standby de @mention, timeout 0/-1);
      //   suspend / collect        — bloqueiam aguardando sinal externo
      //                              (timeout scanner do gateway é o teto).
      // Auditoria 2026-06-04: todos os 6 ciclos dos YAMLs existentes passam
      // por uma dessas guardas — fechamento da adjacência não quebra nada.
      const guarded = cycleNodes.some(nodeId => {
        const s = stepMap.get(nodeId) as Record<string, unknown> | undefined
        const t = s?.["type"]
        if (t === "receive" && s!["max_iterations"] !== undefined) return true
        if (t === "menu") return true
        if (t === "suspend" || t === "collect") return true
        // loop — bounded by the array length + max_iterations cap (the body's
        // menu also blocks on input each iteration).
        if (t === "loop") return true
        return false
      })
      if (!guarded) {
        violations.push(
          cycleNodes.join(" → ") + ` → ${id}  (no blocking guard step)`
        )
      }
      return
    }

    color.set(id, GRAY)
    path.push(id)
    for (const succ of adj.get(id) ?? []) {
      dfs(succ, path)
    }
    path.pop()
    color.set(id, BLACK)
  }

  for (const step of flow.steps) {
    if ((color.get(step.id) ?? WHITE) === WHITE) {
      dfs(step.id, [])
    }
  }

  if (violations.length > 0) {
    const skillId = (flow as Record<string, unknown>)["skill_id"] ?? "unknown"
    throw new Error(
      `SkillFlow "${skillId}" has unguarded cycles — cycles are only allowed ` +
      `when every cycle path passes through a "receive" step with max_iterations defined.\n` +
      violations.map(v => `  Cycle: ${v}`).join("\n")
    )
  }
}

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
     * Dialog primitive §17.3-1 — deploy-time skill parameters from the
     * PoolSkillSlot.config_json of the running skill, exposed to the flow as
     * `$.config.*`. Optional; empty when the launcher provides no slot config.
     */
    config?:           Record<string, unknown>
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
    /**
     * Arc 16 — Journey ID. When set, @ctx.journey.* reads/writes target the journey
     * Redis hash {tenant}:ctx:journey:{journeyId}. Overrides config.journeyId.
     */
    journeyId?:        string
  }): Promise<RunResult> {
    const { tenantId, sessionId, customerId, skillId, flow, sessionContext } = params
    const config            = params.config
    const instanceId        = params.instanceId        ?? "unknown"
    const pipelineSessionId = params.pipelineSessionId ?? sessionId
    const resumeContext     = params.resumeContext
    const segmentId         = params.segmentId
    // run() param takes precedence over config-level journeyId
    const journeyId         = params.journeyId ?? this.config.journeyId

    // ── DAG validation: detect unguarded cycles ───────────────────────────
    // Throws if the flow contains a cycle that does not pass through a
    // receive step with max_iterations — such cycles would run forever.
    // Called once per run() invocation (O(V+E), negligible cost).
    validateFlow(flow)

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
        ...(config ? { config } : {}),
        ...(resumeContext ? { resumeContext } : {}),
        ...(segmentId ? { segmentId } : {}),
        ...(journeyId ? { journeyId } : {}),
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
    config?:           Record<string, unknown>
    instanceId:        string
    resumeContext?:    ResumeContext
    segmentId?:        string
    journeyId?:        string
  }): Promise<RunResult> {
    const { tenantId, sessionId, pipelineSessionId, customerId, skillId, flow, sessionContext, config, instanceId, resumeContext, segmentId } = params
    // Arc 16 — `let`, não `const`: um step `invoke journey_merge` muda a raiz canônica
    // NO MEIO desta mesma execução (ex.: unificar_journey → retomar_resultado, dois
    // steps consecutivos do mesmo run). `_buildContext` roda de novo a CADA iteração
    // do loop abaixo — se `journeyId` fosse fixo aqui, a mutação que invoke.ts faz em
    // `ctx.journeyId` (applyJourneyMergeResult) seria descartada no próximo step, porque
    // o `ctx` daquele step é reconstruído do zero a partir desta variável, não do `ctx`
    // anterior. É exatamente o bug que produzia campos vazios mesmo com o merge certo
    // no Redis: a correção em invoke.ts sozinha resolve o `ctx` do PRÓPRIO step do
    // merge, mas não sobrevive à fronteira do loop sem isto.
    let journeyId = params.journeyId

    // 1. Retomar ou iniciar pipeline (usa pipelineSessionId para state isolation)
    let state = await this.stateManager.get(tenantId, pipelineSessionId)

    if (state?.status === "in_progress") {
      // Retomada após falha do orquestrador — continua do current_step_id
    } else if (state?.status === "suspended" && resumeContext) {
      // Arc 4 — Resuming a suspended workflow.
      // Keep the stored results (sentinel keys, decision keys, step outputs) and
      // continue from state.current_step_id (the step that suspended).
      // The step executor's resume path (ctx.resumeContext.step_id check) handles it.
      // Resetting to a fresh state here would discard all idempotency keys and
      // cause every prior suspend/collect step to re-execute on replay.
      state = { ...state, status: "in_progress" as const }
      await this.stateManager.save(tenantId, pipelineSessionId, state)
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
        maskedScope, transactionOnFailure ?? null, resumeContext, segmentId, journeyId, config,
      )

      // Executar step
      const result = await executeStep(currentStep, ctx)

      // Sincronizar state — o step executor pode ter chamado ctx.saveState
      state = ctx.state

      // Sincronizar estado in-memory da transação (mutados pelos executores)
      maskedScope          = ctx.maskedScope
      transactionOnFailure = ctx.transactionOnFailure

      // Sincronizar journeyId — ver comentário acima (`let journeyId`). invoke.ts
      // (journey_merge) muta ctx.journeyId; sem este sync o próximo _buildContext
      // reconstrói o ctx com a raiz antiga e a mutação vira no-op.
      journeyId = ctx.journeyId

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
    journeyId?:           string,
    config?:              Record<string, unknown>,
  ): StepContext {
    const self = this

    const ctx: StepContext = {
      tenantId,
      sessionId,         // used by notify/menu/MCP calls — always the comms session
      customerId,
      sessionContext,
      ...(config ? { config } : {}),
      state,
      redis: self.config.redis,
      instanceId,
      ...(segmentId ? { segmentId } : {}),
      // Arc 16: journey namespace — @ctx.journey.* reads/writes target journey hash
      ...(journeyId ? { journeyId } : {}),
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

      // Arc 19 — wired only when caller provides persistSuspendWebhook (and persistSuspend is absent)
      ...(!self.config.persistSuspend && self.config.persistSuspendWebhook
        ? { persistSuspendWebhook: (params: Parameters<NonNullable<StepContext["persistSuspendWebhook"]>>[0]) =>
              self.config.persistSuspendWebhook!({ tenant_id: tenantId, session_id: sessionId, ...params }) }
        : {}),

      // Arc 4 — wired only when caller provides persistCollect
      ...(self.config.persistCollect
        ? { persistCollect: (params: Parameters<NonNullable<StepContext["persistCollect"]>>[0]) =>
              self.config.persistCollect!({ tenant_id: tenantId, session_id: sessionId, ...params }) }
        : {}),

      // Arc 19 delegate — wired only when caller provides persistDelegate
      ...(self.config.persistDelegate
        ? { persistDelegate: (params: Parameters<NonNullable<StepContext["persistDelegate"]>>[0]) =>
              self.config.persistDelegate!({ tenant_id: tenantId, session_id: sessionId, ...params }) }
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
