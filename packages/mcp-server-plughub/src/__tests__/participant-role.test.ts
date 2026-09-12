/**
 * participant-role.test.ts
 * O papel de participação lido do roster, e o gate de @mention que o consome.
 * Spec: docs/guias/mention-protocol.md · ficha MEN-01/MEN-02 (decidida 2026-09-12)
 *
 * ── O que estes testes existem para impedir ──────────────────────────────────
 * O resolvedor devolve `primary` como DEFAULT quando não consegue ler — por causa
 * dos consumidores históricos (author do stream, decisão de mascaramento). Um gate
 * de autorização que ignorasse `resolved` autorizaria exatamente nos casos em que
 * não sabe de nada: roster ausente, TTL vencido, participante fora do roster. O
 * caso `primary + não-resolvido ⇒ NÃO autoriza` é a razão de ser deste arquivo.
 *
 * O segundo é o eixo: a regra é POSIÇÃO (quem conduz menciona), nunca espécie. Um
 * `specialist` não convida — seja ele humano ou IA.
 */

import { describe, it, expect } from "vitest"
import {
  resolveParticipantRole,
  resolveRoleByInstance,
  mayRouteMentions,
}                               from "../lib/participant-role"

/** Redis mínimo: só o `get` que o resolvedor usa. */
function fakeRedis(valor: string | null) {
  return { get: async (_k: string) => valor } as never
}

const SID = "11111111-2222-3333-4444-555555555555"
const PID = "human-c30b50d9-6e78-45c1-9adb-d4b635428e96"

/** Entrada de roster no formato que o orchestrator-bridge realmente escreve. */
function roster(entradas: Array<{ pid: string; role: string; tipo?: string }>) {
  return JSON.stringify(entradas.map(e => ({
    session_id:     SID,
    participant_id: e.pid,
    instance_id:    e.pid,          // medido em 2026-09-12: os dois são iguais
    role:           e.role,
    agent_type:     e.tipo ?? "human",
    pool_id:        "retencao_humano",
  })))
}

describe("resolveParticipantRole", () => {

  it("lê o papel do roster e marca como RESOLVIDO", async () => {
    const r = await resolveParticipantRole(
      fakeRedis(roster([{ pid: PID, role: "primary" }])), SID, PID,
    )
    expect(r).toEqual({ role: "primary", resolved: true })
  })

  it("lê `specialist` sem convertê-lo em primary", async () => {
    const r = await resolveParticipantRole(
      fakeRedis(roster([{ pid: PID, role: "specialist" }])), SID, PID,
    )
    expect(r).toEqual({ role: "specialist", resolved: true })
  })

  it("acha o participante certo num roster com vários", async () => {
    const raw = roster([
      { pid: "wrapup_detached_ia-002", role: "primary", tipo: "native" },
      { pid: PID,                      role: "specialist" },
    ])
    const r = await resolveParticipantRole(fakeRedis(raw), SID, PID)
    expect(r).toEqual({ role: "specialist", resolved: true })
  })

  // ── as três formas de NÃO saber ───────────────────────────────────────────
  // Todas caem no mesmo par: default `primary`, `resolved: false`. É o `false` que
  // carrega a informação; o `primary` é herança dos consumidores históricos.

  it("roster AUSENTE ⇒ não-resolvido", async () => {
    const r = await resolveParticipantRole(fakeRedis(null), SID, PID)
    expect(r).toEqual({ role: "primary", resolved: false })
  })

  it("participante FORA do roster ⇒ não-resolvido", async () => {
    const raw = roster([{ pid: "outro-participante", role: "primary" }])
    const r = await resolveParticipantRole(fakeRedis(raw), SID, PID)
    expect(r).toEqual({ role: "primary", resolved: false })
  })

  it("roster ilegível ⇒ não-resolvido, sem estourar", async () => {
    const r = await resolveParticipantRole(fakeRedis("{não é json"), SID, PID)
    expect(r).toEqual({ role: "primary", resolved: false })
  })
})

