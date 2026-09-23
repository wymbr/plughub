/**
 * menu-option-description.test.ts — ORQ-15: a segunda linha da opção chega ao CANAL.
 *
 * O `notification_send` valida `menu.options` com um `z.object({id, label})`, e Zod
 * DESCARTA chave não declarada: a `description` que o `render` já trazia morria aqui,
 * calada, e nenhum canal a via — o mesmo modo de falha que a VOZ-05 5a achou no
 * `collect`. A asserção é sobre o que foi PUBLICADO nas duas saídas que os canais leem
 * (`conversations.outbound` e o stream canônico, que o webchat lê por XREAD); o
 * controle é a opção sem descrição, que tem de sair SEM a chave.
 */
import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerBpmTools } from "../tools/bpm"
import { createCapturingKafkaProducer } from "../infra/kafka"
import type { CapturingKafkaProducer } from "../infra/kafka"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }
const SID = "sess_orq15"
const DESC = "Consultar o que o seu plano inclui"
const OPTIONS = [
  { id: "info_plano", label: "Informações do plano", description: DESC },
  { id: "humano", label: "Falar com uma pessoa" },
]

describe("ORQ-15 — description viaja no menu", () => {
  let kafka: CapturingKafkaProducer
  let redis: InstanceType<typeof RedisMock>
  let call: (input: unknown) => Promise<ToolResponse>

  beforeEach(async () => {
    redis = new RedisMock()
    kafka = createCapturingKafkaProducer()
    const mcp = new McpServer({ name: "test-orq15", version: "0.0.1" })
    registerBpmTools(mcp, { redis, kafka })
    await redis.set(`session:${SID}:meta`, JSON.stringify({ channel: "webchat", tenant_id: "tenant_test" }))
    const reg = (mcp as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
      ._registeredTools!["notification_send"]!
    call = async (i) => {
      try { return await reg.handler(i) } catch (e) { return { isError: true, content: [{ type: "text", text: String(e) }] } }
    }
  })

  const outbound = () => kafka.events
    .filter(e => e.topic === "conversations.outbound" && e.message["type"] === "menu.payload")
    .map(e => e.message["options"] as Array<Record<string, unknown>>)

  const stream = async () => {
    const entries = await redis.xrange(`session:${SID}:stream`, "-", "+")
    const reqs = entries
      .map(([, kv]) => Object.fromEntries(kv.reduce<string[][]>((a, v, i) => (i % 2 ? a[a.length - 1]!.push(v) : a.push([v]), a), [])))
      .filter(f => f["type"] === "interaction_request")
    return reqs.map(f => (JSON.parse(String(f["payload"])) as { options: Array<Record<string, unknown>> }).options)
  }

  it("as duas saídas levam a description — e a opção sem ela sai SEM a chave", async () => {
    const r = await call({ session_id: SID, message: "Sobre o quê?", menu: { interaction: "list", options: OPTIONS } })
    expect(r.isError).toBeFalsy()

    const [k] = outbound()
    expect(k?.[0]?.["description"]).toBe(DESC)
    expect(k?.[1] && "description" in k[1]).toBe(false)

    const [s] = await stream()
    expect(s?.[0]?.["description"]).toBe(DESC)
    expect(s?.[1] && "description" in s[1]).toBe(false)
  })

  it("description LONGA não derruba o menu — o teto é do autor, não da tool", async () => {
    const r = await call({ session_id: SID, message: "?", menu: { interaction: "list",
      options: [{ id: "a", label: "A", description: "x".repeat(200) }] } })
    expect(r.isError).toBeFalsy()
    expect(outbound()[0]?.[0]?.["description"]).toHaveLength(200)
  })

  it("ORQ-19 — o menu leva UM nível: filhos e on_return saem, NOMEADOS", async () => {
    const warns: string[] = []
    const orig = console.warn
    console.warn = (...a: unknown[]) => { warns.push(a.map(String).join(" ")) }
    try {
      const r = await call({ session_id: SID, message: "?", menu: { interaction: "list", options: [
        { id: "sac", label: "SAC", on_return: "pos", options: [{ id: "info_plano", label: "Plano" }] },
        { id: "humano", label: "Pessoa" },
      ] } })
      expect(r.isError).toBeFalsy()
    } finally { console.warn = orig }

    // `.at(-1)`: o ioredis-mock COMPARTILHA dados entre instâncias, então o stream
    // ainda traz os menus dos testes anteriores.
    for (const opts of [outbound().at(-1), (await stream()).at(-1)]) {
      expect(opts?.map(o => o["id"])).toEqual(["sac", "humano"])
      expect(JSON.stringify(opts)).not.toContain("info_plano")
      expect(JSON.stringify(opts)).not.toContain("on_return")
    }
    const w = warns.filter(x => x.includes("ORQ-19"))
    expect(w).toHaveLength(1)
    expect(w[0]).toContain("filhos em [sac]")
    expect(w[0]).toContain("on_return em [sac]")
    expect(w[0]).toContain(`session=${SID}`)
  })

  it("ORQ-19 — menu de um nível não gera aviso (controle)", async () => {
    const warns: string[] = []
    const orig = console.warn
    console.warn = (...a: unknown[]) => { warns.push(a.map(String).join(" ")) }
    try {
      await call({ session_id: SID, message: "?", menu: { interaction: "list", options: OPTIONS } })
    } finally { console.warn = orig }
    expect(warns.filter(x => x.includes("ORQ-19"))).toHaveLength(0)
  })

  it("`examples` NÃO é contrato do canal: o schema da tool o descarta", async () => {
    await call({ session_id: SID, message: "?", menu: { interaction: "list",
      options: [{ id: "a", label: "A", examples: ["frase de classificador"] }] } })
    expect(JSON.stringify(outbound())).not.toContain("frase de classificador")
    expect(JSON.stringify(await stream())).not.toContain("frase de classificador")
  })
})
