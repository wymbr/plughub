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
import { interpolate, resolveVisibility, resolveInputValue } from "../interpolate"
import { computeMaskedFieldIds, isFieldMasked, isStepMasked } from "../masking-policy"
import { redisKeys } from "../redis-keys"

// ── Dialog primitive §17.3-2 — dynamic options/fields ─────────────────────────
// options/fields may be a static array OR a string reference (e.g.
// "$.pipeline_state.dialog_form.nodes.0.options") resolved at runtime from a
// DialogForm loaded via form_get. Resolve to a concrete array before rendering;
// the resolved shape is identical to the static path.
type MenuOption = { id: string; label: string }
type MenuField  = { id: string; label: string; type: string; required?: boolean; masked?: boolean }

async function resolveMenuArray<T>(
  value:        unknown,
  ctx:          StepContext,
  contextStore: StepContext["contextStore"],
): Promise<T[]> {
  if (Array.isArray(value)) return value as T[]
  if (typeof value === "string") {
    const resolved = await resolveInputValue(value, ctx, contextStore)
    return Array.isArray(resolved) ? (resolved as T[]) : []
  }
  return []
}

// ── Dialog primitive §17.4 — dynamic interaction/visibility ───────────────────
// interaction and visibility may be a literal (enum / array) OR a string ref
// ($.pipeline_state.* / @ctx.*) resolved at runtime from a DialogForm's render.
// Only `$.`/`@ctx.` strings are treated as refs — enum literals like "text",
// "all", "agents_only" pass through unchanged.
const REF_PREFIX = /^(\$\.|@ctx\.)/

async function resolveDynamicValue(
  value:        unknown,
  ctx:          StepContext,
  contextStore: StepContext["contextStore"],
): Promise<unknown> {
  if (typeof value === "string" && REF_PREFIX.test(value)) {
    return resolveInputValue(value, ctx, contextStore)
  }
  return value
}

// ── Dialog primitive — retry on format failure ────────────────────────────────
// validation/retry may be a literal object OR a $./@ctx. ref (from a DialogForm
// render). Format-only (numeric/pattern/length/range) — never semantic. Applied
// to the scalar answer of non-`form` interactions; `form` (multi-field) is skipped.
interface MenuValidation {
  numeric?:    boolean
  pattern?:    string
  min_length?: number
  max_length?: number
  min?:        number
  max?:        number
}
interface MenuRetry { reprompt: string; max_attempts: number }

async function resolveObjectRef<T>(
  value:        unknown,
  ctx:          StepContext,
  contextStore: StepContext["contextStore"],
): Promise<T | undefined> {
  if (value == null) return undefined
  const resolved =
    typeof value === "string" && REF_PREFIX.test(value)
      ? await resolveInputValue(value, ctx, contextStore)
      : value
  return resolved && typeof resolved === "object" ? (resolved as T) : undefined
}

/** True when the scalar answer satisfies the format validation. Empty rules ⇒ pass. */
function validateFormat(value: string, v: MenuValidation): boolean {
  const s = value ?? ""
  if (v.numeric && (s.trim() === "" || Number.isNaN(Number(s)))) return false
  if (v.pattern) {
    try { if (!new RegExp(v.pattern).test(s)) return false } catch { /* invalid regex → skip */ }
  }
  if (v.min_length !== undefined && s.length < v.min_length) return false
  if (v.max_length !== undefined && s.length > v.max_length) return false
  if (v.min !== undefined || v.max !== undefined) {
    const n = Number(s)
    if (Number.isNaN(n)) return false
    if (v.min !== undefined && n < v.min) return false
    if (v.max !== undefined && n > v.max) return false
  }
  return true
}

