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
    // `hset`/`hdel` faltavam neste mock, e o step os chama dentro de um try/catch:
    // a ausência virava exceção ENGOLIDA, então nenhum teste podia ver o registro
    // em `menu:waiting` — que é exatamente onde morava o defeito de 2026-08-24.
    hset:   vi.fn().mockResolvedValue(1),
    hdel:   vi.fn().mockResolvedValue(1),
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
      // T7-A: `true` deixou de ser aceito na ESCRITA. Este step exercita o
      // mascaramento step-level, que hoje se declara com um TIPO.
      masked:      "credential",
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
      masked:      "credential",
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
      masked:      "credential",
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
        { id: "senha", label: "Senha", type: "text", required: false, masked: "credential" },  // field-level, TIPADO (T6)
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
        { id: "token",   label: "Token",   type: "text", required: false, masked: "credential" },
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
      masked:      "credential",
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


// ─────────────────────────────────────────────────────────────────────────────
// Endereçamento da espera — o campo do hash e a chave do BLPOP TÊM de concordar
// ─────────────────────────────────────────────────────────────────────────────
//
// Defeito medido em runtime (2026-08-24): o agente de FILA é ativado de propósito
// com `instance_id=""` (`orchestrator-bridge/main.py:5952` — "queue agents don't
// hold a routing slot"). Duas derivações do MESMO `ctx.instanceId` discordavam
// sobre o que é "sem instância":
//
//   · campo do hash  `ctx.instanceId ?? "_default_"`   → `??` só pega null/undefined
//                                                        ⇒ com "" o campo nascia VAZIO
//   · chave do BLPOP `instanceId ? …suffix : …`        → truthiness
//                                                        ⇒ com "" ia p/ a chave sem sufixo
//
// Os leitores (bridge `main.py:9180`, mcp-server `server.ts:2471`) testam
// `!== "_default_"`, que é VERDADEIRO para "", então faziam LPUSH em
// `menu:result:{sid}:` — lista que ninguém consome. Resultado: o agente de fila
// ficava surdo à mensagem do cliente, sem erro em lugar nenhum, enquanto o sinal
// `__agent_available__` continuava chegando (o routing o publica na chave
// session-scoped hardcoded, que casa com o BLPOP). Meia funcionalidade viva foi o
// que manteve o defeito invisível por tanto tempo.
//
// A asserção é RELACIONAL, não literal: não interessa qual nome foi escolhido,
// interessa que os dois lados escolham o MESMO. Um teste que fixasse só o valor de
// `waitingField` passaria com a chave de BLPOP errada.

describe("executeMenu — endereçamento da espera (hash × BLPOP)", () => {

  async function capture(instanceId: string | undefined) {
    const step: MenuStep = {
      id:          "aguardar_mensagem",
      type:        "menu",
      prompt:      "Pode enviar sua mensagem.",
      interaction: "text",
      on_success:  "proximo",
      on_failure:  "falhou",
      timeout_s:   30,
      output_as:   "ultima_mensagem",
    }
    const ctx = makeCtx([`menu:result:s1`, "oi"], { instanceId } as Partial<StepContext>)
    await executeMenu(step, ctx)

    const redis = ctx.redis as any
    const blpopKey = (redis.blpop.mock.calls[0]?.[0] as string[] | undefined)?.[0]
    // Levantar, não coagir para "": um default aqui faria "o BLPOP nem aconteceu"
    // passar pela asserção de "chave sem sufixo" — o teste ficaria verde sobre um
    // step que não esperou por nada.
    if (typeof blpopKey !== "string") {
      throw new Error("executeMenu não chamou blpop com uma lista de chaves")
    }
    return {
      waitingKey:   redis.hset.mock.calls[0][0] as string,
      waitingField: redis.hset.mock.calls[0][1] as string,
      blpopKey,
    }
  }

  it("instância vazia: campo e chave concordam no ramo session-scoped", async () => {
    const { waitingKey, waitingField, blpopKey } = await capture("")

    expect(waitingKey).toBe("menu:waiting:s1")
    // O campo NUNCA pode ser a string vazia: os leitores tratam "" como nome de
    // instância legítimo e sufixam a chave com nada.
    expect(waitingField).not.toBe("")
    expect(waitingField).toBe("_default_")
    // E a chave do BLPOP tem de ser a sem sufixo — jamais terminada em ":".
    expect(blpopKey).toBe("menu:result:s1")
    expect(blpopKey.endsWith(":")).toBe(false)
  })

  it("instância ausente (undefined) se comporta como a vazia", async () => {
    const { waitingField, blpopKey } = await capture(undefined)
    expect(waitingField).toBe("_default_")
    expect(blpopKey).toBe("menu:result:s1")
  })

  it("instância nomeada: campo e chave usam o MESMO id", async () => {
    const { waitingField, blpopKey } = await capture("sac_ia-009")
    expect(waitingField).toBe("sac_ia-009")
    expect(blpopKey).toBe("menu:result:s1:sac_ia-009")
  })

  // A invariante que amarra os três casos. Sem ela, os testes acima só fixam
  // constantes e nada impede que uma das duas derivações mude sozinha de novo.
  it("INVARIANTE: campo é '_default_' se e somente se a chave não tem sufixo", async () => {
    for (const iid of ["", undefined, "inst-1", "sac_ia-009"]) {
      const { waitingField, blpopKey } = await capture(iid)
      const isDefaultField = waitingField === "_default_"
      const isBareKey      = blpopKey === "menu:result:s1"
      expect(isDefaultField).toBe(isBareKey)
    }
  })
})

