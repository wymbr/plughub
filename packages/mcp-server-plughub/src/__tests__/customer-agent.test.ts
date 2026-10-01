/**
 * customer-agent.test.ts — AAS-09 (2026-10-01)
 *
 * `customer_agent_grant` / `customer_agent_revoke`: o token que o PRÓPRIO cliente gera.
 *
 *  1. o TITULAR sai da prova fresca DESTA sessão — nunca do input. Sem prova, prova velha, prova
 *     de outra sessão ou prova de DOIS clientes: recusa, e o auth-api não é chamado;
 *  2. sem o token ligado à sessão a tool recusa (está em `SESSION_BOUND_TOOLS`);
 *  3. o que volta ao fluxo é o LINK de retirada, nunca uma credencial;
 *  4. sem `A2A_PUBLIC_BASE_URL` a tool recusa nomeando a env.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerCustomerAgentTools, holderOfSession } from "../tools/customer-agent"
import { signSessionBoundToken } from "../infra/jwt"
import { journeyCtxKey } from "../tools/journey"
import { SESSION_BOUND_TOOLS, identityEvidenceTag } from "@plughub/schemas"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }
const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, unknown>

function tool(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools?.[name]
  if (!reg) throw new Error(`Tool '${name}' not registered`)
  return reg.handler
}

const T = "tenant_s", SID = "sid-1"
const SESSAO = signSessionBoundToken({ tenant_id: T, session_id: SID, instance_id: "i", skill_id: "k" })
const ent = (v: unknown) => JSON.stringify({ value: v })

async function prova(redis: InstanceType<typeof RedisMock>, mech: "otp" | "whatsapp", cliente: string,
                     opts: { session?: string; agoS?: number } = {}) {
  const quando = new Date(Date.now() - (opts.agoS ?? 10) * 1000).toISOString()
  await redis.hset(journeyCtxKey(T, SID), {
    [identityEvidenceTag(mech, "status")]:            ent("verified"),
    [identityEvidenceTag(mech, "proven_in_session")]: ent(opts.session ?? SID),
    [identityEvidenceTag(mech, "verified_at")]:       ent(quando),
    [identityEvidenceTag(mech, "customer_id")]:       ent(cliente),
  })
}

describe("AAS-09 — o titular sai da prova desta sessão", () => {
  let redis: InstanceType<typeof RedisMock>
  beforeEach(async () => { redis = new RedisMock(); await redis.flushall() })

  it("prova fresca de um cliente → é ele", async () => {
    await prova(redis, "otp", "cli-1")
    const h = await holderOfSession(redis as never, T, SID)
    expect("proof" in h && h.proof.customer_id).toBe("cli-1")
  })

  it.each([
    ["sem prova", async () => {}, "no_fresh_proof"],
    ["prova velha", async () => prova(redis, "otp", "cli-1", { agoS: 3600 }), "no_fresh_proof"],
    ["prova de OUTRA sessão", async () => prova(redis, "otp", "cli-1", { session: "outra" }), "no_fresh_proof"],
    ["dois clientes", async () => { await prova(redis, "otp", "cli-1"); await prova(redis, "whatsapp", "cli-2") }, "ambiguous_holder"],
  ])("%s → recusa", async (_n, arma, motivo) => {
    await arma()
    const h = await holderOfSession(redis as never, T, SID)
    expect("refused" in h && h.refused).toBe(motivo)
  })

  it("dois mecanismos do MESMO cliente não são ambíguos", async () => {
    await prova(redis, "otp", "cli-1", { agoS: 100 })
    await prova(redis, "whatsapp", "cli-1", { agoS: 5 })
    const h = await holderOfSession(redis as never, T, SID)
    expect("proof" in h && h.proof.mechanism).toBe("whatsapp")
  })
})

describe("AAS-09 — as tools", () => {
  let redis: InstanceType<typeof RedisMock>
  let server: McpServer
  let chamadas: Array<{ url: string; body: Record<string, unknown> }>

  function monta(base = "https://pub.example") {
    server = new McpServer({ name: "t", version: "0" })
    registerCustomerAgentTools(server, { redis: redis as never, authApiUrl: "http://auth", authServiceToken: "svc",
                                         a2aPublicBaseUrl: base })
  }

  beforeEach(async () => {
    redis = new RedisMock(); await redis.flushall()
    chamadas = []
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      chamadas.push({ url, body })
      if (url.endsWith("/customer-grants")) {
        return new Response(JSON.stringify({ pickup_code: "pkc_abc", pickup_expires_at: "2026-10-01T12:10:00Z",
          validity_days: 7, allowed_pools: body["allowed_pools"], mandate: body["mandate"] }), { status: 201 })
      }
      return new Response(JSON.stringify({ revoked: ["p1"] }), { status: 200 })
    }))
    monta()
  })
  afterEach(() => vi.unstubAllGlobals())

  it("as duas estão em SESSION_BOUND_TOOLS", () => {
    expect(SESSION_BOUND_TOOLS).toContain("customer_agent_grant")
    expect(SESSION_BOUND_TOOLS).toContain("customer_agent_revoke")
  })

  it("sem token de sessão recusa e não chama o auth-api", async () => {
    const r = await tool(server, "customer_agent_grant")({ pools: ["p"] })
    expect(r.isError).toBe(true)
    expect(corpo(r)["error"]).toBe("missing_session_token")
    expect(chamadas).toHaveLength(0)
  })

  it("sem prova recusa pedindo OTP, e não chama o auth-api", async () => {
    const r = await tool(server, "customer_agent_grant")({ pools: ["p"], session_token: SESSAO })
    expect(corpo(r)).toMatchObject({ error: "no_fresh_proof", identity_required: ["otp"] })
    expect(chamadas).toHaveLength(0)
  })

  it("controle: com prova, emite pelo titular da PROVA — o customer_id do input é ignorado", async () => {
    await prova(redis, "otp", "cli-1")
    const r = await tool(server, "customer_agent_grant")({
      pools: ["p"], mandate: ["consultar"], session_token: SESSAO, customer_id: "OUTRA-PESSOA" })
    expect(r.isError).toBeUndefined()
    expect(chamadas).toHaveLength(1)
    expect(chamadas[0]!.body).toMatchObject({ tenant_id: T, customer_id: "cli-1",
      proof: { mechanism: "otp", session_id: SID }, allowed_pools: ["p"], mandate: ["consultar"] })
    const out = corpo(r)
    expect(out["link"]).toBe("https://pub.example/a2a/customer-token/pkc_abc")
    expect(JSON.stringify(out)).not.toMatch(/pha_/)   // credencial nenhuma volta ao fluxo
  })

  it("sem A2A_PUBLIC_BASE_URL recusa nomeando a env", async () => {
    monta("")
    await prova(redis, "otp", "cli-1")
    const r = await tool(server, "customer_agent_grant")({ pools: ["p"], session_token: SESSAO })
    expect(corpo(r)).toMatchObject({ error: "a2a_not_configured", env: "A2A_PUBLIC_BASE_URL" })
    expect(chamadas).toHaveLength(0)
  })

  it("revogar também exige a prova, e revoga pelo titular dela", async () => {
    const sem = await tool(server, "customer_agent_revoke")({ session_token: SESSAO })
    expect(corpo(sem)["error"]).toBe("no_fresh_proof")
    await prova(redis, "otp", "cli-1")
    const r = await tool(server, "customer_agent_revoke")({ session_token: SESSAO })
    expect(corpo(r)).toEqual({ revoked: ["p1"] })
    expect(chamadas[0]!.body).toEqual({ tenant_id: T, customer_id: "cli-1" })
  })
})
