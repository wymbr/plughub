/**
 * profile-steps.test.ts — o portão de perfil × step (CTR-01 / G1).
 *
 * ⚠️ O caso que carrega peso NÃO é o de recusa — é o `delegate em perfil de
 * agente PASSA`. Ele é o controle que prova que a lista foi corrigida por
 * medição e não por prosa: se alguém reintroduzir `delegate` na lista de
 * proibidos do agente (que é o que o comentário do schema mandava fazer até
 * 2026-09-06), este teste fica vermelho e nomeia os dois pools vivos.
 */

import { describe, it, expect } from "vitest"
import { judgeProfileSteps } from "../lib/profile-steps"
import { skillProfileFor, forbiddenStepsForProfile } from "@plughub/schemas"

const CTX = { poolId: "p_teste", skillId: "skill_teste" }

const flow = (...tipos: string[]) => ({
  steps: tipos.map((t, i) => ({ id: `s${i}`, type: t })),
})

describe("skillProfileFor", () => {
  it("webhook ⇒ workflow", () => {
    expect(skillProfileFor(["webhook"])).toBe("workflow")
    expect(skillProfileFor(["webchat", "webhook"])).toBe("workflow")
  })
  it("demais canais ⇒ agent", () => {
    expect(skillProfileFor(["webchat", "whatsapp"])).toBe("agent")
  })
  // Ausência resolve para o lado RESTRITIVO. ⚠️ População medida em 2026-09-06:
  // 0 de 39 pools sem `channel_types` — o ramo existe por desenho, não por caso.
  it("ausência ⇒ agent (lado restritivo, sem população hoje)", () => {
    expect(skillProfileFor([])).toBe("agent")
    expect(skillProfileFor(undefined)).toBe("agent")
    expect(skillProfileFor(null)).toBe("agent")
  })
})

describe("judgeProfileSteps — recusa", () => {
  it("suspend em pool de agente ⇒ block", () => {
    const v = judgeProfileSteps(flow("menu", "suspend"), ["webchat"], CTX)
    expect(v.kind).toBe("block")
    if (v.kind === "block") {
      expect(v.error).toBe("step_fora_do_perfil")
      expect(v.message).toContain("suspend")
    }
  })
  it("menu em pool webhook ⇒ block", () => {
    const v = judgeProfileSteps(flow("invoke", "menu"), ["webhook"], CTX)
    expect(v.kind).toBe("block")
    if (v.kind === "block") expect(v.message).toContain("menu")
  })
  it("a mensagem nomeia TODOS os tipos ofensores, não só o primeiro", () => {
    const v = judgeProfileSteps(flow("suspend", "collect", "menu"), ["webchat"], CTX)
    if (v.kind !== "block") throw new Error("esperava block")
    expect(v.message).toContain("collect")
    expect(v.message).toContain("suspend")
  })
})

describe("judgeProfileSteps — deixa passar", () => {
  // ── O CONTROLE. Ver o cabeçalho. ──
  it("delegate em perfil de AGENTE passa — é caminho vivo (limite_ia, 186 segmentos)", () => {
    expect(judgeProfileSteps(flow("menu", "delegate"), ["webchat", "whatsapp"], CTX).kind).toBe("ok")
    expect(forbiddenStepsForProfile("agent", [{ type: "delegate" }])).toEqual([])
  })
  it("fluxo conforme ⇒ ok", () => {
    expect(judgeProfileSteps(flow("menu", "invoke", "complete"), ["webchat"], CTX).kind).toBe("ok")
    expect(judgeProfileSteps(flow("suspend", "invoke"), ["webhook"], CTX).kind).toBe("ok")
  })
  // Snapshot ausente é do `skill_sem_definicao`, uma linha acima no set-next —
  // dois erros para o mesmo defeito diriam a coisa errada.
  it("snapshot ausente ou sem steps ⇒ ok (o erro é de outro portão)", () => {
    expect(judgeProfileSteps(null, ["webchat"], CTX).kind).toBe("ok")
    expect(judgeProfileSteps({}, ["webchat"], CTX).kind).toBe("ok")
    expect(judgeProfileSteps({ steps: "nao-e-array" }, ["webchat"], CTX).kind).toBe("ok")
  })
})
