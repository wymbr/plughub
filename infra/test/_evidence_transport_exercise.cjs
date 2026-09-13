// _evidence_transport_exercise.cjs — PID-03 (2026-09-13). Roda DENTRO do container do mcp-server:
//   docker exec -i <mcp> sh -c 'cd /app/packages/mcp-server-plughub && node - <tenant>' < este arquivo
//
// Um `workflow_resume` REAL pelo /sse, sobre sessões sintéticas, sem acordar processo nenhum:
// o token existe só como registro por token VENCIDO, então o gateway responde 404 — e a
// evidência já tem de ter viajado ANTES dessa chamada.
//   leva_mais_nova     A (quem retoma) tem `verified` recente; B (processo) tem `failed` antigo
//                      → B passa a ter o registro de A, inteiro
//   controle_mais_velha  B tem registro MAIS NOVO que A → B fica como estava
//   gateway_404        a retomada em si foi tentada e recusada (token vencido) — sem efeito colateral
const { Client } = require("@modelcontextprotocol/sdk/client/index.js")
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js")
const Redis = require("ioredis")

const TENANT = process.argv[2] || "tenant_demo"
const BASE = "http://localhost:3100"
const E = f => `core.journey.identity.otp.${f}`
const rnd = () => Math.random().toString(36).slice(2, 10)

async function main() {
  const redis = new Redis(process.env.REDIS_URL || "redis://redis:6379")
  const out = { casos: {}, detalhe: {} }
  const limpar = []
  const client = new Client({ name: "probe-pid03", version: "0" })
  const put = (k, tag, value, at) => redis.hset(k, tag, JSON.stringify({ value, updated_at: at, source: "probe" }))
  try {
    await client.connect(new SSEClientTransport(new URL(BASE + "/sse")))
    const tokenDe = async sid => {
      const r = await fetch(BASE + "/internal/session-token", { method: "POST",
        headers: { "content-type": "application/json", "x-service-token": process.env.MCP_INTERNAL_SERVICE_TOKEN || "" },
        body: JSON.stringify({ tenant_id: TENANT, session_id: sid, instance_id: "probe", skill_id: "probe" }) })
      return r.status === 200 ? (await r.json()).session_token : ""
    }
    const cenario = async (nome, aQuando, aStatus, bQuando, bStatus) => {
      const A = "probe-pid03-a-" + rnd(), B = "probe-pid03-b-" + rnd(), tok = "probe-pid03-tok-" + rnd()
      const kA = `${TENANT}:ctx:journey:${A}`, kB = `${TENANT}:ctx:journey:${B}`
      limpar.push(kA, kB, `${TENANT}:ctx:${A}`, `${TENANT}:ctx:${B}`, `${TENANT}:resume_meta:${tok}`)
      await put(kA, E("status"), aStatus, aQuando); await put(kA, E("proven_in_session"), A, aQuando)
      await put(kB, E("status"), bStatus, bQuando); await put(kB, E("proven_in_session"), B, bQuando)
      await redis.set(`${TENANT}:resume_meta:${tok}`, JSON.stringify({ session_id: B, step_id: "s", expires_at: "2020-01-01T00:00:00+00:00" }), "EX", 300)
      const st = await tokenDe(A)
      const r = await client.callTool({ name: "workflow_resume", arguments: { resume_token: tok, decision: "input", session_token: st } })
      let body = {}
      try { body = JSON.parse(r.content?.[0]?.text ?? "{}") } catch {}
      const lido = async f => { const v = await redis.hget(kB, E(f)); return v ? JSON.parse(v).value : null }
      out.detalhe[nome] = { resp: body, b_status: await lido("status"), b_proven: await lido("proven_in_session"), A, B }
      return { A, B, body, b_status: await lido("status"), b_proven: await lido("proven_in_session") }
    }
    const novo = new Date().toISOString(), velho = "2026-01-01T00:00:00.000Z"
    let x = await cenario("leva", novo, "verified", velho, "failed")
    out.casos.leva_mais_nova = x.b_status === "verified" && x.b_proven === x.A
    out.casos.gateway_404 = String(x.body.error || "").startsWith("resume_failed_http_404")
    x = await cenario("controle", velho, "failed", novo, "verified")
    out.casos.controle_mais_velha = x.b_status === "verified" && x.b_proven === x.B
  } finally {
    await client.close().catch(() => {})
    if (limpar.length) await redis.del(...limpar)
    await redis.quit()
  }
  return out
}

main().then(o => console.log(JSON.stringify(o))).catch(e => { console.log(JSON.stringify({ erro: String(e) })); process.exit(0) })
