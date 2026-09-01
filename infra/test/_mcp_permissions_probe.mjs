/**
 * _mcp_permissions_probe.mjs
 * Metade Node do `probe_mcp_permissions_producer.sh` (CAP-06).
 *
 * Existe em Node — e não em shell como os outros probes — porque a proposição só é
 * verificável ATRAVESSANDO a borda: `agent_login` e `invoke` são tools MCP sobre SSE,
 * e um cliente SSE em `curl` não é escrevível. Medir por leitura de código deixaria de
 * fora exatamente o elo que estava quebrado (a assinatura do token).
 *
 * Imprime uma linha `RESULT <ramo> <PASS|FAIL> <detalhe>` por ramo; o shell decide o
 * veredicto. Sempre limpa as skills que criou, inclusive em erro.
 */

// O SDK é resolvido pelo CAMINHO, não pelo nome: este arquivo mora em `infra/test/`,
// que não tem `node_modules`, e a resolução ESM parte da localização do MÓDULO (não do
// cwd) — importar pelo nome falha aqui mesmo rodando de dentro do `e2e-tests`. O mapa
// `exports` do pacote é `"./*" → "./dist/esm/*"`, então o alvo abaixo é o mesmo arquivo
// que `@modelcontextprotocol/sdk/client/index.js` resolveria.
const _SDK = new URL(
  "../../packages/e2e-tests/node_modules/@modelcontextprotocol/sdk/dist/esm/client/",
  import.meta.url,
)
const { Client }             = await import(new URL("index.js", _SDK).href)
const { SSEClientTransport } = await import(new URL("sse.js",   _SDK).href)

const REGISTRY = process.env.REGISTRY_URL ?? "http://localhost:3300"
const MCP_SSE  = process.env.MCP_SSE_URL  ?? "http://localhost:3100/sse"
const TENANT   = process.env.TENANT_ID    ?? "tenant_demo"

const SKILL_COM  = "skill_capseis_probe_com_tools_v1"
const SKILL_SEM  = "skill_capseis_probe_sem_tools_v1"
const DECLARADA  = { mcp_server: "mcp-server-auth", tool: "validate_pin", required: true }
const NAO_DECL   = { mcp_server: "mcp-server-auth", tool: "tool_que_ninguem_declarou" }

let failures = 0
const result = (ramo, ok, detalhe) => {
  if (!ok) failures++
  console.log(`RESULT ${ramo} ${ok ? "PASS" : "FAIL"} ${detalhe}`)
}

// ── Registry ──────────────────────────────────────────────────────────────────

/** Mesma credencial e mesmo header que `_publish_skill.py` — escrita no registry passa
 *  por `requireResourceWrite` (leitura é aberta, escrita não). */
const SVC = process.env.AGENT_REGISTRY_SERVICE_TOKEN ?? "changeme_agent_registry_service_token_demo"

const reg = (path, init = {}) =>
  fetch(`${REGISTRY}${path}`, {
    ...init,
    headers: {
      "X-Tenant-Id":    TENANT,
      "X-User-Id":      "cap06-probe",
      "X-Service-Token": SVC,
      "Content-Type":   "application/json",
      "Accept":         "application/json",
      ...(init.headers ?? {}),
    },
  })

/** Molde a partir de uma skill REAL — inventar um corpo mínimo à mão erraria o schema
 *  e o probe reprovaria por 422, que é ruído e não a proposição. */
async function moldeDeSkillReal() {
  const res  = await reg("/v1/skills")
  const data = await res.json()
  const itens = Array.isArray(data) ? data : (data.skills ?? data.items ?? [])
  const base = itens.find(s => s.flow) ?? itens[0]
  if (!base) throw new Error("nenhuma skill no registry para servir de molde")

  const { skill_id, tenant_id, created_at, updated_at, created_by, published_at,
          deploy_status, status, flow_draft, unpublished_draft, ...resto } = base
  void skill_id; void tenant_id; void created_at; void updated_at; void created_by
  void published_at; void deploy_status; void status; void flow_draft; void unpublished_draft

  // O Postgres devolve `null` nos campos opcionais e o Zod da rota exige objeto/array
  // quando a chave está PRESENTE — `null` não é o mesmo que ausente. Sem esta poda o
  // POST volta 422 e o probe reprovaria pelo motivo errado.
  return Object.fromEntries(Object.entries(resto).filter(([, v]) => v !== null))
}

async function criarSkill(id, tools) {
  const molde = await moldeDeSkillReal()
  const res = await reg("/v1/skills", {
    method: "POST",
    body:   JSON.stringify({ ...molde, skill_id: id, name: id, tools }),
  })
  if (res.status === 409) {
    const put = await reg(`/v1/skills/${id}`, {
      method: "PUT",
      body:   JSON.stringify({ ...molde, name: id, tools }),
    })
    if (!put.ok) throw new Error(`PUT ${id} → ${put.status} ${await put.text()}`)
    return
  }
  if (!res.ok) throw new Error(`POST ${id} → ${res.status} ${await res.text()}`)
}

const apagarSkill = id =>
  reg(`/v1/skills/${id}`, { method: "DELETE" }).catch(() => {})

// ── MCP ───────────────────────────────────────────────────────────────────────

async function comCliente(fn) {
  const client = new Client({ name: "cap06-probe", version: "1.0.0" }, { capabilities: {} })
  await client.connect(new SSEClientTransport(new URL(MCP_SSE)))
  try { return await fn(client) } finally { await client.close().catch(() => {}) }
}

