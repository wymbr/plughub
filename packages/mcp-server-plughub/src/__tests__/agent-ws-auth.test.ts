import { describe, it, expect } from "vitest"
import jwt from "jsonwebtoken"
import { authorizeAgentWs, bearerFromProtocols, poolInGrantScope, AGENT_WS_PROTOCOL } from "../lib/agent-ws-auth"

const SECRET = "segredo-de-teste-com-32-caracteres!!"
const verify = (t: string) => jwt.verify(t, SECRET, { algorithms: ["HS256"] }) as Record<string, unknown>

function tok(sub: string, grant?: { access: string; scope?: string[] }, secret = SECRET) {
  const module_config = grant ? { agent_assist: { atender: grant } } : { contacts: { visualizar: { access: "read_only" } } }
  return jwt.sign({ sub, tenant_id: "t1", module_config }, secret, { algorithm: "HS256", expiresIn: 60 })
}
const proto = (t: string) => `${AGENT_WS_PROTOCOL}, ${t}`

describe("bearerFromProtocols", () => {
  it("le o token depois do marcador, e so ele", () => {
    expect(bearerFromProtocols("plughub.bearer, abc.def.ghi")).toBe("abc.def.ghi")
    expect(bearerFromProtocols(["x", "plughub.bearer", "tok"])).toBe("tok")
    expect(bearerFromProtocols("abc.def.ghi")).toBe("")        // token sem marcador nao vale
    expect(bearerFromProtocols(undefined)).toBe("")
  })
})

describe("poolInGrantScope", () => {
  it("escopo vazio e global; senao exige o pool, com ou sem prefixo", () => {
    expect(poolInGrantScope([], "p1")).toBe(true)
    expect(poolInGrantScope(["pool:p1"], "p1")).toBe(true)
    expect(poolInGrantScope(["p1"], "p1")).toBe(true)
    expect(poolInGrantScope(["pool:p2"], "p1")).toBe(false)
  })
})

describe("authorizeAgentWs", () => {
  const base = { poolId: "p1", queryUserId: "u1", verify }

  it("controle positivo: token do Console com atender no pool abre, identidade = sub", () => {
    const d = authorizeAgentWs({ ...base, protocolHeader: proto(tok("u1", { access: "read_write", scope: ["pool:p1"] })) })
    expect(d).toEqual({ ok: true, sub: "u1", tenantId: "t1" })
  })

  it("sem credencial recusa 4401", () => {
    expect(authorizeAgentWs({ ...base, protocolHeader: undefined })).toMatchObject({ ok: false, code: 4401 })
  })

  it("assinatura errada recusa 4401", () => {
    const t = tok("u1", { access: "read_write" }, "outro-segredo-qualquer-32-chars!!")
    expect(authorizeAgentWs({ ...base, protocolHeader: proto(t) }))
      .toMatchObject({ ok: false, code: 4401, reason: "credencial invalida" })
  })

  it("sub diferente do user_id da query recusa 4403 — nunca troca a identidade calado", () => {
    const t = tok("u2", { access: "read_write" })
    expect(authorizeAgentWs({ ...base, protocolHeader: proto(t) })).toMatchObject({ ok: false, code: 4403 })
  })

  it("sem agent_assist.atender, ou so leitura, recusa 4403", () => {
    expect(authorizeAgentWs({ ...base, protocolHeader: proto(tok("u1")) })).toMatchObject({ ok: false, code: 4403 })
    expect(authorizeAgentWs({ ...base, protocolHeader: proto(tok("u1", { access: "read_only" })) }))
      .toMatchObject({ ok: false, code: 4403 })
  })

  it("atender recortado a outro pool recusa 4403, nomeando o pool", () => {
    const d = authorizeAgentWs({ ...base, protocolHeader: proto(tok("u1", { access: "read_write", scope: ["pool:p2"] })) })
    expect(d).toMatchObject({ ok: false, code: 4403 })
    expect(d.ok ? "" : d.reason).toContain("p1")
  })

  it("verificador sem segredo e 1011, nao culpa do chamador", () => {
    const d = authorizeAgentWs({
      ...base, protocolHeader: proto("x.y.z"),
      verify: () => { throw new Error("sem segredo") }, isUnavailable: () => true,
    })
    expect(d).toMatchObject({ ok: false, code: 1011 })
  })
})
