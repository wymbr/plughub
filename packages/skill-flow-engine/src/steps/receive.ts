/**
 * steps/receive.ts
 * Executor do step type: receive
 *
 * Suspende a execução do skill flow até que um evento matching chegue no
 * stream da sessão. Diferente do menu step, não envia nenhum prompt ao
 * cliente — apenas escuta passivamente.
 *
 * Caso de uso primário: Supervisor Evaluator — escuta a conversa em tempo
 * real, analisa cada turno via reason step e envia feedback targeted
 * somente ao agente que o convidou.
 *
 * Mecanismo Redis (idêntico ao menu step):
 *   HSET receive:waiting:{sessionId} {instanceId} <filter JSON>
 *   BLPOP receive:result:{sessionId}:{instanceId}  session:closed:{sessionId}
 *   HDEL  receive:waiting:{sessionId} {instanceId}  (always in finally)
 *
 * Bridge contract (orchestrator-bridge/main.py):
 *   Em cada evento de stream (message_sent ou event_types declarados), o bridge
 *   verifica receive:waiting:{sessionId}, aplica o filtro e faz LPUSH nos
 *   queues de instâncias que deram match. Echo suppression: bridge nunca
 *   roteia eventos publicados pela própria instância para a fila dela.
 *
 * Suporte a ciclos declarativos (cyclic DAG):
 *   receive → reason → notify → (back-edge) → receive
 *   max_iterations limita o número de iterações; on_max_iterations define
 *   a saída graciosa. O contador é mantido em pipeline_state._receive_iterations_{id}.
 */

