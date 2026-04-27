/**
 * steps/menu.test.ts
 * Tests for masked input routing in the menu step.
 * Spec: docs/guias/masked-input.md
 *
 * Covered:
 *   1. No masking — output_value returned normally
 *   2. step.masked:true (text interaction) — value goes to maskedScope, not output_value
 *   3. step.masked:true (form interaction) — all fields go to maskedScope
 *   4. form with mixed masked/non-masked fields — only non-masked in output_value
 *   5. field.masked:true overrides step.masked:false (field-level precedence)
 *   6. field.masked:false overrides step.masked:true (opt-out from step-level masking)
 *   7. on_timeout path (no masking involved)
 *   8. on_disconnect path (no masking involved)
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { executeMenu }                            from "../../steps/menu"
import type { StepContext }                       from "../../executor"
import type { MenuStep, PipelineState }           from "@plughub/schemas"

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function makeState(): PipelineState {
  return {
    flow_id:         "test_flow",
    current_step_id: "coletar",
    status:          "in_progress",
    started_at:      new Date().toISOString(),
    updated_at:      new Date().toISOString(),
    results:         {},
    retry_counters:  {},
    transitions:     [],
  }
}

function makeCtx(
  blpopReturn: [string, string] | null = [`menu:result:s1`, "resposta"],
  overrides: Partial<StepContext> = {},
): StepContext {
  const redisMock = {
    set:    vi.fn().mockResolvedValue("OK"),
    del:    vi.fn().mockResolvedValue(1),
    expire: vi.fn().mockResolvedValue(1),
    blpop:  vi.fn().mockResolvedValue(blpopReturn),
  }

  return {
    tenantId:             "tenant1",
    sessionId:            "s1",
    customerId:           "c1",
    sessionContext:       {},
    state:                makeState(),
    redis:                redisMock as any,
    mcpCall:              vi.fn().mockResolvedValue({ ok: true }),
    aiGatewayCall:        vi.fn(),
    saveState:            vi.fn().mockResolvedValue(undefined),
    retryStep:            vi.fn(),
    executeFallback:      vi.fn(),
    getJobId:             vi.fn().mockResolvedValue(null),
    setJobId:             vi.fn().mockResolvedValue(undefined),
    clearJobId:           vi.fn().mockResolvedValue(undefined),
    renewLock:            vi.fn().mockResolvedValue(true),
    maskedScope:          {},
    transactionOnFailure: null,
    ...overrides,
  }
}

// ─────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────

describe("executeMenu — masked input", () => {

  // ── 1. No masking — normal output ───────────────────────────────────────

  it("returns output_value normally when masked is not set", async () => {
    const step: MenuStep = {
      id:          "coletar_nome",
      type:        "menu",
      prompt:      "Qual seu nome?",
      interaction: "text",
      on_success:  "proximo",
      on_failure:  "falhou",
      timeout_s:   30,
      output_as:   "nome_cliente",
    }
    const ctx = makeCtx([`menu:result:s1`, "João"])
    const result = await executeMenu(step, ctx)

    expect(result.transition_reason).toBe("on_success")
    expect(result.output_as).toBe("nome_cliente")
    expect(result.output_value).toBe("João")
    expect(ctx.maskedScope).toEqual({})
  })

  // ── 2. step.masked:true (text) — value → maskedScope ──────────────────

  it("routes text response to maskedScope when step.masked:true", async () => {
    const step: MenuStep = {
      id:          "coletar_pin",
      type:        "menu",
      prompt:      "Digite seu PIN:",
      interaction: "text",
      masked:      true,
      on_success:  "validar",
      on_failure:  "falhou",
      timeout_s:   60,
      output_as:   "pin",
    }
    const ctx = makeCtx([`menu:result:s1`, "1234"])
    const result = await executeMenu(step, ctx)

    expect(result.transition_reason).toBe("on_success")
    // Masked value must NOT be in pipeline_state
    expect(result.output_value).toBeUndefined()
    expect(result.output_as).toBeUndefined()
    // Masked value must be in maskedScope
    expect(ctx.maskedScope["pin"]).toBe("1234")
  })

  it("uses step.id as maskedScope key when output_as is absent", async () => {
    const step: MenuStep = {
      id:          "coletar_senha",
      type:        "menu",
      prompt:      "Digite sua senha:",
      interaction: "text",
      masked:      true,
      on_success:  "validar",
      on_failure:  "falhou",
      timeout_s:   60,
    }
    const ctx = makeCtx([`menu:result:s1`, "minhaSenha"])
    await executeMenu(step, ctx)

    expect(ctx.maskedScope["coletar_senha"]).toBe("minhaSenha")
  })

  // ── 3. step.masked:true (form) — all fields → maskedScope ──────────────

  it("routes all form fields to maskedScope when step.masked:true", async () => {
    const formResponse = JSON.stringify({ senha: "abc123", pin: "9999" })
    const step: MenuStep = {
      id:          "coletar_credenciais",
      type:        "menu",
      prompt:      "Informe suas credenciais:",
      interaction: "form",
      masked:      true,
      fields: [
        { id: "senha", label: "Senha", type: "text", required: false },
        { id: "pin",   label: "PIN",   type: "text", required: false },
      ],
      on_success:  "validar",
      on_failure:  "falhou",
      timeout_s:   60,
      output_as:   "credenciais",
    }
    const ctx = makeCtx([`menu:result:s1`, formResponse])
    const result = await executeMenu(step, ctx)

    expect(result.transition_reason).toBe("on_success")
    expect(result.output_value).toBeUndefined()
    expect(result.output_as).toBeUndefined()
    expect(ctx.maskedScope["senha"]).toBe("abc123")
    expect(ctx.maskedScope["pin"]).toBe("9999")
  })

  // ── 4. form with mix — non-masked fields in output_value ───────────────

  it("separates masked and non-masked form fields correctly", async () => {
    const formResponse = JSON.stringify({ nome: "João", senha: "secreta", cpf: "12345" })
    const step: MenuStep = {
      id:          "coletar_dados",
      type:        "menu",
      prompt:      "Preencha o formulário:",
      interaction: "form",
      fields: [
        { id: "nome",  label: "Nome",  type: "text", required: false                  },  // not masked
        { id: "senha", label: "Senha", type: "text", required: false, masked: true    },  // field-level masked
        { id: "cpf",   label: "CPF",   type: "text", required: false                  },  // not masked
      ],
      on_success:  "proximo",
      on_failure:  "falhou",
      timeout_s:   60,
      output_as:   "dados",
    }
    const ctx = makeCtx([`menu:result:s1`, formResponse])
    const result = await executeMenu(step, ctx)

    expect(result.transition_reason).toBe("on_success")
    // Non-masked fields in output
    expect(result.output_as).toBe("dados")
    expect((result.output_value as Record<string, string>)["nome"]).toBe("João")
    expect((result.output_value as Record<string, string>)["cpf"]).toBe("12345")
    expect((result.output_value as Record<string, string>)["senha"]).toBeUndefined()
    // Masked field in maskedScope
    expect(ctx.maskedScope["senha"]).toBe("secreta")
    expect(ctx.maskedScope["nome"]).toBeUndefined()
  })

  // ── 5. field.masked:true overrides step.masked:false ───────────────────

  it("masks field when field.masked:true even if step.masked is not set", async () => {
    const formResponse = JSON.stringify({ usuario: "alice", token: "tk-secret" })
    const step: MenuStep = {
      id:          "coletar_acesso",
      type:        "menu",
      prompt:      "Acesse:",
      interaction: "form",
      fields: [
        { id: "usuario", label: "Usuário", type: "text", required: false                },
        { id: "token",   label: "Token",   type: "text", required: false, masked: true  },
      ],
      on_success:  "proximo",
      on_failure:  "falhou",
      timeout_s:   30,
      output_as:   "acesso",
    }
    const ctx = makeCtx([`menu:result:s1`, formResponse])
    const result = await executeMenu(step, ctx)

    expect((result.output_value as Record<string, string>)["usuario"]).toBe("alice")
    expect((result.output_value as Record<string, string>)["token"]).toBeUndefined()
    expect(ctx.maskedScope["token"]).toBe("tk-secret")
  })

  // ── 6. field.masked:false opts out when step.masked:true ───────────────

  it("does NOT mask field when field.masked:false even if step.masked:true", async () => {
    const formResponse = JSON.stringify({ nome: "Bob", senha: "p@ssword" })
    const step: MenuStep = {
      id:          "formulario",
      type:        "menu",
      prompt:      "Formulário:",
      interaction: "form",
      masked:      true,
      fields: [
        { id: "nome",  label: "Nome",  type: "text", required: false, masked: false },  // explicit opt-out
        { id: "senha", label: "Senha", type: "text", required: false                },  // inherits step.masked
      ],
      on_success:  "proximo",
      on_failure:  "falhou",
      timeout_s:   30,
      output_as:   "resultado",
    }
    const ctx = makeCtx([`menu:result:s1`, formResponse])
    const result = await executeMenu(step, ctx)

    // nome was opted out of masking → stays in output
    expect((result.output_value as Record<string, string>)["nome"]).toBe("Bob")
    // senha inherits step.masked:true → goes to maskedScope
    expect(ctx.maskedScope["senha"]).toBe("p@ssword")
    expect((result.output_value as Record<string, string>)["senha"]).toBeUndefined()
  })

  // ── 7. on_timeout path ──────────────────────────────────────────────────

  it("returns on_timeout when blpop returns null", async () => {
    const step: MenuStep = {
      id:          "aguardar",
      type:        "menu",
      prompt:      "Aguardando...",
      interaction: "text",
      on_success:  "proximo",
      on_failure:  "falhou",
      on_timeout:  "timeout_step",
      timeout_s:   10,
    }
    const ctx = makeCtx(null)  // blpop returns null → timeout
    const result = await executeMenu(step, ctx)

    expect(result.next_step_id).toBe("timeout_step")
    expect(result.transition_reason).toBe("on_failure")
  })

  // ── 8. on_disconnect path ───────────────────────────────────────────────

  it("returns on_disconnect when session:closed key is popped", async () => {
    const step: MenuStep = {
      id:            "aguardar",
      type:          "menu",
      prompt:        "Aguardando...",
      interaction:   "text",
      on_success:    "proximo",
      on_failure:    "falhou",
      on_disconnect: "desconectou",
      timeout_s:     30,
    }
    // blpop returns the closedKey
    const ctx = makeCtx([`session:closed:s1`, "closed"])
    const result = await executeMenu(step, ctx)

    expect(result.next_step_id).toBe("desconectou")
    expect(result.transition_reason).toBe("on_failure")
  })
})
