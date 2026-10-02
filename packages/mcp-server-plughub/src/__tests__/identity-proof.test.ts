/**
 * identity-proof.test.ts — AAS-19 (2026-10-02)
 *
 * `identity_proof_link` / `identity_proof_status`: a prova de posse FORA DE BANDA.
 *
 *  1. o VEREDITO (`identity_proof_status`) é a régua do `judgeResumeEvidence`: desta sessão,
 *     fresca, DESTE cliente — prova de outro cliente, velha ou de outra sessão não vale;
 *  2. a sessão do token do PRÓPRIO cliente (`customer_agent`) só prova o titular;
 *  3. já provado aqui ⇒ `already_proven`, e o gateway não é chamado;
 *  4. o link vem do gateway; recusa dele vira erro NOMEADO, nunca link vazio;
 *  5. sem o token ligado à sessão as duas recusam (estão em `SESSION_BOUND_TOOLS`).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerIdentityProofTools, sessionProof, holderMismatch } from "../tools/identity-proof"
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

describe("AAS-19 — o veredito é desta sessão, deste cliente, agora", () => {
  let redis: InstanceType<typeof RedisMock>
  beforeEach(async () => { redis = new RedisMock(); await redis.flushall() })

  it("controle: OTP fresco do cliente → verificado, com o mecanismo", async () => {
    await prova(redis, "otp", "cli-1")
    const j = await sessionProof(redis as never, T, SID, "cli-1")
    expect(j).toMatchObject({ verified: true, mechanism: "otp" })
  })

  it("a chegada pelo WhatsApp do telefone autoritativo também satisfaz", async () => {
    await prova(redis, "whatsapp", "cli-1")
    expect(await sessionProof(redis as never, T, SID, "cli-1")).toMatchObject({ verified: true, mechanism: "whatsapp" })
  })

  it.each([
    ["sem prova",            async () => {},                                                      "not_verified"],
    ["prova de OUTRO cliente", async () => prova(redis, "otp", "cli-2"),                          "other_customer"],
    ["prova velha",          async () => prova(redis, "otp", "cli-1", { agoS: 3600 }),            "stale"],
    ["prova de OUTRA sessão", async () => prova(redis, "otp", "cli-1", { session: "outra" }),     "other_session"],
  ])("%s → não verificado, com o motivo", async (_n, montar, motivo) => {
    await montar()
    const j = await sessionProof(redis as never, T, SID, "cli-1")
    expect(j.verified).toBe(false)
    expect(j.missing[0]?.reason).toBe(motivo)
  })

  it("sem Redis: não verificado (falha fechada)", async () => {
    expect((await sessionProof(undefined, T, SID, "cli-1")).verified).toBe(false)
  })
})

describe("AAS-19 — na sessão do token do PRÓPRIO cliente, só o titular", () => {
  let redis: InstanceType<typeof RedisMock>
  beforeEach(async () => { redis = new RedisMock(); await redis.flushall() })

  it("customer_agent: outro cliente é recusado, o titular passa", async () => {
    await redis.set(`session:${SID}:meta`, JSON.stringify({ a2a_principal_kind: "customer_agent", customer_id: "cli-1" }))
    expect(await holderMismatch(redis as never, SID, "cli-2")).toBe("customer_not_holder")
    expect(await holderMismatch(redis as never, SID, "cli-1")).toBeNull()
  })

  it("partner (titular só declarado) e sessão comum: qualquer cliente pode ser provado", async () => {
    await redis.set(`session:${SID}:meta`, JSON.stringify({ a2a_principal_kind: "partner", customer_id: "sys:a2a:x" }))
    expect(await holderMismatch(redis as never, SID, "cli-2")).toBeNull()
    await redis.set(`session:${SID}:meta`, JSON.stringify({ channel: "webchat", customer_id: "cli-9" }))
    expect(await holderMismatch(redis as never, SID, "cli-2")).toBeNull()
  })
})

describe("AAS-19 — as tools", () => {
  let redis: InstanceType<typeof RedisMock>
  let server: McpServer
  let fetchMock: ReturnType<typeof vi.fn>
  const GW = "http://gw:8010"

  beforeEach(async () => {
    redis = new RedisMock(); await redis.flushall()
    server = new McpServer({ name: "t", version: "0" })
    registerIdentityProofTools(server, { redis: redis as never, channelGatewayUrl: GW, channelGatewayServiceToken: "svc" })
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  const ENTRADA = { customer_id: "cli-1", kind: "phone", value: "+5511999990000" }

  it("as duas estão na lista das ligadas à sessão, e sem token recusam", async () => {
    expect(SESSION_BOUND_TOOLS).toContain("identity_proof_link")
    expect(SESSION_BOUND_TOOLS).toContain("identity_proof_status")
    for (const nome of ["identity_proof_link", "identity_proof_status"]) {
      const r = await tool(server, nome)({ ...ENTRADA })
      expect(r.isError).toBe(true)
      expect(corpo(r).error).toBe("missing_session_token")
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("controle: pede o link ao gateway com a sessão DO TOKEN e devolve url e prazo", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({
      created: true, url: "https://pub/a2a/proof/prf_x", expires_at: "2026-10-02T12:10:00Z", expires_in_s: 600, anchor_hint: "•••• 00",
    }), { status: 200 }))
    const r = await tool(server, "identity_proof_link")({ ...ENTRADA, session_token: SESSAO })
    expect(r.isError).toBeUndefined()
    expect(corpo(r)).toMatchObject({ already_proven: false, url: "https://pub/a2a/proof/prf_x", expires_in_s: 600 })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe(`${GW}/v1/channels/webhook/identity/proof-link`)
    expect(JSON.parse(init.body)).toEqual({ tenant_id: T, session_id: SID, ...ENTRADA })
    expect(init.headers["X-Service-Token"]).toBe("svc")
  })

  it("já provado nesta sessão → already_proven, sem link e sem chamar o gateway", async () => {
    await prova(redis, "otp", "cli-1")
    const r = await tool(server, "identity_proof_link")({ ...ENTRADA, session_token: SESSAO })
    expect(corpo(r)).toMatchObject({ already_proven: true, mechanism: "otp" })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("recusa do gateway (âncora não autoritativa) vira erro nomeado, nunca link", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ created: false, reason: "anchor_not_authoritative" }), { status: 200 }))
    const r = await tool(server, "identity_proof_link")({ ...ENTRADA, session_token: SESSAO })
    expect(r.isError).toBe(true)
    expect(corpo(r)).toEqual({ error: "proof_link_refused", reason: "anchor_not_authoritative" })
  })

  it("credencial de serviço recusada pelo gateway é dita como tal", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }))
    const r = await tool(server, "identity_proof_link")({ ...ENTRADA, session_token: SESSAO })
    expect(corpo(r).error).toBe("identity_credential_refused")
  })

  it("customer_agent pedindo prova de OUTRO cliente: recusa antes do gateway", async () => {
    await redis.set(`session:${SID}:meta`, JSON.stringify({ a2a_principal_kind: "customer_agent", customer_id: "cli-9" }))
    const r = await tool(server, "identity_proof_link")({ ...ENTRADA, session_token: SESSAO })
    expect(corpo(r)).toEqual({ error: "proof_link_refused", reason: "customer_not_holder" })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("status: lê a evidência desta sessão — não verificado, depois verificado", async () => {
    let r = await tool(server, "identity_proof_status")({ customer_id: "cli-1", session_token: SESSAO })
    expect(corpo(r)).toEqual({ verified: false, missing: ["not_verified"] })
    await prova(redis, "otp", "cli-1")
    r = await tool(server, "identity_proof_status")({ customer_id: "cli-1", session_token: SESSAO })
    expect(corpo(r)).toMatchObject({ verified: true, mechanism: "otp" })
  })
})
