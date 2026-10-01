/**
 * tools/customer-agent.ts — AAS-09 (2026-10-01; adr-a2a-server-binding D6, D9, D13)
 *
 * O token que o PRÓPRIO cliente gera para o assistente dele (o principal `customer_agent`):
 *
 *   customer_agent_grant   emite — devolve um LINK de retirada de uso único, nunca a credencial
 *   customer_agent_revoke  revoga os tokens ativos do titular
 *
 * As duas são tools LIGADAS À SESSÃO (`SESSION_BOUND_TOOLS`): o token de sessão é injetado pelo
 * skill-flow-service e o autor do YAML não escolhe a sessão.
 *
 * **O titular sai da PROVA, nunca do input.** O fluxo do tenant chama depois de a pessoa provar
 * a posse NESTA sessão (OTP, ou a chegada pelo WhatsApp do telefone autoritativo); o titular é o
 * cliente cuja prova está fresca aqui (`freshProofs`, a mesma régua do `judgeResumeEvidence`). Um
 * `customer_id` vindo do fluxo nomearia qualquer um — o fluxo é configuração do tenant, e o token
 * fala em nome de uma PESSOA. Sem prova, ou com prova de dois clientes na mesma sessão, recusa.
 *
 * **A credencial nunca passa pela conversa.** O que volta ao fluxo é o link
 * `{A2A_PUBLIC_BASE_URL}/a2a/customer-token/{código}`; a credencial só NASCE quando a pessoa abre
 * o link e pede (`redeem`, no auth-api), e é mostrada uma vez, no navegador dela. O link fica no
 * transcrito: por isso vale minutos, é de uso único, e a retirada por outro fica registrada e é
 * revogável.
 */

import { z }              from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { freshProofs, type FreshProof } from "@plughub/schemas"
import { withGuard }      from "../infra/tool-guard"
import type { RedisClient } from "../infra/redis"
import { sessionCaller }  from "./workflow"
import { journeyRootOfSession, journeyCtxKey } from "./journey"

export interface CustomerAgentDeps {
  redis?:            RedisClient
  /** auth-api — dono de `agent_principals` (http://auth-api:3200). */
  authApiUrl:        string
  /** `X-Service-Token` das rotas de serviço do auth-api (== PLUGHUB_AUTH_SERVICE_TOKEN lá). */
  authServiceToken:  string
  /** Base PÚBLICA do canal a2a (== PLUGHUB_A2A_PUBLIC_BASE_URL do gateway). Vazia ⇒ recusa. */
  a2aPublicBaseUrl:  string
}

type Resp = { isError?: true; content: Array<{ type: "text"; text: string }> }
const ok   = (o: unknown): Resp => ({ content: [{ type: "text", text: JSON.stringify(o) }] })
const erro = (tool: string, error: string, extra: Record<string, unknown> = {}): Resp => {
  console.warn(`[${tool}] RECUSADO: ${error} ${JSON.stringify(extra)}`)
  return { isError: true, content: [{ type: "text", text: JSON.stringify({ error, ...extra }) }] }
}

/**
 * O titular desta sessão, pela prova fresca. Devolve a prova, ou o motivo da recusa.
 * Exportada para teste: é a decisão que esta ficha existe para tomar certo.
 */
export async function holderOfSession(
  redis: RedisClient | undefined, tenantId: string, sessionId: string, nowMs = Date.now(),
): Promise<{ proof: FreshProof } | { refused: "no_redis" | "no_fresh_proof" | "ambiguous_holder" }> {
  if (!redis) return { refused: "no_redis" }
  const raiz = await journeyRootOfSession(redis, tenantId, sessionId)
  const hash = (await redis.hgetall(journeyCtxKey(tenantId, raiz))) ?? {}
  const provas = freshProofs(hash, { sessionId, nowMs })
  const clientes = new Set(provas.map(p => p.customer_id))
  if (clientes.size === 0) return { refused: "no_fresh_proof" }
  if (clientes.size > 1)  return { refused: "ambiguous_holder" }
  // Dois mecanismos do mesmo cliente: vale o mais recente (a prova de pé mais nova).
  const proof = [...provas].sort((a, b) => Date.parse(b.verified_at) - Date.parse(a.verified_at))[0]!
  return { proof }
}

function authHeaders(deps: CustomerAgentDeps): Record<string, string> {
  return { "Content-Type": "application/json", "X-Service-Token": deps.authServiceToken,
           "X-Service-Name": "mcp-server-plughub" }
}