// ─────────────────────────────────────────────
// F2 do ADR do catálogo de formatos — D5, `format` e D6
// ─────────────────────────────────────────────

function stepEscalar(v: MenuStep["validation"], retry?: MenuStep["retry"]): MenuStep {
  return {
    id: "coletar", type: "menu", prompt: "Informe",
    interaction: "text", on_success: "proximo", on_failure: "falhou",
    timeout_s: 30, output_as: "resposta",
    ...(v ? { validation: v } : {}),
    ...(retry ? { retry } : {}),
  } as MenuStep
}

describe("D5 — validar e reofertar sao dois fatos", () => {
  // A regressao que este bloco guarda e a mais tentadora do arco: ate 2026-09-04
  // `retryEnabled = maxAttempts > 1 && !!validation`, entao declarar validacao
  // SEM retry nao validava nada. `dialog_nps_v1` carregava {numeric, 0..10}
  // inerte por causa disso, e a tela que escreveu a regra nao dizia.

  it("recusa valor invalido MESMO sem retry declarado", async () => {
    const ctx = makeCtx([`menu:result:s1`, "abc"])
    const r = await executeMenu(stepEscalar({ numeric: true }), ctx)
    expect(r.transition_reason).toBe("on_failure")
    expect(r.next_step_id).toBe("falhou")
  })

  it("aceita valor valido sem retry declarado (controle positivo)", async () => {
    // Sem este caso o anterior passaria por recusar TUDO.
    const ctx = makeCtx([`menu:result:s1`, "42"])
    const r = await executeMenu(stepEscalar({ numeric: true }), ctx)
    expect(r.transition_reason).toBe("on_success")
    expect(r.output_value).toBe("42")
  })

  it("com retry, a recusa REOFERTA na mesma superficie antes de falhar", async () => {
    const ctx = makeCtx([`menu:result:s1`, "abc"])
    const r = await executeMenu(
      stepEscalar({ numeric: true }, { reprompt: "So numeros, por favor", max_attempts: 2 }),
      ctx,
    )
    expect(r.transition_reason).toBe("on_failure")
    const enviadas = (ctx.mcpCall as ReturnType<typeof vi.fn>).mock.calls
      .filter(c => c[0] === "notification_send")
    // duas: o prompt e o reprompt. Sem retry seria uma so.
    expect(enviadas.length).toBe(2)
    // A asserção de comprimento acima nao estreita o tipo para o compilador
    // (`noUncheckedIndexedAccess`), e o `?.` sozinho tornaria o teste incapaz de
    // reprovar: `undefined` casaria com um `toMatchObject` frouxo. Extrai-se e
    // exige-se presenca antes de julgar o conteudo.
    const reprompt = enviadas[1]
    expect(reprompt).toBeDefined()
    expect(reprompt?.[1]).toMatchObject({ message: "So numeros, por favor" })
  })

  it("sem validacao declarada, qualquer resposta passa (nao inventamos regra)", async () => {
    const ctx = makeCtx([`menu:result:s1`, "qualquer coisa"])
    const r = await executeMenu(stepEscalar(undefined), ctx)
    expect(r.transition_reason).toBe("on_success")
  })
})

