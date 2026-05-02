/**
 * state.ts
 * Leitura e escrita do PipelineState no Redis.
 * Spec: PlugHub v24.0 seção 9.5i
 *
 * Chave canônica: {tenant_id}:pipeline:{session_id}
 * Persistido a cada transição — garante retomada após falha.
 */

import type { Redis } from "ioredis"
import { PipelineStateSchema, type PipelineState } from "@plughub/schemas"

// ─────────────────────────────────────────────
// Helpers de chave Redis
// ─────────────────────────────────────────────

const PIPELINE_KEY = (tenantId: string, sessionId: string) =>
  `${tenantId}:pipeline:${sessionId}`

/** Lock distribuído — impede execução concorrente do mesmo pipeline. */
const LOCK_KEY = (tenantId: string, sessionId: string) =>
  `${tenantId}:pipeline:${sessionId}:running`

/** job_id do agent_delegate por step — garante idempotência. */
const JOB_KEY = (tenantId: string, sessionId: string, stepId: string) =>
  `${tenantId}:pipeline:${sessionId}:job:${stepId}`

const PIPELINE_TTL_SECONDS = 86_400  // 24h — alinhado com validade de sessão

/**
 * LOCK_TTL_SECONDS deve ser maior que o maior timeout_ms possível de um step menu (300s)
 * mais a margem de HTTP timeout do bridge (60s) = 400s.
 * O step menu renova explicitamente o lock antes do BLPOP via renewLock(),
 * então este valor cobre os demais steps que não fazem renovação explícita.
 */
const LOCK_TTL_SECONDS = 400

// ─────────────────────────────────────────────
// PipelineStateManager
// ─────────────────────────────────────────────

export class PipelineStateManager {
  constructor(private readonly redis: Redis) {}

  /** Lê o pipeline_state ativo de uma sessão. Retorna null se não existe. */
  async get(tenantId: string, sessionId: string): Promise<PipelineState | null> {
    const raw = await this.redis.get(PIPELINE_KEY(tenantId, sessionId))
    if (!raw) return null
    try {
      return PipelineStateSchema.parse(JSON.parse(raw))
    } catch {
      return null
    }
  }

  /**
   * Persiste o pipeline_state no Redis.
   * Chamado a cada transição de step — antes de executar o próximo.
   */
  async save(tenantId: string, sessionId: string, state: PipelineState): Promise<void> {
    await this.redis.set(
      PIPELINE_KEY(tenantId, sessionId),
      JSON.stringify(state),
      "EX",
      PIPELINE_TTL_SECONDS,
    )
  }

  /** Marca o pipeline como concluído. */
  async complete(tenantId: string, sessionId: string, state: PipelineState): Promise<void> {
    await this.save(tenantId, sessionId, { ...state, status: "completed" })
  }

  /** Marca o pipeline como falho. */
  async fail(tenantId: string, sessionId: string, state: PipelineState): Promise<void> {
    await this.save(tenantId, sessionId, { ...state, status: "failed" })
  }

  /** Remove o pipeline_state da sessão (encerramento). */
  async delete(tenantId: string, sessionId: string): Promise<void> {
    await this.redis.del(PIPELINE_KEY(tenantId, sessionId))
  }

  // ── Lock distribuído ────────────────────────────────────────────────────────

  /**
   * Tenta adquirir lock exclusivo de execução para uma instância específica.
   * Armazena o instanceId no valor do lock (não apenas "1") para que seja
   * possível verificar a propriedade antes de renovar ou liberar.
   *
   * Retorna true se adquirido, false se já existe (outra instância rodando).
   *
   * O crash detector (routing-engine) verifica a EXISTÊNCIA deste key antes de
   * re-enfileirar uma conversa — se o key existe, o engine ainda está vivo.
   */
  async acquireLock(tenantId: string, sessionId: string, instanceId: string): Promise<boolean> {
    const result = await this.redis.set(
      LOCK_KEY(tenantId, sessionId),
      instanceId,
      "EX",
      LOCK_TTL_SECONDS,
      "NX",
    )
    return result === "OK"
  }

