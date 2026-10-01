// _customer_agent_exercise.cjs — AAS-09 (2026-10-01). Roda DENTRO do container do mcp-server:
//   docker exec -i <mcp> sh -c 'cd /app/packages/mcp-server-plughub && node - <tenant> <modo> <pool> <cliA> <cliB>' < este arquivo
//
// `customer_agent_grant` / `_revoke` REAIS pelo /sse, sobre sessões sintéticas. A prova de posse é
// FIXTURE: o probe a grava como o `otp_verify` gravaria (registro inteiro, sessão e cliente) — o
// que se mede aqui é o que a tool FAZ com a prova, não o OTP (esse tem probe próprio). Modos:
//   grant   → sem_prova · dois_clientes (recusas) · link_a · link_b (emissões, uma por titular)
//   revoke  → revoga os tokens do cliente A, numa sessão nova com prova fresca de A
const { Client } = require("@modelcontextprotocol/sdk/client/index.js")
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js")
const Redis = require("ioredis")

const [TENANT = "tenant_demo", MODO = "grant", POOL = "", CLI_A = "", CLI_B = ""] = process.argv.slice(2)
const BASE = "http://localhost:3100"
const SVC = process.env.MCP_INTERNAL_SERVICE_TOKEN || ""
const rnd = () => Math.random().toString(36).slice(2, 10)

async function main() {
  const redis = new Redis(process.env.REDIS_URL || "redis://redis:6379")
  const limpar = []
  const out = {}
  const client = new Client({ name: "probe-aas09", version: "0" })
  try {
    await client.connect(new SSEClientTransport(new URL(BASE + "/sse"), { requestInit: { headers: { "x-service-token": SVC } } }))
    const sessao = async (provas) => {
      const sid = "probe-aas09-" + rnd()
      const k = `${TENANT}:ctx:journey:${sid}`
      limpar.push(k, `${TENANT}:ctx:${sid}`)
      const agora = new Date().toISOString()
      for (const [mech, cli] of provas) {
        const put = (f, v) => redis.hset(k, `core.journey.identity.${mech}.${f}`, JSON.stringify({ value: v, updated_at: agora, source: "probe" }))
        await put("status", "verified"); await put("proven_in_session", sid)
        await put("verified_at", agora); await put("customer_id", cli)
      }
      const r = await fetch(BASE + "/internal/session-token", { method: "POST",
        headers: { "content-type": "application/json", "x-service-token": SVC },
        body: JSON.stringify({ tenant_id: TENANT, session_id: sid, instance_id: "probe", skill_id: "probe" }) })
      return r.status === 200 ? (await r.json()).session_token : ""
    }
    const chama = async (name, args) => {
      const r = await client.callTool({ name, arguments: args })
      try { return { isError: !!r.isError, body: JSON.parse(r.content?.[0]?.text ?? "{}") } } catch { return { isError: true, body: {} } }
    }
    if (MODO === "grant") {
      out.sem_prova     = await chama("customer_agent_grant", { pools: [POOL], session_token: await sessao([]) })
      out.dois_clientes = await chama("customer_agent_grant", { pools: [POOL], session_token: await sessao([["otp", CLI_A], ["whatsapp", CLI_B]]) })
      // o customer_id do INPUT é de outra pessoa: o titular tem de sair da prova, não daqui
      out.link_a = await chama("customer_agent_grant", { pools: [POOL], mandate: ["consultar"], customer_id: CLI_B,
                                                         session_token: await sessao([["otp", CLI_A]]) })
      out.link_b = await chama("customer_agent_grant", { pools: [POOL], session_token: await sessao([["whatsapp", CLI_B]]) })
    } else {
      out.revoke = await chama("customer_agent_revoke", { session_token: await sessao([["otp", CLI_A]]) })
    }
  } finally {
    await client.close().catch(() => {})
    if (limpar.length) await redis.del(...limpar)
    await redis.quit()
  }
  return out
}

main().then(o => console.log(JSON.stringify(o))).catch(e => { console.log(JSON.stringify({ erro: String(e) })); process.exit(0) })
