/**
 * infra/tool-guard.ts
 * Wrapper centralizado de injection guard para tools MCP.
 * Spec: PlugHub seção 9.5 — injection guard centralizado.
 *
 * Antes: assertNoInjection() chamado individualmente dentro de cada handler
 *        que aceitava texto livre (notification_send, conversation_escalate).
 *
 * Agora: withGuard() aplicado no REGISTRO de todas as tools BPM —
 *        verifica TODOS os inputs antes de qualquer handler ser invocado.
 *        Cobertura uniforme: nenhum tool BPM escapa da verificação.
 *
 * Design:
 * - Retorna MCP error response (isError: true) em vez de lançar exceção.
 *   Lançar dentro de um handler MCP quebraria o transport SSE e
 *   desconectaria o caller. O erro estruturado permite que o caller trate
 *   a rejeição sem perder a conexão.
 *
 * - A verificação cobre todo o payload de input (recursiva, profundidade 8),
 *   não apenas campos de texto livre. Isso garante cobertura mesmo que novos
 *   campos sejam adicionados ao schema sem atualizar a lista de guardas.
 *
 * Invariante: nenhuma chamada a um tool BPM que contenha padrão de injeção
 * pode alcançar o handler. Verificação ocorre ANTES do Zod.parse().
 */

import { detectInjection } from "./injection_guard"

// ─────────────────────────────────────────────
// Tipos internos
// ─────────────────────────────────────────────

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

type ToolHandler = (input: Record<string, unknown>) => Promise<ToolResult>

// ─────────────────────────────────────────────
// withGuard
// ─────────────────────────────────────────────

/**
 * Envolve um handler de tool MCP com verificação centralizada de injection guard.
 *
 * @param toolName  Nome da tool (incluído na mensagem de erro para diagnóstico)
 * @param handler   Handler original da tool
 * @returns         Novo handler que rejeita inputs maliciosos antes de chamar o original
 *
 * @example
 * server.tool(
 *   "notification_send",
 *   "...",
 *   schema,
 *   withGuard("notification_send", async (input) => { ... })
 * )
 */
export function withGuard(toolName: string, handler: ToolHandler): ToolHandler {
  return async (input: Record<string, unknown>): Promise<ToolResult> => {
    const detection = detectInjection(input)

    if (detection.detected) {
      return {
        isError: true,
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            code:    "INJECTION_DETECTED",
            error:   "injection_detected",
            message: `[injection_guard] Tool '${toolName}': ${detection.description} ` +
                     `(pattern: ${detection.pattern_id}, severity: ${detection.severity}, ` +
                     `matched: "${detection.matched}")`,
          }),
        }],
      }
    }

    return handler(input)
  }
}
