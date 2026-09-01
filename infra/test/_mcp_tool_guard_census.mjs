/**
 * _mcp_tool_guard_census.mjs
 * Censo das tools do `mcp-server-plughub` por CAMADA DE GUARDA (CAP-09, 2026-09-01).
 *
 * POR QUE AST E NÃO grep
 * ======================
 * A pergunta é *"o handler DESTA tool consulta X?"*, e ela é estrutural: um arquivo
 * com 10 tools e um `verifySessionToken` não diz qual delas o usa — `grep -c` devolve
 * um número por ARQUIVO e a proposição é por TOOL. Foi assim que o censo manual
 * inicial produziu "session_token=7" num arquivo de 1 tool. O mesmo motivo do
 * `_route_principal_census.py` ser AST: contar ocorrências não é contar decisores.
 *
 * O QUE ELE MEDE (quatro camadas, independentes)
 * ==============================================
 *   token       — o handler verifica um `session_token` ASSINADO
 *                 (`verifySessionToken` / `verifySessionTokenSafe`)
 *   guard       — a chamada passa pelo injection guard (`withGuard` / `detectInjection`)
 *   audit       — o handler publica em `mcp.audit`
 *   permission  — o handler consulta a lista de permissões (`judgeInvoke`)
 *
 * NÃO julga: só conta e imprime JSON. O veredicto — e a tabela do que é isento por
 * DECISÃO — mora no `probe_mcp_tool_guard_census.sh`, pelo mesmo desenho do
 * `probe_edge_surface.sh`: quem mede e quem decide são arquivos diferentes.
 *
 * DUAS METADES, e elas não se substituem (mesmo desenho do
 * `probe_route_credential_coverage.sh`):
 *   · sem argumento → censo do FONTE por AST;
 *   · `--live`      → enumeração pelo `tools/list` do servidor NO AR.
 * A primeira sozinha não vê tool registrada por caminho que ninguém inclui; a segunda
 * sozinha não vê a camada de guarda, que é fato do código. O probe cruza as duas.
 */

import { readdirSync, readFileSync } from "node:fs"
import { createRequire }             from "node:module"
import { fileURLToPath }             from "node:url"
import { join, basename }            from "node:path"

// ── Metade VIVA: enumera pelo `tools/list` do servidor no ar ──────────────────
//
// Sai antes de tocar no TypeScript de propósito: a metade viva não depende do fonte,
// e misturar as duas faria uma falha de parse derrubar a medição da outra.
if (process.argv.includes("--live")) {
  const SDK = new URL(
    "../../packages/e2e-tests/node_modules/@modelcontextprotocol/sdk/dist/esm/client/",
    import.meta.url,
  )
  const { Client }             = await import(new URL("index.js", SDK).href)
  const { SSEClientTransport } = await import(new URL("sse.js",   SDK).href)

  const url = process.env.MCP_SSE_URL ?? "http://localhost:3100/sse"
  const c = new Client({ name: "cap09-censo", version: "1.0.0" }, { capabilities: {} })
  // SEM Authorization de propósito: se conectar, isso É a medição — o
  // `packages/mcp-server-plughub/CLAUDE.md` afirma que toda tool autentica por JWT
  // no header, e o probe compara essa afirmação com o que o transporte faz.
  await c.connect(new SSEClientTransport(new URL(url)))
  const { tools } = await c.listTools()
  await c.close().catch(() => {})
  console.log(JSON.stringify({
    anonymous_connect: true,
    total: tools.length,
    tools: tools.map(t => t.name).sort(),
  }, null, 2))
  process.exit(0)
}

const ROOT    = fileURLToPath(new URL("../..", import.meta.url))
const TOOLDIR = join(ROOT, "packages/mcp-server-plughub/src/tools")
const require_ = createRequire(join(ROOT, "packages/mcp-server-plughub/package.json"))
const ts = require_("typescript")

/** Identificadores que provam cada camada, procurados DENTRO do handler. */
const MARCA = {
  token:      new Set(["verifySessionToken", "verifySessionTokenSafe"]),
  guard:      new Set(["withGuard", "detectInjection"]),
  permission: new Set(["judgeInvoke"]),
}
const TOPICO_AUDIT = "mcp.audit"

/** Coleta todo identificador e todo literal de string alcançável a partir de `node`. */
function varrer(node, ids, strings) {
  if (ts.isIdentifier(node)) ids.add(node.text)
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) strings.add(node.text)
  node.forEachChild(c => varrer(c, ids, strings))
}

/** `server.tool(...)` — a chamada de registro do SDK MCP. */
function ehRegistro(call) {
  const e = call.expression
  return ts.isPropertyAccessExpression(e)
      && e.name.text === "tool"
      && ts.isIdentifier(e.expression)
      && /server/i.test(e.expression.text)
}

const tools = []

for (const arquivo of readdirSync(TOOLDIR).filter(f => f.endsWith(".ts") && !f.includes(".test."))) {
  const caminho = join(TOOLDIR, arquivo)
  const texto   = readFileSync(caminho, "utf8")
  const sf      = ts.createSourceFile(caminho, texto, ts.ScriptTarget.ES2020, true)

  const visitar = node => {
    if (ts.isCallExpression(node) && ehRegistro(node)) {
      const nomeNode = node.arguments[0]
      const nome = nomeNode && ts.isStringLiteral(nomeNode) ? nomeNode.text : null
      if (nome) {
        // O handler é o ÚLTIMO argumento; varrer a chamada inteira menos o nome
        // cobre tanto `server.tool(n, d, schema, handler)` quanto o embrulho
        // `server.tool(n, d, schema, withGuard(n, handler))`.
        const ids = new Set(), strings = new Set()
        for (let i = 1; i < node.arguments.length; i++) varrer(node.arguments[i], ids, strings)

        const tem = conj => [...conj].some(m => ids.has(m))
        tools.push({
          tool:  nome,
          file:  basename(arquivo),
          line:  sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
          token:      tem(MARCA.token),
          guard:      tem(MARCA.guard),
          permission: tem(MARCA.permission),
          audit:      strings.has(TOPICO_AUDIT),
        })
      }
    }
    node.forEachChild(visitar)
  }
  visitar(sf)
}

tools.sort((a, b) => a.tool.localeCompare(b.tool))
console.log(JSON.stringify({ total: tools.length, tools }, null, 2))
