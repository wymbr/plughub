// _identity_evidence_exercise.cjs — PID-02 (2026-09-13). Roda DENTRO do container do mcp-server:
//   docker exec -i <mcp> sh -c 'cd /app/packages/mcp-server-plughub && node - <tenant>' < este arquivo
//
// Pelo TRANSPORTE MCP e pela ponte REST de verdade, sobre uma sessão SINTÉTICA (meta + nada
// mais), limpa no fim:
//   context_set_recusa        context_set de core.journey.identity.otp.status -> reserved_tag, nada gravado
//   context_set_core_identity idem para core.identity.*
//   context_set_controle      core.workflow.* segue gravando (controle POSITIVO)
//   inject_context_recusa     /api/inject-context com supervisionar -> 403 reserved_tag, nada gravado
//   inject_context_controle   a mesma rota grava session.* (controle)
//   otp_sem_token_recusa      otp_verify sem token de sessão -> missing_session_token
const { Client } = require("@modelcontextprotocol/sdk/client/index.js")
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js")
const jwt = require("jsonwebtoken")
const Redis = require("ioredis")

const TENANT = process.argv[2] || "tenant_demo"
const BASE = "http://localhost:3100"
const SID = "probe-pid02-" + Math.random().toString(36).slice(2, 10)
const TAG = "core.journey.identity.otp.status"

async function main() {
  const redis = new Redis(process.env.REDIS_URL || "redis://redis:6379")
  const out = { casos: {}, detalhe: {}, sessao: SID }
  const c = out.casos
  const hashes = [`${TENANT}:ctx:${SID}`, `${TENANT}:ctx:journey:${SID}`]
  const gravado = async (tag) => {
    for (const h of hashes) if (await redis.hexists(h, tag)) return true
    return false
  }
  await redis.set(`session:${SID}:meta`, JSON.stringify({ tenant_id: TENANT, pool_id: "probe_pid02" }), "EX", 300)
  const client = new Client({ name: "probe-pid02", version: "0" })
  try {
    await client.connect(new SSEClientTransport(new URL(BASE + "/sse")))
    const chama = async (name, args) => {
      const r = await client.callTool({ name, arguments: args })
      let body = {}
      try { body = JSON.parse(r.content?.[0]?.text ?? "{}") } catch { body = { raw: r.content?.[0]?.text } }
      return { isError: r.isError === true, body }
    }

    let r = await chama("context_set", { session_id: SID, tenant_id: TENANT, tag: TAG, value: "verified" })
    out.detalhe.context_set = r
    c.context_set_recusa = r.isError && r.body.error === "reserved_tag" && !(await gravado(TAG))
    r = await chama("context_set", { session_id: SID, tenant_id: TENANT, tag: "core.identity.probe", value: "verified" })
    c.context_set_core_identity = r.isError && r.body.error === "reserved_tag" && !(await gravado("core.identity.probe"))
    r = await chama("context_set", { session_id: SID, tenant_id: TENANT, tag: "core.workflow.probe_pid02", value: "x" })
    c.context_set_controle = !r.isError && (await gravado("core.workflow.probe_pid02"))

    const sup = jwt.sign({ sub: "probe-pid02", tenant_id: TENANT, roles: ["supervisor"],
      module_config: { agent_assist: { atender: { access: "read_write" }, supervisionar: { access: "read_write" } } } },
      process.env.PLUGHUB_JWT_SECRET, { expiresIn: 600 })
    const inject = (key, value) => fetch(`${BASE}/api/inject-context/${SID}`, {
      method: "POST", headers: { "content-type": "application/json", authorization: "Bearer " + sup },
      body: JSON.stringify({ key, value, source: "probe" }) })
    let resp = await inject(TAG, "verified")
    out.detalhe.inject = { status: resp.status, body: await resp.text() }
    c.inject_context_recusa = resp.status === 403 && out.detalhe.inject.body.includes("reserved_tag") && !(await gravado(TAG))
    resp = await inject("session.probe_pid02", "x")
    out.detalhe.inject_controle = resp.status
    c.inject_context_controle = resp.status === 200 && (await gravado("session.probe_pid02"))

    r = await chama("otp_verify", { tenant_id: TENANT, customer_id: "cus_x", kind: "phone", value: "+5511900000000", code: "0" })
    out.detalhe.otp = r
    c.otp_sem_token_recusa = r.isError && r.body.error === "missing_session_token"
  } finally {
    await client.close().catch(() => {})
    await redis.del(`session:${SID}:meta`, ...hashes)
    await redis.quit()
  }
  return out
}

main().then(o => console.log(JSON.stringify(o))).catch(e => { console.log(JSON.stringify({ erro: String(e) })); process.exit(0) })
