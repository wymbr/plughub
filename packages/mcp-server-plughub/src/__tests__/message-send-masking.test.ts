/**
 * message-send-masking.test.ts — MSK-04: o `message_send` mascara TODA mensagem, e a
 * falha do mascaramento nunca entrega o original.
 *
 * O QUE ESTAVA ERRADO (medido em 2026-09-24/25)
 * =============================================
 *   1. só `customer`/`primary` eram mascarados — um especialista em conferência
 *      gravava CPF em claro no stream;
 *   2. o `conversations.events` (→ ClickHouse `messages`) levava o `content` ORIGINAL
 *      mesmo quando o stream guardava o token: o dado ia em claro para o analítico;
 *   3. a falha caía num `catch {}` e entregava o ORIGINAL, sem log.
 *
 * As asserções vêm em pares: mascarar sem a testemunha de que o texto limpo passa
 * intacto ficaria verde com um mascarador que apagasse tudo.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerSessionTools } from "../tools/session"
import { createCapturingKafkaProducer, type CapturingKafkaProducer } from "../infra/kafka"
import { signSessionToken } from "../infra/jwt"
import { MaskingService } from "../lib/masking"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

const SID  = "sess_msk04"
const PID  = "7c1f8d2e-3a4b-4c5d-8e9f-0a1b2c3d4e5f"
const CPF  = "529.982.247-25"

function handler(server: McpServer, name: string) {
  const reg = (server as unknown as Record<string, Record<string, {
    handler: (i: unknown) => Promise<ToolResponse>
  }>>)._registeredTools?.[name]
  if (!reg) throw new Error(`tool ${name} não registrada`)
  return reg.handler
}

describe("MSK-04 — message_send mascara todo papel e nunca degrada para o original", () => {
  let redis: InstanceType<typeof RedisMock>
  let kafka: CapturingKafkaProducer
  let send:  (i: unknown) => Promise<ToolResponse>
  let token: string

  beforeEach(async () => {
    redis = new RedisMock()
    // ioredis-mock COMPARTILHA os dados entre instâncias: sem isto o stream acumula
    // as entradas dos testes anteriores e o teste lê a mensagem de outro.
    await redis.flushall()
    kafka = createCapturingKafkaProducer()
    const server = new McpServer({ name: "t", version: "0" })
    registerSessionTools(server, { redis: redis as never, kafka })
    send  = handler(server, "message_send")
    token = signSessionToken({
      tenant_id: "tenant_test", agent_type_id: "especialista_v1",
      instance_id: "especialista_v1-001", permissions: [],
    })
    // O participante é ESPECIALISTA — o papel que ficava fora da máscara.
    await redis.set(`session:${SID}:participants`,
      JSON.stringify([{ participant_id: PID, role: "specialist" }]))
  })

  afterEach(() => vi.restoreAllMocks())

  const msg = (text: string) => ({
    session_token: token, session_id: SID, participant_id: PID,
    content: { type: "text", text }, visibility: "all",
  })

  /** O `content` que foi para o ClickHouse (conversations.events). */
  function analitico(): string {
    const ev = kafka.events.find(e =>
      e.topic === "conversations.events" && e.message["event_type"] === "message_sent")
    expect(ev, "message_sent não publicado").toBeDefined()
    return String(ev!.message["content"])
  }

  /** O `content` que ficou no stream canônico (o `original_content` é outro campo). */
  async function noStream(): Promise<Record<string, unknown>> {
    const entries = await redis.xrange(`session:${SID}:stream`, "-", "+")
    expect(entries.length).toBe(1)
    const fields = entries[0]![1] as string[]
    const payload = JSON.parse(fields[fields.indexOf("payload") + 1]!) as Record<string, unknown>
    return payload
  }

  it("especialista com CPF: stream e ClickHouse recebem o TOKEN, não o valor", async () => {
    const r = await send(msg(`Confirmo o CPF ${CPF}`))
    expect(r.isError).toBeFalsy()
    const p = await noStream()
    expect(JSON.stringify(p["content"])).not.toContain(CPF)
    expect(JSON.stringify(p["content"])).toContain("[cpf:tk_")
    expect(p["masked"]).toBe(true)
    // O original fica no campo próprio, sob authorized_roles — é o cofre, não vazamento.
    expect(JSON.stringify(p["original_content"])).toContain(CPF)
    expect(analitico()).not.toContain(CPF)
    expect(analitico()).toContain("[cpf:tk_")
  })

  // CTX-12: 11 dígitos CRUS — o DV decide a categoria, não a ordem das regras.
  it("CPF CRU com DV válido vira token de CPF — não de telefone", async () => {
    await send(msg("Meu CPF é 52998224725"))
    const c = JSON.stringify((await noStream())["content"])
    expect(c).toContain("[cpf:tk_")
    expect(c).not.toContain("[phone:tk_")
    expect(c).not.toContain("52998224725")
  })

  it("celular CRU (DV de CPF inválido) continua token de TELEFONE", async () => {
    await send(msg("Meu celular é 11987654321"))
    const c = JSON.stringify((await noStream())["content"])
    expect(c).toContain("[phone:tk_")
    expect(c).not.toContain("[cpf:tk_")
  })

  it("TESTEMUNHA: texto sem dado sensível sai intacto nos dois destinos", async () => {
    await send(msg("Vou verificar e já retorno"))
    const p = await noStream()
    expect(p["masked"]).toBe(false)
    expect(p["original_content"]).toBeUndefined()
    expect(analitico()).toContain("Vou verificar e já retorno")
  })

  it("cofre de tokens FALHOU: o trecho sai só com o display, e o log nomeia categoria e sessão", async () => {
    const setOriginal = redis.set.bind(redis)
    vi.spyOn(redis, "set").mockImplementation(((k: string, ...rest: unknown[]) =>
      k.includes(":token:")
        ? Promise.reject(new Error("vault down"))
        : (setOriginal as (...a: unknown[]) => Promise<unknown>)(k, ...rest)) as never)
    const err = vi.spyOn(console, "error").mockImplementation(() => {})

    await send(msg(`CPF ${CPF}`))
    expect(analitico()).not.toContain(CPF)
    expect(analitico()).not.toContain("[cpf:tk_")
    const log = err.mock.calls.map(c => String(c[0])).join("\n")
    expect(log).toContain("categoria=cpf")
    expect(log).toContain(`session=${SID}`)
    // A falha é contida NO TRECHO: não aciona a degradação geral. Sem esta linha, uma
    // exceção que subisse ao handler também passaria (a rede pura mascara igual), e o
    // resto da mensagem perderia o token dos trechos em que o cofre funcionou.
    expect(log).not.toContain("mascaramento FALHOU")
  })

  it("falha INESPERADA do mascaramento: degrada para a rede pura, nunca para o original", async () => {
    vi.spyOn(MaskingService, "applyMasking").mockRejectedValue(new Error("boom"))
    const err = vi.spyOn(console, "error").mockImplementation(() => {})

    await send(msg(`CPF ${CPF}`))
    expect(analitico()).not.toContain(CPF)
    expect((await noStream())["masked"]).toBe(true)
    expect(err.mock.calls.map(c => String(c[0])).join("\n")).toContain(`session=${SID}`)
  })
})
