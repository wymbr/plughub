/**
 * identity-evidence.test.ts — PID-02 (2026-09-13)
 *
 * "Quem verifica grava" (ADR D6). Três proposições:
 *
 *  1. o funil genérico (`writeContextTag`, que serve `context_set` e `/api/inject-context`)
 *     RECUSA `core.identity.*` e `core.journey.identity.*` sem escrever — e o `core.*` que
 *     fluxos já usam segue livre;
 *  2. `writeIdentityEvidence` grava o formato do D4 na JOURNEY: `status` sempre, a prova só
 *     quando `verified`, e a prova REMOVIDA nos outros status;
 *  3. `otp_challenge`/`otp_verify` gravam a evidência na mesma chamada, na sessão do token.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { writeContextTag, writeIdentityEvidence, ReservedContextTagError } from "../tools/journey"
import { registerWorkflowTools, otpEvidenceStatus } from "../tools/workflow"
import { signSessionBoundToken } from "../infra/jwt"
import type { RedisClient } from "../infra/redis"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }
const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, unknown>

function fakeRedis() {
  const hashes = new Map<string, Map<string, string>>()
  const redis = {
    hset: async (k: string, f: string, v: string) => { if (!hashes.has(k)) hashes.set(k, new Map()); hashes.get(k)!.set(f, v); return 1 },
    hget: async (k: string, f: string) => hashes.get(k)?.get(f) ?? null,
    hdel: async (k: string, ...fs: string[]) => { for (const f of fs) hashes.get(k)?.delete(f); return fs.length },
    expire: async () => 1,
    pipeline: () => ({ hset: () => {}, exec: async () => [] }),
  } as unknown as RedisClient
  const valor = (k: string, f: string) => {
    const raw = hashes.get(k)?.get(f)
    return raw === undefined ? undefined : (JSON.parse(raw) as { value: unknown }).value
  }
  return { redis, hashes, valor }
}

let n = 0
const tenant = () => `t_evid_${++n}`
const TAG = (f: string) => `core.journey.identity.otp.${f}`

function tool(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools?.[name]
  if (!reg) throw new Error(`Tool '${name}' not registered`)
  return reg.handler
}

let gateway: Record<string, unknown> = {}
let chamadasGateway = 0
beforeEach(() => {
  chamadasGateway = 0
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).includes("/config/")) {
      return { ok: true, status: 200, json: async () => ({ entries: {} }) } as unknown as Response
    }
    chamadasGateway++
    return new Response(JSON.stringify(gateway), { status: 200 })
  }))
  vi.spyOn(console, "warn").mockImplementation(() => {})
  vi.spyOn(console, "error").mockImplementation(() => {})
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe("PID-02 — o funil genérico não grava evidência", () => {
  it.each(["core.journey.identity.otp.status", "core.identity.qualquer"])("%s → recusa, sem hset", async (tag) => {
    const { redis, hashes } = fakeRedis()
    await expect(writeContextTag(redis, tenant(), "s1", tag, { value: "verified" })).rejects.toBeInstanceOf(ReservedContextTagError)
    expect(hashes.size).toBe(0)
  })

  it("controle: core.workflow.* e core.journey.* (não-identidade) seguem gravando", async () => {
    const { redis, hashes } = fakeRedis()
    const t = tenant()
    await writeContextTag(redis, t, "s1", "core.workflow.dialog_form_id", { value: "f" })
    await writeContextTag(redis, t, "s1", "core.journey.pedido", { value: "p" })
    expect(hashes.get(`${t}:ctx:s1`)?.has("core.workflow.dialog_form_id")).toBe(true)
    expect(hashes.get(`${t}:ctx:journey:s1`)?.has("core.journey.pedido")).toBe(true)
  })
})

describe("PID-02 — writeIdentityEvidence (formato do D4)", () => {
  it("verified grava os cinco campos na JOURNEY", async () => {
    const { redis, valor } = fakeRedis()
    const t = tenant()
    await writeIdentityEvidence(redis, t, "s1", "otp", {
      status: "verified", anchor_kind: "phone", verified_at: "2026-09-13T00:00:00Z", source: "authoritative", proven_in_session: "s1",
    })
    const k = `${t}:ctx:journey:s1`
    expect([valor(k, TAG("status")), valor(k, TAG("anchor_kind")), valor(k, TAG("source")), valor(k, TAG("proven_in_session"))])
      .toEqual(["verified", "phone", "authoritative", "s1"])
    expect(valor(k, TAG("verified_at"))).toBe("2026-09-13T00:00:00Z")
    expect(valor(`${t}:ctx:s1`, TAG("status"))).toBeUndefined()
  })

  it("failed depois de verified REMOVE a prova antiga — um failed de hoje não convive com o verified_at de ontem", async () => {
    const { redis, valor } = fakeRedis()
    const t = tenant()
    await writeIdentityEvidence(redis, t, "s1", "otp", { status: "verified", anchor_kind: "phone", verified_at: "x", source: "authoritative", proven_in_session: "s1" })
    await writeIdentityEvidence(redis, t, "s1", "otp", { status: "failed", anchor_kind: "phone", verified_at: "IGNORADO", proven_in_session: "IGNORADO" })
    const k = `${t}:ctx:journey:s1`
    expect(valor(k, TAG("status"))).toBe("failed")
    expect([valor(k, TAG("verified_at")), valor(k, TAG("source")), valor(k, TAG("proven_in_session"))]).toEqual([undefined, undefined, undefined])
  })

  it("o status do gateway vira o da evidência", () => {
    expect(otpEvidenceStatus({ verified: true })).toBe("verified")
    expect(otpEvidenceStatus({ verified: false, reason: "no_challenge" })).toBe("expired")
    expect(otpEvidenceStatus({ verified: false, reason: "wrong_code" })).toBe("failed")
    expect(otpEvidenceStatus({ verified: false, reason: "too_many_attempts" })).toBe("failed")
  })
})

describe("PID-02 — otp_* gravam a evidência na mesma chamada", () => {
  const ENTRADA = { tenant_id: "", customer_id: "cus_a", kind: "phone", value: "+5511999990001" }

  function montar(t: string, comRedis = true) {
    const fr = fakeRedis()
    const server = new McpServer({ name: "t", version: "0" })
    registerWorkflowTools(server, { channelGatewayUrl: "http://gw", tenantId: t, ...(comRedis ? { redis: fr.redis } : {}) })
    const token = signSessionBoundToken({ tenant_id: t, session_id: "sess_prova", instance_id: "i", skill_id: "k" })
    return { ...fr, server, token }
  }

  it("verify OK → verified, com a sessão do TOKEN e a procedência do gateway", async () => {
    const t = tenant()
    const { server, token, valor } = montar(t)
    gateway = { verified: true, verification_class: "possessed", provenance: "authoritative" }
    const r = await tool(server, "otp_verify")({ ...ENTRADA, tenant_id: t, code: "1", session_token: token })
    expect(r.isError).toBeFalsy()
    const k = `${t}:ctx:journey:sess_prova`
    expect(valor(k, TAG("status"))).toBe("verified")
    expect(valor(k, TAG("proven_in_session"))).toBe("sess_prova")
    expect(valor(k, TAG("source"))).toBe("authoritative")
    expect(typeof valor(k, TAG("verified_at"))).toBe("string")
  })

  it("verify com código errado → failed, sem prova", async () => {
    const t = tenant()
    const { server, token, valor } = montar(t)
    gateway = { verified: false, reason: "wrong_code" }
    await tool(server, "otp_verify")({ ...ENTRADA, tenant_id: t, code: "9", session_token: token })
    const k = `${t}:ctx:journey:sess_prova`
    expect(valor(k, TAG("status"))).toBe("failed")
    expect(valor(k, TAG("proven_in_session"))).toBeUndefined()
  })

  it("challenge enviado → pending; recusado → not_run", async () => {
    const t = tenant()
    const { server, token, valor } = montar(t)
    gateway = { sent: true, delivery: "dev_log" }
    await tool(server, "otp_challenge")({ ...ENTRADA, tenant_id: t, session_token: token })
    expect(valor(`${t}:ctx:journey:sess_prova`, TAG("status"))).toBe("pending")
    gateway = { sent: false, reason: "anchor_not_authoritative" }
    const r = await tool(server, "otp_challenge")({ ...ENTRADA, tenant_id: t, session_token: token })
    expect(r.isError).toBe(true)
    expect(valor(`${t}:ctx:journey:sess_prova`, TAG("status"))).toBe("not_run")
  })

  it("sem token de sessão recusa ANTES do gateway — a prova não saberia onde gravar", async () => {
    const t = tenant()
    const { server } = montar(t)
    const r = await tool(server, "otp_verify")({ ...ENTRADA, tenant_id: t, code: "1" })
    expect(corpo(r)).toMatchObject({ error: "missing_session_token" })
    expect(chamadasGateway).toBe(0)
  })

  it("posse provada sem onde registrar é ERRO nomeado, não sucesso", async () => {
    const t = tenant()
    const { server, token } = montar(t, false)
    gateway = { verified: true, verification_class: "possessed", provenance: "authoritative" }
    const r = await tool(server, "otp_verify")({ ...ENTRADA, tenant_id: t, code: "1", session_token: token })
    expect(r.isError).toBe(true)
    expect(corpo(r)).toMatchObject({ error: "evidence_write_failed", status: "verified" })
  })
})
