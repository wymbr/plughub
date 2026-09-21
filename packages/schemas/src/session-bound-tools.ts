/**
 * session-bound-tools.ts — PID-01 (2026-09-13)
 *
 * UMA casa para "quais tools exigem o token LIGADO À SESSÃO" e para como ele chega a
 * elas. O mcp-server confere (`tools/workflow.ts`); o skill-flow-service injeta. Com a
 * lista em dois lugares, uma tool nova gateada de um lado só nasceria recusando em
 * todo skill — ou, pior, aceitando o token que o YAML escolheu.
 *
 * A injeção SOBRESCREVE o que o input trouxer: o autor do fluxo não escolhe a sessão
 * em nome da qual a tool age. Sem token (bridge não conseguiu emitir), o campo é
 * REMOVIDO, e a tool recusa com erro nomeado — nunca com o valor que o YAML pôs.
 */

// PID-02: otp_challenge/otp_verify entram porque a prova grava `proven_in_session` e a
// journey da SESSÃO que verificou — sem o token, a tool não sabe onde gravar.
export const SESSION_BOUND_TOOLS: readonly string[] = ["pending_workflow_get", "workflow_resume", "otp_challenge", "otp_verify"]

// MEN-08 (2026-09-21): tools que recebem o MESMO token para saber QUEM chama, sem exigi-lo.
// `conversation_escalate` decide o destino do contato — prerrogativa de quem CONDUZ —, e só
// o `instance_id` assinado diz se o chamador é o condutor ou um convidado. Não entra na
// lista de cima porque exigir o token ali faria uma falha de emissão (bridge × mcp-server)
// deixar TODO contato sem escalação; sem token, a tool segue como antes e diz que não conferiu.
export const SESSION_IDENTIFIED_TOOLS: readonly string[] = ["conversation_escalate"]

export const SESSION_BOUND_SERVER = "mcp-server-plughub"

export function isSessionBoundTool(tool: string, mcpServer?: string): boolean {
  return (!mcpServer || mcpServer === SESSION_BOUND_SERVER) && SESSION_BOUND_TOOLS.includes(tool)
}

function receivesSessionToken(tool: string, mcpServer?: string): boolean {
  return (!mcpServer || mcpServer === SESSION_BOUND_SERVER)
    && (SESSION_BOUND_TOOLS.includes(tool) || SESSION_IDENTIFIED_TOOLS.includes(tool))
}

export function injectSessionToken(
  tool: string,
  input: unknown,
  sessionToken: string | undefined,
  mcpServer?: string,
): unknown {
  if (!receivesSessionToken(tool, mcpServer)) return input
  const base = (input && typeof input === "object" ? { ...(input as Record<string, unknown>) } : {})
  delete base["session_token"]
  return sessionToken ? { ...base, session_token: sessionToken } : base
}
