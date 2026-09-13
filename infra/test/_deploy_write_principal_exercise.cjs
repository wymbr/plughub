// _deploy_write_principal_exercise.cjs — PID-07. Roda DENTRO do container do
// agent-registry (`docker exec -i … node - <tenant> <pool> <skill>`): o segredo e o
// token de serviço vêm do ambiente do próprio serviço, nunca de constante no probe.
// Imprime UMA linha JSON: {casos:{…}, detalhe:{…}}.
const crypto = require("crypto")
const [TENANT, POOL, SKILL] = process.argv.slice(2)
const SECRET = process.env.PLUGHUB_JWT_SECRET || ""
const SVC = process.env.AGENT_REGISTRY_SERVICE_TOKEN || ""
const B = `http://localhost:${process.env.PORT || 3300}`

const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url")
function mint(tenant, email, grants) {
  const module_config = {}
  for (const [m, c] of grants) (module_config[m] ??= {})[c] = { access: "read_write" }
  const h = b64({ alg: "HS256", typ: "JWT" })
  const p = b64({ sub: `u-${email}`, email, tenant_id: tenant, exp: Math.floor(Date.now() / 1000) + 600, module_config })
  return `${h}.${p}.${crypto.createHmac("sha256", SECRET).update(`${h}.${p}`).digest("base64url")}`
}
async function call(method, path, headers, body) {
  const r = await fetch(B + path, { method, headers: { "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body) })
  let j = null
  try { j = await r.json() } catch { /* corpo não-JSON */ }
  return { status: r.status, body: j }
}
const setByNext = async () => {
  const r = await call("GET", `/v1/pools/${POOL}/slots`, { "x-tenant-id": TENANT })
  return r.body?.slots?.next?.set_by ?? null
}

;(async () => {
  const casos = {}, detalhe = {}
  if (!SECRET || !SVC) {
    console.log(JSON.stringify({ erro: "container sem PLUGHUB_JWT_SECRET/AGENT_REGISTRY_SERVICE_TOKEN" }))
    return
  }
  const EMAIL = "devops-pid07@probe"
  const devops = mint(TENANT, EMAIL, [["skill_flows", "operacao"]])
  const resources = mint(TENANT, "resources-pid07@probe", [["config", "resources"]])
  const alheio = mint("tenant_pid07_alheio", "alheio-pid07@probe", [["skill_flows", "operacao"], ["config", "resources"]])
  const slotPath = `/v1/pools/${POOL}/slots/next`
  const corpo = { skill_id: SKILL }

  let r = await call("PUT", slotPath, { "x-tenant-id": TENANT }, corpo)
  casos.anonimo_401 = r.status === 401; detalhe.anonimo = r.status

  r = await call("PUT", slotPath, { authorization: `Bearer ${resources}`, "x-tenant-id": TENANT }, corpo)
  casos.resources_sozinho_403_nomeia_campo = r.status === 403 && String(r.body?.message).includes("skill_flows.operacao")
  detalhe.resources = [r.status, r.body?.message]

  // positivo: o devops grava, com x-user-id FORJADO — o autor tem de ser o do token
  r = await call("PUT", slotPath, { authorization: `Bearer ${devops}`, "x-tenant-id": TENANT, "x-user-id": "forjado-pid07" }, corpo)
  const autor1 = await setByNext()
  casos.devops_grava = r.status === 200; detalhe.devops = [r.status, r.body?.error]
  casos.autor_do_token_nao_do_header = autor1 === EMAIL; detalhe.autor_bearer = autor1

  // tenant alheio mandando o header do tenant do pool
  r = await call("PUT", slotPath, { authorization: `Bearer ${alheio}`, "x-tenant-id": TENANT }, corpo)
  casos.slot_tenant_alheio_recusado = r.status === 403 && r.body?.error === "tenant_mismatch"
  detalhe.slot_alheio = [r.status, r.body?.error]
  casos.slot_alheio_nao_gravou = (await setByNext()) === EMAIL

  r = await call("PUT", `/v1/pools/${POOL}`, { authorization: `Bearer ${alheio}`, "x-tenant-id": TENANT }, {})
  casos.pools_tenant_alheio_recusado = r.status === 403 && r.body?.error === "tenant_mismatch"
  detalhe.pools_alheio = [r.status, r.body?.error]

  r = await call("POST", `/v1/pools/${POOL}/promote`, { authorization: `Bearer ${alheio}`, "x-tenant-id": TENANT }, {})
  casos.promote_tenant_alheio_recusado = r.status === 403 && r.body?.error === "tenant_mismatch"
  detalhe.promote_alheio = [r.status, r.body?.error]

  // controle: serviço diz em nome de quem age
  r = await call("PUT", slotPath, { "x-service-token": SVC, "x-tenant-id": TENANT, "x-user-id": "probe_deploy_write_principal" }, corpo)
  const autor2 = await setByNext()
  casos.servico_grava_com_autor_declarado = r.status === 200 && autor2 === "probe_deploy_write_principal"
  detalhe.servico = [r.status, autor2]

  console.log(JSON.stringify({ casos, detalhe }))
})().catch((e) => console.log(JSON.stringify({ erro: String(e) })))