export function registerCustomerAgentTools(server: McpServer, deps: CustomerAgentDeps): void {
  const tokenDesc = "Token LIGADO À SESSÃO, injetado pelo skill-flow-service. Não declare no YAML."

  server.tool(
    "customer_agent_grant",
    "AAS-09 — the customer generates a personal token for THEIR OWN AI assistant to call this " +
    "tenant's agents over A2A. Call ONLY after the customer proved possession in this session " +
    "(otp_verify, or arrival by the authoritative WhatsApp). The holder comes from that proof, never " +
    "from input. Returns a ONE-TIME pickup link (minutes) to send to the customer: the token itself " +
    "is shown once in their browser and never passes through the conversation.",
    {
      pools:         z.array(z.string().min(1)).min(1).max(10).describe(
        "Pools the assistant may call. Each must expose A2A to customer_agent and declare the customer_agent policy."),
      mandate:       z.array(z.string().min(1)).max(20).optional().describe(
        "What the person declared the assistant may do (e.g. ['consultar']). Carried in the token."),
      display_name:  z.string().min(1).max(120).optional(),
      tenant_id:     z.string().optional(),
      session_token: z.string().optional().describe(tokenDesc),
    } as any,
    withGuard("customer_agent_grant", async (input: Record<string, unknown>) => {
      const T = "customer_agent_grant"
      const quem = sessionCaller(T, input)
      if ("refused" in quem) return quem.refused
      const p = z.object({
        pools:        z.array(z.string().min(1)).min(1).max(10),
        mandate:      z.array(z.string().min(1)).max(20).optional(),
        display_name: z.string().min(1).max(120).optional(),
      }).safeParse(input)
      if (!p.success) return erro(T, "invalid_input", { message: p.error.message })
      if (!deps.a2aPublicBaseUrl) {
        console.error(`[${T}] A2A_PUBLIC_BASE_URL vazia: nenhum link de retirada pode ser montado`)
        return erro(T, "a2a_not_configured", { env: "A2A_PUBLIC_BASE_URL" })
      }
      const { tenant_id, session_id } = quem.caller
      const h = await holderOfSession(deps.redis, tenant_id, session_id)
      if ("refused" in h) {
        // Sem prova fresca o fluxo deve oferecer OTP e chamar de novo — a recusa diz isso.
        return erro(T, h.refused, { identity_required: ["otp"], session_id })
      }
      let res: Response
      try {
        res = await fetch(`${deps.authApiUrl}/auth/v1/agent-principals/customer-grants`, {
          method: "POST", headers: authHeaders(deps),
          body: JSON.stringify({
            tenant_id,
            customer_id: h.proof.customer_id,
            proof: { mechanism: h.proof.mechanism, verified_at: h.proof.verified_at, session_id },
            allowed_pools: p.data.pools,
            mandate: p.data.mandate ?? [],
            ...(p.data.display_name ? { display_name: p.data.display_name } : {}),
          }),
        })
      } catch (e) {
        console.error(`[${T}] auth-api inalcançável: ${String(e)}`)
        return erro(T, "auth_api_unavailable")
      }
      const corpo = await res.json().catch(() => ({})) as Record<string, unknown>
      if (res.status === 401 || res.status === 503) {
        console.error(`[${T}] auth-api recusou a credencial de serviço (HTTP ${res.status}) — confira ` +
          "AUTH_SERVICE_TOKEN aqui e PLUGHUB_AUTH_SERVICE_TOKEN no auth-api")
        return erro(T, "auth_credential_refused", { status: res.status })
      }
      if (!res.ok) return erro(T, "grant_refused", { status: res.status, detail: corpo["detail"] })
      const base = deps.a2aPublicBaseUrl.replace(/\/+$/, "")
      // O que volta ao fluxo NÃO contém o código em claro além do link: é o link que se manda.
      return ok({
        link:              `${base}/a2a/customer-token/${String(corpo["pickup_code"])}`,
        link_expires_at:   corpo["pickup_expires_at"],
        validity_days:     corpo["validity_days"],
        allowed_pools:     corpo["allowed_pools"],
        mandate:           corpo["mandate"],
        proof_mechanism:   h.proof.mechanism,
      })
    }),
  )

  server.tool(
    "customer_agent_revoke",
    "AAS-09 — revoke the customer's own A2A assistant tokens (all of them, or one by principal_id). " +
    "Requires fresh proof of possession in this session, like the grant: the holder comes from the proof.",
    {
      principal_id:  z.string().optional(),
      tenant_id:     z.string().optional(),
      session_token: z.string().optional().describe(tokenDesc),
    } as any,
    withGuard("customer_agent_revoke", async (input: Record<string, unknown>) => {
      const T = "customer_agent_revoke"
      const quem = sessionCaller(T, input)
      if ("refused" in quem) return quem.refused
      const principal_id = typeof input["principal_id"] === "string" && input["principal_id"]
        ? input["principal_id"] as string : undefined
      const { tenant_id, session_id } = quem.caller
      const h = await holderOfSession(deps.redis, tenant_id, session_id)
      if ("refused" in h) return erro(T, h.refused, { identity_required: ["otp"], session_id })
      let res: Response
      try {
        res = await fetch(`${deps.authApiUrl}/auth/v1/agent-principals/customer/revoke`, {
          method: "POST", headers: authHeaders(deps),
          body: JSON.stringify({ tenant_id, customer_id: h.proof.customer_id, ...(principal_id ? { principal_id } : {}) }),
        })
      } catch (e) {
        console.error(`[${T}] auth-api inalcançável: ${String(e)}`)
        return erro(T, "auth_api_unavailable")
      }
      const corpo = await res.json().catch(() => ({})) as Record<string, unknown>
      if (!res.ok) return erro(T, "revoke_failed", { status: res.status, detail: corpo["detail"] })
      return ok({ revoked: corpo["revoked"] ?? [] })
    }),
  )
}