describe("`format` resolve pelo catalogo", () => {
  it("date_br recusa 31/02/2026 — casa a FORMA e falha a semantica", async () => {
    const ctx = makeCtx([`menu:result:s1`, "31/02/2026"])
    const r = await executeMenu(stepEscalar({ format: "date_br" }), ctx)
    expect(r.transition_reason).toBe("on_failure")
  })

  it("date_br aceita 29/02/2024 (bissexto) — controle positivo", async () => {
    const ctx = makeCtx([`menu:result:s1`, "29/02/2024"])
    const r = await executeMenu(stepEscalar({ format: "date_br" }), ctx)
    expect(r.transition_reason).toBe("on_success")
  })

  it("formato desconhecido RECUSA, nunca libera", async () => {
    const ctx = makeCtx([`menu:result:s1`, "qualquer"])
    const r = await executeMenu(stepEscalar({ format: "nao_existe" }), ctx)
    expect(r.transition_reason).toBe("on_failure")
  })

  it("campo estreito aperta o formato, nunca o afrouxa", async () => {
    // `digits` aceitaria 123456; o max_length da pergunta o recusa.
    const ctx = makeCtx([`menu:result:s1`, "123456"])
    const r = await executeMenu(stepEscalar({ format: "digits", max_length: 4 }), ctx)
    expect(r.transition_reason).toBe("on_failure")
  })
})

describe("D6 — o campo de um form valida sozinho", () => {
  const stepForm = (campos: unknown[]): MenuStep => ({
    id: "dados", type: "menu", prompt: "Preencha",
    interaction: "form", on_success: "proximo", on_failure: "falhou",
    timeout_s: 30, output_as: "dados",
    fields: campos,
  } as unknown as MenuStep)

  const CAMPOS = [
    { id: "nascimento", label: "Nascimento", type: "text", required: true,
      validation: { format: "date_br" } },
    { id: "obs", label: "Observacao", type: "text", required: false },
  ]

  it("recusa quando um campo do form falha o formato, e NOMEIA o campo", async () => {
    const spy = vi.spyOn(console, "warn").mockImplementation(() => {})
    const ctx = makeCtx([`menu:result:s1`,
      JSON.stringify({ nascimento: "31/02/2026", obs: "oi" })])
    const r = await executeMenu(stepForm(CAMPOS), ctx)
    expect(r.transition_reason).toBe("on_failure")
    // "o formulario esta invalido" obriga quem responde a adivinhar qual campo e.
    expect(spy.mock.calls.flat().join(" ")).toContain("nascimento")
    spy.mockRestore()
  })

  it("aceita quando todos os campos passam (controle positivo)", async () => {
    const ctx = makeCtx([`menu:result:s1`,
      JSON.stringify({ nascimento: "01/01/2026", obs: "oi" })])
    const r = await executeMenu(stepForm(CAMPOS), ctx)
    expect(r.transition_reason).toBe("on_success")
  })

  it("campo NAO obrigatorio vazio nao reprova — formato nao e preenchimento", async () => {
    const campos = [{ id: "nascimento", label: "N", type: "text", required: false,
                      validation: { format: "date_br" } }]
    const ctx = makeCtx([`menu:result:s1`, JSON.stringify({ nascimento: "" })])
    const r = await executeMenu(stepForm(campos), ctx)
    expect(r.transition_reason).toBe("on_success")
  })

  it("campo OBRIGATORIO vazio reprova", async () => {
    const ctx = makeCtx([`menu:result:s1`, JSON.stringify({ obs: "so isso" })])
    const r = await executeMenu(stepForm(CAMPOS), ctx)
    expect(r.transition_reason).toBe("on_failure")
  })

  it("form sem campo declarando validacao segue passando", async () => {
    // Desfecho legitimo, e o teste existe para que ninguem "conserte" isso com
    // um default: nao ha o que julgar quando ninguem declarou regra.
    const campos = [{ id: "livre", label: "Livre", type: "text", required: false }]
    const ctx = makeCtx([`menu:result:s1`, JSON.stringify({ livre: "$$$" })])
    const r = await executeMenu(stepForm(campos), ctx)
    expect(r.transition_reason).toBe("on_success")
  })

  it("resposta de form que nao e JSON reprova quando ha regra a aplicar", async () => {
    const ctx = makeCtx([`menu:result:s1`, "isto nao e json"])
    const r = await executeMenu(stepForm(CAMPOS), ctx)
    expect(r.transition_reason).toBe("on_failure")
  })
})
