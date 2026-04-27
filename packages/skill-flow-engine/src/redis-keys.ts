/**
 * redis-keys.ts
 * Constantes de chaves Redis usadas pelo Skill Flow Engine.
 *
 * Estas chaves são contratos entre o skill-flow-engine (TypeScript) e o
 * orchestrator-bridge (Python). Alterar o formato requer atualização em ambos.
 *
 * Referência Python:
 *   packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py
 *   Buscar: "menu:waiting", "menu:masked", "session:closed", "menu:result"
 */

export const redisKeys = {
  /**
   * BLPOP key: menu step aguarda resposta do cliente aqui.
   * Bridge faz LPUSH quando o cliente envia uma mensagem durante uma sessão IA.
   */
  menuResult: (sessionId: string) => `menu:result:${sessionId}`,

  /**
   * Flag de presença: definida com TTL antes do BLPOP, removida logo após.
   * Bridge consulta para decidir se deve fazer LPUSH em cenário de conferência
   * (múltiplos agentes no mesmo contact — garante que a resposta do cliente
   * chegue ao AI agent bloqueado no BLPOP).
   */
  menuWaiting: (sessionId: string) => `menu:waiting:${sessionId}`,

  /**
   * Flag de mascaramento: definida quando step.masked=true.
   * Bridge lê antes de encaminhar a resposta ao agente humano:
   *   - flag ausente → encaminha normalmente
   *   - flag presente → substitui valor por "[entrada mascarada]" (visibility: agents_only)
   * Garante que PINs/senhas nunca apareçam na UI do agente humano.
   */
  menuMasked: (sessionId: string) => `menu:masked:${sessionId}`,

  /**
   * Sinal de desconexão: bridge faz LPUSH aqui quando contact_closed chega.
   * Desbloqueia o BLPOP imediatamente, retornando on_disconnect no menu step.
   */
  sessionClosed: (sessionId: string) => `session:closed:${sessionId}`,

  /**
   * Activity flag: sinaliza ao CrashDetector que o agente está vivo e bloqueado
   * num BLPOP — evita re-enfileiramento falso por expiração do heartbeat (30s).
   * Renovado a cada 15s pelo menu step.
   */
  activeInstance: (tenantId: string, sessionId: string, instanceId: string) =>
    `${tenantId}:session:${sessionId}:active_instance:${instanceId}`,
}
