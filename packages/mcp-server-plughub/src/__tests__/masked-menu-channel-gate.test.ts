/**
 * masked-menu-channel-gate.test.ts
 * NIV-03 (metade de RUNTIME) — um menu MASCARADO não sai para canal que não sabe
 * mascarar. Fecha a MSK-01.
 *
 * POR QUE ESTE TESTE EXISTE. O pool `limite_ia` declara `[webchat, whatsapp]` e roda
 * um fluxo que mascara CVV; no WhatsApp o campo virava formulário comum — sem
 * fallback, sem aviso e sem recusa. A tabela que dizia `supports_masked_input: false`
 * para whatsapp/sms/email era **comentário sem leitor** (removida na NIV-01).
 *
 * As asserções vêm em PARES, porque cada metade sozinha passa pelo motivo errado:
 *
 *   * recusar × entregar — um gate que recusasse todo menu mascarado passaria no
 *     caso do whatsapp e quebraria o webchat, que é o único canal onde a coleta
 *     mascarada funciona hoje.
 *   * mascarado × não mascarado — sem o par, um gate que recusasse todo menu no
 *     whatsapp (mascarado ou não) ficaria verde e derrubaria o canal inteiro.
 *   * cliente × `agents_only` — o menu de operador não vai ao canal do cliente;
 *     guardá-lo recusaria wrap-up e NPS interno sem ganho nenhum.
 *
 * E a asserção que importa mais não é o `isError`: é que **nada foi publicado em
 * `conversations.outbound`**. Recusar e publicar ao mesmo tempo seria o pior dos
 * dois mundos — o fluxo falha e o valor vaza assim mesmo.
 */

import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerBpmTools } from "../tools/bpm"
import { createCapturingKafkaProducer } from "../infra/kafka"
import type { CapturingKafkaProducer } from "../infra/kafka"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

function makeTestServer(mcpServer: McpServer) {
  return {
    callTool: async (name: string, input: unknown): Promise<ToolResponse> => {
      const reg = (mcpServer as unknown as Record<string, Record<string, {
        handler: (i: unknown) => Promise<ToolResponse>
      }>>)._registeredTools?.[name]
      if (!reg) throw new Error(`Tool '${name}' not registered`)
      try {
        return await reg.handler(input)
      } catch (e) {
        return { isError: true, content: [{ type: "text", text: String(e) }] }
      }
    },
  }
}

const SID = "sess_niv03"

describe("NIV-03 — menu mascarado × capacidade do canal", () => {
  let redis:  InstanceType<typeof RedisMock>
  let kafka:  CapturingKafkaProducer
  let server: ReturnType<typeof makeTestServer>

  async function comCanal(channel: string) {
    await redis.set(`session:${SID}:meta`, JSON.stringify({ channel, tenant_id: "tenant_test" }))
  }

  /** Menus entregues ao CANAL do cliente (o que o outbound consumer roteia). */
  function menusNoCanal() {
    return kafka.events.filter(
      (e) => e.topic === "conversations.outbound" && e.message["type"] === "menu.payload",
    )
  }

  const MENU_MASCARADO = {
    session_id: SID,
    message:    "Informe o CVV",
    menu: {
      interaction:   "form",
      fields:        [{ id: "cvv", label: "CVV" }],
      masked_fields: ["cvv"],
      masked_types:  { cvv: "card_cvv" },
    },
  }

  beforeEach(() => {
    redis  = new RedisMock()
    kafka  = createCapturingKafkaProducer()
    const mcpServer = new McpServer({ name: "test-niv03", version: "0.0.1" })
    registerBpmTools(mcpServer, { redis, kafka })
    server = makeTestServer(mcpServer)
  })

  // ── o par recusar × entregar ───────────────────────────────────────────────

  it("RECUSA menu mascarado no whatsapp, e nada é publicado no canal", async () => {
    await comCanal("whatsapp")
    const res = await server.callTool("notification_send", MENU_MASCARADO)

    expect(res.isError).toBe(true)
    expect(res.content[0]!.text).toContain("masked_input_unsupported")
    // A asserção que importa: o menu NÃO viajou.
    expect(menusNoCanal()).toHaveLength(0)
  })

  it("ENTREGA o mesmo menu mascarado no webchat", async () => {
    await comCanal("webchat")
    const res = await server.callTool("notification_send", MENU_MASCARADO)

    expect(res.isError).toBeFalsy()
    expect(menusNoCanal()).toHaveLength(1)
    expect(menusNoCanal()[0]!.message["masked_fields"]).toEqual(["cvv"])
  })

  // ── o par mascarado × não mascarado ────────────────────────────────────────

  it("ENTREGA menu NÃO mascarado no whatsapp — o gate é sobre a máscara, não sobre o canal", async () => {
    await comCanal("whatsapp")
    const res = await server.callTool("notification_send", {
      session_id: SID,
      message:    "Escolha uma opção",
      menu:       { interaction: "button", options: [{ id: "a", label: "A" }] },
    })

    expect(res.isError).toBeFalsy()
    expect(menusNoCanal()).toHaveLength(1)
  })

  // ── o par cliente × operador ───────────────────────────────────────────────

  it("NÃO guarda menu mascarado com visibility agents_only — aquele não vai ao canal", async () => {
    await comCanal("whatsapp")
    const res = await server.callTool("notification_send", {
      ...MENU_MASCARADO,
      visibility: "agents_only",
    })

    expect(res.isError).toBeFalsy()
    // E segue sem publicar no canal do cliente, que é o desenho do agents_only.
    expect(menusNoCanal()).toHaveLength(0)
  })

  // ── sms e email caem no mesmo lado, e é a tabela que decide ────────────────

  it("RECUSA em sms e em email pelo mesmo predicado", async () => {
    for (const canal of ["sms", "email"]) {
      redis  = new RedisMock()
      kafka  = createCapturingKafkaProducer()
      const m = new McpServer({ name: "t", version: "0.0.1" })
      registerBpmTools(m, { redis, kafka })
      server = makeTestServer(m)
      await comCanal(canal)

      const res = await server.callTool("notification_send", MENU_MASCARADO)
      expect(res.isError, `canal ${canal} deveria recusar`).toBe(true)
      expect(menusNoCanal(), `canal ${canal} publicou`).toHaveLength(0)
    }
  })

  // ── a recusa tem de NOMEAR o conserto ──────────────────────────────────────

  it("a recusa nomeia o canal e os campos — sem eles o autor não sabe onde mexer", async () => {
    await comCanal("sms")
    const res = await server.callTool("notification_send", MENU_MASCARADO)
    const txt = res.content[0]!.text
    expect(txt).toContain("sms")
    expect(txt).toContain("cvv")
  })
})