describe("mayRouteMentions — quem conduz menciona; quem foi convidado não convida", () => {

  it("PRIMARY resolvido autoriza", () => {
    expect(mayRouteMentions({ role: "primary", resolved: true })).toBe(true)
  })

  // ⚠️ O teste que justifica o arquivo. O default do resolvedor é `primary`, então
  // um gate que olhasse só o `role` autorizaria justamente quando não sabe nada —
  // e falharia ABERTO em roster ausente, TTL vencido e participante fora do roster,
  // que são as três situações mais prováveis de acontecer em produção.
  it("primary NÃO-RESOLVIDO não autoriza — o gate falha FECHADO", () => {
    expect(mayRouteMentions({ role: "primary", resolved: false })).toBe(false)
  })

  it.each(["specialist", "supervisor", "evaluator", "reviewer", "queue"])(
    "%s não convida, mesmo resolvido",
    (papel) => {
      expect(mayRouteMentions({ role: papel, resolved: true })).toBe(false)
    },
  )

  // O eixo é POSIÇÃO, não espécie: o `agent_type` (`human`/`native`/`ai`) vive na
  // MESMA entrada do roster e NÃO entra nesta decisão. A IA que conduz a conversa é
  // a `primary` e menciona; o humano convidado como especialista não menciona.
  // Foi afirmar o contrário em quatro lugares, sem mecanismo, que gerou a MEN-01.
  it("não lê espécie: a decisão depende só do par (role, resolved)", () => {
    expect(mayRouteMentions({ role: "primary", resolved: true })).toBe(true)
    expect(mayRouteMentions({ role: "specialist", resolved: true })).toBe(false)
  })

  // `human` era o segundo valor aceito pelo gate até 2026-09-12 e NUNCA existiu no
  // domínio de papel — o roster escreve `primary`/`specialist`, a spec acrescenta
  // `supervisor`/`evaluator`. Se alguém o reintroduzir por engano, reprova aqui.
  it("`human` não é papel e não autoriza", () => {
    expect(mayRouteMentions({ role: "human", resolved: true })).toBe(false)
  })
})

describe("resolveRoleByInstance — a identidade que o chamador NÃO escolhe", () => {

  // O `message_send` recebe `participant_id` no INPUT e verifica o `session_token` ao
  // lado. Resolver o papel pelo primeiro deixa qualquer portador de token nomear um
  // participante que seja `primary` no roster e mencionar como ele — o defeito
  // estrutural que derrubou o gate do avaliador (CAP-01). Por isso o gate de @mention
  // casa pelo `instance_id` que viaja ASSINADO.

  it("casa pelo instance_id e resolve o papel", async () => {
    const raw = roster([{ pid: PID, role: "specialist" }])
    const r = await resolveRoleByInstance(fakeRedis(raw), SID, PID)
    expect(r).toEqual({ role: "specialist", resolved: true })
  })

  it("instance_id vazio ⇒ não-resolvido (não há identidade a conferir)", async () => {
    const raw = roster([{ pid: PID, role: "primary" }])
    const r = await resolveRoleByInstance(fakeRedis(raw), SID, "")
    expect(r).toEqual({ role: "primary", resolved: false })
  })

  // ⚠️ O teste que fecha o furo: um chamador que NOMEIE outro participante não é
  // resolvido, porque a busca é pelo campo `instance_id` e o que ele declarou não
  // viaja assinado. Sem fallback para `participant_id` — um fallback aqui devolveria
  // ao chamador a escolha da identidade, que é o que esta função existe para tirar.
  it("instância que não está no roster não resolve, mesmo havendo um primary lá",
    async () => {
      const raw = roster([{ pid: "quem-conduz-de-verdade", role: "primary" }])
      const r = await resolveRoleByInstance(fakeRedis(raw), SID, "instancia-forasteira")
      expect(r).toEqual({ role: "primary", resolved: false })
      expect(mayRouteMentions(r)).toBe(false)
    })

  it("roster ausente ⇒ não-resolvido", async () => {
    const r = await resolveRoleByInstance(fakeRedis(null), SID, PID)
    expect(r).toEqual({ role: "primary", resolved: false })
  })
})
