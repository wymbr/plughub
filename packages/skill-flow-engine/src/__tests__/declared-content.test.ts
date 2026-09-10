/**
 * declared-content.test.ts — a rede não palpita sobre o ROTEIRO.
 *
 * ── O defeito que estes casos fixam, medido em 2026-09-10 ───────────────────
 *
 * Num contato real do `limite_ia` o cliente recebeu
 *
 *     "Qual é o seu CPF? (só números, ex: (##) ****-####)"
 *
 * enquanto a fonte semeada (`infra/dialog/dialog_limite_roteiro.json:25`) diz
 *
 *     "Qual é o seu CPF? (só números, ex: 52998224725)"
 *
 * A rede de texto livre mascarou o EXEMPLO do próprio roteiro, em voo — e ainda o
 * tipou errado: o padrão de CPF exige pontuação, então 11 dígitos crus casaram o de
 * TELEFONE. O cliente leu um gabarito de telefone onde se pedia um CPF.
 *
 * ⚠️ **O caso que decide não é o primeiro.** "Roteiro carimbado atravessa intacto"
 * sozinho ficaria verde numa implementação que simplesmente DESLIGASSE a rede — e
 * essa implementação devolveria o CPF do cliente ao operador. Por isso cada isenção
 * aqui vem em par com o seu controle: a MESMA string, no MESMO estado, sem o carimbo
 * (ou numa chave que o cliente escreveu), continua sendo mascarada.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { DECLARED_CONTENT_TOOLS } from "@plughub/schemas"
import type { InvokeStep, PipelineState, SkillFlow } from "@plughub/schemas"
import { filtrarTextoLivre }    from "../ctx-audit"
import { PipelineStateManager } from "../state"
import { executeInvoke }        from "../steps/invoke"
import { SkillFlowEngine }      from "../engine"
import type { StepContext }     from "../executor"

/** A linha literal do roteiro semeado. Se ela mudar na fonte, este teste tem de mudar junto. */
const ROTEIRO = "Qual é o seu CPF? (só números, ex: 52998224725)"

const CLIENTE = { stepType: "notify", visibility: "all", stepId: "perguntar" }

function estado(results: Record<string, unknown>): PipelineState {
  return {
    flow_id:         "f",
    current_step_id: "x",
    status:          "in_progress",
    started_at:      new Date().toISOString(),
    updated_at:      new Date().toISOString(),
    results,
    retry_counters:  {},
    transitions:     [],
  } as unknown as PipelineState
}

// ─────────────────────────────────────────────
// 1 · A rede × o carimbo
// ─────────────────────────────────────────────

const espiaInfo = () => vi.spyOn(console, "info").mockImplementation(() => {})

describe("a REDE dispensa conteúdo declarado — e só ele", () => {
  let i: ReturnType<typeof espiaInfo>
  beforeEach(() => { i = espiaInfo() })
  afterEach(() => { i.mockRestore() })

  const REF = "$.pipeline_state.dialog.render.prompt"

  it("SEM carimbo, o roteiro é mascarado — é o defeito de 2026-09-10, virado teste", () => {
    // Controle que dá sentido a todos os outros: prova que este caminho PODE ficar
    // vermelho, e mostra exatamente o dano medido (telefone sobre um pedido de CPF).
    const fora = String(filtrarTextoLivre(ROTEIRO, CLIENTE, REF))
    expect(fora).not.toBe(ROTEIRO)
    expect(fora).toContain("(##) ****-####")
  })

  it("COM carimbo, o roteiro atravessa byte a byte", () => {
    const fora = filtrarTextoLivre(ROTEIRO, CLIENTE, REF, new Set(["dialog"]))
    expect(fora).toBe(ROTEIRO)
  })

  it("a isenção é REGISTRADA — 'não olhou' tem de se distinguir de 'não achou'", () => {
    // ⚠️ Raiz PRÓPRIA de propósito: `registra` emite uma linha por combinação
    // distinta POR PROCESSO, então reusar `dialog` aqui mediria se outro caso deste
    // arquivo já logou — e passaria ou falharia pela ordem dos testes, não pelo
    // produto.
    filtrarTextoLivre(ROTEIRO, CLIENTE, "$.pipeline_state.roteiro_do_log.prompt",
      new Set(["roteiro_do_log"]))
    expect(i.mock.calls.flat().join(" ")).toContain("REDE DISPENSADA")
  })

  it("⚠️ a isenção NÃO vaza para a chave vizinha — a resposta do cliente continua mascarada", () => {
    // O carimbo é de `dialog`; `answers` é o que o cliente digitou, no MESMO estado.
    // Sem este caso, isentar o pipeline inteiro passaria em todos os outros.
    const resposta = "meu cpf é 529.883.653-09"
    const fora = String(filtrarTextoLivre(
      resposta, CLIENTE, "$.pipeline_state.answers.cpf", new Set(["dialog"])))
    expect(fora).not.toContain("529.883.653-09")
  })

  it("carimbo AUSENTE é restritivo — quem não sabe da proveniência não isenta", () => {
    expect(filtrarTextoLivre(ROTEIRO, CLIENTE, REF, undefined)).not.toBe(ROTEIRO)
    expect(filtrarTextoLivre(ROTEIRO, CLIENTE, REF, new Set())).not.toBe(ROTEIRO)
  })

  it("a raiz é a do PIPELINE_STATE — `$.session.dialog` não herda a isenção", () => {
    // `session.*` e `config.*` não passam por `setResult` e não têm carimbo nenhum.
    // Casar por nome de chave, e não pela origem, daria isenção a quem só se parece.
    const fora = filtrarTextoLivre(ROTEIRO, CLIENTE, "$.session.dialog.prompt", new Set(["dialog"]))
    expect(fora).not.toBe(ROTEIRO)
  })

  it("a forma em colchetes resolve a mesma raiz que o ponto", () => {
    const fora = filtrarTextoLivre(
      ROTEIRO, CLIENTE, "$.pipeline_state['dialog'].render.prompt", new Set(["dialog"]))
    expect(fora).toBe(ROTEIRO)
  })
})

