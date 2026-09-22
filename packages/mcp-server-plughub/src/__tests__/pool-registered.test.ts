/**
 * AGH-04 — o login humano PERGUNTA se o pool existe; nunca o cria.
 *
 * Proposições: 200 → registered · 404 → absent (o chamador RECUSA) · outro status ou rede fora →
 * unverified com o motivo (o chamador SEGUE) · e em nenhum desfecho sai um POST.
 */
import { describe, it, expect } from "vitest"
import { checkPoolRegistered } from "../lib/pool-registered"

function fake(status: number | Error, body = "") {
  const calls: Array<{ url: string; method: string; headers: Record<string, string> }> = []
  const f = (async (url: string, init?: RequestInit) => {
    calls.push({ url, method: String(init?.method), headers: (init?.headers ?? {}) as Record<string, string> })
    if (status instanceof Error) throw status
    return new Response(body, { status })
  }) as unknown as typeof fetch
  return { f, calls }
}

describe("AGH-04 — checkPoolRegistered", () => {
  it("200 → registered, por GET, com tenant e credencial de serviço", async () => {
    const { f, calls } = fake(200, "{}")
    expect(await checkPoolRegistered("http://reg", "t1", "retencao_humano-int", "svc", f))
      .toEqual({ kind: "registered" })
    expect(calls).toEqual([{
      url: "http://reg/v1/pools/retencao_humano-int", method: "GET",
      headers: { "x-tenant-id": "t1", "x-service-token": "svc" },
    }])
  })

  it("404 → absent", async () => {
    const { f } = fake(404)
    expect(await checkPoolRegistered("http://reg", "t1", "inventado", "svc", f)).toEqual({ kind: "absent" })
  })

  it("outro status → unverified, com o status e o corpo no motivo", async () => {
    const { f } = fake(503, "down")
    const r = await checkPoolRegistered("http://reg", "t1", "p", "", f)
    expect(r.kind).toBe("unverified")
    expect(r.kind === "unverified" && r.why).toContain("HTTP 503: down")
  })

  it("rede fora → unverified (não recusa login por falha de infra)", async () => {
    const { f } = fake(new Error("ECONNREFUSED"))
    const r = await checkPoolRegistered("http://reg", "t1", "p", "", f)
    expect(r.kind === "unverified" && r.why).toContain("inalcançável")
  })

  it("sem credencial não manda header vazio, e nunca faz POST", async () => {
    const { f, calls } = fake(200)
    await checkPoolRegistered("http://reg", "t1", "a b", "", f)
    expect(calls[0]!.headers).toEqual({ "x-tenant-id": "t1" })
    expect(calls[0]!.url).toBe("http://reg/v1/pools/a%20b")
    expect(calls.every(c => c.method === "GET")).toBe(true)
  })
})
