/**
 * possession-filter.test.ts — Fase D (D5) do ADR
 * `adr-work-item-requeue-and-agent-affinity.md`: **a tela não é fonte de posse**.
 *
 * `pool:pending_assignment:{pool}` sobrevive ao F5 e é reentregue ao agente que
 * reconecta. As duas guardas anteriores cobriam "sessão fechada" e "assignment de
 * outro agente", e NENHUMA cobria o caso do item de fila pull devolvido: a
 * workflow segue suspensa (sem `session:closed`) e o `instance_id` bate (é o mesmo
 * agente). Daí o formulário órfão na tela, com o mesmo id em CONTACTS e em
 * PULL QUEUES.
 *
 * O veredicto é o MESMO de 4 ramos do submit (Fase A) e do drop (Fase B) — a regra
 * de posse existe num formato só, e estes testes são o que impede os três de
 * divergirem.
 */

import { describe, it, expect } from "vitest"
import { shouldDropOnPossession } from "../lib/assignment-filter"

const ME     = "human-u-admin"
const THEIRS = "human-u-operator"

describe("shouldDropOnPossession", () => {
  it("entrega quando EU detenho o item (via lease)", () => {
    const d = shouldDropOnPossession(
      { found: true, instance_id: ME, via: "lease", in_queue: false }, ME,
    )
    expect(d.drop).toBe(false)
    expect(d.reason).toContain("held_by_me")
  })

  it("entrega quando EU detenho o item pelo registro durável (lease vencida)", () => {
    // O caso que mais importa depois da Fase A: passados 180 s a lease some, e só
    // o registro responde. Se este ramo descartasse, o F5 tardio deixaria o agente
    // sem o formulário de um item que ele DETÉM.
    const d = shouldDropOnPossession(
      { found: true, instance_id: ME, via: "record", in_queue: false }, ME,
    )
    expect(d.drop).toBe(false)
    expect(d.reason).toBe("held_by_me:record")
  })

  it("DESCARTA quando outro agente detém o item", () => {
    const d = shouldDropOnPossession(
      { found: true, instance_id: THEIRS, via: "record", in_queue: false }, ME,
    )
    expect(d.drop).toBe(true)
    expect(d.reason).toContain(THEIRS)
  })

  it("DESCARTA quando ninguém detém e o item está NA FILA", () => {
    // É o defeito que a Fase D fecha. Se este teste passar a esperar `false`,
    // o formulário órfão voltou.
    const d = shouldDropOnPossession(
      { found: false, in_queue: true }, ME,
    )
    expect(d.drop).toBe(true)
    expect(d.reason).toBe("back_in_queue")
  })

  it("entrega quando não há item de fila (contato push / encerrado / legado)", () => {
    // Ausência HONESTA — distinta de "ninguém detém". Descartar aqui quebraria o
    // reconnect de contato de cliente, que nunca teve claim.
    const d = shouldDropOnPossession(
      { found: false, in_queue: false }, ME,
    )
    expect(d.drop).toBe(false)
    expect(d.reason).toBe("no_work_item")
  })

  it("entrega quando o árbitro não respondeu — e diz que não conferiu", () => {
    // `null` ≠ "ninguém detém". Recusar reconexão por falha de rede seria pior que
    // o fail-open; o motivo no retorno é o que permite ao chamador avisar.
    const d = shouldDropOnPossession(null, ME)
    expect(d.drop).toBe(false)
    expect(d.reason).toBe("arbiter_unreachable")
  })

  it("nunca descarta por posse quando a conexão não tem identidade (cliente legado)", () => {
    // Mesma postura conservadora do `shouldDropAssignment`: sem
    // `expectedInstanceId` não há com o que comparar, e inventar a comparação
    // derrubaria a sessão de um cliente antigo.
    const d = shouldDropOnPossession(
      { found: true, instance_id: THEIRS, via: "lease", in_queue: false }, "",
    )
    expect(d.drop).toBe(false)
    expect(d.reason).toBe("legacy_client_no_identity")
  })

  it("descarta item na fila mesmo sem identidade da conexão", () => {
    // O ramo `back_in_queue` NÃO depende de identidade: item no ZSET não tem dono
    // nenhum, então não há a quem entregá-lo — nem ao cliente legado.
    const d = shouldDropOnPossession({ found: false, in_queue: true }, "")
    expect(d.drop).toBe(true)
    expect(d.reason).toBe("back_in_queue")
  })

  it("entrega quando o holder não traz instance_id (defensivo, não sobre-filtra)", () => {
    const d = shouldDropOnPossession({ found: true, in_queue: false }, ME)
    expect(d.drop).toBe(false)
  })
})
