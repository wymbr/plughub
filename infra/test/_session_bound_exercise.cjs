// _session_bound_exercise.cjs — PID-01 (2026-09-13). Roda DENTRO do container do mcp-server:
//   docker exec -i <mcp> sh -c 'cd /app/packages/mcp-server-plughub && node - <tenant>' < este arquivo
//
// Mede, pelo TRANSPORTE MCP de verdade (/sse), o que um chamador SEM a ativação do bridge
// consegue nas duas tools de retomada, e o que o emissor interno aceita.
//   anon_pending_recusa        pending_workflow_get sem token        -> missing_session_token
//   anon_resume_recusa         workflow_resume sem token             -> missing_session_token
//   agente_recusado            token de AGENTE (mesmo segredo)       -> invalid_session_token
//   yaml_tenant_alheio_recusa  token de sessao + tenant_id de outro  -> tenant_mismatch
//   emissor_sem_credencial_401 /internal/session-token sem header    -> 401
//   emissor_emite              com o header                          -> 200 + token
//   pending_com_token_age      controle POSITIVO: a tool responde (nao e recusa)
//   resume_com_token_chega     controle POSITIVO: passa o portao e chega ao gateway
const { Client } = require("@modelcontextprotocol/sdk/client/index.js")
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js")
const jwt = require("jsonwebtoken")

const TENANT = process.argv[2] || "tenant_demo"
const BASE = "http://localhost:3100"

async function main() {
  const out = { casos: {}, detalhe: {} }
  const c = out.casos
  const client = new Client({ name: "probe-pid01", version: "0" })
  await client.connect(new SSEClientTransport(new URL(BASE + "/sse")))
  const chama = async (name, args) => {
    const r = await client.callTool({ name, arguments: args })
    let body = {}
    try { body = JSON.parse(r.content?.[0]?.text ?? "{}") } catch { body = { raw: r.content?.[0]?.text } }
    return { isError: r.isError === true, body }
  }
  const erro = (r, e) => r.isError && r.body.error === e

  let r = await chama("pending_workflow_get", { tenant_id: TENANT, contact_identifier: "5511900000000" })
  out.detalhe.anon_pending = r
  c.anon_pending_recusa = erro(r, "missing_session_token")
  r = await chama("workflow_resume", { resume_token: "probe-pid01-inexistente", decision: "input" })
  out.detalhe.anon_resume = r
  c.anon_resume_recusa = erro(r, "missing_session_token")

  const agente = jwt.sign({ tenant_id: TENANT, agent_type_id: "x", instance_id: "x", permissions: [] },
                          process.env.JWT_SECRET, { expiresIn: 600 })
  r = await chama("pending_workflow_get", { contact_identifier: "5511900000000", session_token: agente })
  c.agente_recusado = erro(r, "invalid_session_token")

  let resp = await fetch(BASE + "/internal/session-token", { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ tenant_id: TENANT, session_id: "probe-pid01" }) })
  c.emissor_sem_credencial_401 = resp.status === 401
  resp = await fetch(BASE + "/internal/session-token", { method: "POST",
    headers: { "content-type": "application/json", "x-service-token": process.env.MCP_INTERNAL_SERVICE_TOKEN || "" },
    body: JSON.stringify({ tenant_id: TENANT, session_id: "probe-pid01", instance_id: "probe", skill_id: "probe" }) })
  const emitido = resp.status === 200 ? (await resp.json()).session_token : ""
  c.emissor_emite = !!emitido

  r = await chama("pending_workflow_get", { tenant_id: "tenant_outro", contact_identifier: "5511900000000", session_token: emitido })
  c.yaml_tenant_alheio_recusa = erro(r, "tenant_mismatch")
  r = await chama("pending_workflow_get", { contact_identifier: "5511900000000", session_token: emitido })
  out.detalhe.pending_com_token = r
  c.pending_com_token_age = !r.isError && "found" in r.body
  r = await chama("workflow_resume", { resume_token: "probe-pid01-inexistente", decision: "input", session_token: emitido })
  out.detalhe.resume_com_token = r
  c.resume_com_token_chega = r.isError && String(r.body.error || "").startsWith("resume_failed_http_")

  await client.close()
  return out
}

main().then(o => console.log(JSON.stringify(o))).catch(e => { console.log(JSON.stringify({ erro: String(e) })); process.exit(0) })
