/**
 * _mcp_rest_census.mjs
 * Censo das rotas REST do `mcp-server-plughub` por CREDENCIAL (CAP-10, 2026-09-01).
 *
 * POR QUE ESTE CENSO É DIFERENTE DO DE TOOLS
 * ==========================================
 * O `_mcp_tool_guard_census.mjs` conta as **tools MCP**, que vivem atrás de `/sse` — e
 * medido em 2026-09-01, **a borda não publica `/sse`**. Estas rotas são a outra metade
 * do mesmo processo, e a borda **publica** (`location ~ ^/api(/|$)` no nginx que o
 * `packages/platform-ui/Dockerfile` gera). São, portanto, a superfície REALMENTE
 * exposta do mcp-server — e por isso pedem censo próprio.
 *
 * É a regra da § Security pela quarta vez: *um censo desenhado para um eixo não prova
 * nada sobre o eixo vizinho*. O censo de tools atravessa estas rotas intacto, porque
 * elas não registram tool nenhuma.
 *
 * AST, não grep: `server.ts` tem ~2700 linhas e uma dezena de `requireJwtRole`; contar
 * ocorrências não diz QUAIS das 25 rotas decidem. Mesmo motivo do
 * `_route_principal_census.py`.
 *
 * NÃO julga — imprime JSON. O veredicto e a tabela declarada moram no
 * `probe_mcp_rest_surface.sh`.
 */

import { createRequire } from "node:module"
import { readFileSync }  from "node:fs"
import { fileURLToPath } from "node:url"
import { join }          from "node:path"

const ROOT = fileURLToPath(new URL("../..", import.meta.url))
const require_ = createRequire(join(ROOT, "packages/mcp-server-plughub/package.json"))
const ts = require_("typescript")

const ARQ = join(ROOT, "packages/mcp-server-plughub/src/server.ts")
const sf  = ts.createSourceFile(ARQ, readFileSync(ARQ, "utf8"), ts.ScriptTarget.ES2020, true)

/**
 * Quem responde *"este chamador pode?"*. Verificar ASSINATURA conta — é o mínimo.
 *
 * ⚠️ FALSO NEGATIVO CORRIGIDO em 2026-09-01, no mesmo dia em que este arquivo nasceu.
 * A primeira versão só listava identificadores de JWT, e por isso marcou
 * `/internal/context-snapshot` e `/internal/context-audit` como SEM CREDENCIAL — elas
 * são gateadas por **`x-service-token`** contra `MCP_INTERNAL_SERVICE_TOKEN`, e ainda
 * FALHAM FECHADAS (503 quando o env não está setado). Duas rotas relatadas como
 * abertas sem estarem.
 *
 * A lição não é "faltou um nome na lista": é que **um censo de credencial precisa
 * cobrir todas as FORMAS de credencial da casa**, e esta casa tem duas — JWT de
 * usuário e token de serviço. Foi a mesma trilha que o `probe_authz_single_verifier`
 * percorreu ao descobrir que contar quem decodifica JWT não conta quem resolve escopo.
 * Por isso a detecção inclui o LITERAL do header, não só identificadores.
 */
const CREDENCIAL = new Set([
  "requireJwtRole", "verifyJwtPayload", "verifyUserJwt", "bearerFromHeader",
  "optionalPoolPrincipal", "requirePoolPrincipal", "abacCan",
])
/** Formas de credencial expressas como STRING (header lido à mão). */
const CREDENCIAL_LITERAL = new Set(["x-service-token", "authorization"])
const METODOS = new Set(["get", "post", "put", "delete", "patch"])

const rotas = []
const varrer = (n, ids, strs) => {
  if (ts.isIdentifier(n)) ids.add(n.text)
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) strs.add(n.text.toLowerCase())
  n.forEachChild(c => varrer(c, ids, strs))
}

const visitar = node => {
  if (ts.isCallExpression(node)) {
    const e = node.expression
    if (ts.isPropertyAccessExpression(e) && METODOS.has(e.name.text)
        && ts.isIdentifier(e.expression) && e.expression.text === "app") {
      const p = node.arguments[0]
      if (p && ts.isStringLiteral(p)) {
        const ids = new Set(), strs = new Set()
        for (let i = 1; i < node.arguments.length; i++) varrer(node.arguments[i], ids, strs)
        rotas.push({
          key:    `${e.name.text.toUpperCase()} ${p.text}`,
          method: e.name.text.toUpperCase(),
          route:  p.text,
          // `publicada` reflete o nginx do platform-ui: só `^/api(/|$)` (e o
          // WebSocket `^/agent-ws`) alcançam este serviço pela borda.
          published: p.text.startsWith("/api"),
          credentials: [
            ...[...CREDENCIAL].filter(c => ids.has(c)),
            ...[...CREDENCIAL_LITERAL].filter(h => strs.has(h)),
          ],
          line:   sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
        })
      }
    }
  }
  node.forEachChild(visitar)
}
visitar(sf)

rotas.sort((a, b) => a.key.localeCompare(b.key))
console.log(JSON.stringify({ total: rotas.length, routes: rotas }, null, 2))
