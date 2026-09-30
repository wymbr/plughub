/**
 * rule-preemption.ts — a escalação por REGRA pára a IA na fronteira do passo (RUL-02).
 *
 * Uma regra do rules-engine dispara no meio da conversa (a cada turno da IA). Quem é dono
 * da ativação — o orchestrator-bridge — confere as condições e grava a MARCA
 * `session:{sid}:rule_escalation` com a instância da IA que conduz. O engine não é
 * interrompido: no topo de cada passo, a run DESSA instância vê a marca e escala A SI MESMA
 * pelo `escalate` que já existe (`conversation_escalate`: saída do agente + novo roteamento).
 * O passo em curso termina antes — no pior caso uma chamada de LLM ou um notify já
 * despachado —, e nada fica pela metade (decisão do dono, 2026-09-30).
 *
 * Regras:
 *   - **Só a instância nomeada obedece.** Especialista, hook e a IA do pool de destino (outra
 *     instância) passam direto: a marca é para quem CONDUZ.
 *   - **Uma vez.** `…:taken` (SET NX) faz exatamente uma run agir, mesmo com duas réplicas.
 *   - **O aviso ao cliente é da regra**, opcional; a plataforma não inventa texto (NIV-13).
 *     Vai direto ao `notification_send`, SEM interpolação: o texto é config do tenant e não
 *     deve abrir `{{$.pipeline_state…}}` para o cliente.
 *   - **Falha barulhenta, nunca silenciosa.** Escalar falhou → ERROR e o fluxo segue do passo
 *     em que estava; a marca fica `taken`, e a regra não tenta de novo nesta sessão.
 */

import type { Redis } from "ioredis"
import type { EscalateStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "./executor"
import { executeEscalate } from "./steps/escalate"
import { redisKeys } from "./redis-keys"

export const RULE_ESCALATION_REASON = "rule_escalation"

export const ruleEscalationKey = (sessionId: string) => `session:${sessionId}:rule_escalation`
export const ruleEscalationTakenKey = (sessionId: string) => `session:${sessionId}:rule_escalation:taken`

export interface RuleEscalationMark {
  instance_id:      string
  target_pool:      string
  rule_id:          string
  customer_notice?: string
}

type RedisLike = Pick<Redis, "get" | "set" | "del">

export function parseMark(raw: string | null): RuleEscalationMark | null {
  if (!raw) return null
  try {
    const m = JSON.parse(raw) as Record<string, unknown>
    if (typeof m["instance_id"] !== "string" || !m["instance_id"]) return null
    if (typeof m["target_pool"] !== "string" || !m["target_pool"]) return null
    return {
      instance_id: m["instance_id"],
      target_pool: m["target_pool"],
      rule_id:     typeof m["rule_id"] === "string" ? m["rule_id"] : "",
      ...(typeof m["customer_notice"] === "string" && m["customer_notice"].trim()
        ? { customer_notice: m["customer_notice"] } : {}),
    }
  } catch {
    return null
  }
}

/**
 * A marca vale para ESTA run? Devolve a marca só se a instância confere e esta run a tomou
 * (SET NX). Sem marca, marca de outra instância, ou já tomada → null.
 */
export async function claimRulePreemption(
  redis: RedisLike, sessionId: string, instanceId: string | undefined,
): Promise<RuleEscalationMark | null> {
  if (!instanceId) return null
  const raw = await redis.get(ruleEscalationKey(sessionId))
  if (!raw) return null
  const mark = parseMark(raw)
  if (!mark) {
    console.error(`[rule-preemption] marca ilegivel em session=${sessionId}: ${raw.slice(0, 160)} — ignorada`)
    return null
  }
  if (mark.instance_id !== instanceId) return null
  const took = await redis.set(ruleEscalationTakenKey(sessionId), instanceId, "EX", 86_400, "NX")
  if (!took) return null
  // O bridge pôs um sinal na fila desta instância para ACORDAR a espera; se a run estava
  // estacionada (ou nem esperava), o sinal sobra — e desviaria o próximo menu desta mesma
  // instância nesta sessão para o `on_failure`. Esta run vai sair; a fila é dela.
  try {
    await redis.del(redisKeys.menuSignal(sessionId, instanceId))
  } catch (err) {
    console.warn(`[rule-preemption] fila de sinal nao limpa session=${sessionId}: ${String(err)}`)
  }
  return mark
}

/**
 * Executa a escalação da marca: aviso opcional + o `escalate` de sempre. Devolve o resultado
 * do `escalate` quando o contato SAIU; `null` quando falhou — e aí o engine segue o passo em
 * que estava, porque a escalação não aconteceu.
 */
export async function runRuleEscalation(
  mark: RuleEscalationMark, ctx: StepContext, fromStepId: string,
): Promise<StepResult | null> {
  console.warn(
    `[rule-preemption] regra ${mark.rule_id} escala session=${ctx.sessionId} ` +
    `instance=${mark.instance_id} → pool=${mark.target_pool} (parado antes do passo ${fromStepId})`,
  )
  if (mark.customer_notice) {
    try {
      await ctx.mcpCall("notification_send", {
        session_id: ctx.sessionId,
        message:    mark.customer_notice,
        channel:    "session",
        visibility: "all",
        ...(ctx.segmentId ? { segment_id: ctx.segmentId } : {}),
        ...(ctx.instanceId ? { instance_id: ctx.instanceId } : {}),
      })
    } catch (err) {
      // O aviso é cortesia; a escalação é o que a regra decidiu. Segue, DITO.
      console.error(`[rule-preemption] aviso ao cliente falhou session=${ctx.sessionId}: ` +
                    (err instanceof Error ? err.message : String(err)))
    }
  }
  const step: EscalateStep = {
    id:      "__rule_escalation__",
    type:    "escalate",
    target:  { pool: mark.target_pool },
    context: "pipeline_state",
    reason:  RULE_ESCALATION_REASON,
    // Sem on_failure próprio: falhar volta ao passo em que o fluxo estava (abaixo).
  }
  const result = await executeEscalate(step, ctx)
  if (result.next_step_id !== "__awaiting_escalation__") {
    console.error(
      `[rule-preemption] escalacao da regra ${mark.rule_id} FALHOU session=${ctx.sessionId} ` +
      `→ pool=${mark.target_pool}; o fluxo segue do passo ${fromStepId} e a regra nao tenta de novo`,
    )
    return null
  }
  return result
}
