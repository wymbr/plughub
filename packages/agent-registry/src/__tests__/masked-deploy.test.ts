/**
 * masked-deploy.test.ts
 * NIV-03 (metade de DEPLOY) — skill que mascara × canais do pool.
 *
 * POR QUE ESTE TESTE EXISTE. A metade de runtime (guarda no `notification_send`)
 * fecha o vazamento, mas só quando já existe cliente do outro lado. Um pool cujos
 * canais NENHUM sabe mascarar é um deploy que não funciona em contato algum, e
 * isso é decidível estaticamente — no único instante em que há um humano olhando.
 *
 * Os pares que importam, e por que cada metade sozinha engana:
 *
 *   * bloquear × avisar — um veredicto que só bloqueasse proibiria o pool
 *     multicanal legítimo (coleta sensível no webchat, resto no whatsapp), que é
 *     exatamente a configuração de `auth_ia`/`auth_form_ia` hoje. Um que só
 *     avisasse deixaria passar o deploy impossível.
 *   * mascara × não mascara — sem o segundo, um veredicto que bloqueasse todo
 *     deploy em pool sem webchat ficaria verde e derrubaria o parque inteiro.
 *   * canais declarados × canais AUSENTES — pool sem `channel_types` não afirma
 *     "não atendo canal nenhum": afirma que ninguém declarou. Converter omissão em
 *     recusa é o modo de falha oposto, e igualmente caro.
 */

import { describe, it, expect } from "vitest"
import { judgeMaskedDeploy, maskedDeclarations } from "../lib/masked-deploy"

const CTX = { poolId: "pool_x", skillId: "skill_x" }

const FLOW_MASCARA_NO_STEP = {
  steps: [
    { id: "saudacao",     type: "notify" },
    { id: "coletar_pin",  type: "menu", masked: "credential" },
  ],
}

const FLOW_MASCARA_NO_CAMPO = {
  steps: [
    {
      id: "coletar_dados", type: "menu",
      fields: [
        { id: "email", label: "Email" },
        { id: "senha", label: "Senha", masked: "credential" },
      ],
    },
  ],
}

const FLOW_LIMPO = { steps: [{ id: "menu", type: "menu", fields: [{ id: "opcao" }] }] }

// ── maskedDeclarations — o par detectar × não inventar ──────────────────────

describe("maskedDeclarations", () => {
  it("acha máscara no nível do STEP", () => {
    expect(maskedDeclarations(FLOW_MASCARA_NO_STEP)).toEqual(["coletar_pin"])
  })

  it("acha máscara no nível do CAMPO, e rotula onde mexer", () => {
    expect(maskedDeclarations(FLOW_MASCARA_NO_CAMPO)).toEqual(["coletar_dados.senha"])
  })

  it("não inventa máscara em fluxo limpo", () => {
    expect(maskedDeclarations(FLOW_LIMPO)).toEqual([])
    expect(maskedDeclarations(null)).toEqual([])
    expect(maskedDeclarations({})).toEqual([])
  })

  it("respeita o override explícito `false`", () => {
    // `false` é a única forma de dizer "este campo NÃO mascara"; tratá-lo como
    // declaração inverteria o que o autor escreveu.
    expect(maskedDeclarations({
      steps: [{ id: "s", masked: false, fields: [{ id: "f", masked: false }] }],
    })).toEqual([])
  })
})

// ── judgeMaskedDeploy — bloquear × avisar × deixar em paz ───────────────────

describe("judgeMaskedDeploy", () => {
  it("BLOQUEIA quando nenhum canal do pool sabe mascarar", () => {
    const v = judgeMaskedDeploy(FLOW_MASCARA_NO_STEP, ["whatsapp", "sms"], CTX)
    expect(v.kind).toBe("block")
    if (v.kind === "block") {
      expect(v.error).toBe("masked_sem_canal_capaz")
      // A mensagem nomeia ONDE mexer — sem isso o autor procura no lugar errado.
      expect(v.message).toContain("coletar_pin")
      expect(v.message).toContain("whatsapp")
      expect(v.message).toContain("webchat")
    }
  })

  it("AVISA (não bloqueia) no pool parcialmente capaz — é a config de auth_ia hoje", () => {
    const v = judgeMaskedDeploy(FLOW_MASCARA_NO_STEP, ["webchat", "whatsapp"], CTX)
    expect(v.kind).toBe("warn")
    if (v.kind === "warn") expect(v.warning).toContain("whatsapp")
  })

  it("deixa passar quando TODOS os canais sabem mascarar", () => {
    expect(judgeMaskedDeploy(FLOW_MASCARA_NO_STEP, ["webchat"], CTX).kind).toBe("ok")
  })

  it("deixa passar fluxo que não mascara, mesmo em pool sem canal capaz", () => {
    // O par obrigatório: sem ele, um veredicto que bloqueasse todo pool sem
    // webchat passaria no primeiro caso e derrubaria o parque.
    expect(judgeMaskedDeploy(FLOW_LIMPO, ["whatsapp", "sms"], CTX).kind).toBe("ok")
  })

  it("pool SEM channel_types declarado não é bloqueado — omissão não é zero", () => {
    expect(judgeMaskedDeploy(FLOW_MASCARA_NO_STEP, [], CTX).kind).toBe("ok")
    expect(judgeMaskedDeploy(FLOW_MASCARA_NO_STEP, undefined, CTX).kind).toBe("ok")
  })

  it("máscara de CAMPO conta igual à de step para o veredicto", () => {
    expect(judgeMaskedDeploy(FLOW_MASCARA_NO_CAMPO, ["sms"], CTX).kind).toBe("block")
  })
})