import type { ReceiveStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { interpolate, resolveVisibility } from "../interpolate"
import { redisKeys } from "../redis-keys"

/** Payload gravado no output_as quando um evento é recebido */
interface ReceivedEventPayload {
  event_type:  string
  author_id:   string
  author_role: string
  content:     string
  received_at: string
}

export async function executeReceive(
  step: ReceiveStep,
  ctx:  StepContext
): Promise<StepResult> {
  // instanceId é obrigatório para o receive step: sem ele não há fila isolada.
  // Defensive: se ausente, falha imediatamente — não vale fazer BLPOP em fila global.
  if (!ctx.instanceId) {
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  // ── Ciclo: verificar max_iterations ─────────────────────────────────────
  // O contador fica em pipeline_state._receive_iterations_{stepId} — não
  // interfere com o output_as do step nem com outros campos do state.
  const iterKey = `_receive_iterations_${step.id}`
  if (step.max_iterations !== undefined) {
    const currentIter = (ctx.state.results?.[iterKey] as number | undefined) ?? 0
    if (currentIter >= step.max_iterations) {
      // Zerar contador para reuso futuro do step (ex: reinício de fluxo)
      await ctx.saveState({
        ...ctx.state,
        results: { ...(ctx.state.results ?? {}), [iterKey]: 0 },
      })
      return {
        next_step_id:      step.on_max_iterations ?? step.on_failure,
        transition_reason: "on_failure",
      }
    }
    // Incrementar contador antes de bloquear
    await ctx.saveState({
      ...ctx.state,
      results: { ...(ctx.state.results ?? {}), [iterKey]: currentIter + 1 },
    })
  }

  // ── notify opcional: mensagem enviada ANTES de bloquear ─────────────────
  // Sinaliza prontidão ao agente convidante sem disturbar o cliente.
  // Usa "agents_only" por padrão — nunca chega ao cliente.
  if (step.notify) {
    try {
      const resolvedMessage    = await interpolate(step.notify.message, ctx, ctx.contextStore,
        { stepType: "notify", visibility: step.notify.visibility, stepId: step.id })
      const resolvedVisibility = await resolveVisibility(step.notify.visibility ?? "agents_only", ctx, ctx.contextStore)
      await ctx.mcpCall("notification_send", {
        session_id: ctx.sessionId,
        message:    resolvedMessage,
        channel:    "session",
        visibility: resolvedVisibility,
        ...(ctx.segmentId  ? { segment_id:  ctx.segmentId  } : {}),
        ...(ctx.instanceId ? { instance_id: ctx.instanceId } : {}),
      })
    } catch {
      // Non-fatal — notify falhou mas o BLPOP ainda pode prosseguir
    }
  }

  // ── Calcular timeout ─────────────────────────────────────────────────────
  // -1 = bloquear indefinidamente (timeout 0 no BLPOP = sem timeout no Redis)
  // >0 = timeout em segundos
  const isInfinite = step.timeout_s === -1
  const timeoutSec = isInfinite ? 14400 : step.timeout_s  // 14400s = 4h = TTL máximo de sessão

  // ── Chaves Redis ─────────────────────────────────────────────────────────
  const resultKey  = redisKeys.receiveResult(ctx.sessionId, ctx.instanceId)
  const closedKey  = redisKeys.sessionClosed(ctx.sessionId)
  const waitingKey = redisKeys.receiveWaiting(ctx.sessionId)

  // ── Registrar filtro no HASH receive:waiting ─────────────────────────────
  // O bridge consulta este HASH para descobrir quais instâncias estão bloqueadas
  // e para qual delas o evento deve ser roteado.
  const filterPayload = JSON.stringify({
    author_role:  step.filter?.author_role  ?? null,
    visibility:   step.filter?.visibility   ?? null,
    event_types:  step.filter?.event_types  ?? ["message_sent"],
  })
  try {
    await ctx.redis.hset(waitingKey, ctx.instanceId, filterPayload)
    await ctx.redis.expire(waitingKey, timeoutSec + 10)
  } catch {
    // Non-fatal — degradation: bridge may not route the event
  }

  // ── Renovar execution lock antes do BLPOP ───────────────────────────────
  // Evita que o lock expire durante bloqueios longos.
  // Se renewLock retornar false: outra instância assumiu o lock (crash recovery).
  const lockStillHeld = ctx.renewLock ? await ctx.renewLock(timeoutSec + 60) : true
  if (!lockStillHeld) {
    try { await ctx.redis.hdel(waitingKey, ctx.instanceId) } catch { /* noop */ }
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  // ── Activity flag — CrashDetector ───────────────────────────────────────
  // Sinaliza que o agente está vivo e bloqueado num BLPOP, evitando
  // re-enfileiramento falso por expiração de heartbeat.
  const ACTIVITY_TTL_S = 30
  const activityKey    = redisKeys.activeInstance(ctx.tenantId, ctx.sessionId, ctx.instanceId)
  let activityRenewTimer: ReturnType<typeof setInterval> | null = null

  try {
    await ctx.redis.set(activityKey, "1", "EX", ACTIVITY_TTL_S)
    activityRenewTimer = setInterval(async () => {
      try { await ctx.redis.expire(activityKey, ACTIVITY_TTL_S) } catch { /* noop */ }
    }, 15_000)
  } catch {
    // Non-fatal
  }

  // ── BLPOP ────────────────────────────────────────────────────────────────
  // Monitora dois keys simultaneamente:
  //   receive:result:{sessionId}:{instanceId}  — bridge LPUSH quando evento passa no filtro
  //   session:closed:{sessionId}               — bridge LPUSH quando contact_closed chega
  // timeout 0 no BLPOP = bloqueio indefinido (suporte nativo do Redis)
  try {
    const blpopTimeout = isInfinite ? 0 : timeoutSec
    const result = await ctx.redis.blpop([resultKey, closedKey], blpopTimeout)

    if (result === null) {
      // Timeout expirou sem nenhum evento
      return {
        next_step_id:      step.on_timeout ?? step.on_failure,
        transition_reason: "on_failure",
      }
    }

    const [key, value] = result

    if (key === closedKey) {
      // Sessão fechada enquanto aguardava
      return {
        next_step_id:      step.on_disconnect ?? step.on_failure,
        transition_reason: "on_failure",
      }
    }

    // key === resultKey — evento recebido
    // Parsear o payload publicado pelo bridge
    let eventPayload: ReceivedEventPayload | undefined
    try {
      eventPayload = JSON.parse(value) as ReceivedEventPayload
    } catch {
      // Payload inválido — bridge publicou algo inesperado; tratar como evento genérico
      eventPayload = {
        event_type:  "message_sent",
        author_id:   "",
        author_role: "",
        content:     value,
        received_at: new Date().toISOString(),
      }
    }

    // ── Limpar sentinelas de idempotência de notify ──────────────────────────
    // Notify steps usam um sentinel "__notified__" para crash recovery.
    // Em flows cíclicos (receive → notify → receive), esse sentinel permanece
    // "completed" após a primeira execução e bloqueia todas as iterações
    // subsequentes silenciosamente. Ao receber um novo evento, limpamos todos
    // os sentinels para garantir que steps notify downstream re-executem.
    try {
      const clearedResults: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(ctx.state.results ?? {})) {
        if (!k.endsWith(":__notified__")) {
          clearedResults[k] = v
        }
      }
      await ctx.saveState({ ...ctx.state, results: clearedResults })
    } catch {
      // Non-fatal — se falhar, notify step ainda pode tentar executar
    }

    const successResult: StepResult = {
      next_step_id:      step.on_message,
      transition_reason: "on_success",
    }
    if (step.output_as !== undefined) {
      successResult.output_as    = step.output_as
      successResult.output_value = eventPayload
    }
    return successResult

  } finally {
    // Cleanup — sempre executado independentemente do resultado
    // HDEL em vez de DEL para não apagar entradas de outras instâncias
    try { await ctx.redis.hdel(waitingKey, ctx.instanceId!) } catch { /* noop */ }

    // Parar timer de renovação do activity flag e remover a flag
    if (activityRenewTimer !== null) {
      clearInterval(activityRenewTimer)
    }
    try { await ctx.redis.del(activityKey) } catch { /* noop */ }
  }
}
