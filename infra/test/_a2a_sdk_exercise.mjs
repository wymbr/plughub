// _a2a_sdk_exercise.mjs — o SDK OFICIAL do A2A em JavaScript (@a2a-js/sdk) contra a porta `a2a`
// (AAS-08). Peça do `probe_aas08_a2a_sdk.sh`: roda num container `node:20-alpine` com o SDK numa
// versão FIXA e imprime UMA linha JSON por ramo (`{"ramo","ok","detalhe"}`); o probe julga.
//
// O SDK JS lê as respostas com os `fromJSON` gerados do proto (ts-proto): enum fora do domínio,
// oneof sem caso e campo com tipo errado aparecem aqui, não no `curl`.
// Entrada por env: A2A_BASE, A2A_CRED, A2A_CRED2.
import { ClientFactory, ClientFactoryOptions, JsonRpcTransportFactory, DefaultAgentCardResolver }
  from "@a2a-js/sdk/client"
import { Role, TaskState } from "@a2a-js/sdk"
import { randomUUID } from "node:crypto"

const BASE = process.env.A2A_BASE
const out = (ramo, ok, detalhe = "") =>
  console.log(JSON.stringify({ ramo, ok: !!ok, detalhe: String(detalhe).slice(0, 400) }))

function comCredencial(cred) {
  return (url, init = {}) => {
    const headers = new Headers(init.headers || {})
    headers.set("Authorization", `Bearer ${cred}`)
    return fetch(url, { ...init, headers })
  }
}

async function cliente(cred) {
  const fetchImpl = comCredencial(cred)
  const opts = ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
    transports: [new JsonRpcTransportFactory({ fetchImpl })],
    cardResolver: new DefaultAgentCardResolver({ fetchImpl }),
  })
  return new ClientFactory(opts).createFromUrl(BASE)
}

const msg = (parts, extra = {}) => ({
  message: {
    messageId: randomUUID(), contextId: "", taskId: "", role: Role.ROLE_USER, parts,
    metadata: undefined, extensions: [], referenceTaskIds: [], ...extra,
  },
  configuration: undefined, metadata: undefined, tenant: "",
})
const txt = (t) => ({ content: { $case: "text", value: t }, metadata: undefined, filename: "", mediaType: "" })
const dat = (d) => ({ content: { $case: "data", value: d }, metadata: undefined, filename: "", mediaType: "" })
const nomeEstado = (s) => TaskState[s] ?? String(s)
const taskDe = (r) => (r?.payload?.$case === "task" ? r.payload.value : r?.$case === "task" ? r.value : r)

let c, tid, ctx
try {
  c = await cliente(process.env.A2A_CRED)
  out("J1", true, "card resolvido e cliente JSONRPC criado")
} catch (e) { out("J1", false, `${e.name}: ${e.message}`); process.exit(0) }

try {
  const r = await c.sendMessage(msg([txt("segunda via"), dat({ linha: "11999990000" })]))
  const t = taskDe(r)
  tid = t.id; ctx = t.contextId
  out("J2", t.status.state === TaskState.TASK_STATE_INPUT_REQUIRED, `estado=${nomeEstado(t.status.state)}`)
} catch (e) { out("J2", false, `${e.name}: ${e.message}`); process.exit(0) }

try {
  const t = taskDe(await c.sendMessage(msg([txt("2")], { taskId: tid, contextId: ctx })))
  const art = t.artifacts?.[0]?.parts?.[0]?.content
  out("J3", t.status.state === TaskState.TASK_STATE_COMPLETED && art?.$case === "data" && art.value === "pix",
      `estado=${nomeEstado(t.status.state)} artefato=${JSON.stringify(art)}`)
} catch (e) { out("J3", false, `${e.name}: ${e.message}`) }

try {
  const t = await c.getTask({ id: tid, historyLength: undefined, tenant: "" })
  out("J4", t.status.state === TaskState.TASK_STATE_COMPLETED && (t.history || []).length >= 2,
      `estado=${nomeEstado(t.status.state)} historico=${(t.history || []).length}`)
} catch (e) { out("J4", false, `${e.name}: ${e.message}`) }

try {
  const r = await c.listTasks({ contextId: ctx, tenant: "", status: 0, pageSize: undefined, pageToken: "",
                                historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: undefined })
  out("J5", (r.tasks || []).some((t) => t.id === tid), `n=${(r.tasks || []).length}`)
} catch (e) { out("J5", false, `${e.name}: ${e.message}`) }

let tid2 = ""
try {
  const tipos = []
  let ult
  for await (const ev of c.sendMessageStream(msg([dat({ linha: "1" })], { contextId: ctx }))) {
    tipos.push(ev.payload?.$case)
    if (ev.payload?.$case === "task") tid2 ||= ev.payload.value.id
    ult = ev.payload
  }
  const st = ult?.$case === "statusUpdate" ? ult.value.status.state : ult?.value?.status?.state
  out("J6", tipos[0] === "task" && st === TaskState.TASK_STATE_INPUT_REQUIRED, `tipos=${tipos} ultimo=${nomeEstado(st)}`)
} catch (e) { out("J6", false, `${e.name}: ${e.message}`) }

if (tid2) {
  try {
    const tipos = []
    for await (const ev of c.resubscribeTask({ id: tid2, tenant: "" })) tipos.push(ev.payload?.$case)
    out("J7", tipos.join(",") === "task", `tipos=${tipos}`)
  } catch (e) { out("J7", false, `${e.name}: ${e.message}`) }
  try {
    await c.cancelTask({ id: tid2, tenant: "", metadata: undefined })
    let s
    for (let i = 0; i < 40; i++) {
      s = (await c.getTask({ id: tid2, historyLength: undefined, tenant: "" })).status.state
      if (s === TaskState.TASK_STATE_CANCELED) break
      await new Promise((r) => setTimeout(r, 500))
    }
    out("J8", s === TaskState.TASK_STATE_CANCELED, `estado=${nomeEstado(s)}`)
  } catch (e) { out("J8", false, `${e.name}: ${e.message}`) }
}

try {
  const c2 = await cliente(process.env.A2A_CRED2)
  await c2.getTask({ id: tid, historyLength: undefined, tenant: "" })
  out("J9", false, "outro principal LEU a task")
} catch (e) { out("J9", /TaskNotFound/.test(e.name) || /TaskNotFound/.test(e.constructor?.name ?? ""), `${e.name}: ${e.message}`) }
