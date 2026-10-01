/**
 * ALW-18 (e WCH-02) — o histórico do Console é a projeção do stream canônico.
 *
 * Proposições, cada uma com o controle ao lado:
 *   - chamada começou e não terminou → ativa; terminou → não; nunca houve → não; contato fechou → não;
 *   - entram cliente, agente de IA, agente humano, supervisor e o aviso de sistema, NA ORDEM do
 *     stream, com o autor certo — o humano pela instância `human-*`, nunca por consulta a Redis;
 *   - o texto é o `content` (mascarado), NUNCA o `original_content`;
 *   - ficam de fora: fala transcrita do cliente, prompt de menu, mensagem dirigida que não inclui o
 *     cliente; e mensagem dirigida AO cliente entra (controle da regra anterior);
 *   - entrada malformada não derruba a projeção.
 */
import { describe, it, expect } from "vitest"
import { projectStreamForConsole } from "../lib/console-history"
import type { RawStreamEntry } from "../lib/console-history"

let n = 0
const e = (f: Record<string, string>): RawStreamEntry =>
  [`${++n}-0`, Object.entries(f).flat()]

const call = (state: string) => e({ type: "media.call", state, payload: JSON.stringify({ state }) })
const msg = (role: string, instance: string, text: string, opts: {
  visibility?: string, contentType?: string, original?: string, type?: string,
} = {}) => e({
  type:        opts.type ?? "message",
  timestamp:   `2026-09-28T10:00:${String(n).padStart(2, "0")}Z`,
  author_id:   instance,
  author_role: role,
  author:      JSON.stringify({ participant_id: instance, instance_id: instance, role }),
  visibility:  opts.visibility ?? JSON.stringify("all"),
  event_id:    `ev-${text}`,
  payload:     JSON.stringify({
    message_id: `m-${text}`,
    content:    { type: opts.contentType ?? "text", text },
    ...(opts.original ? { original_content: { type: "text", text: opts.original }, masked: true } : {}),
  }),
})

describe("projectStreamForConsole — estado da chamada (WCH-02)", () => {
  it("chamada iniciada e não encerrada → ativa", () => {
    expect(projectStreamForConsole([call("started")]).callActive).toBe(true)
  })

  it("controles: encerrada, nunca houve, contato fechado → inativa", () => {
    expect(projectStreamForConsole([call("started"), call("ended")]).callActive).toBe(false)
    expect(projectStreamForConsole([]).callActive).toBe(false)
    expect(projectStreamForConsole([call("started"), e({ type: "session_closed" })]).callActive).toBe(false)
  })

  it("segunda chamada no mesmo contato: vale a ÚLTIMA", () => {
    expect(projectStreamForConsole([call("started"), call("ended"), call("started")]).callActive).toBe(true)
  })

  it("state só no payload (sem campo plano) também é lido", () => {
    const só = e({ type: "media.call", payload: JSON.stringify({ state: "started" }) })
    expect(projectStreamForConsole([só]).callActive).toBe(true)
  })
})

describe("projectStreamForConsole — a conversa (ALW-18)", () => {
  it("todos os autores, na ordem do stream, com o autor certo", () => {
    const r = projectStreamForConsole([
      msg("system", "routing-engine", "Aguardando", { type: "system_notice" }),
      msg("customer", "cli-1", "oi"),
      msg("primary", "demo_ia-009", "Ola, sou a IA"),
      msg("primary", "human-7f3a", "Ola, sou o atendente"),
      msg("supervisor", "sup-1", "nota", { visibility: "agents_only" }),
    ])
    expect(r.messages.map(m => [m.author, m.text])).toEqual([
      ["system", "Aguardando"],
      ["customer", "oi"],
      ["agent_ai", "Ola, sou a IA"],
      ["agent_human", "Ola, sou o atendente"],
      ["supervisor", "nota"],
    ])
    expect(r.messages[4]).toMatchObject({ id: "m-nota", visibility: "agents_only" })
  })

  it("o texto é o content mascarado — o original NUNCA sai", () => {
    const r = projectStreamForConsole([msg("customer", "cli-1", "meu cpf [cpf:tk_ab12:***00]", {
      original: "meu cpf 123.456.789-00",
    })])
    expect(r.messages).toHaveLength(1)
    expect(JSON.stringify(r)).not.toContain("123.456.789-00")
    expect(r.messages[0]!.text).toContain("[cpf:tk_ab12:***00]")
  })

  it("fala transcrita do cliente fica de fora; o texto digitado do mesmo cliente entra (controle)", () => {
    const r = projectStreamForConsole([
      msg("customer", "cli-1", "Atendente.", { contentType: "audio_transcript" }),
      msg("customer", "cli-1", "4821"),
    ])
    expect(r.messages.map(m => m.text)).toEqual(["4821"])
  })

  it("prompt de menu (interaction_request) fica de fora", () => {
    const r = projectStreamForConsole([msg("primary", "demo_ia-009", "Como posso ajudar?", { type: "interaction_request" })])
    expect(r.messages).toEqual([])
  })

  it("dirigida SEM o cliente fica de fora; dirigida AO cliente entra (controle)", () => {
    const r = projectStreamForConsole([
      msg("customer", "cli-1", "oi"),
      msg("supervisor", "sup-1", "privada", { visibility: JSON.stringify(["human-7f3a"]) }),
      msg("specialist", "nps-001", "De 0 a 10?", { visibility: JSON.stringify(["cli-1"]) }),
    ])
    expect(r.messages.map(m => m.text)).toEqual(["oi", "De 0 a 10?"])
    expect(r.messages[1]!.visibility).toEqual(["cli-1"])
  })

  it("entrada malformada não derruba a projeção", () => {
    const lixo = e({ type: "message", author: "{", payload: "não é json" })
    expect(projectStreamForConsole([lixo, call("started")])).toEqual({ callActive: true, messages: [] })
  })
})

describe("projectStreamForConsole — anexo do cliente (VOZ-28)", () => {
  it("o anexo gravado pelo bridge chega ao Console junto com o indicador", () => {
    const att = { media_type: "document", file_id: "f-1", url: "http://gw/webchat/v1/attachments/f-1" }
    const anexo = e({
      type: "message", author_id: "c-1", author_role: "customer",
      author: JSON.stringify({ participant_id: "c-1", instance_id: "c-1", role: "customer" }),
      visibility: JSON.stringify("all"),
      payload: JSON.stringify({ message_id: "m-anexo", content: { type: "text", text: "[Anexo: contrato.pdf]", attachment: att } }),
    })
    const [m] = projectStreamForConsole([anexo]).messages
    expect(m.text).toBe("[Anexo: contrato.pdf]")
    // ATT-06: a entrada antiga gravou o link da porta pública; ele não chega ao Console
    expect(m.attachment).toEqual({ media_type: "document", file_id: "f-1" })
  })

  it("controle: mensagem sem anexo não ganha o campo", () => {
    const [m] = projectStreamForConsole([msg("customer", "c-1", "oi")]).messages
    expect("attachment" in m).toBe(false)
  })
})
