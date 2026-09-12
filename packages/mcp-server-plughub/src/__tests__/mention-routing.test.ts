/**
 * mention-routing.test.ts
 * O comando de @mention como EVENTO: registro durável + aviso ao emissor.
 * Spec: docs/guias/mention-protocol.md · fichas MEN-05/MEN-06 (decididas 2026-09-12)
 *
 * ── O que estes testes existem para impedir ──────────────────────────────────
 * A MEN-05 tirou o `@alias` do texto da mensagem. Isso fechou o vazamento da
 * sintaxe interna ao cliente — e, sem mais nada, teria apagado a única casa em que
 * a AUTORIA do convite existia: o `participant_joined` do convidado registra quem
 * ENTROU, nunca quem PEDIU. O evento `mention_command` no stream é essa casa, e é
 * durável; o `mention.ack` é feedback e é efêmero. Os dois são testados aqui
 * separadamente porque confundi-los é o risco que a MEN-06 nomeou.
 */

import { describe, it, expect, vi } from "vitest"
import { routeMentions }            from "../lib/mention-routing"

const TENANT = "tenant_demo"
const SID    = "11111111-2222-3333-4444-555555555555"
const EMISSOR = "human-c30b50d9-6e78-45c1-9adb-d4b635428e96"

/**
 * Redis mínimo que grava o que foi chamado. `xadd` variádico como o real — é por
 * ele que `writeStreamEntry` escreve, e é nele que o evento durável aparece.
 */
function fakeRedis(opts: { mentionable?: Record<string, string> } = {}) {
  const xadds:    unknown[][] = []
  const publishes: Array<{ canal: string; msg: Record<string, unknown> }> = []
  return {
    xadds, publishes,
    get: async (k: string) => {
      if (k === `${TENANT}:pool_config:retencao_humano`) {
        return JSON.stringify({ mentionable_pools: opts.mentionable ?? { auth_form: "auth_form_ia" } })
      }
      if (k === `session:${SID}:meta`) {
        return JSON.stringify({ customer_id: "cus_1", channel: "webchat" })
      }
      return null
    },
    hget: async () => null,
    xadd: async (...args: unknown[]) => { xadds.push(args); return "1-0" },
    publish: async (canal: string, raw: string) => {
      publishes.push({ canal, msg: JSON.parse(raw) as Record<string, unknown> })
      return 1
    },
  }
}

const fakeKafka = () => {
  const pubs: Array<{ topico: string; msg: Record<string, unknown> }> = []
  return {
    pubs,
    publish: async (topico: string, msg: Record<string, unknown>) => { pubs.push({ topico, msg }) },
  }
}

function chamar(redis: ReturnType<typeof fakeRedis>, kafka: ReturnType<typeof fakeKafka>, text: string) {
  return routeMentions({
    text,
    tenantId:          TENANT,
    sessionId:         SID,
    senderPoolId:      "retencao_humano",
    fromParticipantId: EMISSOR,
    fromRole:          "primary",
    redis:             redis as never,
    kafka:             kafka as never,
    timestamp:         "2026-09-12T12:00:00.000Z",
    logPrefix:         "[teste]",
  })
}

/** Lê o entry de stream escrito por `writeStreamEntry` a partir dos args do xadd. */
function campoDoXadd(args: unknown[], campo: string): string | undefined {
  const i = args.indexOf(campo)
  return i >= 0 ? (args[i + 1] as string) : undefined
}

describe("routeMentions — o comando vira EVENTO DURÁVEL", () => {

  it("escreve `mention_command` no stream, com o emissor como autor", async () => {
    const redis = fakeRedis(); const kafka = fakeKafka()
    const n = await chamar(redis, kafka, "@auth_form")
    expect(n).toBe(1)

    expect(redis.xadds).toHaveLength(1)
    const args = redis.xadds[0]!
    expect(campoDoXadd(args, "type")).toBe("mention_command")
    // ⚠️ A autoria é o ponto do evento: sem ela, quem convidou o especialista deixa
    // de ser respondível por qualquer dado depois que o texto sai da mensagem.
    expect(campoDoXadd(args, "author_id")).toBe(EMISSOR)
    expect(campoDoXadd(args, "author_role")).toBe("primary")

    const payload = JSON.parse(campoDoXadd(args, "payload") as string) as Record<string, unknown>
    expect(payload["alias"]).toBe("auth_form")
    expect(payload["target_pool_id"]).toBe("auth_form_ia")
    expect(payload["outcome"]).toBe("routed")
  })

  it("o evento é `agents_only` — comando de plataforma nunca é para o cliente", async () => {
    const redis = fakeRedis(); const kafka = fakeKafka()
    await chamar(redis, kafka, "@auth_form")
    expect(campoDoXadd(redis.xadds[0]!, "visibility")).toBe(JSON.stringify("agents_only"))
  })

  it("continua convidando o pool — o registro é ADICIONAL, não substitui", async () => {
    const redis = fakeRedis(); const kafka = fakeKafka()
    await chamar(redis, kafka, "@auth_form")
    const alvos = kafka.pubs.map(p => p.msg["pool_id"])
    expect(alvos).toContain("auth_form_ia")
  })
})

describe("routeMentions — o aviso ao EMISSOR", () => {

  it("avisa com desfecho `routed`, nomeando e endereçando o emissor", async () => {
    const redis = fakeRedis(); const kafka = fakeKafka()
    await chamar(redis, kafka, "@auth_form")

    const acks = redis.publishes.filter(p => p.msg["type"] === "mention.ack")
    expect(acks).toHaveLength(1)
    expect(acks[0]!.canal).toBe(`agent:events:${SID}`)
    expect(acks[0]!.msg["outcome"]).toBe("routed")
    expect(acks[0]!.msg["recipient_participant_id"]).toBe(EMISSOR)
    expect(acks[0]!.msg["from_participant_id"]).toBe(EMISSOR)
    expect(acks[0]!.msg["target_pool_id"]).toBe("auth_form_ia")
  })

  // ⚠️ Antes de 2026-09-12 um alias inexistente só ia para o `console.log`: o texto
  // do emissor aparecia na tela e nada acontecia, indistinguível de comando aceito.
  // Agora o não-desfecho tem nome.
  it("alias desconhecido AVISA o emissor, e não vira evento no stream", async () => {
    const redis = fakeRedis(); const kafka = fakeKafka()
    const n = await chamar(redis, kafka, "@nao_existe")
    expect(n).toBe(0)

    const acks = redis.publishes.filter(p => p.msg["type"] === "mention.ack")
    expect(acks).toHaveLength(1)
    expect(acks[0]!.msg["outcome"]).toBe("unknown_alias")
    expect(acks[0]!.msg["alias"]).toBe("nao_existe")

    // O stream registra o que ACONTECEU na sessão; alias que não resolve não é fato
    // da sessão — é retorno ao emissor. Se isto virar evento, a série de
    // `mention_command` deixa de contar convites e passa a contar tentativas.
    expect(redis.xadds).toHaveLength(0)
  })

  it("o ack é best-effort e NUNCA derruba o roteamento — mas loga", async () => {
    const redis = fakeRedis(); const kafka = fakeKafka()
    redis.publish = async () => { throw new Error("pubsub fora do ar") }
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})

    const n = await chamar(redis, kafka, "@auth_form")

    expect(n).toBe(1)                                  // o convite aconteceu
    expect(redis.xadds).toHaveLength(1)                // a autoria ficou registrada
    expect(warn).toHaveBeenCalled()                    // e a falha do aviso não é muda
    warn.mockRestore()
  })
})
