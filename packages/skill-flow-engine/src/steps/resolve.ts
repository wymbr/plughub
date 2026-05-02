/**
 * steps/resolve.ts
 * Executor do step type: resolve
 * Arc 5 / Context-Aware Fase 3 — coleta de contexto inline e declarativa.
 *
 * Substitui a necessidade de delegar a coleta de contexto para um agente
 * especialista (agente_contexto_ia_v1) via task step. Executa o mesmo pipeline
 * dentro do engine corrente sem criar uma sessão extra, eliminando a latência
 * de routing e a complexidade de conferência.
 *
 * Pipeline de 5 fases:
 *
 *   Fase 1 — Gap check:    Consulta ContextStore. Se todos os campos
 *                          estiverem com confiança suficiente → on_success
 *                          imediato com method=cache. 0 LLM calls.
 *
 *   Fase 2 — CRM lookup:   (opcional) Chama MCP tool para preencher gaps.
 *                          Extrai outputs para ContextStore via context_tags.
 *                          Re-verifica gaps. Se resolvido → on_success com
 *                          method=crm. 0 LLM calls. Erros são não-fatais.
 *
 *   Fase 3 — LLM question: Gera uma pergunta consolidada cobrindo todos os
 *                          gaps restantes. Recebe { gaps, context }.
 *                          Retorna { pergunta: string }.
 *                          Erro → avança com method=skipped (não bloqueia).
 *
 *   Fase 4 — Input BLPOP:  Envia pergunta via notification_send e aguarda
 *                          resposta do cliente (mesmas Redis keys que menu).
 *                          Timeout → on_success com method=timeout.
 *                          Disconnect → on_success com method=disconnected.
 *
 *   Fase 5 — LLM extract:  Extrai campos estruturados da resposta do cliente.
 *                          Escreve campos não-nulos no ContextStore com
 *                          confidence=0.7 e source=ai_inferred:{step.id}.
 *                          Erro → não-fatal. Avança com method=customer_input.
 *
 * Garantias de não-bloqueio:
 *   - Qualquer erro nas fases 2, 3, 5 → avança para on_success (nunca descarta)
 *   - Timeout/disconnect na fase 4 → on_success (nunca on_failure)
 *   - on_failure: apenas para falhas catastróficas (notification_send, lock roubado)
 *   - Se ctx.contextStore ausente → on_success imediato com method=no_contextstore
 */

import type { ResolveStep }              from "@plughub/schemas"
import type { StepContext, StepResult }  from "../executor"
import { resolveInputMap }               from "../interpolate"
import { extractOutputsToCtx }           from "../context-accumulator-util"
import { redisKeys }                     from "../redis-keys"

// ── Tipo de saída do resolve step ──────────────────────────────────────────────

export interface ResolveOutput {
  resolved: boolean
  method:   "cache" | "crm" | "customer_input" | "timeout" | "disconnected" | "skipped" | "no_contextstore"
  /** Campos que ainda estão ausentes ou com baixa confiança após o resolve */
  remaining_gaps?: string[]
}

// ── Constantes ─────────────────────────────────────────────────────────────────

const ACTIVITY_TTL_S    = 30
const ACTIVITY_RENEW_MS = 15_000

// ── Implementação ──────────────────────────────────────────────────────────────

