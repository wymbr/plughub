/**
 * tools/identity-proof.ts — AAS-19 (2026-10-02; adr-a2a-server-binding D12 item 2)
 *
 * A prova de posse FORA DE BANDA: quando o skill exige que a pessoa prove quem é e quem conversa
 * não é a pessoa (o assistente dela pelo canal `a2a`, ou um parceiro), o código não pode passar
 * pela conversa — o agente nunca o vê. O fluxo pede um LINK; a pessoa abre no navegador, recebe o
 * código no canal autoritativo do cadastro e o digita LÁ; quem confere é o gateway e quem grava a
 * evidência é o escritor único (`/internal/identity-evidence`).
 *
 *   identity_proof_link    cria o link (no gateway, dono da página e do código) para ESTE cliente
 *                          nesta sessão. Já provado aqui ⇒ `already_proven`, sem link.
 *   identity_proof_status  o VEREDITO: esta sessão provou a posse DESTE cliente, agora?
 *                          (a mesma régua do `judgeResumeEvidence`). É o que o fluxo lê para
 *                          seguir — nunca o valor que acordou o `menu`.
 *
 * O padrão do fluxo (o `menu` é só a ESPERA; quem decide é o status):
 *
 *   invoke identity_proof_link → menu (prompt com o link, timeout ≤ expires_in_s)
 *     → invoke identity_proof_status → choice verified
 *
 * No canal `a2a` a task fica em `TASK_STATE_AUTH_REQUIRED` enquanto o link está pendente e o menu
 * espera; a página, ao confirmar, acorda o menu por SINAL da plataforma (nunca texto do cliente).
 * O chamador pode mandar mensagem nesse estado (spec § 7.6.1: negociar ou recusar); ela chega ao
 * menu como resposta, e o status continua dizendo a verdade.
 *
 * Ligadas à sessão (`SESSION_BOUND_TOOLS`): a prova grava `proven_in_session` desta sessão.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { judgeResumeEvidence, type ResumeEvidenceMiss } from "@plughub/schemas"
import { withGuard }      from "../infra/tool-guard"
import type { RedisClient } from "../infra/redis"
import { sessionCaller }  from "./workflow"
import { journeyRootOfSession, journeyCtxKey } from "./journey"

export interface IdentityProofDeps {
  redis?:                     RedisClient
  channelGatewayUrl:          string
  /** `X-Service-Token` das rotas `/identity/*` do gateway (IDN-06). */
  channelGatewayServiceToken: string
}

type Resp = { isError?: true; content: Array<{ type: "text"; text: string }> }
const ok   = (o: unknown): Resp => ({ content: [{ type: "text", text: JSON.stringify(o) }] })
const erro = (tool: string, error: string, extra: Record<string, unknown> = {}): Resp => {
  console.warn(`[${tool}] RECUSADO: ${error} ${JSON.stringify(extra)}`)
  return { isError: true, content: [{ type: "text", text: JSON.stringify({ error, ...extra }) }] }
}

/** A sessão provou a posse DESTE cliente, agora? Exportada: é a decisão que o fluxo lê. */
export async function sessionProof(
  redis: RedisClient | undefined, tenantId: string, sessionId: string, customerId: string, nowMs = Date.now(),
): Promise<{ verified: boolean; missing: ResumeEvidenceMiss[]; mechanism?: string; verified_at?: string }> {
  if (!redis) return { verified: false, missing: [{ mechanism: "otp", reason: "not_verified" }] }
  const raiz = await journeyRootOfSession(redis, tenantId, sessionId)
  const hash = (await redis.hgetall(journeyCtxKey(tenantId, raiz))) ?? {}
  const j = judgeResumeEvidence(["otp"], hash, { sessionId, nowMs, customerId })
  if (!j.satisfied) return { verified: false, missing: j.missing }
  // Qual mecanismo provou: o mais recente dos que satisfazem.
  let melhor: { mechanism: string; verified_at: string } | undefined
  for (const m of ["otp", "whatsapp"]) {
    try {
      const v = JSON.parse(hash[`core.journey.identity.${m}.verified_at`] ?? "null")?.value
      const c = JSON.parse(hash[`core.journey.identity.${m}.customer_id`] ?? "null")?.value
      if (v && c === customerId && (!melhor || Date.parse(v) > Date.parse(melhor.verified_at))) {
        melhor = { mechanism: m, verified_at: String(v) }
      }
    } catch { /* tag ilegível não é prova */ }
  }
  return { verified: true, missing: [], ...(melhor ?? {}) }
}

/**
 * No token do PRÓPRIO cliente (`customer_agent`) a sessão É do titular: provar outro cliente nela
 * daria evidência de uma pessoa numa conversa que fala por outra. Devolve o motivo, ou null.
 */
export async function holderMismatch(
  redis: RedisClient | undefined, sessionId: string, customerId: string,
): Promise<string | null> {
  if (!redis) return null
  const raw = await redis.get(`session:${sessionId}:meta`)
  if (!raw) return null
  let meta: Record<string, unknown> = {}
  try { meta = JSON.parse(raw) as Record<string, unknown> } catch { return null }
  if (meta["a2a_principal_kind"] !== "customer_agent") return null
  return String(meta["customer_id"] ?? "") === customerId ? null : "customer_not_holder"
}