// ─────────────────────────────────────────────
// 2 · O carimbo × quem escreve a chave
// ─────────────────────────────────────────────

describe("o carimbo é fato do ESCRITOR, e some quando outro escreve", () => {
  it("`setResult(declarado)` carimba a chave", () => {
    const s = PipelineStateManager.setResult(estado({}), "dialog", { render: {} }, true)
    expect([...PipelineStateManager.chavesDeclaradas(s)]).toEqual(["dialog"])
  })

  it("⚠️ reescrever a MESMA chave sem o carimbo o REMOVE — carimbo obsoleto é o pior desfecho", () => {
    // Um `menu` que reaproveitasse a chave de um `form_get` herdaria a isenção, e a
    // resposta do cliente sairia crua com selo de conteúdo declarado. A remoção é por
    // construção: quem escreve sem afirmar, desafirma.
    const a = PipelineStateManager.setResult(estado({}), "dialog", { render: {} }, true)
    const b = PipelineStateManager.setResult(a, "dialog", { cpf: "529.883.653-09" }, false)
    expect(PipelineStateManager.chavesDeclaradas(b).has("dialog")).toBe(false)
    expect(b.results[PipelineStateManager.CHAVE_DECLARADO]).toBeUndefined()
  })

  it("escrever OUTRA chave não mexe no carimbo existente", () => {
    const a = PipelineStateManager.setResult(estado({}), "dialog", { render: {} }, true)
    const b = PipelineStateManager.setResult(a, "answers", { cpf: "x" }, false)
    expect(PipelineStateManager.chavesDeclaradas(b).has("dialog")).toBe(true)
    expect(PipelineStateManager.chavesDeclaradas(b).has("answers")).toBe(false)
  })

  it("o default de `setResult` é NÃO declarado — a isenção se afirma, não se herda", () => {
    const s = PipelineStateManager.setResult(estado({}), "dialog", { render: {} })
    expect(PipelineStateManager.chavesDeclaradas(s).size).toBe(0)
  })

  it("estado sem a chave reservada devolve conjunto vazio, não explode", () => {
    expect(PipelineStateManager.chavesDeclaradas(estado({})).size).toBe(0)
    expect(PipelineStateManager.chavesDeclaradas(
      estado({ [PipelineStateManager.CHAVE_DECLARADO]: "não é array" })).size).toBe(0)
  })
})

// ─────────────────────────────────────────────
// 3 · A allowlist, e o `invoke` que a consulta
// ─────────────────────────────────────────────