/** Devolve o objeto JSON que a tool escreveu no bloco de texto. */
function corpo(res) {
  const txt = res?.content?.find(c => c.type === "text")?.text ?? "{}"
  try { return JSON.parse(txt) } catch { return { _raw: txt } }
}

const call = (client, name, args) =>
  client.callTool({ name, arguments: args }).then(r => ({ res: r, body: corpo(r) }))

/** Payload do JWT — DECODIFICA, não verifica: o probe quer ver o que foi assinado. */
function payloadDoToken(token) {
  const parte = String(token).split(".")[1] ?? ""
  return JSON.parse(Buffer.from(parte, "base64url").toString("utf8"))
}

// ── Execução ──────────────────────────────────────────────────────────────────

async function main() {
  // P0 · testemunha de que a virada é OPT-IN: quantas skills declaram tools hoje.
  {
    const data  = await (await reg("/v1/skills")).json()
    const itens = Array.isArray(data) ? data : (data.skills ?? data.items ?? [])
    const com   = itens.filter(s => Array.isArray(s.tools) && s.tools.length > 0)
                       .map(s => s.skill_id)
                       .filter(id => id !== SKILL_COM)
    result("P0", true, `skills preexistentes com tools: ${com.length} (${com.join(",") || "nenhuma"})`)
  }

  await criarSkill(SKILL_COM, [DECLARADA])
  await criarSkill(SKILL_SEM, [])

  await comCliente(async client => {
    // ── P1 · O ELO QUE FALTAVA: o token carrega o tools[] declarado ───────────
    const login = await call(client, "agent_login", {
      agent_type_id: SKILL_COM,
      instance_id:   "cap06-probe-com-001",
      tenant_id:     TENANT,
    })
    if (login.res.isError) {
      result("P1", false, `agent_login falhou: ${JSON.stringify(login.body)}`)
      result("P3", false, "não executado — P1 falhou")
      result("P4", false, "não executado — P1 falhou")
      return
    }
    const tokenCom = login.body.session_token
    const perms    = payloadDoToken(tokenCom).permissions
    const esperado = `${DECLARADA.mcp_server}:${DECLARADA.tool}`
    result(
      "P1",
      Array.isArray(perms) && perms.length === 1 && perms[0] === esperado,
      `permissions no token = ${JSON.stringify(perms)} (esperado ["${esperado}"])`,
    )

    // ── P2 · CONTROLE: skill SEM tools continua com [] — a virada é opt-in ────
    const loginSem = await call(client, "agent_login", {
      agent_type_id: SKILL_SEM,
      instance_id:   "cap06-probe-sem-001",
      tenant_id:     TENANT,
    })
    const permsSem = loginSem.res.isError ? null : payloadDoToken(loginSem.body.session_token).permissions
    result(
      "P2",
      Array.isArray(permsSem) && permsSem.length === 0,
      `permissions da skill sem tools = ${JSON.stringify(permsSem)} (esperado [])`,
    )

    // ── P3 · a tool DECLARADA atravessa o gate ───────────────────────────────
    // Não se exige sucesso do upstream (o domain server pode nem responder) — a
    // proposição é só que o VEREDICTO de permissão deixou passar.
    const inv = await call(client, "invoke", {
      session_token: tokenCom,
      mcp_server:    DECLARADA.mcp_server,
      tool:          DECLARADA.tool,
      params:        { pin: "0000" },
    })
    const negadoP3 = inv.body?.error === "permission_denied"
    result(
      "P3",
      !negadoP3,
      `erro=${inv.body?.error ?? "(nenhum)"} msg=${JSON.stringify(inv.body?.message ?? "")} `
        + `— não pode ser permission_denied`,
    )

    // ── P4 · a tool NÃO declarada é negada, e a recusa NOMEIA ────────────────
    const inv4 = await call(client, "invoke", {
      session_token: tokenCom,
      mcp_server:    NAO_DECL.mcp_server,
      tool:          NAO_DECL.tool,
      params:        {},
    })
    const msg = String(inv4.body?.message ?? "")
    result(
      "P4",
      inv4.body?.error === "permission_denied"
        && msg.includes(`${NAO_DECL.mcp_server}:${NAO_DECL.tool}`)
        && msg.includes(esperado),
      `erro=${inv4.body?.error} nomeia_exigida=${msg.includes(`${NAO_DECL.mcp_server}:${NAO_DECL.tool}`)} `
        + `nomeia_autorizadas=${msg.includes(esperado)}`,
    )

    // ── P5 · CAP-05 medido: lista vazia NEGA — era o default de todo agente ──
    if (!loginSem.res.isError) {
      const inv5 = await call(client, "invoke", {
        session_token: loginSem.body.session_token,
        mcp_server:    DECLARADA.mcp_server,
        tool:          DECLARADA.tool,
        params:        { pin: "0000" },
      })
      result(
        "P5",
        inv5.body?.error === "permission_denied",
        `agente sem tools é negado (erro=${inv5.body?.error}) — a borda segue fechada p/ quem não declara`,
      )
    } else {
      result("P5", false, "não executado — login da skill sem tools falhou")
    }
  })
}

main()
  .catch(e => { failures++; console.log(`RESULT FATAL FAIL ${e?.message ?? e}`) })
  .finally(async () => {
    await apagarSkill(SKILL_COM)
    await apagarSkill(SKILL_SEM)
    process.exit(failures === 0 ? 0 : 1)
  })