export function registerIdentityProofTools(server: McpServer, deps: IdentityProofDeps): void {
  const tokenDesc = "Token LIGADO À SESSÃO, injetado pelo skill-flow-service. Não declare no YAML."
  const kind = z.enum(["phone", "email"])

  server.tool(
    "identity_proof_link",
    "AAS-19 — out-of-band proof of possession. Creates a link the PERSON opens in a browser: the " +
    "code goes to the authoritative phone/e-mail of this customer and is typed THERE, never in the " +
    "conversation (the agent never sees it). Send the returned `url` in a `menu` prompt (timeout ≤ " +
    "`expires_in_s`) and, when the menu returns, read `identity_proof_status` — never the menu value. " +
    "On the a2a channel the task shows TASK_STATE_AUTH_REQUIRED while it waits. Already proven here " +
    "⇒ `already_proven: true` and no link. Refusals (`reason`): anchor_not_authoritative · " +
    "undeliverable_kind · delivery_unavailable · customer_not_holder · session_unknown.",
    {
      tenant_id:     z.string().optional(),
      customer_id:   z.string().min(1).describe("customer_id nativo (customer_resolve) — de quem é a posse a provar."),
      kind:          kind.describe("Âncora entregável: phone ou email."),
      value:         z.string().min(1).describe("Valor da âncora; tem de ser AUTORITATIVA para este cliente no cadastro."),
      session_token: z.string().optional().describe(tokenDesc),
    } as any,
    withGuard("identity_proof_link", async (input: Record<string, unknown>) => {
      const T = "identity_proof_link"
      const quem = sessionCaller(T, input)
      if ("refused" in quem) return quem.refused
      const p = z.object({ customer_id: z.string().min(1), kind, value: z.string().min(1) }).safeParse(input)
      if (!p.success) return erro(T, "invalid_input", { message: p.error.message })
      const { tenant_id, session_id } = quem.caller
      const fora = await holderMismatch(deps.redis, session_id, p.data.customer_id)
      if (fora) return erro(T, "proof_link_refused", { reason: fora })
      const ja = await sessionProof(deps.redis, tenant_id, session_id, p.data.customer_id)
      if (ja.verified) return ok({ already_proven: true, mechanism: ja.mechanism, verified_at: ja.verified_at })
      let res: Response
      try {
        res = await fetch(`${deps.channelGatewayUrl}/v1/channels/webhook/identity/proof-link`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Service-Token": deps.channelGatewayServiceToken,
                     "X-Service-Name": "mcp-server-plughub" },
          body: JSON.stringify({ tenant_id, session_id, ...p.data }),
        })
      } catch (e) {
        console.error(`[${T}] channel-gateway inalcançável: ${String(e)}`)
        return erro(T, "proof_link_unavailable")
      }
      const corpo = await res.json().catch(() => ({})) as Record<string, unknown>
      if (res.status === 401 || res.status === 403) {
        console.error(`[${T}] gateway recusou a credencial de serviço (HTTP ${res.status}) — confira ` +
          "CHANNEL_GATEWAY_SERVICE_TOKEN aqui e no gateway")
        return erro(T, "identity_credential_refused", { status: res.status })
      }
      if (!res.ok) return erro(T, "proof_link_unavailable", { status: res.status, detail: corpo["detail"] })
      if (corpo["created"] !== true) return erro(T, "proof_link_refused", { reason: corpo["reason"] ?? "unknown" })
      return ok({
        already_proven: false,
        url:            corpo["url"],
        expires_at:     corpo["expires_at"],
        expires_in_s:   corpo["expires_in_s"],
        anchor_hint:    corpo["anchor_hint"],
      })
    }),
  )

  server.tool(
    "identity_proof_status",
    "AAS-19 — the verdict: did THIS session prove possession of THIS customer, now (OTP or arrival by " +
    "the authoritative WhatsApp, ≤ 15 min)? Read it after the menu that waits for identity_proof_link.",
    {
      tenant_id:     z.string().optional(),
      customer_id:   z.string().min(1).describe("O MESMO customer_id do identity_proof_link."),
      session_token: z.string().optional().describe(tokenDesc),
    } as any,
    withGuard("identity_proof_status", async (input: Record<string, unknown>) => {
      const T = "identity_proof_status"
      const quem = sessionCaller(T, input)
      if ("refused" in quem) return quem.refused
      const customerId = typeof input["customer_id"] === "string" ? input["customer_id"] as string : ""
      if (!customerId) return erro(T, "invalid_input", { message: "customer_id obrigatório" })
      if (!deps.redis) console.error(`[${T}] sem Redis: a prova não pode ser lida — respondendo NÃO verificada`)
      const j = await sessionProof(deps.redis, quem.caller.tenant_id, quem.caller.session_id, customerId)
      return ok({ verified: j.verified, ...(j.verified
        ? { mechanism: j.mechanism, verified_at: j.verified_at }
        : { missing: j.missing.map(m => m.reason) }) })
    }),
  )
}
