/**
 * platform-console-tags.test.ts — o pacote do formulário chega a quem passou pelo
 * portão de POOL, qualquer que seja o papel (CNS-24).
 *
 * O defeito: a CNS-11 moveu `session.dialog_form_id` e o token de retomada para
 * `core.workflow.*`, e o portão de namespace do operador (`["service","session"]`)
 * passou a escondê-los. O wrap-up do operador abria como contato vazio.
 *
 * ⚠️ O caso que decide não é "as tags estão na lista". É o da SOBRESCRITA: um pool
 * que declara o próprio `operator_allow_tags` SUBSTITUI o default, e se o pacote do
 * formulário morasse no default, o primeiro pool configurado trancaria o operador
 * outra vez, sem erro. Hoje nenhum pool sobrescreve — por isso o caso existe.
 */
import { describe, it, expect } from "vitest"
import { PLATFORM_CONSOLE_TAGS, withPlatformConsoleTags } from "../lib/context-masking"

/** O que o `DialogFormRenderer` da Console lê fora de `session.*`. */
const O_QUE_A_CONSOLE_LE = [
  "core.workflow.dialog_form_id",          // isFormFillSnapshot
  "core.workflow.delegate_resume_token",   // resumeTokenOf, delegate-conference
  "core.workflow.resume_token",            // resumeTokenOf, webhook delegate
]

describe("o pacote do formulário é da PLATAFORMA", () => {
  it("cobre tudo o que a Console lê fora de `session.*`", () => {
    for (const tag of O_QUE_A_CONSOLE_LE) expect(PLATFORM_CONSOLE_TAGS).toContain(tag)
  })

  it("pool SEM lista própria recebe o pacote — o caso do defeito", () => {
    expect(withPlatformConsoleTags([])).toEqual(expect.arrayContaining(O_QUE_A_CONSOLE_LE))
  })

  it("⚠️ pool que SOBRESCREVE a lista não tira o pacote — somar, nunca substituir", () => {
    const doPool = ["caller.customer_id", "session.algo_do_tenant"]
    const fora = withPlatformConsoleTags(doPool)
    expect(fora).toEqual(expect.arrayContaining([...doPool, ...O_QUE_A_CONSOLE_LE]))
  })

  it("as tags do pool sobrevivem, e nada se repete", () => {
    const fora = withPlatformConsoleTags(["caller.customer_id", "core.workflow.dialog_form_id"])
    expect(fora).toContain("caller.customer_id")
    expect(new Set(fora).size).toBe(fora.length)
  })
})

describe("o que o pacote NÃO é", () => {
  it("são tags EXATAS — nunca curinga, nunca o namespace `core` inteiro", () => {
    // Abrir `core.*` entregaria ao operador fila, ETA e ids de contato. Um
    // "simplificador" que trocasse a lista por `core` passaria nos casos acima.
    for (const tag of PLATFORM_CONSOLE_TAGS) {
      expect(tag.split(".")).toHaveLength(3)
      expect(tag).not.toMatch(/\*/)
    }
    expect(PLATFORM_CONSOLE_TAGS).not.toContain("core")
  })

  it("é congelada — ninguém a estende em runtime por acidente", () => {
    expect(Object.isFrozen(PLATFORM_CONSOLE_TAGS)).toBe(true)
  })
})
