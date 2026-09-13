// _resume_requirement_exercise.cjs — PID-06 (2026-09-13). Roda DENTRO do container do mcp-server:
//   docker exec -i <mcp> sh -c 'cd /app/packages/mcp-server-plughub && node - <fase> <tenant> <customer> <fone> [code]' < este arquivo
//
// Duas fases, porque o código do OTP só aparece no log do gateway e quem o lê é o probe:
//   desafia  cria duas sessões SINTÉTICAS (S1 prova, S2 não) e dispara o OTP em S1.
//            Imprime {s1, s2, desafio}.
//   mede     verifica o código em S1 e pede a pendência do cliente pelas DUAS sessões,
//            com a mesma âncora (o celular, agora `possessed` no cadastro).
//            Imprime {casos, detalhe}.
//
// A pergunta: o `resume_token` sai para quem NÃO provou nesta sessão? Antes da PID-06 saía
// para qualquer sessão, bastando a posse durável da âncora (vetor (4) do ADR).
const { Client } = require("@modelcontextprotocol/sdk/client/index.js")
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js")
const Redis = require("ioredis")

const [FASE, TENANT, CUST, FONE, CODE] = process.argv.slice(2)
const BASE = "http://localhost:3100"
const SIDS = { s1: "probe-pid06-prova", s2: "probe-pid06-sem-prova" }

async function token(redis, sid) {
  await redis.set(`session:${sid}:meta`, JSON.stringify({ tenant_id: TENANT, pool_id: "probe_pid06" }), "EX", 900)
  const r = await fetch(BASE + "/internal/session-token", { method: "POST",
    headers: { "content-type": "application/json", "x-service-token": process.env.MCP_INTERNAL_SERVICE_TOKEN || "" },
    body: JSON.stringify({ tenant_id: TENANT, session_id: sid, instance_id: "probe-pid06", skill_id: "probe" }) })
  return r.status === 200 ? (await r.json()).session_token : ""
}

async function main() {
  const redis = new Redis(process.env.REDIS_URL || "redis://redis:6379")
  const client = new Client({ name: "probe-pid06", version: "0" })
  await client.connect(new SSEClientTransport(new URL(BASE + "/sse")))
  const chama = async (name, args) => {
    const r = await client.callTool({ name, arguments: args })
    let body = {}
    try { body = JSON.parse(r.content?.[0]?.text ?? "{}") } catch { body = { raw: r.content?.[0]?.text } }
    return { isError: r.isError === true, body }
  }
  try {
    if (FASE === "desafia") {
      // sessões novas a cada rodada: evidência velha de outra execução não pode ajudar
      for (const sid of Object.values(SIDS)) {
        await redis.del(`${TENANT}:ctx:${sid}`, `${TENANT}:ctx:journey:${sid}`)
      }
      const t1 = await token(redis, SIDS.s1)
      const r = await chama("otp_challenge", { tenant_id: TENANT, customer_id: CUST, kind: "phone", value: FONE, session_token: t1 })
      console.log(JSON.stringify({ ...SIDS, desafio: r }))
      return
    }
    const out = { casos: {}, detalhe: {} }
    const c = out.casos
    const t1 = await token(redis, SIDS.s1)
    const t2 = await token(redis, SIDS.s2)
    const v = await chama("otp_verify", { tenant_id: TENANT, customer_id: CUST, kind: "phone", value: FONE, code: CODE, session_token: t1 })
    out.detalhe.verify = v
    c.s1_provou = !v.isError && v.body.verified === true
    const anchors = [{ kind: "phone", value: FONE }]
    const p1 = await chama("pending_workflow_get", { tenant_id: TENANT, anchors, session_token: t1 })
    const p2 = await chama("pending_workflow_get", { tenant_id: TENANT, anchors, session_token: t2 })
    const tokensDe = (b) => [b.resume_token, ...((b.pendings || []).map(p => p.resume_token))].filter(Boolean)
    out.detalhe.p1 = { found: p1.body.found, count: p1.body.count, tokens: tokensDe(p1.body).length,
                       required: p1.body.pendings?.map(p => p.resume_requires) }
    out.detalhe.p2 = { found: p2.body.found, count: p2.body.count, tokens: tokensDe(p2.body).length,
                       verification_required: p2.body.verification_required, identity_required: p2.body.identity_required }
    c.s1_recebe_token = !p1.isError && p1.body.found === true && tokensDe(p1.body).length > 0
    c.s2_nao_recebe_token = !p2.isError && tokensDe(p2.body).length === 0
    c.s2_pede_verificacao = p2.body.verification_required === true
    console.log(JSON.stringify(out))
  } finally {
    await client.close().catch(() => {})
    redis.disconnect()
  }
}
main().catch((e) => { console.log(JSON.stringify({ erro: String(e && e.stack || e) })); process.exit(0) })
