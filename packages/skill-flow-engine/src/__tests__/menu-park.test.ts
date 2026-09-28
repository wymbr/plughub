/**
 * DUR-01 F1 — o `menu` ESTACIONA em vez de bloquear (`menuWait: "park"`).
 *
 * Contra Redis REAL (`REDIS_URL`, db 15): a peça que decide é um Lua atômico — soltar
 * o lock e marcar `parked` só com a caixa vazia —, e um mock sem Lua não testaria
 * atomicidade nenhuma, só a minha leitura dela. Sem `REDIS_URL` a suíte aparece como
 * PULADA, nunca verde.
 *
 * O que cada caso prova, e o que o faria reprovar:
 *  1. estaciona: devolve `awaiting_input`, SEM lock, com `parked`, prazo no ZSET e o
 *     `menu:waiting` ainda de pé (o bridge e a voz dependem dele);
 *  2. acordar consome a resposta SEM reenviar o prompt — é o registro de espera que diz
 *     "o prompt já foi"; sem ele o cliente seria perguntado de novo;
 *  3. a corrida: resposta chegando entre a leitura da caixa e o estacionamento NÃO fica
 *     órfã — o Lua recusa estacionar e o mesmo run a processa;
 *  4. prazo vencido e caixa vazia ⇒ `on_timeout`; fechamento ⇒ `on_disconnect`;
 *  5. contadores sobrevivem entre execuções: 3 respostas fora da opção, em 3 runs,
 *     esgotam os 2 reenvios e saem por `on_invalid` (sem persistir, reenviaria sempre);
 *  6. ciclo: revisitar o menu por TRANSIÇÃO manda o prompt de novo (a sentinela
 *     `__menu_wait__` é limpa pelo `addTransition`);
 *  7. dentro de `begin_transaction` o menu BLOQUEIA, mesmo com `park` pedido;
 *  8. o Lua isolado: `input_pending` mantém o lock; `lock_lost` não toca em nada.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from "vitest"
import Redis from "ioredis"
import type { SkillFlow } from "@plughub/schemas"
import { SkillFlowEngine } from "../engine"
import { PipelineStateManager, MENU_DEADLINES_KEY } from "../state"
import { redisKeys } from "../redis-keys"
import { waitRecordKey } from "../steps/menu"

const REDIS_URL = process.env["REDIS_URL"]
const T    = "tenant_park"
const INST = "sac_ia-001"

const lockKey    = (sid: string) => `${T}:pipeline:${sid}:running`
const parkedKey  = (sid: string) => `${T}:pipeline:${sid}:parked`
const stateKey   = (sid: string) => `${T}:pipeline:${sid}`

const simples: SkillFlow = {
  entry: "pergunta",
  steps: [
    { id: "pergunta", type: "menu", interaction: "text", prompt: "Qual o seu pedido?",
      timeout_s: 60, output_as: "resposta",
      on_success: "fim", on_timeout: "expirou", on_disconnect: "saiu", on_failure: "falhou" },
    { id: "fim",     type: "complete", outcome: "resolved" },
    { id: "expirou", type: "complete", outcome: "abandoned" },
    { id: "saiu",    type: "complete", outcome: "callback" },
    { id: "falhou",  type: "complete", outcome: "failed" },
  ],
} as unknown as SkillFlow

const botoes: SkillFlow = {
  entry: "escolha",
  steps: [
    { id: "escolha", type: "menu", interaction: "button", prompt: "Escolha:",
      options: [{ id: "a", label: "A" }, { id: "b", label: "B" }], timeout_s: 60,
      output_as: "opcao", on_success: "fim", on_invalid: "invalido", on_failure: "falhou" },
    { id: "fim",      type: "complete", outcome: "resolved" },
    { id: "invalido", type: "complete", outcome: "escalated" },
    { id: "falhou",   type: "complete", outcome: "failed" },
  ],
} as unknown as SkillFlow

const ciclo: SkillFlow = {
  entry: "pergunta",
  steps: [
    { id: "pergunta", type: "menu", interaction: "text", prompt: "De novo?", timeout_s: 60,
      output_as: "resposta", on_success: "decide", on_failure: "falhou" },
    { id: "decide", type: "choice",
      conditions: [{ field: "$.pipeline_state.resposta", operator: "eq", value: "de novo", next: "pergunta" }],
      default: "fim" },
    { id: "fim",    type: "complete", outcome: "resolved" },
    { id: "falhou", type: "complete", outcome: "failed" },
  ],
} as unknown as SkillFlow

const transacao: SkillFlow = {
  entry: "tx",
  steps: [
    { id: "tx", type: "begin_transaction", on_failure: "falhou" },
    { id: "senha", type: "menu", interaction: "text", prompt: "Senha:", timeout_s: 60,
      masked: "credential", output_as: "senha", on_success: "tx_fim", on_failure: "falhou" },
    { id: "tx_fim", type: "end_transaction", result_as: "tx_result", on_success: "fim" },
    { id: "fim",    type: "complete", outcome: "resolved" },
    { id: "falhou", type: "complete", outcome: "failed" },
  ],
} as unknown as SkillFlow

describe.skipIf(!REDIS_URL)("DUR-01 — menu estacionado (Redis real)", () => {
  let redis: Redis
  let prompts: Array<Record<string, unknown>>

  beforeAll(() => { redis = new Redis(`${REDIS_URL}/15`) })
  afterAll(async () => { await redis.quit() })
  beforeEach(async () => {
    await redis.flushdb()
    prompts = []
  })

  function engine(r: Redis = redis) {
    return new SkillFlowEngine({
      redis: r as never,
      mcpCall: async (tool, input) => {
        if (tool === "notification_send") prompts.push(input as Record<string, unknown>)
        return {}
      },
      aiGatewayCall: async () => ({}),
    })
  }

  async function rodar(sid: string, flow: SkillFlow, e = engine(), menuWait: "block" | "park" = "park") {
    const r = await e.run({
      tenantId: T, sessionId: sid, customerId: "c1", instanceId: INST,
      skillId: "skill_teste", flow, sessionContext: {}, menuWait,
    })
    if (!("outcome" in r)) throw new Error(`run sem outcome: ${JSON.stringify(r)}`)
    return r
  }

  const responder = (sid: string, texto: string) => redis.lpush(redisKeys.menuResult(sid, INST), texto)

  it("1. estaciona: sem lock, com parked, prazo no ZSET e menu:waiting de pé", async () => {
    const r = await rodar("s1", simples)
    expect(r.outcome).toBe("awaiting_input")
    expect(prompts).toHaveLength(1)
    expect(await redis.exists(lockKey("s1"))).toBe(0)
    const parked = JSON.parse((await redis.get(parkedKey("s1")))!)
    expect(parked.step_id).toBe("pergunta")
    expect(parked.instance_id).toBe(INST)
    const score = await redis.zscore(MENU_DEADLINES_KEY(T), "s1")
    expect(Number(score)).toBeGreaterThan(Date.now() + 50_000)
    expect(await redis.hexists(redisKeys.menuWaiting("s1"), INST)).toBe(1)
    const state = JSON.parse((await redis.get(stateKey("s1")))!)
    expect(state.status).toBe("in_progress")
    expect(state.current_step_id).toBe("pergunta")
  })

  it("2. acordar consome a resposta SEM reenviar o prompt, e limpa o estacionamento", async () => {
    await rodar("s2", simples)
    await responder("s2", "segunda via")
    const r = await rodar("s2", simples)
    expect(r.outcome).toBe("resolved")
    expect(r.pipeline_state.results["resposta"]).toBe("segunda via")
    expect(prompts).toHaveLength(1)
    expect(await redis.exists(parkedKey("s2"))).toBe(0)
    expect(await redis.zscore(MENU_DEADLINES_KEY(T), "s2")).toBeNull()
    expect(await redis.hexists(redisKeys.menuWaiting("s2"), INST)).toBe(0)
    expect(await redis.exists(lockKey("s2"))).toBe(0)
  })

  it("2b. acordar sem nada na caixa estaciona de novo, sem gastar prompt", async () => {
    await rodar("s2b", simples)
    const r = await rodar("s2b", simples)
    expect(r.outcome).toBe("awaiting_input")
    expect(prompts).toHaveLength(1)
    expect(await redis.exists(parkedKey("s2b"))).toBe(1)
  })

  it("3. a corrida: resposta entre a leitura e o estacionamento não fica órfã", async () => {
    // Um cliente Redis cujo PRIMEIRO eval (o do park) é precedido pela resposta.
    const r2 = new Redis(`${REDIS_URL}/15`)
    const original = r2.eval.bind(r2)
    let injetado = false
    vi.spyOn(r2, "eval").mockImplementation((async (...a: unknown[]) => {
      if (!injetado && String(a[0]).includes("llen")) {
        injetado = true
        await redis.lpush(redisKeys.menuResult("s3", INST), "chegou junto")
      }
      return (original as (...x: unknown[]) => Promise<unknown>)(...a)
    }) as never)
    try {
      const r = await rodar("s3", simples, engine(r2))
      expect(injetado).toBe(true)
      expect(r.outcome).toBe("resolved")
      expect(r.pipeline_state.results["resposta"]).toBe("chegou junto")
      expect(prompts).toHaveLength(1)
      expect(await redis.exists(parkedKey("s3"))).toBe(0)
    } finally {
      await r2.quit()
    }
  })

  it("4a. prazo vencido com a caixa vazia ⇒ on_timeout", async () => {
    await rodar("s4", simples)
    const state = JSON.parse((await redis.get(stateKey("s4")))!)
    state.results[waitRecordKey("pergunta")].deadline_ms = Date.now() - 1
    await redis.set(stateKey("s4"), JSON.stringify(state))
    const r = await rodar("s4", simples)
    expect(r.outcome).toBe("abandoned")
    expect(prompts).toHaveLength(1)
  })

  it("4b. fechamento do contato ⇒ on_disconnect", async () => {
    await rodar("s4b", simples)
    await redis.lpush(redisKeys.sessionClosed("s4b"), "1")
    const r = await rodar("s4b", simples)
    expect(r.outcome).toBe("callback")
  })

  it("5. os reenvios fora da opção sobrevivem entre execuções e esgotam em on_invalid", async () => {
    await rodar("s5", botoes)
    for (const esperado of [2, 3]) {
      await responder("s5", "x")
      const r = await rodar("s5", botoes)
      expect(r.outcome).toBe("awaiting_input")
      expect(prompts).toHaveLength(esperado)   // o menu é REENVIADO a cada recusa
    }
    await responder("s5", "x")
    const r = await rodar("s5", botoes)
    expect(r.outcome).toBe("escalated")
    expect(prompts).toHaveLength(3)
  })

  it("6. ciclo: revisitar o menu por transição manda o prompt de novo", async () => {
    await rodar("s6", ciclo)
    await responder("s6", "de novo")
    const r = await rodar("s6", ciclo)
    expect(r.outcome).toBe("awaiting_input")
    expect(prompts).toHaveLength(2)
    await responder("s6", "chega")
    expect((await rodar("s6", ciclo)).outcome).toBe("resolved")
  })

  it("7. dentro de begin_transaction BLOQUEIA, mesmo com park pedido", async () => {
    // A resposta chega DEPOIS que o menu já está esperando: bloqueando, o BLPOP a
    // recebe no mesmo run; estacionando, o run voltaria `awaiting_input` antes dela —
    // e o valor mascarado se perderia entre execuções. (Com a resposta já na lista
    // antes do run, os dois modos a leriam na hora e o teste não distinguiria nada.)
    // Conexão PRÓPRIA para a resposta tardia: o BLPOP ocupa a do engine, e um LPUSH
    // na mesma conexão ficaria na fila atrás dele até o prazo do menu.
    const cliente = new Redis(`${REDIS_URL}/15`)
    const tardia = setTimeout(() => { void cliente.lpush(redisKeys.menuResult("s7", INST), "1234") }, 300)
    const r = await rodar("s7", transacao).finally(async () => {
      clearTimeout(tardia)
      await cliente.quit()
    })
    expect(r.outcome).toBe("resolved")
    expect(await redis.exists(parkedKey("s7"))).toBe(0)
    expect(JSON.stringify(r.pipeline_state.results)).not.toContain("1234")
  })

  it("9. acordar atrasado (wakeOnly) não recomeça o fluxo depois que ele terminou", async () => {
    await rodar("s9", simples)
    await responder("s9", "ok")
    expect((await rodar("s9", simples)).outcome).toBe("resolved")
    const r = await engine().run({
      tenantId: T, sessionId: "s9", customerId: "c1", instanceId: INST,
      skillId: "skill_teste", flow: simples, sessionContext: {}, menuWait: "park", wakeOnly: true,
    })
    expect("error" in r && r.error).toBe("NOT_PARKED")
    expect(prompts).toHaveLength(1)            // sem wakeOnly, a saudação sairia de novo
    expect(await redis.exists(lockKey("s9"))).toBe(0)
  })

  it("9b. wakeOnly numa sessão sem pipeline não nasce nada", async () => {
    const r = await engine().run({
      tenantId: T, sessionId: "s9b", customerId: "c1", instanceId: INST,
      skillId: "skill_teste", flow: simples, sessionContext: {}, menuWait: "park", wakeOnly: true,
    })
    expect("error" in r && r.error).toBe("NOT_PARKED")
    expect(prompts).toHaveLength(0)
    expect(await redis.exists(stateKey("s9b"))).toBe(0)
  })

  describe("8. o Lua do estacionamento, isolado", () => {
    const sm = () => new PipelineStateManager(redis as never)
    const req = (keys: string[]) => ({ step_id: "p", watch_keys: keys, deadline_ms: Date.now() + 1000 })

    it("caixa com item ⇒ input_pending, e o lock CONTINUA de quem o tinha", async () => {
      await redis.set(lockKey("l1"), INST)
      await redis.lpush("caixa:l1", "oi")
      expect(await sm().park(T, "l1", INST, req(["caixa:l1"]))).toBe("input_pending")
      expect(await redis.get(lockKey("l1"))).toBe(INST)
      expect(await redis.exists(parkedKey("l1"))).toBe(0)
    })

    it("caixa vazia ⇒ parked: lock solto, marca e prazo gravados", async () => {
      await redis.set(lockKey("l2"), INST)
      expect(await sm().park(T, "l2", INST, req(["caixa:l2"]))).toBe("parked")
      expect(await redis.exists(lockKey("l2"))).toBe(0)
      expect(await redis.exists(parkedKey("l2"))).toBe(1)
      expect(await redis.zscore(MENU_DEADLINES_KEY(T), "l2")).not.toBeNull()
    })

    it("lock de outra execução ⇒ lock_lost, e nada é tocado", async () => {
      await redis.set(lockKey("l3"), "outra-001")
      expect(await sm().park(T, "l3", INST, req(["caixa:l3"]))).toBe("lock_lost")
      expect(await redis.get(lockKey("l3"))).toBe("outra-001")
      expect(await redis.exists(parkedKey("l3"))).toBe(0)
    })

    it("sem prazo (espera infinita) não entra no ZSET", async () => {
      await redis.set(lockKey("l4"), INST)
      await sm().park(T, "l4", INST, { step_id: "p", watch_keys: ["caixa:l4"], deadline_ms: null })
      expect(await redis.zscore(MENU_DEADLINES_KEY(T), "l4")).toBeNull()
    })
  })
})
