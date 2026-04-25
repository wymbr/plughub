/**
 * regressions.ts
 * Regression cases para bugs encontrados e corrigidos.
 *
 * Cada caso documenta:
 *   - O que quebrou e quando
 *   - A causa raiz
 *   - A correção aplicada
 *
 * Estes testes são executados no runner com --regression flag.
 * São intencionalmente mais focados e mais rápidos que os cenários E2E completos.
 */

import { randomUUID }  from "crypto"
import type { ScenarioContext, ScenarioResult, Assertion } from "./types"
import { McpTestClient } from "../lib/mcp-client"
import { genSessionId, seedSessionMeta } from "../lib/redis-client"
import { pass, fail } from "../lib/report"

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now()
  const assertions: Assertion[] = []

  const mcp = new McpTestClient(ctx.mcpServerUrl)

  try {
    await mcp.connect()

    // ══════════════════════════════════════════════════════════════════════════
    // Regression 1: session_context_get retornava zero mensagens após message_send
    //
    // Data: 2026-04-20
    // Causa: session_context_get empurrava apenas obj["payload"] para o array
    //        de mensagens. MessageSchema exige message_id, session_id, timestamp,
    //        author, visibility — campos que só existem no nível do evento Redis
    //        Stream, não dentro do payload. O ZodError silencioso resultava em
    //        zero mensagens retornadas.
    //
    // Correção: tools/session.ts — session_context_get constrói o objeto completo
    //           combinando campos do stream (event_id→message_id, timestamp, author,
    //           visibility) com os campos do payload (content, masked, etc.).
    //
    // Fix: original_content adicionado a MessageSchema como optional para sobreviver
    //      ao SessionContextSchema.parse() para roles autorizados.
    // ══════════════════════════════════════════════════════════════════════════

    const r1SessionId     = genSessionId()
    const r1ParticipantId = randomUUID()
    const r1InstanceId    = `e2e-regression-${randomUUID()}`
    await seedSessionMeta(ctx.redis, r1SessionId, ctx.tenantId, randomUUID())

    let r1Token = ""
    try {
      const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", r1InstanceId)
      r1Token = login.session_token
      await mcp.agentReady(r1Token)
      await mcp.agentBusyV2(r1Token, r1SessionId, r1ParticipantId)
    } catch (err) {
      assertions.push(fail("R1 setup", String(err)))
      return buildResult(assertions, startAt, "R1 setup failed")
    }

    // Envia uma mensagem
    const msgResult = await mcp.messageSend(
      r1Token, r1SessionId, r1ParticipantId,
      { type: "text", text: "Mensagem de teste de regressão." },
      "all"
    )
    assertions.push(
      !("isError" in msgResult)
        ? pass("R1: message_send retorna sucesso")
        : fail("R1: message_send", msgResult)
    )

    // session_context_get deve retornar a mensagem enviada — não zero
    const ctxResult = await mcp.callTool("session_context_get", {
      session_token:  r1Token,
      session_id:     r1SessionId,
      participant_id: r1ParticipantId,
    })
    const ctxData     = (ctxResult.isError ? {} : (ctxResult.data ?? {})) as Record<string, unknown>
    const messages    = ctxData["messages"] as unknown[] | undefined
    const msgCount    = Array.isArray(messages) ? messages.length : 0

    assertions.push(
      msgCount > 0
        ? pass("R1: session_context_get retorna mensagens (ZodError corrigido)", {
            messages_count: msgCount,
          })
        : fail("R1: session_context_get retornou zero mensagens (regressão!)", {
            messages_count: msgCount,
            context_data:   ctxData,
          })
    )

    // Limpa
    await mcp.agentDoneV2({
      session_token:  r1Token,
      session_id:     r1SessionId,
      participant_id: r1ParticipantId,
      outcome:        "resolved",
      issue_status:   "Regression test R1 complete",
    })

    // ══════════════════════════════════════════════════════════════════════════
    // Regression 2: callTool().data era {} para evaluation_context_get e
    //               evaluation_submit
    //
    // Data: 2026-04-20
    // Causa: Cenário 09 acessava (ctxGet as any).content?.[0]?.text mas
    //        McpTestClient.callTool() já deserializa o conteúdo em .data.
    //        A propriedade .content não existe em McpCallResult — o acesso
    //        retornava undefined → fallback ?? "{}" → objeto vazio → assertions
    //        falhavam com "result: {}".
    //
    // Correção: scenarios/09_session_replayer.ts — mudou para
    //           ctxGet.isError ? {} : (ctxGet.data ?? {})
    //           Sem acesso a .content diretamente.
    //
    // Este regression case valida que callTool() retorna .data populado
    // para tools que retornam JSON, não um wrapper com .content[0].text.
    // ══════════════════════════════════════════════════════════════════════════

    const r2SessionId     = genSessionId()
    const r2ParticipantId = randomUUID()
    const r2InstanceId    = `e2e-regression2-${randomUUID()}`
    await seedSessionMeta(ctx.redis, r2SessionId, ctx.tenantId, randomUUID())

    let r2Token = ""
    try {
      const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", r2InstanceId)
      r2Token = login.session_token
      await mcp.agentReady(r2Token)
      await mcp.agentBusyV2(r2Token, r2SessionId, r2ParticipantId)
    } catch (err) {
      assertions.push(fail("R2 setup", String(err)))
      return buildResult(assertions, startAt, "R2 setup failed")
    }

    // Envia mensagem e encerra para testar session_context_get retorno direto
    await mcp.messageSend(
      r2Token, r2SessionId, r2ParticipantId,
      { type: "text", text: "Teste de parsing de callTool." },
      "all"
    )

    // Valida que callTool().data é um objeto com os campos esperados, não {}
    const ctxR2 = await mcp.callTool("session_context_get", {
      session_token:  r2Token,
      session_id:     r2SessionId,
      participant_id: r2ParticipantId,
    })
    const ctxR2Data = (ctxR2.isError ? {} : (ctxR2.data ?? {})) as Record<string, unknown>
    const hasSessionId = typeof ctxR2Data["session_id"] === "string"

    assertions.push(
      hasSessionId
        ? pass("R2: callTool().data contém session_id (parsing correto)", {
            session_id: ctxR2Data["session_id"],
          })
        : fail("R2: callTool().data está vazio ou sem session_id (regressão de parsing!)", {
            data: ctxR2Data,
          })
    )

    // Limpa
    await mcp.agentDoneV2({
      session_token:  r2Token,
      session_id:     r2SessionId,
      participant_id: r2ParticipantId,
      outcome:        "resolved",
      issue_status:   "Regression test R2 complete",
    })

  } catch (err) {
    assertions.push(fail("Regression suite unexpected error", String(err)))
  } finally {
    await mcp.disconnect().catch(() => undefined)
  }

  return buildResult(assertions, startAt)
}

function buildResult(
  assertions: Assertion[],
  startAt:    number,
  error?:     string
): ScenarioResult {
  return {
    scenario_id: "R",
    name:        "Regression Suite — casos documentados",
    passed:      assertions.every((a) => a.passed) && !error,
    assertions,
    duration_ms: Date.now() - startAt,
    error,
  }
}
