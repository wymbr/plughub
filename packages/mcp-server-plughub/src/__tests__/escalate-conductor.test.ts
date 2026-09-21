/**
 * MEN-08 — só quem CONDUZ decide o destino do contato.
 *
 * Medido em 2026-09-21 (sessão `e681ff62`): o `@auth_form`, convidado pelo humano, chamou
 * `conversation_escalate` — o cliente leu "Transferindo…" com o humano já na conversa, e o
 * routing-engine roteou o contato de novo. O papel vem do ROSTER pela instância ASSINADA.
 *
 * Proposições, cada uma com o controle ao lado:
 *   - convidado (leitura positiva do roster) é RECUSADO e nada é publicado nem escrito;
 *   - quem conduz passa, e a saída no stream leva a instância real (não "ai-agent");
 *   - sem token / papel não resolvido: segue como antes (não deixa o contato sem destino);
 *   - token de OUTRA sessão é recusado.
 */
import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerBpmTools } from "../tools/bpm"
import { createCapturingKafkaProducer } from "../infra/kafka"
import type { CapturingKafkaProducer } from "../infra/kafka"
import { signSessionBoundToken } from "../infra/jwt"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

const SID = "550e8400-e29b-41d4-a716-4466554400a8"
const HUMANO = "human-u1"
const CONVIDADO = "auth_form_ia-001"

const tok = (instance: string, session = SID) =>
  signSessionBoundToken({ tenant_id: "tenant_test", session_id: session, instance_id: instance, skill_id: "k" })

describe("MEN-08 — conversation_escalate só para quem conduz", () => {
  let redis: InstanceType<typeof RedisMock>
  let kafka: CapturingKafkaProducer
  let call: (input: unknown) => Promise<ToolResponse>

  beforeEach(async () => {
    redis = new RedisMock()
    await redis.flushall()   // o ioredis-mock COMPARTILHA dados entre instâncias
    kafka = createCapturingKafkaProducer()
    const mcp = new McpServer({ name: "t", version: "0" })
    registerBpmTools(mcp, { redis, kafka } as never)
    const reg = (mcp as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
      ._registeredTools!["conversation_escalate"]!
    call = (i) => reg.handler(i)
    await redis.setex(`session:${SID}:meta`, 3600, JSON.stringify({
      tenant_id: "tenant_test", contact_id: "c1", customer_id: "c1", channel: "webchat",
    }))
    await redis.setex(`session:${SID}:participants`, 3600, JSON.stringify([
      { participant_id: HUMANO, instance_id: HUMANO, role: "primary" },
      { participant_id: CONVIDADO, instance_id: CONVIDADO, role: "specialist" },
    ]))
  })

  const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, unknown>
  const publicou = () => kafka.events.some(e => e.topic === "conversations.inbound")
  const saidas = async () => (await redis.xrange(`session:${SID}:stream`, "-", "+"))
    .map(([, f]) => Object.fromEntries(Array.from({ length: f.length / 2 }, (_, i) => [f[2 * i], f[2 * i + 1]])))
    .filter(f => f["type"] === "participant_left")

  it("o convidado é RECUSADO, sem roteamento e sem saída no stream", async () => {
    const r = await call({ session_id: SID, target_pool: "retencao_humano", session_token: tok(CONVIDADO) })
    expect(r.isError).toBe(true)
    expect(corpo(r)["error"]).toBe("escalate_not_conductor")
    expect(String(corpo(r)["detail"])).toContain("specialist")
    expect(publicou()).toBe(false)
    expect(await saidas()).toEqual([])
  })

  it("controle: quem conduz escala, e a saída leva a instância real", async () => {
    const r = await call({ session_id: SID, target_pool: "retencao_humano", session_token: tok(HUMANO) })
    expect(r.isError).toBeFalsy()
    expect(corpo(r)["escalated"]).toBe(true)
    expect(publicou()).toBe(true)
    const [saida] = await saidas()
    expect(saida!["author_id"]).toBe(HUMANO)
  })

  it("sem token: segue como antes (não deixa o contato sem destino)", async () => {
    const r = await call({ session_id: SID, target_pool: "retencao_humano" })
    expect(corpo(r)["escalated"]).toBe(true)
    const [saida] = await saidas()
    expect(saida!["author_id"]).toBe("ai-agent")
  })

  it("instância fora do roster: não conferido, segue", async () => {
    const r = await call({ session_id: SID, target_pool: "retencao_humano", session_token: tok("desconhecida-001") })
    expect(corpo(r)["escalated"]).toBe(true)
  })

  it("token de OUTRA sessão é recusado", async () => {
    const r = await call({ session_id: SID, target_pool: "retencao_humano",
                          session_token: tok(HUMANO, "550e8400-e29b-41d4-a716-000000000000") })
    expect(corpo(r)["error"]).toBe("session_mismatch")
    expect(publicou()).toBe(false)
  })
})
