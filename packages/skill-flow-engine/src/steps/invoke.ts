/**
 * steps/invoke.ts
 * Executor do step type: invoke
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Chama tool MCP diretamente e persiste resultado no pipeline_state.
 * Operação atômica — não encadeia múltiplas tools nem tem loop de raciocínio.
 *
 * Idempotência (sentinel de dois estágios):
 *   O step usa uma chave sentinela em pipeline_state.results para detectar
 *   reexecuções após crash do engine:
 *
 *   1. Antes da chamada MCP: grava sentinel = "dispatched" → saveState
 *   2. Após resultado disponível: grava sentinel = "completed" + output → saveState
 *
 *   Na retomada:
 *   - sentinel = "completed" + resultado presente → retorna resultado salvo
 *     sem re-chamar o MCP. Fecha a janela principal de crash (entre o MCP
 *     retornar e a transição do engine ser persistida).
 *   - sentinel = "dispatched" sem resultado → MCP foi chamado mas o resultado
 *     não foi persistido; re-executa a chamada. Semântica at-least-once para
 *     tools não idempotentes (janela de crash residual muito curta).
 *   - sem sentinel → primeira execução normal.
 */

import type { InvokeStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"
import { resolveInputMap }       from "../interpolate"
import { extractOutputsToCtx }   from "../context-accumulator-util"

/**
 * Arc 16 fix: `journey_merge` pode mudar a raiz canônica da journey NO MEIO de uma
 * execução de flow em andamento (não é o único caso — qualquer skill que faça merge
 * e depois leia `@ctx.journey.*` no mesmo run cai nisto). `ctx.journeyId` é lido uma
 * vez em `engine.run()` e passado por referência a cada step — mutá-lo aqui é o que
 * faz o PRÓXIMO step da mesma execução (ex.: um `delegate` logo depois) enxergar a
 * raiz nova, sem esperar um novo run com `journeyId` recalculado.
 *
 * Identifica a tool pelo NOME, não por um flag na step — `journey_merge` é uma tool
 * nativa fixa (`@plughub/mcp-server-plughub`), então não há necessidade de tornar
 * isto configurável por YAML.
 */
function applyJourneyMergeResult(
  step: InvokeStep,
  result: unknown,
  ctx:  StepContext,
): void {
  const toolName = step.target?.tool ?? step.tool ?? ""
  if (toolName !== "journey_merge") return
  if (!result || typeof result !== "object") return

  const canonicalRoot = (result as Record<string, unknown>).canonical_root
  if (typeof canonicalRoot === "string" && canonicalRoot.length > 0) {
    ctx.journeyId = canonicalRoot
  }
}

export async function executeInvoke(
  step: InvokeStep,
  ctx:  StepContext
): Promise<StepResult> {
  const outputKey   = step.output_as
  const sentinelKey = `${step.id}:__invoked__`

  // ── Idempotência: checar se a chamada MCP já completou com sucesso ─────────
  if (ctx.state.results[sentinelKey] === "completed") {
    // Resultado já gravado em uma execução anterior — retornar sem re-chamar MCP
    const storedResult = outputKey !== undefined ? ctx.state.results[outputKey] : undefined
    applyJourneyMergeResult(step, storedResult, ctx)
    return {
      next_step_id:      step.on_success,
      ...(outputKey !== undefined && { output_as: outputKey }),
      output_value:      storedResult,
      transition_reason: "on_success",
    }
  }

  // Resolver inputs — literais, referências $.* (JSONPath) ou @ctx.* (ContextStore)
  let resolvedInput: Record<string, unknown>
  try {
    resolvedInput = await resolveInputMap(
      step.input ?? {} as Record<string, unknown>,
      ctx,
      ctx.contextStore,
    )
  } catch (resolveErr) {
    // Degradação nunca silenciosa (CLAUDE.md § Postura de Engenharia): a resolução
    // de input que falha NÃO pode virar um on_failure mudo — loga o motivo. NÃO
    // logamos o resolvedInput (carrega session_token/JWT e campos livres do cliente).
    console.error(
      "[invoke] step=%s tool=%s resolveInputMap THREW: %s",
      step.id, step.tool ?? step.target?.tool ?? "?",
      resolveErr instanceof Error ? resolveErr.message : String(resolveErr),
    )
    throw resolveErr
  }

  // step.target (external MCP) and step.tool (native plughub) are both optional;
  // at least one must be present — validated at runtime per spec 4.7.
  const toolName  = step.target?.tool        ?? step.tool  ?? ""
  const mcpServer = step.target?.mcp_server  ?? "mcp-server-plughub"

  // ── Fase 1: gravar sentinel "dispatched" antes da chamada MCP ────────────
  // Permite distinguir "nunca chamado" de "chamado mas sem resultado" na retomada.
  ctx.state = {
    ...ctx.state,
    results: { ...ctx.state.results, [sentinelKey]: "dispatched" },
  }
  await ctx.saveState(ctx.state)

  try {
    const result = await ctx.mcpCall(toolName, resolvedInput, mcpServer)

    // ── Fase 2: gravar resultado + sentinel "completed" ───────────────────
    // Fecha a janela de crash entre o MCP retornar e a transição ser persistida
    // pelo loop principal do engine. Na retomada, o sentinel "completed" garante
    // que a chamada MCP não será re-executada.
    ctx.state = {
      ...ctx.state,
      results: {
        ...ctx.state.results,
        [sentinelKey]: "completed",
        ...(outputKey !== undefined && { [outputKey]: result }),
      },
    }
    await ctx.saveState(ctx.state)

    // Arc 16 — `journey_merge` muda a raiz canônica DENTRO da mesma execução de flow
    // (ex.: skill_limite_entrada_v1's unificar_journey → retomar_resultado, dois steps
    // consecutivos do mesmo run). `ctx.journeyId` é um parâmetro fixado uma vez no início
    // do run (engine.run()) — sem este patch, todo `@ctx.journey.*` lido por um step
    // POSTERIOR ao merge, na MESMA execução, resolve contra a raiz ANTIGA (a do próprio
    // contato, pré-merge — vazia), não a canônica que acabou de receber os dados. O merge
    // fica correto no Redis (prova: aliases + hash da raiz canônica ambos certos) e a
    // leitura minutos depois (um NOVO run, com journeyId fresco) também funciona — só o
    // `@ctx.journey.*` do MESMO run, logo após o merge, ficava cego. Sem isto, o sintoma
    // é indistinguível de "merge não fez nada": campos vazios, choice de resultado cai no
    // default (mascarando leitura falha como recusa) — mesma classe do bug do session_token.
    applyJourneyMergeResult(step, result, ctx)

    // ── context_tags.outputs: escrever campos do resultado no ContextStore ──
    // Complementa McpInterceptor: aplica quando o interceptor não tem a anotação.
    // Fire-and-forget — não bloqueia a transição do step.
    if (step.context_tags?.outputs && ctx.contextStore) {
      extractOutputsToCtx(
        ctx.contextStore,
        ctx.sessionId,
        ctx.customerId,
        step.context_tags.outputs,
        result as Record<string, unknown>,
        `mcp_call:${mcpServer}:${toolName}`,
        ctx.segmentId,
        ctx.journeyId,
      ).catch(err => {
        console.error("[invoke] CTX_OUTPUT_EXTRACTION_FAILED", String(err))
      })
    }

    return {
      next_step_id:      step.on_success,
      ...(outputKey !== undefined && { output_as: outputKey }),
      output_value:      result,
      transition_reason: "on_success",
    }
  } catch (error) {
    console.error(
      "[invoke] step=%s tool=%s mcpCall THREW → on_failure=%s: %s",
      step.id, toolName, step.on_failure,
      error instanceof Error ? error.message : String(error),
    )
    // Sentinel permanece como "dispatched" — na retomada via catch/retry,
    // o step será re-executado (at-least-once para a janela de crash residual).
    return {
      next_step_id:      step.on_failure,
      ...(outputKey !== undefined && { output_as: outputKey }),
      output_value: {
        error:      error instanceof Error ? error.message : "invoke_failed",
        mcp_server: mcpServer,
        tool:       toolName,
      },
      transition_reason: "on_failure",
    }
  }
}

// resolveInput removed — replaced by resolveInputMap from ../interpolate
// (supports both $.* JSONPath and @ctx.* ContextStore references)
