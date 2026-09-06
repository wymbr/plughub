/**
 * steps/escalate.ts
 * Executor do step type: escalate
 * Spec: PlugHub v24.0 seção 4.7 + 9.5i
 *
 * Deriva para pool via Rules Engine com pipeline_state como contexto.
 * O Rules Engine aloca o agente do pool, que recebe o pipeline_state
 * no context_package, executa, e retorna o controle ao orquestrador.
 */

import type { EscalateStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { resolveInputValue } from "../interpolate"

/**
 * Resolve o pool de destino — literal, ref pura (`$.`/`@ctx.`) ou template `{{…}}`.
 *
 * ── Por que isto existe (F3 do `adr-orchestrator-tree-navigation`) ───────────
 *
 * *"Despache para o pool que ESTA FOLHA endereça"* não era expressável: o alvo era
 * passado CRU ao `conversation_escalate`, então um orquestrador com N destinos
 * precisava de N steps `escalate` e um `choice` de N condições — a tabela de
 * roteamento escrita no controle de fluxo, que é exatamente o que a D2 tira de lá.
 *
 * ⚠️ **NADA aqui adivinha.** Se a referência não resolver, o step vai para
 * `on_failure` NOMEANDO o que não resolveu. A tentação óbvia — cair no literal, ou
 * no pool da sessão — é o `queue_pool_id or pool_id` do CLAUDE.md: um fallback de
 * ENDEREÇO que converte config ausente em despacho para o lugar errado, em silêncio.
 * Endereço recusa ALTO.
 *
 * ⚠️ E a ref não resolvida **não pode seguir como se fosse um pool**: era o estado
 * anterior a esta fase, e falhava lá no Routing Engine, longe de quem autorou.
 */
async function resolvePoolAlvo(
  step: EscalateStep,
  ctx:  StepContext,
): Promise<{ pool: string } | { erro: string }> {
  const declarado = step.target.pool

  const ehRef = declarado.startsWith("$.") || declarado.startsWith("@") || declarado.includes("{{")
  if (!ehRef) return { pool: declarado }

  const resolvido = await resolveInputValue(declarado, ctx, ctx.contextStore)

  if (typeof resolvido !== "string" || resolvido.trim() === "") {
    return {
      erro: `target.pool "${declarado}" resolveu para ` +
            `${resolvido === undefined ? "AUSENTE" : JSON.stringify(resolvido)} — ` +
            `sem pool nao ha para onde despachar, e adivinhar um seria despachar o ` +
            `contato para o lugar errado sem ninguem ficar sabendo`,
    }
  }

  // Uma ref que sobra na string e ref NAO resolvida: `interpolate` troca o
  // placeholder por "" quando a chave falta, entao `"{{$.x}}_ia"` viraria `"_ia"`
  // — um pool_id bem-formado que nao existe. Barrar aqui poe o erro ao lado da
  // causa; deixar passar o poe no Routing Engine, tres servicos adiante.
  if (resolvido.includes("{{") || resolvido.startsWith("$.") || resolvido.startsWith("@")) {
    return { erro: `target.pool "${declarado}" resolveu para "${resolvido}", que ainda e uma referencia` }
  }

  return { pool: resolvido }
}

export async function executeEscalate(
  step: EscalateStep,
  ctx:  StepContext
): Promise<StepResult> {
  const alvo = await resolvePoolAlvo(step, ctx)
  if ("erro" in alvo) {
    console.error(
      "[escalate] alvo nao resolvido, session=" + ctx.sessionId + ", step=" + step.id +
      ": " + alvo.erro,
    )
    return {
      next_step_id:      step.on_failure ?? "__complete__",
      ...(step.on_failure ? {} : { outcome: "error" as const }),
      transition_reason: "on_failure" as const,
    }
  }

  // Deriva para pool via conversation_escalate com pipeline_state completo
  try {
    await ctx.mcpCall("conversation_escalate", {
      session_id:     ctx.sessionId,
      target_pool:    alvo.pool,
      pipeline_state: ctx.state,
      error_reason:   step.error_reason,
      // F7: motivo de escalação normalizado declarado pelo agente IA.
      escalation_reason: step.reason,
    })
  } catch (err) {
    // Graceful degradation — same pattern as notify/invoke steps.
    // Without this, an MCP failure would propagate as an uncaught exception,
    // crashing the engine (500) and causing the bridge to close the session.
    const msg = err instanceof Error ? err.message : String(err)
    console.error(
      "[escalate] conversation_escalate failed for session=" + ctx.sessionId +
      " target_pool=" + alvo.pool + ": " + msg,
    )
    // If on_failure is defined, transition there; otherwise complete with error
    // to avoid a crash from undefined step id.
    if (step.on_failure) {
      return {
        next_step_id:      step.on_failure,
        transition_reason: "on_failure",
      }
    }
    return {
      next_step_id:      "__complete__",
      outcome:           "error",
      transition_reason: "on_failure",
    }
  }

  // O Rules Engine atualiza o pipeline_state quando o agente do pool
  // sinaliza agent_done. O engine detecta isso via polling do pipeline_state.
  // Quando retornar, o step escalate terá seu resultado em state.results.
  // F7: persiste o motivo normalizado em results.escalation_reason — o bridge
  // o lê ao fechar o segmento do agente IA que escalou.
  return {
    next_step_id:      "__awaiting_escalation__",
    transition_reason: "on_success",
    ...(step.reason ? { output_as: "escalation_reason", output_value: step.reason } : {}),
  }
}
