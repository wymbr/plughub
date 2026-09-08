/**
 * required-config.test.ts — o portão de parâmetro de deploy obrigatório.
 *
 * ⚠️ O caso que carrega peso NÃO é o de recusa — é `0 e false NÃO são vazios`.
 * A implementação óbvia (`if (!valor)`) reprova um `max_attempts: 0` e um
 * `dry_run: false`, que são valores legítimos, e é o defeito de truthiness que
 * este repositório já cataloga (`if not x` × `is None`, e o `??` × truthiness do
 * `instanceId`). Se alguém "simplificar" a função `vazio`, estes dois ficam
 * vermelhos antes de alguém descobrir pelo pool que não deploya.
 *
 * O segundo controle é `skill sem config_params passa`: 30 dos 31 slots vivos
 * estão nessa situação, e uma regra que os reprovasse seria desligada no mesmo dia.
 */

import { describe, it, expect } from "vitest"
import { judgeRequiredConfig } from "../lib/required-config"

const CTX = { poolId: "demo_ia", skillId: "skill_navegacao_v1" }

const OBRIG = [{ key: "form_id", label: "Árvore de navegação", required: true }]
const OPCIONAL = [{ key: "grain", required: false }]

describe("judgeRequiredConfig — quem NÃO é julgado", () => {
  it("skill sem config_params passa (a maioria viva)", () => {
    expect(judgeRequiredConfig(undefined, {}, CTX).kind).toBe("ok")
    expect(judgeRequiredConfig(null, {}, CTX).kind).toBe("ok")
    expect(judgeRequiredConfig([], {}, CTX).kind).toBe("ok")
  })

  it("parâmetro opcional ausente passa", () => {
    expect(judgeRequiredConfig(OPCIONAL, {}, CTX).kind).toBe("ok")
  })
})

describe("judgeRequiredConfig — recusa", () => {
  it("obrigatório ausente ⇒ block nomeando a chave e o rótulo", () => {
    const v = judgeRequiredConfig(OBRIG, {}, CTX)
    expect(v.kind).toBe("block")
    if (v.kind === "block") {
      expect(v.error).toBe("config_obrigatoria_ausente")
      expect(v.message).toContain("form_id")
      expect(v.message).toContain("Árvore de navegação")
      expect(v.message).toContain("demo_ia")
      expect(v.message).toContain("skill_navegacao_v1")
    }
  })

  it("string só de espaço é vazia — preencher sem preencher", () => {
    expect(judgeRequiredConfig(OBRIG, { form_id: "   " }, CTX).kind).toBe("block")
  })

  it("null e undefined são vazios", () => {
    expect(judgeRequiredConfig(OBRIG, { form_id: null }, CTX).kind).toBe("block")
    expect(judgeRequiredConfig(OBRIG, { form_id: undefined }, CTX).kind).toBe("block")
  })

  it("objeto e array vazios são vazios (o channel_policy salvo em branco)", () => {
    const p = [{ key: "channel_policy", required: true }]
    expect(judgeRequiredConfig(p, { channel_policy: {} }, CTX).kind).toBe("block")
    expect(judgeRequiredConfig(p, { channel_policy: [] }, CTX).kind).toBe("block")
  })

  it("config_json ausente ou não-objeto não absolve", () => {
    expect(judgeRequiredConfig(OBRIG, undefined, CTX).kind).toBe("block")
    expect(judgeRequiredConfig(OBRIG, "nao sou objeto", CTX).kind).toBe("block")
    expect(judgeRequiredConfig(OBRIG, [1, 2], CTX).kind).toBe("block")
  })

  it("nomeia TODOS os que faltam, não só o primeiro", () => {
    const p = [
      { key: "form_id", required: true },
      { key: "grain", required: false },
      { key: "outbound_pool", required: true },
    ]
    const v = judgeRequiredConfig(p, { grain: "session" }, CTX)
    expect(v.kind).toBe("block")
    if (v.kind === "block") {
      expect(v.message).toContain("form_id")
      expect(v.message).toContain("outbound_pool")
      expect(v.message).not.toContain("grain")
    }
  })
})

describe("judgeRequiredConfig — o controle que impede a 'simplificação'", () => {
  it("0 NÃO é vazio", () => {
    const p = [{ key: "max_attempts", required: true }]
    expect(judgeRequiredConfig(p, { max_attempts: 0 }, CTX).kind).toBe("ok")
  })

  it("false NÃO é vazio", () => {
    const p = [{ key: "dry_run", required: true }]
    expect(judgeRequiredConfig(p, { dry_run: false }, CTX).kind).toBe("ok")
  })

  it("preenchido de verdade passa", () => {
    expect(judgeRequiredConfig(OBRIG, { form_id: "dialog_x" }, CTX).kind).toBe("ok")
  })

  it("descritor malformado não derruba o deploy", () => {
    // Um `config_params` sem `key` (edição manual do skill) não pode virar 500
    // nem recusa muda: ele simplesmente não é julgável.
    const v = judgeRequiredConfig([{ required: true } as never, ...OBRIG], { form_id: "x" }, CTX)
    expect(v.kind).toBe("ok")
  })
})