export async function executeMenu(
  step: MenuStep,
  ctx:  StepContext
): Promise<StepResult> {
  // 1. Enviar prompt ao cliente via notification_send
  //    Interpola {{$.pipeline_state.*}} antes de enviar — permite que o prompt
  //    use valores calculados em steps anteriores (ex: pergunta gerada por reason).
  const resolvedPrompt = await interpolate(step.prompt, ctx, ctx.contextStore)
  // §17.4 — interaction/visibility may be a runtime ref; resolve before use.
  const rawInteraction = await resolveDynamicValue(step.interaction, ctx, ctx.contextStore)
  const resolvedInteraction = (typeof rawInteraction === "string" && rawInteraction.length > 0)
    ? rawInteraction
    : "text"
  const rawVisibility = await resolveDynamicValue(step.visibility ?? "all", ctx, ctx.contextStore)
  const resolvedVisibility = await resolveVisibility(
    rawVisibility as Parameters<typeof resolveVisibility>[0],
    ctx,
    ctx.contextStore,
  )
  // §17.3-2 — resolve options/fields (static array or runtime ref) to concrete arrays.
  const resolvedOptions = await resolveMenuArray<MenuOption>(step.options, ctx, ctx.contextStore)
  const resolvedFields  = await resolveMenuArray<MenuField>(step.fields, ctx, ctx.contextStore)
  // Retry — resolve format validation + reprompt (literal or ref). Absent ⇒ no retry.
  const resolvedValidation = await resolveObjectRef<MenuValidation>(step.validation, ctx, ctx.contextStore)
  const resolvedRetry      = await resolveObjectRef<MenuRetry>(step.retry, ctx, ctx.contextStore)
  // §21 — timeout_s may be a literal number OR a $./@ctx. ref (e.g. a DialogForm's
  // render.timeout_s). Resolve to a number; fall back to 300 on anything unusable.
  const rawTimeout = await resolveDynamicValue(step.timeout_s, ctx, ctx.contextStore)
  const resolvedTimeoutS = typeof rawTimeout === "number"
    ? rawTimeout
    : (typeof rawTimeout === "string" && rawTimeout.trim() !== "" && Number.isFinite(Number(rawTimeout)))
      ? Number(rawTimeout)
      : 300
  const maxAttempts =
    resolvedRetry && typeof resolvedRetry.max_attempts === "number" && resolvedRetry.max_attempts >= 1
      ? resolvedRetry.max_attempts
      : 1
  // Retry only makes sense for scalar answers with a validation rule.
  const retryEnabled =
    maxAttempts > 1 && !!resolvedValidation && resolvedInteraction !== "form"
  // Computa a lista de IDs mascarados usando a política centralizada.
  // Declarado fora do try para que waitingMeta (abaixo) possa referenciá-lo.
  // Para interações sem fields[] (text, button, list), usa o output_as/step.id como
  // campo implícito — permite que o webchat renderize <input type="password"> quando masked=true.
  const implicitFieldId = step.output_as ?? step.id
  const maskedFieldIds  = computeMaskedFieldIds(step.masked, resolvedFields, implicitFieldId)

  try {
    // Sempre inclui o objeto menu para todas as interações.
    // bpm.ts (mcp-server-plughub) decide o tipo de evento Kafka:
    //   - interaction !== "text" → sempre menu.payload → interaction.request no webchat
    //   - interaction === "text" com masked_fields → menu.payload → interaction.request mascarado
    //   - interaction === "text" sem masked_fields → message.text → bubble normal (compatibilidade)
    await ctx.mcpCall("notification_send", {
      session_id: ctx.sessionId,
      message:    resolvedPrompt,
      channel:    "session",
      visibility: resolvedVisibility,
      ...(ctx.segmentId ? { segment_id: ctx.segmentId } : {}),
      ...(ctx.instanceId ? { instance_id: ctx.instanceId } : {}),
      menu: {
        interaction:   resolvedInteraction,
        options:       resolvedOptions,
        fields:        resolvedFields,
        masked_fields: maskedFieldIds.length > 0 ? maskedFieldIds : undefined,
      },
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
  const isInfinite  = resolvedTimeoutS === 0 || resolvedTimeoutS === -1
  const timeoutSec  = isInfinite ? 14400 : resolvedTimeoutS
  const resultKey   = redisKeys.menuResult(ctx.sessionId, ctx.instanceId)
  const closedKey   = redisKeys.sessionClosed(ctx.sessionId)
  const waitingKey  = redisKeys.menuWaiting(ctx.sessionId)
  const maskedKey   = redisKeys.menuMasked(ctx.sessionId)

  // ── Field name no HASH menu:waiting ────────────────────────────────────
  // Cada agente bloqueado usa seu instanceId como field; "_default_" para
  // instâncias legadas sem instanceId.  O bridge faz HGETALL e roteia por
  // visibility — customer messages vão para agentes com visibility "all"
  // ou array incluindo o customer; agents_only vai para agentes internos.
  //
  // ⚠️ `||`, NUNCA `??` — e o valor em jogo é a string VAZIA, não null.
  //
  // Este campo e a chave de BLPOP (`redisKeys.menuResult`, linha 201) derivam do
  // MESMO `ctx.instanceId` e TÊM de concordar sobre o que é "sem instância".
  // `menuResult` decide por truthiness (`redis-keys.ts:28`), então `""` cai no
  // ramo session-scoped `menu:result:{sid}`. Com `??` aqui, `""` sobrevivia e o
  // campo do hash nascia com nome VAZIO — e os leitores (bridge `main.py:9180`,
  // mcp-server `server.ts:2471/2487/2497/3641`) testam `!== "_default_"`, que é
  // verdadeiro para `""`, logo faziam LPUSH em `menu:result:{sid}:` (dois-pontos
  // final). Mensagem entregue a uma lista que ninguém escuta, sem erro nenhum.
  //
  // Quem sofria: o AGENTE DE FILA, único ativado com `instance_id=""` de propósito
  // (`orchestrator-bridge/main.py:5952` — "queue agents don't hold a routing slot").
  // Ele ficava SURDO à mensagem do cliente enquanto o `__agent_available__`
  // continuava funcionando, porque o routing publica esse sinal na chave
  // session-scoped hardcoded (`kafka_listener.py:728`) — a mesma do BLPOP. Meia
  // funcionalidade viva é o que manteve o defeito invisível.
  const waitingField = ctx.instanceId || "_default_"
  const waitingMeta  = JSON.stringify({
    visibility:    resolvedVisibility,
    masked:        isStepMasked(step.masked),
    // Include the computed per-field masked IDs so the orchestrator-bridge can
    // redact individual form fields (e.g. senha, codigo_2fa) without suppressing
    // the entire submission when only some fields are masked.
    masked_fields: maskedFieldIds.length > 0 ? maskedFieldIds : [],
    // Mention-protocol standby: routers must NOT feed plain messages to this
    // BLPOP — it wakes only via mention_command_dispatch interrupts.
    standby:       step.standby === true,
  })
  try {
    // HSET + EXPIRE: cada agente registra sua entrada no hash.
    // TTL ligeiramente maior que o timeout para cobrir latências de rede.
    // Para espera infinita: 14400s garante que a flag sobreviva a sessão inteira.
    await ctx.redis.hset(waitingKey, waitingField, waitingMeta)
    await ctx.redis.expire(waitingKey, timeoutSec + 10)

    // Backward compat: manter key legada menu:masked para bridges antigos
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
    activityKey = redisKeys.activeInstance(ctx.tenantId, ctx.sessionId, ctx.instanceId)
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
    let value: string
    let attempt = 0

    // ── Retry loop (dialog primitive) ───────────────────────────────────────
    // Repete SOMENTE quando o cliente respondeu com formato inválido (reprompt na
    // mesma superfície). Timeout, desconexão e interrupts de @mention saem direto.
    // eslint-disable-next-line no-constant-condition
    while (true) {
      attempt++
      // Renova o lock antes de re-esperar (retries podem somar mais que timeout_s).
      if (attempt > 1 && ctx.renewLock) {
        const stillHeld = await ctx.renewLock(timeoutSec + 60)
        if (!stillHeld) {
          return { next_step_id: step.on_failure, transition_reason: "on_failure" }
        }
      }

      const result = await ctx.redis.blpop([resultKey, closedKey], blpopTimeout)

      if (result === null) {
        // Timeout — nenhuma resposta e nenhuma desconexão dentro de timeout_s
        // (result === null nunca ocorre quando timeout_s = 0 / BLPOP com timeout 0)
        return {
          next_step_id:      step.on_timeout ?? step.on_failure,
          transition_reason: "on_failure",
        }
      }

      const [key, raw] = result

      if (key === closedKey) {
        // Cliente desconectou durante a espera
        return {
          next_step_id:      step.on_disconnect ?? step.on_failure,
          transition_reason: "on_failure",
        }
      }

      // ── @mention command interrupts ───────────────────────────────────────
      // The mention_command_dispatch BPM tool may LPUSH a special JSON payload to
      // menu:result:{sessionId} to interrupt a blocked menu step:
      //   { "_mention_trigger_step": "step_id" }  — jump to a specific step
      //   { "_mention_terminate": true }           — agent should exit the conference
      //
      // These interrupts are injected only by the orchestrator bridge, never by clients.
      if (key === resultKey) {
        try {
          const parsed = JSON.parse(raw) as Record<string, unknown>
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

      // Cliente respondeu com `raw`. Gate de retry: formato apenas, escalar.
      if (retryEnabled && resolvedValidation && !validateFormat(raw, resolvedValidation)) {
        if (attempt >= maxAttempts) {
          // Formato nunca satisfeito dentro de max_attempts → falha (chamador decide).
          return { next_step_id: step.on_failure, transition_reason: "on_failure" }
        }
        // Reprompt na mesma superfície e espera de novo.
        try {
          await ctx.mcpCall("notification_send", {
            session_id: ctx.sessionId,
            message:    resolvedRetry!.reprompt,
            channel:    "session",
            visibility: resolvedVisibility,
            ...(ctx.segmentId ? { segment_id: ctx.segmentId } : {}),
            ...(ctx.instanceId ? { instance_id: ctx.instanceId } : {}),
            menu: {
              interaction:   resolvedInteraction,
              options:       resolvedOptions,
              fields:        resolvedFields,
              masked_fields: maskedFieldIds.length > 0 ? maskedFieldIds : undefined,
            },
          })
        } catch {
          return { next_step_id: step.on_failure, transition_reason: "on_failure" }
        }
        continue
      }

      // Formato ok (ou sem validação) — sai do loop com o valor coletado.
      value = raw
      break
    }

    // cliente respondeu
    // ── Masked input handling ───────────────────────────────────────────
    // Se o step ou algum de seus campos têm masked:true, os valores sensíveis
    // devem ir para ctx.maskedScope — nunca para pipeline_state.results.
    //
    // Lógica de precedência (field-level > step-level):
    //   field.masked === true  → campo mascarado, independente de step.masked
    //   field.masked === false → campo NÃO mascarado, mesmo que step.masked=true
    //   step.masked === true   → todos os campos sem field.masked explícito são mascarados
    // A DECLARAÇÃO viaja inteira (pode ser id de tipo); quem a interpreta é a
    // masking-policy. `step.masked === true` aqui daria `false` para `masked: "cpf"`
    // e o campo cairia em pipeline_state em claro.
    const stepMasked = step.masked
    const hasFieldDefs = resolvedFields.length > 0

    if (!isStepMasked(stepMasked) && !hasFieldDefs) {
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
    if (resolvedInteraction === "form") {
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

    // Classificar cada campo em mascarado vs. não-mascarado usando a política centralizada.
    // Garante que a mesma regra de precedência usada no envio (computeMaskedFieldIds)
    // também se aplica ao routing da resposta — os dois lados são sempre consistentes.
    const nonMaskedOutput: Record<string, string> = {}

    for (const [fieldId, fieldValue] of Object.entries(responseMap)) {
      const fieldDef = resolvedFields.find(f => f.id === fieldId)
      // Campos não declarados em step.fields herdam step.masked (tratados como undefined)
      const syntheticField = fieldDef ?? { id: fieldId }

      if (isFieldMasked(syntheticField, stepMasked)) {
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
      resolvedInteraction === "form" && hasNonMasked ? nonMaskedOutput
      : resolvedInteraction !== "form" && hasNonMasked ? nonMaskedOutput[step.output_as ?? step.id]
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
    // Remover este agente do hash de espera — HDEL em vez de DEL para não
    // apagar entradas de outros agentes bloqueados na mesma sessão (cenário
    // de conferência com NPS + wrap-up paralelos).
    try {
      await ctx.redis.hdel(waitingKey, waitingField)
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
