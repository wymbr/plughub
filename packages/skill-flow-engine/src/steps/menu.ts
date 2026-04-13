/**
 * steps/menu.ts
 * Executor do step type: menu
 * Spec: PlugHub v24.0 seção 4.7
 *
 * Envia um prompt ao cliente e suspende a execução até que uma resposta
 * chegue via Redis (BLPOP em menu:result:{session_id}).
 *
 * O Orchestrator Bridge é responsável por:
 *   - Publicar a resposta do cliente em menu:result:{session_id}
 *     quando recebe um NormalizedInboundEvent para uma sessão IA.
 *   - Publicar em session:closed:{session_id} quando o cliente desconecta
 *     (contact_closed), para desbloquear o BLPOP imediatamente.
 *
 * Flag de presença — menu:waiting:{session_id}:
 *   Definida com TTL antes do BLPOP e removida logo após.
 *   O Orchestrator Bridge consulta esse key para decidir se deve fazer
 *   LPUSH em menu:result quando a sessão também tem um agente humano ativo
 *   (cenário de conferência — múltiplos agentes no mesmo contact).
 *
 * Três saídas possíveis:
 *   on_success    — cliente respondeu
 *   on_timeout    — nenhuma resposta dentro de timeout_s (defaults to on_failure; never fires when timeout_s = 0)
 *   on_disconnect — cliente desconectou durante a espera (defaults to on_failure)
 */

import type { MenuStep } from "@plughub/schemas"
import type { StepContext, StepResult } from "../executor"

export async function executeMenu(
  step: MenuStep,
  ctx:  StepContext
): Promise<StepResult> {
  // 1. Enviar prompt ao cliente via notification_send
  try {
    await ctx.mcpCall("notification_send", {
      session_id: ctx.sessionId,
      message:    step.prompt,
      channel:    "session",
      menu: step.interaction !== "text" ? {
        interaction: step.interaction,
        options:     step.options ?? [],
      } : undefined,
    })
  } catch {
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  // 2. Registrar flag de espera — consultada pelo bridge em cenários de conferência
  //    para entregar mensagens ao BLPOP mesmo quando há agente humano no mesmo session_id.
  //
  //    timeout_s === 0 significa espera indefinida: o menu bloqueia até o cliente
  //    responder ou desconectar (idle timeout da sessão dispara on_disconnect).
  //    Nesse caso usamos o TTL máximo de sessão (14400s = 4h) como limite superior
  //    para waitingKey e execution lock — suficiente para cobrir qualquer sessão ativa.
  const isInfinite  = step.timeout_s === 0
  const timeoutSec  = isInfinite ? 14400 : step.timeout_s
  const resultKey   = `menu:result:${ctx.sessionId}`
  const closedKey   = `session:closed:${ctx.sessionId}`
  const waitingKey  = `menu:waiting:${ctx.sessionId}`

  try {
    // TTL ligeiramente maior que o timeout para cobrir latências de rede.
    // Para espera infinita: 14400s garante que a flag sobreviva a sessão inteira.
    await ctx.redis.set(waitingKey, "1", "EX", timeoutSec + 10)
  } catch {
    // Non-fatal — degradation: conference scenario may not route correctly,
    // but single-agent flow still works
  }

  // 3. Renovar o execution lock antes do BLPOP.
  //    O lock TTL padrão (400s) seria suficiente para a maioria dos casos, mas
  //    menus com timeout_s próximo de 400s poderiam expirar durante a espera.
  //    Renovamos com timeout_s + 60s de margem para garantir que o lock sobreviva
  //    ao BLPOP inteiro e ao retorno HTTP para o bridge (margem adicional).
  //    Para espera infinita: 14400 + 60s cobre o TTL máximo de sessão.
  //
  //    Se renewLock retornar false, o lock foi tomado por outra instância durante
  //    uma janela de crash recovery — abortar graciosamente evita que duas instâncias
  //    avancem o pipeline_state simultaneamente.
  const lockStillHeld = await ctx.renewLock(timeoutSec + 60)
  if (!lockStillHeld) {
    // Outra instância assumiu o lock (crash recovery) — abortar sem erros
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  // 4. Aguardar resposta do cliente ou sinal de desconexão — o que chegar primeiro.
  //    BLPOP monitora dois keys simultaneamente:
  //      menu:result:{sessionId}    — bridge faz LPUSH quando cliente envia mensagem
  //      session:closed:{sessionId} — bridge faz LPUSH quando contact_closed chega
  //    timeout 0 no BLPOP = bloqueio indefinido (suporte nativo do Redis).
  //    Para menus infinitos, on_disconnect é a saída natural quando a sessão expira.
  try {
    const blpopTimeout = isInfinite ? 0 : timeoutSec
    const result = await ctx.redis.blpop([resultKey, closedKey], blpopTimeout)

    if (result === null) {
      // Timeout — nenhuma resposta e nenhuma desconexão dentro de timeout_s
      // (result === null nunca ocorre quando timeout_s = 0 / BLPOP com timeout 0)
      return {
        next_step_id:      step.on_timeout ?? step.on_failure,
        transition_reason: "on_failure",
      }
    }

    const [key, value] = result

    if (key === closedKey) {
      // Cliente desconectou durante a espera
      return {
        next_step_id:      step.on_disconnect ?? step.on_failure,
        transition_reason: "on_failure",
      }
    }

    // key === resultKey — cliente respondeu
    const successResult: StepResult = {
      next_step_id:      step.on_success,
      transition_reason: "on_success",
      output_value:      value,
    }
    if (step.output_as !== undefined) {
      successResult.output_as = step.output_as
    }
    return successResult

  } finally {
    // Remover flag de espera independente do resultado
    try {
      await ctx.redis.del(waitingKey)
    } catch {
      // Non-fatal
    }
  }
}
