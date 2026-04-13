/**
 * agent.ts
 * definePlugHubAgent — wrapper function principal do SDK.
 * Spec: PlugHub v24.0 seções 4.6a, 4.6d, 4.6h
 *
 * Encapsula o ciclo de vida completo do agente:
 * login → ready → [busy → handler → done] → logout
 *
 * O handler do agente recebe apenas o contexto mapeado pelo adapter —
 * nunca o context_package interno da plataforma diretamente.
 */

import {
  ContextPackageSchema,
  AgentDoneSchema,
  type ContextPackage,
  type AgentDone,
  type Issue,
} from "@plughub/schemas"
import { PlugHubAdapter }     from "./adapter"
import { LifecycleManager }   from "./lifecycle"
import { observability }      from "./observability"

// ─────────────────────────────────────────────
// Tipos do handler
// ─────────────────────────────────────────────

export interface AgentHandlerContext {
  /** Contexto mapeado pelo PlugHubAdapter — no schema do agente */
  context:     Record<string, unknown>
  /** session_id da plataforma — apenas para referência, não para lógica */
  session_id:  string
  /** Número do turno atual */
  turn_number: number
}

export interface AgentHandlerResult {
  /** Resultado no schema do agente — mapeado pelo adapter para agent_done */
  result:      Record<string, unknown>
  /** Issues tratados neste atendimento — obrigatório */
  issues:      Issue[]
  /** Razão do handoff — obrigatório quando outcome !== "resolved" */
  handoff_reason?: string
}

export type AgentHandler = (
  ctx: AgentHandlerContext
) => Promise<AgentHandlerResult>

// ─────────────────────────────────────────────
// Config do definePlugHubAgent
// ─────────────────────────────────────────────

export interface PlugHubAgentConfig {
  agent_type_id: string
  pools:         string[]
  server_url:    string
  adapter:       PlugHubAdapter
  handler:       AgentHandler
  /** Executado quando a plataforma envia uma nova conversa */
  on_error?:     (error: Error, session_id?: string) => void
}

// ─────────────────────────────────────────────
// definePlugHubAgent
// ─────────────────────────────────────────────

export interface PlugHubAgentInstance {
  /** Inicia o ciclo de vida — login + ready */
  start(): Promise<void>
  /** Graceful shutdown */
  stop(): Promise<void>
  /**
   * Processa uma conversa recebida da plataforma.
   * Chamado pelo MCP Server quando o Routing Engine aloca o agente.
   * Gerencia busy → handler → done automaticamente.
   */
  handleConversation(rawContextPackage: unknown): Promise<void>
}

export function definePlugHubAgent(
  config: PlugHubAgentConfig
): PlugHubAgentInstance {

  const lifecycle = new LifecycleManager({
    server_url:    config.server_url,
    agent_type_id: config.agent_type_id,
    tenant_id:     "",  // preenchido após login via JWT
  })

  let _turn_counter = 0

  async function handleConversation(rawContextPackage: unknown): Promise<void> {
    // 1. Validar o context_package recebido
    const pkg = ContextPackageSchema.parse(rawContextPackage)
    const session_id = pkg.session_id
    _turn_counter++

    // 2. Iniciar observabilidade
    observability.startTurn({
      "plughub.session_id":    session_id,
      "plughub.tenant_id":     pkg.tenant_id,
      "plughub.agent_type_id": config.agent_type_id,
      "plughub.pool":          config.pools[0] ?? "",
      "plughub.turn_number":   _turn_counter,
    })

    try {
      // 3. Marcar como ocupado
      await lifecycle.busy(session_id, pkg.customer_data.customer_id)

      // 4. Mapear context_package para o schema do agente
      const mappedContext = config.adapter.fromPlatform(pkg)

      // 5. Executar o handler do agente
      const handlerResult = await config.handler({
        context:     mappedContext,
        session_id,
        turn_number: _turn_counter,
      })

      // 6. Mapear resultado do agente para o contrato da plataforma
      const platformResult = config.adapter.toPlatform(handlerResult.result)

      // 7. Validar e sinalizar conclusão
      const agentDonePayload = AgentDoneSchema.parse({
        session_id,
        agent_id:       lifecycle.instance_id,
        outcome:        platformResult.outcome,
        issue_status:   handlerResult.issues,
        handoff_reason: handlerResult.handoff_reason,
        completed_at:   new Date().toISOString(),
      })

      await lifecycle.done(agentDonePayload)

    } catch (error) {
      // Sinalizar falha — a plataforma trata escalação
      await lifecycle.done({
        session_id,
        outcome:        "escalated_human",
        issue_status:   [{
          issue_id:    `error_${Date.now()}`,
          description: error instanceof Error ? error.message : "Erro desconhecido",
          status:      "unresolved",
        }],
        handoff_reason: "sdk_handler_error",
      }).catch(() => {}) // best-effort

      config.on_error?.(
        error instanceof Error ? error : new Error(String(error)),
        session_id
      )
    } finally {
      observability.endTurn()
    }
  }

  async function start(): Promise<void> {
    lifecycle.registerShutdownHook()
    await lifecycle.login()
    await lifecycle.ready()
  }

  async function stop(): Promise<void> {
    await lifecycle.logout()
  }

  return { start, stop, handleConversation }
}
