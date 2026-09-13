/**
 * tools/workflow.ts
 * Arc 19 — Webhook Workflow tools for skill-flow agents.
 *
 * Exposes workflow_trigger so that intake agents (or any skill-flow step)
 * can start an async webhook workflow session and propagate:
 *   - origin_session_id  (traceability link to the initiating session)
 *   - context            (seed ContextStore entries for the new session)
 *
 * The tool POSTs to channel-gateway POST /v1/channels/webhook/{skill_id}.
 * Channel-gateway writes the context to ContextStore BEFORE publishing to
 * Kafka, so the first skill-flow step already has @ctx.* available.
 *
 * Design notes:
 *   - tenant_id and origin_session_id are passed explicitly as inputs.
 *     In skill-flow YAML invoke steps, use $.tenant_id and $.session_id
 *     which are built-in references added to the JSONPath evalContext.
 *   - context_* prefixed inputs are collected into the ContextStore seed dict.
 *     Key mapping: "context_session_foo_bar" → "session.foo.bar" (underscores
 *     after the first two segments become dots). For explicit control, pass
 *     context as flat "ctx_tag" fields where the tag uses underscores as
 *     dot separators: ctx_session_numero_atual → "session.numero.atual"
 *     ... or simply pass context_json (JSON string).
 *   - Errors returned as MCP error response, never thrown unhandled.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { withGuard }      from "../infra/tool-guard"
import { verifySessionBoundToken, type SessionBoundPayload } from "../infra/jwt"
import type { RedisClient } from "../infra/redis"
import { writeIdentityEvidence } from "./journey"
import type { IdentityEvidenceRecord } from "@plughub/schemas"

/**
 * PID-01 — as tools de retomada exigem o token LIGADO À SESSÃO (ver `infra/jwt.ts`).
 * O skill-flow-service o injeta; o autor do YAML não o escreve e não pode trocá-lo.
 * O tenant passa a vir do token: um `tenant_id` diferente no input é recusa, não escolha.
 */
// A lista mora em `@plughub/schemas` (`SESSION_BOUND_TOOLS`), lida também pelo injetor.

type Recusa = { isError: true; content: Array<{ type: "text"; text: string }> }

export function sessionCaller(
  tool: string, input: Record<string, unknown>,
): { caller: SessionBoundPayload } | { refused: Recusa } {
  const recusa = (error: string, message: string): { refused: Recusa } => {
    console.warn(`[${tool}] RECUSADO: ${error} — ${message}`)
    return { refused: { isError: true, content: [{ type: "text", text: JSON.stringify({ error, message }) }] } }
  }
  const tok = input["session_token"]
  if (typeof tok !== "string" || !tok) {
    return recusa("missing_session_token",
      "esta tool exige o token de sessao que o bridge emite na ativacao (injetado pelo skill-flow-service)")
  }
  let caller: SessionBoundPayload
  try {
    caller = verifySessionBoundToken(tok)
  } catch {
    return recusa("invalid_session_token", "token de sessao invalido, expirado ou de outro tipo")
  }
  const pedido = input["tenant_id"]
  if (typeof pedido === "string" && pedido && pedido !== caller.tenant_id) {
    return recusa("tenant_mismatch", `tenant do input (${pedido}) diverge do da sessao (${caller.tenant_id})`)
  }
  return { caller }
}

// ─── Dependências injetadas ──────────────────────────────────────────────────

export interface WorkflowDeps {
  channelGatewayUrl: string   // e.g. http://channel-gateway:8010
  tenantId:          string
  // IDN-06 — `X-Service-Token` das rotas de identidade/pendência do gateway
  // (= PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN lá). Vazio ⇒ o gateway recusa (401), e
  // as tools dizem isso em vez de responder "sem pendência".
  channelGatewayServiceToken?: string
  /**
   * PID-02 — onde `otp_challenge`/`otp_verify` gravam a EVIDÊNCIA (journey da sessão que
   * verificou). Ausente ⇒ o verify devolve `evidence_write_failed`: posse provada sem
   * registro é a prova que ninguém consegue consultar, e isso não pode parecer sucesso.
   */
  redis?: RedisClient
}

/** PID-02 — o resultado do verify do gateway, na linguagem da evidência (ADR D4). */
export function otpEvidenceStatus(body: { verified?: boolean; reason?: string }): IdentityEvidenceRecord["status"] {
  if (body.verified === true) return "verified"
  // `no_challenge` = o desafio não existe mais; o caso dominante é o TTL ter vencido.
  if (body.reason === "no_challenge") return "expired"
  return "failed"
}