describe("quem tem direito ao carimbo", () => {
  it("`form_get` sim; tool de DOMÍNIO não", () => {
    // O CRM devolve dado de pessoa — ali a rede é justamente o que se quer.
    expect(DECLARED_CONTENT_TOOLS.has("form_get")).toBe(true)
    expect(DECLARED_CONTENT_TOOLS.has("customer_get")).toBe(false)
  })

  function ctxDe(mcpCall: ReturnType<typeof vi.fn>): StepContext {
    const c = {
      tenantId: "t", sessionId: "s", customerId: "c",
      sessionContext: {}, state: estado({}), redis: {} as never,
      mcpCall, aiGatewayCall: vi.fn(),
      saveState: vi.fn().mockResolvedValue(undefined),
      retryStep: vi.fn(), executeFallback: vi.fn(),
      getJobId: vi.fn().mockResolvedValue(null),
      setJobId: vi.fn(), clearJobId: vi.fn(),
      renewLock: vi.fn().mockResolvedValue(true),
      maskedScope: {}, transactionOnFailure: null,
    } as unknown as StepContext
    return c
  }

  const passo = (tool: string): InvokeStep => ({
    id: "carregar_form", type: "invoke", tool,
    input: {}, output_as: "dialog",
    on_success: "seguir", on_failure: "falhar",
  } as unknown as InvokeStep)

  it("o `invoke` de `form_get` declara o resultado", async () => {
    const r = await executeInvoke(passo("form_get"), ctxDe(vi.fn().mockResolvedValue({ render: {} })))
    expect(r.output_declared).toBe(true)
  })

  it("o `invoke` de qualquer outra tool NÃO declara", async () => {
    const r = await executeInvoke(passo("customer_get"), ctxDe(vi.fn().mockResolvedValue({ cpf: "x" })))
    expect(r.output_declared).toBe(false)
  })

  it("o `on_failure` não declara — payload de erro não é roteiro", async () => {
    const r = await executeInvoke(
      passo("form_get"), ctxDe(vi.fn().mockRejectedValue(new Error("boom"))))
    expect(r.output_declared).toBeUndefined()
  })
})

// ─────────────────────────────────────────────
// 4 · A COSTURA — o caso que prova a fiação inteira
// ─────────────────────────────────────────────

/**
 * Os blocos acima provam cada peça. Nenhum deles pega o elo que o defeito real
 * atravessou: `invoke` → `engine.setResult` → `interpolate` → rede. Uma linha
 * esquecida no engine deixaria todos verdes e o cliente continuaria lendo
 * `(##) ****-####`. Este caso roda o flow de verdade.
 */
describe("de ponta a ponta: o roteiro chega ao cliente como foi escrito", () => {
  const redis = {
    get:  vi.fn().mockResolvedValue(null),
    set:  vi.fn().mockResolvedValue("OK"),
    del:  vi.fn().mockResolvedValue(1),
    eval: vi.fn().mockResolvedValue(1),
  }

  const flow = (tool: string): SkillFlow => ({
    entry: "carregar_form",
    steps: [
      {
        id: "carregar_form", type: "invoke", tool,
        input: {}, output_as: "dialog",
        on_success: "perguntar", on_failure: "fim",
      },
      {
        id: "perguntar", type: "notify",
        message: "{{$.pipeline_state.dialog.render.prompt}}",
        visibility: "all",
        on_success: "fim", on_failure: "fim",
      },
      { id: "fim", type: "complete", outcome: "resolved" },
    ],
  } as unknown as SkillFlow)

  async function mensagemAoCliente(tool: string): Promise<string> {
    const mcpCall = vi.fn().mockImplementation(async (nome: string) =>
      nome === tool ? { render: { prompt: ROTEIRO } } : { ok: true })
    const engine = new SkillFlowEngine({
      redis: redis as never, mcpCall, aiGatewayCall: vi.fn(),
    })
    await engine.run({
      tenantId: "tenant-test", sessionId: "sess-1", customerId: "cli-1",
      skillId: "skill_teste", flow: flow(tool), sessionContext: {},
    })
    const envio = mcpCall.mock.calls.find(c => c[0] === "notification_send")
    return String((envio?.[1] as { message?: unknown })?.message ?? "")
  }

  beforeEach(() => { vi.clearAllMocks(); redis.get.mockResolvedValue(null); redis.set.mockResolvedValue("OK") })

  it("vindo de `form_get`, o exemplo de CPF sai INTEIRO", async () => {
    expect(await mensagemAoCliente("form_get")).toBe(ROTEIRO)
  })

  it("⚠️ vindo de outra tool, a MESMA string é mascarada — a fiação não isenta tudo", async () => {
    // Controle positivo da costura: o mesmo flow, o mesmo texto, o mesmo caminho —
    // só a proveniência muda. Sem ele, um engine que ignorasse `output_declared` e
    // desligasse a rede passaria no caso de cima.
    const fora = await mensagemAoCliente("customer_get")
    expect(fora).not.toBe(ROTEIRO)
    expect(fora).toContain("(##) ****-####")
  })
})
