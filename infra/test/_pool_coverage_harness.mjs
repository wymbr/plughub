/**
 * _pool_coverage_harness.mjs — exercita `access/pool-coverage.ts` (AUT-14).
 *
 * Chamado por `probe_pool_coverage.sh`, que compila o arquivo de PRODUCAO.
 *
 * POR QUE UM PORTAO PARA UM CALCULO DE TELA. Porque o modo de falha dele e mudo nas
 * duas direcoes: contar usuario INATIVO faz um pool orfao parecer coberto (o alarme nao
 * dispara, e o sintoma ja era ausencia); e avisar sobre orfao PREEXISTENTE ao desativar
 * alguem sem relacao com ele culpa a mudanca errada, o aviso vira ruido e alguem o
 * desliga. Nenhum dos dois fica vermelho sozinho.
 */
import assert from "node:assert/strict"
import { pathToFileURL } from "node:url"
import { join } from "node:path"

const dir = process.argv[2]
if (!dir) { console.error("uso: harness.mjs <dir-compilado>"); process.exit(2) }
const { computeCoverage, orphansAfter } = await import(pathToFileURL(join(dir, "pool-coverage.mjs")).href)

const u = (id, active, pools) => ({ id, active, accessible_pools: pools })

const falhas = []
async function caso(nome, fn) {
  try { await fn(); console.log(`  \x1b[32mOK\x1b[0m           ${nome}`) }
  catch (e) { falhas.push(nome); console.log(`  \x1b[31mFALHA\x1b[0m        ${nome}\n               ${e.message}`) }
}

console.log("== probe_pool_coverage ==\n")

await caso("todo pool tem chave, inclusive com zero", async () => {
  const c = computeCoverage([u("a", true, ["p1"])], ["p1", "p2"])
  assert.deepEqual(c.byPool, { p1: 1, p2: 0 })
  // Chave FALTANDO viraria `undefined`, e o `undefined === 0` do consumidor decidiria
  // errado sem reclamar. Zero explicito e o que torna a ausencia legivel.
  assert.ok("p2" in c.byPool, "pool sem cobertura sumiu do mapa")
})

await caso("usuario INATIVO nao vigia — pool fica orfao", async () => {
  const c = computeCoverage([u("a", false, ["p1"])], ["p1"])
  assert.equal(c.byPool.p1, 0)
  assert.deepEqual(c.orphans, ["p1"])
})

await caso("orfao e SO zero: um unico usuario NAO e alarme", async () => {
  // A decisao do dono: contar "preso a um" como alarme publicaria EXPOSICAO como DANO
  // (D14.1), e na instalacao medida seriam 31 de 36 — 86% da populacao.
  const c = computeCoverage([u("a", true, ["p1"])], ["p1"])
  assert.deepEqual(c.orphans, [])
  assert.equal(c.byPool.p1, 1)
})

await caso("escopo com pool inexistente nao infla cobertura", async () => {
  const c = computeCoverage([u("a", true, ["p1", "pool_removido"])], ["p1"])
  assert.deepEqual(c.byPool, { p1: 1 })
})

await caso("sem usuario nenhum: todos orfaos", async () => {
  const c = computeCoverage([], ["p1", "p2"])
  assert.deepEqual(c.orphans, ["p1", "p2"])
})

await caso("DESATIVAR o unico vigia acusa o pool", async () => {
  const users = [u("a", true, ["p1", "p2"]), u("b", true, ["p2"])]
  const r = orphansAfter(users, ["p1", "p2"], { id: "a", active: false, accessiblePools: ["p1", "p2"] })
  assert.deepEqual(r, ["p1"], "p2 tem outro vigia e nao deveria ser acusado")
})

await caso("REMOVER pool do escopo do unico vigia acusa igual", async () => {
  const users = [u("a", true, ["p1"])]
  const r = orphansAfter(users, ["p1"], { id: "a", active: true, accessiblePools: [] })
  assert.deepEqual(r, ["p1"])
})

await caso("orfao PREEXISTENTE nao e culpa desta mudanca", async () => {
  // Sem esta regra o aviso dispara em toda edicao que toque num tenant com um orfao
  // antigo — e um aviso que sempre aparece e um aviso que ninguem le.
  const users = [u("a", true, ["p1"]), u("b", true, [])]
  const r = orphansAfter(users, ["p1", "p2"], { id: "b", active: false, accessiblePools: [] })
  assert.deepEqual(r, [], "acusou p2, que ja estava orfao antes")
})

await caso("desativar quem nao era o unico vigia nao acusa nada", async () => {
  const users = [u("a", true, ["p1"]), u("b", true, ["p1"])]
  const r = orphansAfter(users, ["p1"], { id: "a", active: false, accessiblePools: ["p1"] })
  assert.deepEqual(r, [])
})

await caso("31-de-36: desativar a conta unica acusa os 31, nao os 36", async () => {
  // Reproduz a medicao real de 2026-08-31: `admin@` com 36 pools, e 5 deles tambem
  // alcancados por outras contas.
  const todos = Array.from({ length: 36 }, (_, i) => `p${i}`)
  const outros = todos.slice(31)
  const users = [u("admin", true, todos), ...outros.map((p, i) => u(`op${i}`, true, [p]))]
  const r = orphansAfter(users, todos, { id: "admin", active: false, accessiblePools: todos })
  assert.equal(r.length, 31, `acusou ${r.length} pools, esperado 31`)
})

await caso("populacao CORTADA nao afirma orfao nenhum", async () => {
  // `GET /auth/users` pagina com limit=100 (medido em 2026-08-31). Com a lista cortada,
  // um pool coberto por quem nao veio na pagina apareceria como orfao — alarme falso, e
  // e assim que se ensina a ignorar alarme. Ausencia DECLARADA, nunca numero plausivel.
  const c = computeCoverage([], ["p1", "p2"], true)
  assert.deepEqual(c.orphans, [], "afirmou orfao sobre populacao incompleta")
  assert.equal(c.truncated, true)
  // O mapa continua servindo de DADO: e um piso honesto ("ao menos N").
  assert.deepEqual(c.byPool, { p1: 0, p2: 0 })
})

console.log(falhas.length ? `\n${falhas.length} FALHA(S)` : "\nVERDE")
process.exit(falhas.length ? 1 : 0)