/** Headers das rotas `/identity/*` e `/pending/*` do gateway (IDN-06). */
export function identityHeaders(deps: WorkflowDeps, json = false): Record<string, string> {
  const h: Record<string, string> = {
    "X-Service-Token": deps.channelGatewayServiceToken ?? "",
    "X-Service-Name":  "mcp-server-plughub",
  }
  if (json) h["Content-Type"] = "application/json"
  return h
}

/**
 * Credencial recusada NÃO é "não achei". Sem isto um token faltando no deploy vira
 * `found: false` — o cliente com pendência ouve que não tem nenhuma, e nada fica
 * vermelho. Devolve o resultado de erro, ou null quando o status não é de credencial.
 */
function credentialRefused(res: Response, tool: string) {
  if (res.status !== 401 && res.status !== 403) return null
  console.error(`[${tool}] channel-gateway RECUSOU a credencial de servico (HTTP ${res.status}) — ` +
    "confira CHANNEL_GATEWAY_SERVICE_TOKEN aqui e PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN no gateway")
  return {
    isError: true as const,
    content: [{ type: "text" as const, text: JSON.stringify({ error: "identity_credential_refused", status: res.status }) }],
  }
}

// ─── Schemas ─────────────────────────────────────────────────────────────────

const WorkflowTriggerInputSchema = z.object({
  tenant_id: z.string().min(1).describe(
    "Tenant ID. In skill-flow YAML use $.tenant_id (built-in reference)."
  ),

  /**
   * S4 — **O POOL É A UNIDADE ENDEREÇÁVEL.** Skill + config são detalhe INTERNO do
   * deploy do pool (slot `current` + `config_json`), não um endereço.
   *
   * Endereçar por `skill_id` reabre a pergunta que o modelo de slots existe para
   * fechar — "qual config está rodando?" —, porque o MESMO skill pode estar deployado
   * em N pools com configs diferentes (é exatamente o desenho do survey: um
   * `skill_survey_outbound_v1` em três pools, um por grão). Nesse regime a resolução
   * por skill é AMBÍGUA, e o router escolheria um por score, em silêncio.
   *
   * `pool_id` vence sobre `skill_id`. `skill_id` fica como legado (um pool por skill).
   */
  pool_id: z.string().optional().describe(
    "Webhook POOL to trigger (canonical address). The pool runs whatever skill its " +
    "`current` deploy slot holds, with that slot's config. Preferred over skill_id: " +
    "the same skill may be deployed in several pools with different configs."
  ),

  skill_id: z.string().optional().describe(
    "LEGACY address: skill of the webhook workflow (e.g. 'skill_portabilidade_demo_v1'). " +
    "Only unambiguous while exactly one webhook pool declares it. Prefer pool_id."
  ),

  origin_session_id: z.string().optional().describe(
    "Current session ID — stored as core.workflow.origin_session_id in the new workflow " +
    "session ContextStore for traceability. In skill-flow YAML use $.session_id."
  ),

  /**
   * T3 — **Proveniência ≠ pertença.**
   *
   * Hoje a raiz (`root_session_id`, "de que processo faço parte") é herdada
   * INCONDICIONALMENTE do chamador. Mas nem toda filha continua o processo do pai: se,
   * dentro de um atendimento, o cliente pede algo **sem relação**, o processo novo era
   * engolido pela journey do antigo — e não havia como dizer "isto é outra coisa".
   *
   * `journey: "new"` faz a sessão nascer como **sua própria raiz** (journey nova, com
   * `@ctx.journey.*` próprio, desfecho próprio, linha própria na Vista Processos) — **mas
   * `origin_session_id` continua apontando para o pai**, então o fio de proveniência
   * sobrevive e atravessa a fronteira. Duas journeys, e o fio que as liga.
   *
   * Quem decide é o **skill**, não a plataforma: é conhecimento de negócio ("o cliente
   * pediu outra coisa"), e a plataforma não tem como inferir.
   *
   * Simétrico ao `journey_merge`: **`new` corta no nascimento; `merge` une depois.** Os
   * dois compõem — cortou cedo demais e era o mesmo assunto? O merge une. Mas **não há
   * split retroativo** (união não tem inverso), o que empurra o desenho para
   * *"na dúvida, corte"*.
   *
   * Só existe aqui, e não em `delegate`/`collect`: aqueles são o processo **estendendo a
   * mão** (delegar I/O, contatar o cliente) — parte do processo chamador por definição.
   * Um skill que quer começar outra coisa **dispara um workflow**.
   */
  journey: z.enum(["inherit", "new"]).optional().describe(
    "inherit (default): the new session joins the caller's journey. " +
    "new: it starts its OWN journey (root = self), while origin_session_id still points " +
    "back to the caller — provenance crosses the boundary, membership does not. Use when " +
    "the customer asked for something unrelated to the current process."
  ),

  context_json: z.string().optional().describe(
    "JSON-encoded ContextStore seed entries {tag: value} for the new session. " +
    "Written before routing so workflow step 1 can read them via @ctx.*. " +
    "Example: '{\"session.numero_atual\": \"11999999999\"}'"
  ),

  customer_id: z.string().optional().describe(
    "Customer identifier for the new webhook session."
  ),
})

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerWorkflowTools(
  server: McpServer,
  deps:   WorkflowDeps,
) {
  server.tool(
    "workflow_trigger",
    "Trigger an async webhook workflow session (Arc 19 unified session model). " +
    "Creates a new session with channel_type=webhook, seeds its ContextStore with " +
    "the provided context entries, and links it to the current session via " +
    "origin_session_id. The workflow runs independently — the current session " +
    "continues normally after this call. Returns { workflow_session_id, status }.",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    WorkflowTriggerInputSchema.shape as any,
    withGuard("workflow_trigger", async (input: Record<string, unknown>) => {
      console.log("[workflow_trigger] invoked input=%j", input)
      const parsed = WorkflowTriggerInputSchema.safeParse(input)
      if (!parsed.success) {
        console.error("[workflow_trigger] validation failed: %s", parsed.error.message)
        return {
          content: [{ type: "text" as const, text: `Invalid input: ${parsed.error.message}` }],
          isError: true,
        }
      }
      const {
        tenant_id, pool_id, skill_id, origin_session_id, context_json, customer_id, journey,
      } = parsed.data
      console.log(
        "[workflow_trigger] parsed ok tenant=%s pool=%s skill=%s origin=%s journey=%s",
        tenant_id, pool_id ?? "-", skill_id ?? "-", origin_session_id, journey ?? "inherit",
      )

      // S4: um endereço é obrigatório, e o POOL é o canônico.
      if (!pool_id && !skill_id) {
        return {
          content: [{ type: "text" as const, text:
            "workflow_trigger exige `pool_id` (canônico) ou `skill_id` (legado)." }],
          isError: true,
        }
      }

      // ── Parse context_json ────────────────────────────────────────────────
      let context: Record<string, string> = {}
      if (context_json) {
        try {
          context = JSON.parse(context_json) as Record<string, string>
        } catch {
          return {
            content: [{ type: "text" as const, text: `Invalid context_json: must be a valid JSON object string` }],
            isError: true,
          }
        }
      }

      // ── POST to channel-gateway trigger endpoint ───────────────────────────
      // S4: pool vence sobre skill. A rota /pool/{id} roteia DIRETO ao pool, que roda o
      // skill do seu slot `current` com o config daquele slot — sem resolução por skill,
      // que é ambígua quando o mesmo skill está deployado em N pools.
      const url = pool_id
        ? `${deps.channelGatewayUrl}/v1/channels/webhook/pool/${encodeURIComponent(pool_id)}`
        : `${deps.channelGatewayUrl}/v1/channels/webhook/${encodeURIComponent(skill_id!)}`
      console.log("[workflow_trigger] POST %s body=%j", url, { tenant_id, trigger_type: "task", origin_session_id })
      const body = {
        tenant_id,
        trigger_type:      "task",
        origin_session_id: origin_session_id ?? null,
        customer_id:       customer_id ?? null,
        // T3: pertença. `new` = a sessão nasce como sua PRÓPRIA raiz (journey nova);
        // `origin_session_id` (acima) segue apontando para o pai de qualquer forma.
        journey:           journey ?? "inherit",
        context,
      }

      let res: Response
      try {
        res = await fetch(url, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(body),
        })
      } catch (err) {
        return {
          content: [{ type: "text" as const, text: `Channel gateway unreachable: ${String(err)}` }],
          isError: true,
        }
      }

      if (!res.ok) {
        const text = await res.text().catch(() => "")
        return {
          content: [{ type: "text" as const, text: `Trigger failed (HTTP ${res.status}): ${text}` }],
          isError: true,
        }
      }

      const data = (await res.json()) as { session_id: string }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            workflow_session_id: data.session_id,
            origin_session_id:   origin_session_id ?? null,
            skill_id,
            status:              "triggered",
          }),
        }],
      }
    }),
  )

  // ── workflow_resume ─────────────────────────────────────────────────────────
  //
  // Called by an I/O agent at the end of its skill to resume the parent
  // workflow that delegated work to it via the delegate() step.
  //
  // The agent reads the resume_token from its own ContextStore:
  //   @ctx.core.workflow.resume_token   (written by the delegate step engine)
  //
  // This tool posts to channel-gateway POST /v1/channels/webhook/resume/{token}.
  //
  // decision values:
  //   "input"    — agent collected data (e.g. customer confirmed, form filled)
  //   "approved" — agent received an approval signal
  //   "rejected" — agent received a rejection or cancellation
  //   "timeout"  — agent timed out waiting for customer response
  //
  // payload: any data collected by the agent, merged into the delegate step's
  //   output_value in the workflow pipeline_state.

  server.tool(
    "workflow_resume",
    "Resume a parent workflow that delegated I/O to this agent via the delegate() step. " +
    "Call this as the last step of your skill after completing the assigned I/O task. " +
    "Pass resume_token from @ctx.core.workflow.resume_token (interpolated by skill-flow-engine). " +
    "decision: 'input' (data collected), 'approved', 'rejected', or 'timeout'.",
    {
      resume_token: z.string().min(1).describe(
        "The resume token for the parent workflow. In skill-flow YAML use " +
        "@ctx.core.workflow.resume_token — the engine resolves it from ContextStore."
      ),
      decision: z.enum(["input", "approved", "rejected", "timeout"]).describe(
        "Outcome of the I/O task. 'input' = data collected from customer. " +
        "'approved'/'rejected' = explicit customer choice. 'timeout' = no response received."
      ),
      payload: z.record(z.unknown()).optional().describe(
        "Data collected by the agent. Merged into the delegate step output in workflow pipeline_state."
      ),
      resume_origin: z.string().optional().describe(
        "Identity Resolver (nível b) — how the customer returned: same_channel|token|identity. " +
        "'identity' when resuming a pending discovered via cross-channel identity lookup. In " +
        "skill-flow YAML pass @ctx.session.resume_origin; absent/unresolved/invalid → 'token'."
      ),
      session_token: z.string().optional().describe(
        "PID-01 — token LIGADO À SESSÃO, injetado pelo skill-flow-service. Não declare no YAML."
      ),
    } as any,
    withGuard("workflow_resume", async (input: Record<string, unknown>) => {
      const quem = sessionCaller("workflow_resume", input)
      if ("refused" in quem) return quem.refused
      const parsed = z.object({
        resume_token:  z.string().min(1),
        decision:      z.enum(["input", "approved", "rejected", "timeout"]),
        payload:       z.record(z.unknown()).optional(),
        // Loose on purpose: a skill may pass @ctx.session.resume_origin that
        // resolves to undefined/empty on non-reconnect paths — tolerate it and
        // fall back to "token" rather than failing the resume with a validation
        // error (which would break the normal confirmation flow).
        resume_origin: z.string().optional(),
      }).safeParse(input)

      if (!parsed.success) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "validation_error",
            message: parsed.error.message,
          }) }],
        }
      }

      const { resume_token, decision, payload, resume_origin } = parsed.data
      // Only forward a recognised origin; anything else (undefined/""/garbage
      // from an unresolved @ctx ref) is dropped → endpoint defaults to "token".
      const validOrigin =
        resume_origin && ["same_channel", "token", "identity"].includes(resume_origin)
          ? resume_origin
          : undefined

      // POST to channel-gateway webhook resume endpoint
      const url = `${deps.channelGatewayUrl}/v1/channels/webhook/resume/${encodeURIComponent(resume_token)}`
      let res: Response
      try {
        res = await fetch(url, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            // PID-01: o tenant é o da SESSÃO que retoma, não o do env do processo.
            tenant_id: quem.caller.tenant_id,
            // Fase E.3: source default "agent" (um agente retomou o workflow via
            // delegate). Um source explícito no payload do chamador prevalece
            // (ex.: intake cancelar → "customer_reconnect").
            payload:   { decision, source: "agent", ...(payload ?? {}) },
            // Identity Resolver (nível b) — top-level axis distinct from payload.source.
            // Omitted when the caller didn't set it → endpoint defaults to "token".
            ...(validOrigin ? { resume_origin: validOrigin } : {}),
          }),
        })
      } catch (err) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "channel_gateway_unreachable",
            message: String(err),
          }) }],
        }
      }

      if (!res.ok) {
        const text = await res.text().catch(() => "")
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: `resume_failed_http_${res.status}`,
            message: text,
          }) }],
        }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ resumed: true, decision }),
        }],
      }
    }),
  )

  // ── pending_workflow_get ─────────────────────────────────────────────────────
  //
  // Checks whether a customer has an active pending workflow awaiting their
  // confirmation (created via the delegate() step pattern).
  //
  // Called by intake agents after collecting the customer's contact_identifier.
  // If a pending workflow is found, the agent presents a menu so the customer
  // can continue their existing process instead of starting a new one.
  //
  // The lookup is O(1): channel-gateway writes a {tenant}:pending_workflow:{id}
  // key when delegate() creates Session C, validated against resume_tokens.
  //
  // Returns (output_as in YAML → $.pipeline_state.<output_as>):
  //   { found: false }
  //   { found: true, resume_token, context: { numero_atual, operadora_destino, ... } }

  server.tool(
    "pending_workflow_get",
    "Check whether the customer has an active pending workflow awaiting their confirmation. " +
    "Preferred: pass anchors[] (phone/email/cpf/princ) collected during intake — the Identity " +
    "Resolver maps them to a native customer_id and finds pendings across channels. " +
    "Legacy: pass a single contact_identifier. " +
    "If found=true, present a menu so the customer can continue; use resume_token with workflow_resume.",
    {
      anchors: z.array(z.object({
        kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
        value: z.string().min(1),
      })).optional().describe(
        "Identity anchors collected during intake (preferred). Cross-channel resolution."
      ),
      contact_identifier: z.string().optional().describe(
        "LEGACY single lookup key (phone/email). Treated as one inferred phone anchor."
      ),
      tenant_id: z.string().optional().describe(
        "Tenant ID (opcional desde a PID-01: vale o da sessão; se vier, tem de ser o mesmo)."
      ),
      session_token: z.string().optional().describe(
        "PID-01 — token LIGADO À SESSÃO, injetado pelo skill-flow-service. Não declare no YAML."
      ),
    } as any,
    withGuard("pending_workflow_get", async (input: Record<string, unknown>) => {
      const quem = sessionCaller("pending_workflow_get", input)
      if ("refused" in quem) return quem.refused
      const parsed = z.object({
        anchors: z.array(z.object({
          kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
          value: z.string().min(1),
        })).optional(),
        contact_identifier: z.string().optional(),
      }).safeParse(input)

      if (!parsed.success) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "invalid_input",
            message: parsed.error.message,
          }) }],
        }
      }

      const { anchors, contact_identifier } = parsed.data
      const tenant_id = quem.caller.tenant_id

      // Preferred path: anchors → Identity Resolver (Lookup 1 → Lookup 2, cross-channel).
      if (anchors && anchors.length > 0) {
        try {
          const rRes = await fetch(`${deps.channelGatewayUrl}/v1/channels/webhook/identity/resolve`, {
            method:  "POST",
            headers: identityHeaders(deps, true),
            body:    JSON.stringify({ tenant_id, anchors, provision: false }),
          })
          const rRef = credentialRefused(rRes, "pending_workflow_get")
          if (rRef) return rRef
          if (!rRes.ok) {
            return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
          }
          const ref = await rRes.json() as { customer_id: string; status: string; matched_by?: string; verification_class?: string }
          if (!ref.customer_id) {
            // IDN-12: ambíguo chega SEM customer_id desde 2026-09-13 — antes chegava com o
            // do primeiro candidato, e esta tool entregava as pendências (e o
            // resume_token) de um cliente escolhido ao acaso. `ambiguous: true` deixa o
            // fluxo pedir outra âncora em vez de concluir "não há pendência".
            return { content: [{ type: "text" as const, text: JSON.stringify(
              ref.matched_by === "ambiguous"
                ? { found: false, count: 0, ambiguous: true }
                : { found: false, count: 0 },
            ) }] }
          }
          // ── Safe-default gate (Identity Resolver nível b, Fase 3) ──────────────
          // Retomada cross-canal (pending_by_customer) só é ACIONÁVEL quando a
          // âncora resolvente é `possessed` (posse provada por OTP). Com âncora
          // apenas `claimed`, NÃO revelamos se há pendência (anti-enumeração) e
          // devolvemos verification_required — o fluxo pode oferecer OTP e
          // re-consultar. É garantia de plataforma: o resume_token nunca sai daqui
          // sem posse. Ver docs/adr/adr-identity-channel-possession.md.
          if (ref.verification_class !== "possessed") {
            return { content: [{ type: "text" as const, text: JSON.stringify({
              found: false, count: 0,
              customer_id: ref.customer_id,
              verification_required: true,
            }) }] }
          }
          const pRes = await fetch(
            `${deps.channelGatewayUrl}/v1/channels/webhook/pending/by-customer/${encodeURIComponent(ref.customer_id)}?tenant_id=${encodeURIComponent(tenant_id)}`,
            { headers: identityHeaders(deps) },
          )
          const pRef = credentialRefused(pRes, "pending_workflow_get")
          if (pRef) return pRef
          const pdata = pRes.ok
            ? await pRes.json() as { found: boolean; count: number; pendings: unknown[] }
            : { found: false, count: 0, pendings: [] }
          return {
            content: [{ type: "text" as const, text: JSON.stringify({ customer_id: ref.customer_id, ...pdata }) }],
          }
        } catch {
          return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
        }
      }

      // Legacy path: single contact_identifier → old by-handle endpoint.
      if (!contact_identifier) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "invalid_input", message: "provide anchors[] or contact_identifier",
          }) }],
        }
      }
      const url = `${deps.channelGatewayUrl}/v1/channels/webhook/pending/${encodeURIComponent(contact_identifier)}?tenant_id=${encodeURIComponent(tenant_id)}`
      let res: Response
      try {
        res = await fetch(url, { headers: identityHeaders(deps) })
      } catch (err) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
      }
      const lRef = credentialRefused(res, "pending_workflow_get")
      if (lRef) return lRef
      if (!res.ok) {
        return { content: [{ type: "text" as const, text: JSON.stringify({ found: false }) }] }
      }
      const data = await res.json() as { found: boolean; resume_token?: string; context?: Record<string, string> }
      return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
    }),
  )

  // ── customer_resolve ─────────────────────────────────────────────────────────
  //
  // Identity-only resolution (no pendings). Resolves/provisions a native
  // customer_id from anchors — used to key history, memory, or to stamp identity
  // before delegating. PII travels only on the loopback body; hashing is
  // server-side in the channel-gateway.
  //
  // Returns: { customer_id, status, matched_by, confidence }

  server.tool(
    "customer_resolve",
    "Resolve (or provision) the native customer_id from identity anchors (phone/email/cpf/princ). " +
    "Use to identify the customer before loading history or delegating. " +
    "provision=true creates an ephemeral prospect when no match exists.",
    {
      anchors: z.array(z.object({
        kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
        value: z.string().min(1),
      })).min(1).describe("Identity anchors collected during intake."),
      tenant_id: z.string().min(1).describe("Tenant ID. In skill-flow YAML use $.tenant_id."),
      provision: z.boolean().optional().describe("Create an ephemeral prospect if no match (default true)."),
    } as any,
    withGuard("customer_resolve", async (input: Record<string, unknown>) => {
      const parsed = z.object({
        anchors: z.array(z.object({
          kind:  z.enum(["phone", "email", "cpf", "princ", "dev"]),
          value: z.string().min(1),
        })).min(1),
        tenant_id: z.string().min(1),
        provision: z.boolean().optional(),
      }).safeParse(input)

      if (!parsed.success) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({
            error: "invalid_input", message: parsed.error.message,
          }) }],
        }
      }

      const { anchors, tenant_id, provision } = parsed.data
      try {
        const res = await fetch(`${deps.channelGatewayUrl}/v1/channels/webhook/identity/resolve`, {
          method:  "POST",
          headers: identityHeaders(deps, true),
          body:    JSON.stringify({ tenant_id, anchors, provision: provision ?? true }),
        })
        const cRef = credentialRefused(res, "customer_resolve")
        if (cRef) return cRef
        if (!res.ok) {
          return {
            isError: true,
            content: [{ type: "text" as const, text: JSON.stringify({ error: `resolve_failed_http_${res.status}` }) }],
          }
        }
        const data = await res.json() as { customer_id?: string; matched_by?: string }
        if (data.matched_by === "ambiguous") {
          // IDN-12: os skills que chamam esta tool gravam `caller.customer_id` direto do
          // resultado e desviam para `on_failure` ("segue sem carimbar") quando ela falha.
          // Ambíguo É esse caso — não há cliente a carimbar —, e devolvê-lo como sucesso
          // com id vazio gravaria uma tag vazia no ContextStore.
          return {
            isError: true,
            content: [{ type: "text" as const, text: JSON.stringify({
              error: "ambiguous",
              message: "as ancoras identificam mais de um cliente no mesmo score — peça outra ancora",
            }) }],
          }
        }
        return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
      } catch (err) {
        return {
          isError: true,
          content: [{ type: "text" as const, text: JSON.stringify({ error: "resolve_unreachable" }) }],
        }
      }
    }),
  )

  // ── OTP + enrichment (Identity Resolver nível b, Fase 2) ──────────────────────
  // Step-up de POSSE de canal, componível e OPCIONAL — o fluxo aciona conforme a
  // necessidade de negócio. Verificar é escolha do fluxo; confiar é consequência
  // (a âncora só vira `possessed` — confiável para retomada sensível — via OTP).

  const _kindSchema = z.enum(["phone", "email", "cpf", "princ", "dev"])

  async function _postIdentity(path: string, body: unknown, errKey: string) {
    try {
      const res = await fetch(`${deps.channelGatewayUrl}${path}`, {
        method:  "POST",
        headers: identityHeaders(deps, true),
        body:    JSON.stringify(body),
      })
      const iRef = credentialRefused(res, errKey)
      if (iRef) return iRef
      if (!res.ok) {
        return { isError: true as const, content: [{ type: "text" as const, text: JSON.stringify({ error: `${errKey}_http_${res.status}` }) }] }
      }
      return { content: [{ type: "text" as const, text: JSON.stringify(await res.json()) }] }
    } catch {
      return { isError: true as const, content: [{ type: "text" as const, text: JSON.stringify({ error: `${errKey}_unreachable` }) }] }
    }
  }

  async function _gravarEvidencia(caller: SessionBoundPayload, record: IdentityEvidenceRecord) {
    if (!deps.redis) throw new Error("WorkflowDeps.redis ausente — sem onde gravar a evidência")
    await writeIdentityEvidence(deps.redis, caller.tenant_id, caller.session_id, "otp", record)
  }

  server.tool(
    "otp_challenge",
    "OTP de posse de canal (step-up OPCIONAL). Emite um código para uma âncora " +
    "ENTREGÁVEL (phone/email) do cliente, para provar que ele controla aquele canal. " +
    "Só é emitido quando a âncora é de procedência AUTORITATIVA para esse customer_id " +
    "(veio do cadastro importado) — OTP contra o número que o próprio cliente " +
    "informou não prova nada. Qualquer recusa volta como erro com `reason` " +
    "(undeliverable_kind · delivery_unavailable · customer_required · " +
    "anchor_not_authoritative · rate_limited · invalid_anchor): trate no on_failure, " +
    "com uma mensagem que NÃO diga ao cliente qual foi o motivo. " +
    "Entrega mockada no demo (código no dev_code/log).",
    {
      tenant_id:   z.string().min(1).describe("Tenant ID. Em skill-flow use $.tenant_id."),
      customer_id: z.string().min(1).describe("customer_id nativo (customer_resolve) — o MESMO que irá ao otp_verify."),
      kind:        _kindSchema.describe("Tipo da âncora a desafiar. Só phone e email admitem OTP."),
      value:       z.string().min(1).describe("Valor da âncora (telefone/e-mail)."),
      session_token: z.string().optional().describe(
        "PID-02 — token LIGADO À SESSÃO, injetado pelo skill-flow-service. Não declare no YAML."
      ),
    } as any,
    withGuard("otp_challenge", async (input: Record<string, unknown>) => {
      const p = z.object({
        tenant_id: z.string().min(1), customer_id: z.string().min(1),
        kind: _kindSchema, value: z.string().min(1),
      }).safeParse(input)
      if (!p.success) return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "invalid_input", message: p.error.message }) }] }
      const quem = sessionCaller("otp_challenge", input)
      if ("refused" in quem) return quem.refused
      const data = { ...p.data, tenant_id: quem.caller.tenant_id }
      const res = await _postIdentity("/v1/channels/webhook/identity/otp/challenge", data, "otp_challenge_failed")
      if ("isError" in res && res.isError) return res
      // PID-10: `sent: false` é RECUSA, não resposta. Devolvê-la como sucesso levava o
      // skill ao passo seguinte — pedir ao cliente um código que ninguém mandou.
      const body = JSON.parse(res.content[0]!.text) as { sent?: boolean; reason?: string }
      // PID-02: o mecanismo SEMPRE escreve o status (D4) — `pending` quando o código saiu,
      // `not_run` quando a prova nem começou. Falhar aqui só loga: o verify escreve de novo.
      await _gravarEvidencia(quem.caller, {
        status: body.sent === true ? "pending" : "not_run", anchor_kind: p.data.kind,
      }).catch(err => console.error(`[otp_challenge] evidência NÃO gravada session=${quem.caller.session_id}: ${String(err)}`))
      if (body.sent !== true) {
        return { isError: true as const, content: [{ type: "text" as const, text: JSON.stringify({
          error: "otp_not_sent", reason: body.reason ?? "unknown",
        }) }] }
      }
      return res
    }),
  )

  server.tool(
    "otp_verify",
    "OTP de posse — confere o código informado pelo cliente. No sucesso, a âncora " +
    "vira `possessed` (verificada) e durável no cadastro do customer_id — é a ÚNICA " +
    "forma de uma âncora virar confiável para ações sensíveis.",
    {
      tenant_id:   z.string().min(1).describe("Tenant ID."),
      customer_id: z.string().min(1).describe("customer_id nativo (customer_resolve)."),
      kind:        _kindSchema,
      value:       z.string().min(1),
      code:        z.string().min(1).describe("Código informado pelo cliente."),
      session_token: z.string().optional().describe(
        "PID-02 — token LIGADO À SESSÃO, injetado pelo skill-flow-service. Não declare no YAML."
      ),
    } as any,
    withGuard("otp_verify", async (input: Record<string, unknown>) => {
      const p = z.object({
        tenant_id: z.string().min(1), customer_id: z.string().min(1),
        kind: _kindSchema, value: z.string().min(1), code: z.string().min(1),
      }).safeParse(input)
      if (!p.success) return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "invalid_input", message: p.error.message }) }] }
      const quem = sessionCaller("otp_verify", input)
      if ("refused" in quem) return quem.refused
      const data = { ...p.data, tenant_id: quem.caller.tenant_id }
      const res = await _postIdentity("/v1/channels/webhook/identity/otp/verify", data, "otp_verify_failed")
      if ("isError" in res && res.isError) return res
      const body = JSON.parse(res.content[0]!.text) as { verified?: boolean; reason?: string; provenance?: string | null }
      const status = otpEvidenceStatus(body)
      // PID-02 — quem verifica grava (D6), na mesma chamada, no servidor. O fluxo não
      // escreve nada disto: `context_set` recusa o prefixo.
      try {
        await _gravarEvidencia(quem.caller, {
          status, anchor_kind: p.data.kind,
          ...(status === "verified" ? {
            verified_at:       new Date().toISOString(),
            proven_in_session: quem.caller.session_id,
            ...(body.provenance ? { source: body.provenance } : {}),
          } : {}),
        })
      } catch (err) {
        console.error(`[otp_verify] evidência NÃO gravada session=${quem.caller.session_id} status=${status}: ${String(err)}`)
        return { isError: true as const, content: [{ type: "text" as const, text: JSON.stringify({
          error: "evidence_write_failed", status,
        }) }] }
      }
      return res
    }),
  )

  server.tool(
    "customer_attach_key",
    "Enriquecimento da base de clientes — anexa uma âncora (phone/email/…) ao " +
    "customer_id como `claimed` (não-verificada). Para tornar a chave confiável " +
    "(`possessed`), use otp_challenge/otp_verify — esta tool NUNCA marca posse.",
    {
      tenant_id:   z.string().min(1).describe("Tenant ID."),
      customer_id: z.string().min(1).describe("customer_id nativo."),
      kind:        _kindSchema,
      value:       z.string().min(1),
    } as any,
    withGuard("customer_attach_key", async (input: Record<string, unknown>) => {
      const p = z.object({
        tenant_id: z.string().min(1), customer_id: z.string().min(1),
        kind: _kindSchema, value: z.string().min(1),
      }).safeParse(input)
      if (!p.success) return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "invalid_input", message: p.error.message }) }] }
      return _postIdentity("/v1/channels/webhook/identity/key/attach", p.data, "attach_key_failed")
    }),
  )

  server.tool(
    "customer_update_attributes",
    "Enriquecimento da base — faz merge de atributos MASCARADOS/NÃO-SENSÍVEIS de " +
    "identificação no cadastro do cliente (customers.attributes). Nunca perfil " +
    "completo, credencial ou dado sensível — isso vive no CRM do tenant.",
    {
      tenant_id:   z.string().min(1).describe("Tenant ID."),
      customer_id: z.string().min(1).describe("customer_id nativo."),
      attributes:  z.record(z.unknown()).describe("Pares chave→valor (mascarados/não-sensíveis)."),
    } as any,
    withGuard("customer_update_attributes", async (input: Record<string, unknown>) => {
      const p = z.object({
        tenant_id: z.string().min(1), customer_id: z.string().min(1),
        attributes: z.record(z.unknown()),
      }).safeParse(input)
      if (!p.success) return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: "invalid_input", message: p.error.message }) }] }
      return _postIdentity("/v1/channels/webhook/identity/attributes", p.data, "attributes_failed")
    }),
  )
}
