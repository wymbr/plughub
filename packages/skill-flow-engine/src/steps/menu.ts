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
import { interpolate } from "../interpolate"

export async function executeMenu(
  step: MenuStep,
  ctx:  StepContext
): Promise<StepResult> {
  // 1. Enviar prompt ao cliente via notification_send
  //    Interpola {{$.pipeline_state.*}} antes de enviar — permite que o prompt
  //    use valores calculados em steps anteriores (ex: pergunta gerada por reason).
  const resolvedPrompt = await interpolate(step.prompt, ctx, ctx.contextStore)
  try {
    // Build the list of masked field IDs to send to the Channel Gateway.
    // A field is considered masked when:
    //   - field.masked is explicitly true, OR
    //   - field.masked is undefined and step.masked is true
    // This mirrors the routing logic applied after the BLPOP response.
    const maskedFieldIds: string[] = []
    if (step.fields && (step.masked || step.fields.some(f => f.masked === true))) {
      for (const field of step.fields) {
        const fieldMasked = field.masked
        const isMasked    = fieldMasked === true || (fieldMasked === undefined && step.masked === true)
        if (isMasked) maskedFieldIds.push(field.id)
      }
    }

    await ctx.mcpCall("notification_send", {
      session_id: ctx.sessionId,
      message:    resolvedPrompt,
      channel:    "session",
      visibility: step.visibility ?? "all",
      menu: step.interaction !== "text" ? {
        interaction:   step.interaction,
        options:       step.options ?? [],
        fields:        step.fields  ?? [],
        masked_fields: maskedFieldIds.length > 0 ? maskedFieldIds : undefined,
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
  // timeout_s === 0 or -1 both mean "block indefinitely" (spec §4.7: -1 = block indefinitely)
  const isInfinite  = step.timeout_s === 0 || step.timeout_s === -1
  const timeoutSec  = isInfinite ? 14400 : (step.timeout_s ?? 300)
  const resultKey   = `menu:result:${ctx.sessionId}`
  const closedKey   = `session:closed:${ctx.sessionId}`
  const waitingKey  = `menu:waiting:${ctx.sessionId}`
  const maskedKey   = `menu:masked:${ctx.sessionId}`

  try {
    // TTL ligeiramente maior que o timeout para cobrir latências de rede.
    // Para espera infinita: 14400s garante que a flag sobreviva a sessão inteira.
    await ctx.redis.set(waitingKey, "1", "EX", timeoutSec + 10)

    // Sinaliza ao orchestrator-bridge que este menu é mascarado — o bridge
    // deve suprimir o valor enviado pelo cliente ao encaminhar para o agente
    // humano, garantindo que o PIN/senha nunca apareça na UI do agente.
    if (step.masked) {
      await ctx.redis.set(maskedKey, "1", "EX", timeoutSec + 10)
    }
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
  // renewLock é opcional na interface — se não fornecido, assume que o lock está válido
  const lockStillHeld = ctx.renewLock ? await ctx.renewLock(timeoutSec + 60) : true
  if (!lockStillHeld) {
    // Outra instância assumiu o lock (crash recovery) — abortar sem erros
    return {
      next_step_id:      step.on_failure,
      transition_reason: "on_failure",
    }
  }

  // 4. Sinalizar atividade para o CrashDetector (B2-03).
  //    O heartbeat TTL do agente (30s) pode expirar durante o BLPOP (até 300s).
  //    O activity flag diz ao CrashDetector que o agente está vivo e bloqueado,
  //    evitando que a conversa seja re-enfileirada como se fosse um crash real.
  //    Key: {tenantId}:session:{sessionId}:active_instance:{instanceId}
  //    Renovado a cada 15s para cobrir BLPOPs longos.
  const ACTIVITY_TTL_S = 30
  let activityKey: string | null = null
  let activityRenewTimer: ReturnType<typeof setInterval> | null = null

  if (ctx.instanceId) {
    activityKey = `${ctx.tenantId}:session:${ctx.sessionId}:active_instance:${ctx.instanceId}`
    try {
      await ctx.redis.set(activityKey, "1", "EX", ACTIVITY_TTL_S)
    } catch {
      // Non-fatal — CrashDetector may see a false positive but single-agent flow is unaffected
      activityKey = null
    }
    if (activityKey) {
      activityRenewTimer = setInterval(async () => {
        try {
          await ctx.redis.expire(activityKey!, ACTIVITY_TTL_S)
        } catch {
          // Non-fatal
        }
      }, 15_000)
    }
  }

  // 5. Aguardar resposta do cliente ou sinal de desconexão — o que chegar primeiro.
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

    // ── @mention command interrupts ─────────────────────────────────────────
    // The mention_command_dispatch BPM tool may LPUSH a special JSON payload to
    // menu:result:{sessionId} to interrupt a blocked menu step:
    //   { "_mention_trigger_step": "step_id" }  — jump to a specific step
    //   { "_mention_terminate": true }           — agent should exit the conference
    //
    // These interrupts are injected only by the orchestrator bridge, never by clients.
    if (key === resultKey) {
      try {
        const parsed = JSON.parse(value) as Record<string, unknown>
        if (typeof parsed["_mention_trigger_step"] === "string") {
          // trigger_step: jump to the declared step
          return {
            next_step_id:      parsed["_mention_trigger_step"],
            transition_reason: "on_success",
          }
        }
        if (parsed["_mention_terminate"] === true) {
          // terminate_self: return on_failure so the engine cleans up
          return {
            next_step_id:      step.on_failure,
            transition_reason: "on_failure",
          }
        }
      } catch {
        // Not a JSON object — normal string response from the client; fall through
      }
    }

    // key === resultKey — cliente respondeu
    // ── Masked input handling ───────────────────────────────────────────
    // Se o step ou algum de seus campos têm masked:true, os valores sensíveis
    // devem ir para ctx.maskedScope — nunca para pipeline_state.results.
    //
    // Lógica de precedência (field-level > step-level):
    //   field.masked === true  → campo mascarado, independente de step.masked
    //   field.masked === false → campo NÃO mascarado, mesmo que step.masked=true
    //   step.masked === true   → todos os campos sem field.masked explícito são mascarados
    const stepMasked = step.masked === true
    const hasFieldDefs = step.fields && step.fields.length > 0

    if (!stepMasked && !hasFieldDefs) {
      // Caminho rápido: nenhum mascaramento configurado
      const successResult: StepResult = {
        next_step_id:      step.on_success,
        transition_reason: "on_success",
        output_value:      value,
      }
      if (step.output_as !== undefined) {
        successResult.output_as = step.output_as
      }
      return successResult
    }

    // Parse da resposta do cliente (pode ser JSON para form, string para outros)
    let responseMap: Record<string, string>
    if (step.interaction === "form") {
      try {
        const parsed = JSON.parse(value) as unknown
        responseMap = typeof parsed === "object" && parsed !== null
          ? (parsed as Record<string, string>)
          : {}
      } catch {
        responseMap = {}
      }
    } else {
      // text, button, list, checklist — resposta é uma string scalar
      // Usa o field_id do step (output_as ou step.id) como chave
      const key = step.output_as ?? step.id
      responseMap = { [key]: value }
    }

    // Classificar cada campo em mascarado vs. não-mascarado
    const nonMaskedOutput: Record<string, string> = {}

    for (const [fieldId, fieldValue] of Object.entries(responseMap)) {
      const fieldDef    = step.fields?.find(f => f.id === fieldId)
      const fieldMasked = fieldDef?.masked  // undefined | true | false

      // field.masked explícito tem precedência sobre step.masked
      const isMasked = fieldMasked === true || (fieldMasked === undefined && stepMasked)

      if (isMasked) {
        ctx.maskedScope[fieldId] = fieldValue  // vai para escopo em memória
      } else {
        nonMaskedOutput[fieldId] = fieldValue  // vai para pipeline_state
      }
    }

    // Retorna apenas os campos não-mascarados no output normal.
    // Se todos os campos eram mascarados, não há output_as útil a persistir.
    const hasNonMasked = Object.keys(nonMaskedOutput).length > 0

    // Para interações não-form, o scalar mascarado era a única saída → nada a persistir
    // Para form, pode haver mix de mascarados e não-mascarados
    const outputValue =
      step.interaction === "form" && hasNonMasked ? nonMaskedOutput
      : step.interaction !== "form" && hasNonMasked ? nonMaskedOutput[step.output_as ?? step.id]
      : undefined

    const successResult: StepResult = {
      next_step_id:      step.on_success,
      transition_reason: "on_success",
    }
    if (outputValue !== undefined && step.output_as !== undefined) {
      successResult.output_as    = step.output_as
      successResult.output_value = outputValue
    }
    return successResult

  } finally {
    // Remover flags de espera independente do resultado
    try {
      await ctx.redis.del(waitingKey)
    } catch {
      // Non-fatal
    }
    // Remover flag de menu mascarado — o bridge usa essa flag para suprimir
    // o encaminhamento do valor ao agente humano; pode ser apagada agora.
    if (step.masked) {
      try {
        await ctx.redis.del(maskedKey)
      } catch {
        // Non-fatal
      }
    }
    // Limpar activity flag e timer de renovação (B2-03)
    if (activityRenewTimer !== null) {
      clearInterval(activityRenewTimer)
    }
    if (activityKey) {
      try {
        await ctx.redis.del(activityKey)
      } catch {
        // Non-fatal
      }
    }
  }
}