  /**
   * Renova o TTL do lock apenas se ainda pertence a esta instância.
   * Operação atômica via Lua para evitar race entre GET e EXPIRE.
   *
   * Retorna true se renovado, false se o lock pertence a outra instância.
   * Retornar false é sinal para o engine abortar graciosamente.
   */
  async renewLock(
    tenantId:   string,
    sessionId:  string,
    instanceId: string,
    ttlSeconds: number = LOCK_TTL_SECONDS,
  ): Promise<boolean> {
    const lua = `
      if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("expire", KEYS[1], ARGV[2])
      else
        return 0
      end
    `
    const result = await this.redis.eval(
      lua, 1,
      LOCK_KEY(tenantId, sessionId),
      instanceId,
      String(ttlSeconds),
    ) as number
    return result === 1
  }

  /**
   * Libera o lock apenas se ainda pertence a esta instância.
   * Operação atômica via Lua para evitar apagar o lock de outra instância
   * (ex: crash recovery que adquiriu o lock após TTL expirar).
   */
  async releaseLock(tenantId: string, sessionId: string, instanceId: string): Promise<void> {
    const lua = `
      if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
      else
        return 0
      end
    `
    await this.redis.eval(
      lua, 1,
      LOCK_KEY(tenantId, sessionId),
      instanceId,
    )
  }

  // ── job_id por step (idempotência do agent_delegate) ──────────────────────

  /** Retorna o job_id ativo para um step, ou null se não existe. */
  async getJobId(tenantId: string, sessionId: string, stepId: string): Promise<string | null> {
    return this.redis.get(JOB_KEY(tenantId, sessionId, stepId))
  }

  /**
   * Persiste o job_id de um step.
   * TTL alinhado com o pipeline para evitar acúmulo de chaves.
   */
  async setJobId(tenantId: string, sessionId: string, stepId: string, jobId: string): Promise<void> {
    await this.redis.set(
      JOB_KEY(tenantId, sessionId, stepId),
      jobId,
      "EX",
      PIPELINE_TTL_SECONDS,
    )
  }

  /** Remove o job_id de um step (após conclusão). */
  async clearJobId(tenantId: string, sessionId: string, stepId: string): Promise<void> {
    await this.redis.del(JOB_KEY(tenantId, sessionId, stepId))
  }

  // ── Static — transformações imutáveis do PipelineState ────────────────────

  /** Cria um novo pipeline_state para uma sessão. */
  static create(flowId: string, entryStepId: string): PipelineState {
    const now = new Date().toISOString()
    return PipelineStateSchema.parse({
      flow_id:         flowId,
      current_step_id: entryStepId,
      status:          "in_progress",
      started_at:      now,
      updated_at:      now,
      results:         {},
      retry_counters:  {},
      transitions:     [],
    })
  }

  /** Registra uma transição de step no histórico. */
  static addTransition(
    state:    PipelineState,
    fromStep: string,
    toStep:   string,
    reason:   PipelineState["transitions"][number]["reason"],
  ): PipelineState {
    return {
      ...state,
      current_step_id: toStep,
      updated_at:      new Date().toISOString(),
      transitions: [
        ...state.transitions,
        { from_step: fromStep, to_step: toStep, reason, timestamp: new Date().toISOString() },
      ],
    }
  }

  /** Persiste o resultado de um step no pipeline_state. */
  static setResult(
    state:    PipelineState,
    outputAs: string,
    result:   unknown,
  ): PipelineState {
    return {
      ...state,
      updated_at: new Date().toISOString(),
      results: { ...state.results, [outputAs]: result },
    }
  }

  /** Incrementa o contador de retry de um step catch. */
  static incrementRetry(state: PipelineState, stepId: string): PipelineState {
    const current = state.retry_counters[stepId] ?? 0
    return {
      ...state,
      updated_at:      new Date().toISOString(),
      retry_counters: { ...state.retry_counters, [stepId]: current + 1 },
    }
  }
}