export async function executeResolve(
  step: ResolveStep,
  ctx:  StepContext,
): Promise<StepResult> {

  // ── Sem ContextStore: degradação graciosa ─────────────────────────────────
  if (!ctx.contextStore) {
    return _success(step, { resolved: false, method: "no_contextstore" })
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Fase 1 — Gap check: ContextStore já tem tudo?
  // ─────────────────────────────────────────────────────────────────────────
  const requiredFields = step.required_fields.map(f => ({
    tag:            f.tag,
    confidence_min: f.confidence_min,
    required:       f.required,
  }))

  let gaps = await ctx.contextStore.getMissing(ctx.sessionId, requiredFields, ctx.customerId)

  if (gaps.complete) {
    return _success(step, { resolved: true, method: "cache" })
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Fase 2 — CRM lookup (opcional)
  // ─────────────────────────────────────────────────────────────────────────
  if (step.crm_lookup) {
    try {
      const resolvedInput = await resolveInputMap(
        step.crm_lookup.input ?? {},
        ctx,
        ctx.contextStore,
      )

      // Chama o tool do mcp-server configurado (ex: mcp-server-crm/customer_get)
      const rawResult = await ctx.mcpCall(
        step.crm_lookup.tool,
        resolvedInput,
        step.crm_lookup.mcp_server,
      )

      // Propaga outputs para o ContextStore via context_tags (idêntico ao invoke step)
      if (step.crm_lookup.context_tags?.outputs && rawResult !== null) {
        await extractOutputsToCtx(
          ctx.contextStore,
          ctx.sessionId,
          ctx.customerId,
          step.crm_lookup.context_tags.outputs,
          rawResult as Record<string, unknown>,
          `mcp_call:${step.crm_lookup.mcp_server}:${step.crm_lookup.tool}`,
          ctx.segmentId,
        )
      }

      // Re-verifica gaps após CRM
      gaps = await ctx.contextStore.getMissing(ctx.sessionId, requiredFields, ctx.customerId)
      if (gaps.complete) {
        return _success(step, { resolved: true, method: "crm" })
      }
    } catch {
      // CRM indisponível ou erro de tool — não-fatal, continua para coleta manual
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Fase 3 — LLM: gerar pergunta consolidada
  // ─────────────────────────────────────────────────────────────────────────
  let pergunta: string | null = null

  try {
    const gapTags   = [...gaps.missing, ...gaps.low_confidence.map(lc => lc.tag)]
    const ctxValues = await _buildContextSnapshot(ctx)

    const questionResult = await ctx.aiGatewayCall({
      prompt_id:     step.question_prompt_id,
      input:         { gaps: gapTags, context: ctxValues },
      output_schema: { pergunta: { type: "string", required: true } },
      session_id:    ctx.sessionId,
      attempt:       0,
    })

    if (
      questionResult !== null &&
      typeof questionResult === "object" &&
      "pergunta" in (questionResult as object) &&
      typeof (questionResult as Record<string, unknown>)["pergunta"] === "string"
    ) {
      pergunta = (questionResult as Record<string, string>)["pergunta"] ?? null
    }
  } catch {
    // LLM indisponível — avança sem perguntar ao cliente
  }

  if (!pergunta) {
    // Não foi possível gerar a pergunta — avança sem bloquear
    const remainingGaps = [...gaps.missing, ...gaps.low_confidence.map(lc => lc.tag)]
    return _success(step, { resolved: false, method: "skipped", remaining_gaps: remainingGaps })
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Fase 4 — Enviar pergunta e aguardar resposta (mesmo padrão do menu step)
  // ─────────────────────────────────────────────────────────────────────────
  try {
    await ctx.mcpCall("notification_send", {
      session_id: ctx.sessionId,
      message:    pergunta,
      channel:    "session",
      visibility: "all",
    })
  } catch {
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  const isInfinite  = step.timeout_s === 0 || step.timeout_s === -1
  const timeoutSec  = isInfinite ? 14400 : (step.timeout_s ?? 300)
  const resultKey   = redisKeys.menuResult(ctx.sessionId)
  const closedKey   = redisKeys.sessionClosed(ctx.sessionId)
  const waitingKey  = redisKeys.menuWaiting(ctx.sessionId)

  // Flag de espera (mesma que o menu step — bridge já sabe lidar com ela)
  try {
    await ctx.redis.set(waitingKey, "1", "EX", timeoutSec + 10)
  } catch {
    // Non-fatal
  }

  // Renova o execution lock antes do BLPOP
  const lockStillHeld = ctx.renewLock ? await ctx.renewLock(timeoutSec + 60) : true
  if (!lockStillHeld) {
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  // Activity flag para o CrashDetector (B2-03)
  let activityKey:         string | null                        = null
  let activityRenewTimer:  ReturnType<typeof setInterval> | null = null

  if (ctx.instanceId) {
    activityKey = redisKeys.activeInstance(ctx.tenantId, ctx.sessionId, ctx.instanceId)
    try {
      await ctx.redis.set(activityKey, "1", "EX", ACTIVITY_TTL_S)
    } catch {
      activityKey = null
    }
    if (activityKey) {
      activityRenewTimer = setInterval(async () => {
        try {
          await ctx.redis.expire(activityKey!, ACTIVITY_TTL_S)
        } catch {
          // Non-fatal
        }
      }, ACTIVITY_RENEW_MS)
    }
  }

  let customerResponse: string | null = null

  try {
    const blpopTimeout = isInfinite ? 0 : timeoutSec
    const result = await ctx.redis.blpop([resultKey, closedKey], blpopTimeout)

    if (result === null) {
      // Timeout
      const remainingGaps = [...gaps.missing, ...gaps.low_confidence.map(lc => lc.tag)]
      return _success(step, { resolved: false, method: "timeout", remaining_gaps: remainingGaps })
    }

    const [key, value] = result

    if (key === closedKey) {
      // Cliente desconectou
      const remainingGaps = [...gaps.missing, ...gaps.low_confidence.map(lc => lc.tag)]
      return _success(step, { resolved: false, method: "disconnected", remaining_gaps: remainingGaps })
    }

    // Verificar @mention interrupts (mesma lógica do menu step)
    if (key === resultKey) {
      try {
        const parsed = JSON.parse(value) as Record<string, unknown>
        if (typeof parsed["_mention_trigger_step"] === "string") {
          return {
            next_step_id:      parsed["_mention_trigger_step"],
            transition_reason: "on_success",
          }
        }
        if (parsed["_mention_terminate"] === true) {
          return {
            next_step_id:      step.on_failure,
            transition_reason: "on_failure",
          }
        }
      } catch {
        // Não é JSON — resposta normal do cliente
      }
    }

    customerResponse = value

  } finally {
    // Limpeza de flags (sempre executada)
    try { await ctx.redis.del(waitingKey) } catch { /* non-fatal */ }
    if (activityRenewTimer !== null) {
      clearInterval(activityRenewTimer)
    }
    if (activityKey) {
      try { await ctx.redis.del(activityKey) } catch { /* non-fatal */ }
    }
  }

  if (!customerResponse) {
    // Situação inesperada — não deveria ocorrer
    return _success(step, { resolved: false, method: "skipped" })
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Fase 5 — LLM: extrair campos da resposta do cliente
  // ─────────────────────────────────────────────────────────────────────────
  const gapTags    = [...gaps.missing, ...gaps.low_confidence.map(lc => lc.tag)]
  const ctxSnapshot = await _buildContextSnapshot(ctx)

  try {
    const extractResult = await ctx.aiGatewayCall({
      prompt_id:     step.extract_prompt_id,
      input:         {
        response:        customerResponse,
        required_fields: gapTags,
        context:         ctxSnapshot,
      },
      output_schema: {
        fields: { type: "object", required: true },
      },
      session_id:    ctx.sessionId,
      attempt:       0,
    })

    if (
      extractResult !== null &&
      typeof extractResult === "object" &&
      "fields" in (extractResult as object)
    ) {
      const fields = (extractResult as { fields: Record<string, unknown> }).fields

      if (typeof fields === "object" && fields !== null) {
        for (const [fieldTag, fieldValue] of Object.entries(fields)) {
          if (fieldValue === null || fieldValue === undefined) continue
          try {
            await ctx.contextStore.set(
              ctx.sessionId,
              fieldTag,
              {
                value:      fieldValue,
                confidence: 0.7,
                source:     `ai_inferred:${step.id}`,
                visibility: "agents_only",
              },
              undefined,
              ctx.customerId,
            )
          } catch {
            // Non-fatal: um campo com falha não cancela os outros
          }
        }
      }
    }
  } catch {
    // LLM de extração falhou — avança sem os campos extraídos
  }

  // Verifica se conseguimos resolver com a extração
  try {
    const finalGaps = await ctx.contextStore.getMissing(ctx.sessionId, requiredFields, ctx.customerId)
    return _success(step, {
      resolved: finalGaps.complete,
      method:   "customer_input",
      ...(finalGaps.complete ? {} : {
        remaining_gaps: [...finalGaps.missing, ...finalGaps.low_confidence.map(lc => lc.tag)],
      }),
    })
  } catch {
    return _success(step, { resolved: true, method: "customer_input" })
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

/**
 * Retorna on_success com output_value preenchido se output_as estiver configurado.
 */
function _success(step: ResolveStep, output: ResolveOutput): StepResult {
  const result: StepResult = {
    next_step_id:      step.on_success,
    transition_reason: "on_success",
  }
  if (step.output_as !== undefined) {
    result.output_as    = step.output_as
    result.output_value = output
  }
  return result
}

/**
 * Constrói um snapshot do ContextStore com os campos disponíveis
 * para contextualizar as chamadas LLM de geração de pergunta e extração.
 */
async function _buildContextSnapshot(
  ctx:  StepContext,
): Promise<Record<string, unknown>> {
  if (!ctx.contextStore) return {}
  try {
    const all = await ctx.contextStore.getAll(ctx.sessionId)
    // Retorna apenas os valores (não os ContextEntry completos) para o LLM
    return Object.fromEntries(
      Object.entries(all).map(([tag, entry]) => [tag, entry.value])
    )
  } catch {
    return {}
  }
}
