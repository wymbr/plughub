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
   * BLPOP key: menu step aguarda resposta aqui.
   *
   * Quando instanceId é fornecido, cada agente recebe sua própria fila isolada:
   *   menu:result:{sessionId}:{instanceId}
   * Isso evita race conditions em cenários de conferência com múltiplos agentes
   * bloqueados simultaneamente (ex: NPS + wrap-up em paralelo).
   *
   * Bridge/mcp-server consultam o hash menu:waiting:{sessionId} para descobrir
   * qual fila usar ao rotear uma mensagem.
   *
   * Fallback sem instanceId: menu:result:{sessionId} (comportamento legado).
   */
  menuResult: (sessionId: string, instanceId?: string) =>
    instanceId
      ? `menu:result:${sessionId}:${instanceId}`
      : `menu:result:${sessionId}`,

  /**
   * HASH de presença: armazena metadados de cada agente bloqueado em menu step.
   *
   * Key:   menu:waiting:{sessionId}
   * Field: instanceId (ou "_default_" como fallback)
   * Value: JSON({ visibility, masked })
   *
   *   visibility — a mesma declarada no notification_send do menu step:
   *     "all" | "agents_only" | ["participant_id_1", ...]
   *   masked — true se o step captura dados sensíveis (PIN, senha)
   *
   * Bridge e mcp-server fazem HGETALL e roteiam a mensagem para o agente
   * cuja visibility corresponde ao remetente:
   *   - Customer envia → agente com visibility "all" ou array incluindo customer
   *   - Agent humano envia → agente com visibility "agents_only" ou array incluindo o agent
   *
   * Definida com TTL antes do BLPOP, campo removido via HDEL após resposta.
   */
  menuWaiting: (sessionId: string) => `menu:waiting:${sessionId}`,

  /**
   * @deprecated Substituído pelo campo "masked" dentro do hash menu:waiting.
   * Mantido apenas para backward compat com bridges que ainda não foram atualizados.
   */
  menuMasked: (sessionId: string) => `menu:masked:${sessionId}`,

  /**
   * Sinal de desconexão: bridge faz LPUSH aqui quando contact_closed chega.
   * Desbloqueia o BLPOP imediatamente, retornando on_disconnect no menu step.
   *
   * Quando múltiplos agentes estão bloqueados simultaneamente, o bridge faz
   * LPUSH N vezes (N = número de entradas no hash menu:waiting) para garantir
   * que todos os BLPOPs sejam desbloqueados.
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
