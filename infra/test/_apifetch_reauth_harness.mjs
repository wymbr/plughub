/**
 * _apifetch_reauth_harness.mjs — exercita o `apiFetch` REAL contra um `fetch` de mentira.
 *
 * Chamado por `probe_apifetch_reauth.sh`, que compila `api/apiFetch.ts` +
 * `auth/token-store.ts` (os arquivos de producao, nao copias) e passa o diretorio aqui.
 *
 * POR QUE ISTO EXISTE. O `platform-ui` nao tem framework de teste — nenhum, medido em
 * 2026-08-31: sem vitest, sem jest, zero arquivos `*.test.*`. Acrescentar um e decisao
 * maior que esta tarefa; nao ter instrumento nenhum para o re-auth reativo seria pior,
 * porque o modo de falha dele e SILENCIOSO: com o single-flight quebrado, a sessao morre
 * exatamente quando tenta se salvar, e na tela isso aparece como "deslogou sozinho".
 *
 * O que se mede aqui e comportamento OBSERVAVEL do modulo: quantas vezes ele chama a
 * rede, com qual header, e quantas vezes pede renovacao.
 */
import assert from "node:assert/strict"
import { pathToFileURL } from "node:url"
import { join } from "node:path"

const dir = process.argv[2]
if (!dir) { console.error("uso: harness.mjs <dir-compilado>"); process.exit(2) }

// `import()` com caminho absoluto de Windows precisa de URL `file://` — caminho cru vira
// "protocolo c:" e o loader recusa. Vale para os dois sistemas, entao nao ha ramo.
const mod = (nome) => import(pathToFileURL(join(dir, nome)).href)

const store = await mod("token-store.mjs")
const { apiFetch } = await mod("apiFetch.mjs")

let chamadas = []          // cada entrada: { url, auth }
let respostas = []         // fila de status a devolver
let reauthCount = 0

function resetar({ statuses, token = "T0", reauthToken = "T1" }) {
  chamadas = []
  respostas = [...statuses]
  reauthCount = 0
  store.setAccessToken(token)
  store.setReauthorizer(async () => {
    reauthCount += 1
    // Assincronia REAL: sem ela, chamadas "concorrentes" seriam sequenciais e o teste
    // do single-flight passaria sem exercer a corrida que ele existe para pegar.
    await new Promise((r) => setTimeout(r, 5))
    if (reauthToken) store.setAccessToken(reauthToken)
    return reauthToken
  })
}

globalThis.fetch = async (url, init) => {
  const h = new Headers(init?.headers)
  chamadas.push({ url, auth: h.get("Authorization") })
  const status = respostas.shift() ?? 200
  return new Response(null, { status })
}

const falhas = []
async function caso(nome, fn) {
  try { await fn(); console.log(`  \x1b[32mOK\x1b[0m           ${nome}`) }
  catch (e) { falhas.push(nome); console.log(`  \x1b[31mFALHA\x1b[0m        ${nome}\n               ${e.message}`) }
}

console.log("== probe_apifetch_reauth ==\n")

await caso("200 passa direto: uma chamada, nenhuma renovacao", async () => {
  resetar({ statuses: [200] })
  const r = await apiFetch("/x")
  assert.equal(r.status, 200)
  assert.equal(chamadas.length, 1)
  assert.equal(reauthCount, 0, "renovou sem 401")
  assert.equal(chamadas[0].auth, "Bearer T0")
})

await caso("401 renova e RETENTA uma vez, com o token NOVO", async () => {
  resetar({ statuses: [401, 200] })
  const r = await apiFetch("/x")
  assert.equal(r.status, 200, "a retentativa nao aconteceu")
  assert.equal(chamadas.length, 2)
  assert.equal(reauthCount, 1)
  assert.equal(chamadas[1].auth, "Bearer T1", "retentou com o token VELHO — renovar sem usar nao adianta")
})

await caso("401 persistente para em DUAS chamadas — nunca laco", async () => {
  resetar({ statuses: [401, 401, 401, 401] })
  const r = await apiFetch("/x")
  assert.equal(r.status, 401)
  assert.equal(chamadas.length, 2, `bateu ${chamadas.length}x no servidor`)
})

await caso("sem sessao renovavel: devolve o 401 ORIGINAL, sem engolir", async () => {
  resetar({ statuses: [401], reauthToken: null })
  const r = await apiFetch("/x")
  assert.equal(r.status, 401, "o 401 foi engolido — a tela nunca mandaria ao login")
  assert.equal(chamadas.length, 1, "retentou sem token novo")
})

await caso("SINGLE-FLIGHT: cinco 401 concorrentes renovam UMA vez", async () => {
  // O refresh token e ROTATIVO: cada renovacao invalida a anterior. Sem dedup, cinco
  // renovacoes simultaneas se derrubam e a sessao morre ao tentar se salvar.
  resetar({ statuses: [401, 401, 401, 401, 401, 200, 200, 200, 200, 200] })
  const rs = await Promise.all([1, 2, 3, 4, 5].map(() => apiFetch("/x")))
  assert.deepEqual(rs.map((r) => r.status), [200, 200, 200, 200, 200])
  assert.equal(reauthCount, 1, `renovou ${reauthCount}x — o single-flight nao esta dedupando`)
  assert.equal(chamadas.length, 10)
})

await caso("credencial DO CHAMADOR: nao sobrescreve, nao renova", async () => {
  resetar({ statuses: [401] })
  const r = await apiFetch("/x", { headers: { Authorization: "Bearer DELE" } })
  assert.equal(r.status, 401)
  assert.equal(chamadas[0].auth, "Bearer DELE", "sobrescreveu a credencial do chamador")
  assert.equal(reauthCount, 0, "renovou uma sessao que nao e a dona daquela chamada")
})

await caso("sem reauthorizer registrado (pre-login): 401 sai intacto", async () => {
  resetar({ statuses: [401] })
  store.setReauthorizer(null)
  const r = await apiFetch("/x")
  assert.equal(r.status, 401)
  assert.equal(chamadas.length, 1)
})

console.log(falhas.length ? `\n${falhas.length} FALHA(S)` : "\nVERDE")
process.exit(falhas.length ? 1 : 0)
