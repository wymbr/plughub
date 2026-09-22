/**
 * AGH-05 — o último fato de presença DESTA instância no stream.
 *
 * Proposições, cada uma com o controle ao lado:
 *   - devolve o ÚLTIMO (joined depois de left → joined; left depois de joined → left);
 *   - presença de OUTRA instância não conta;
 *   - sem presença nenhuma → "" (quem pergunta grava);
 *   - atravessa páginas (a presença pode estar a mais de 200 entradas do fim).
 */
import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { lastPresenceEvent } from "../lib/stream-presence"

const SID = "sessao-presenca"
const KEY = `session:${SID}:stream`

describe("AGH-05 — lastPresenceEvent", () => {
  let redis: InstanceType<typeof RedisMock>

  beforeEach(async () => {
    redis = new RedisMock()
    await redis.flushall()   // o ioredis-mock COMPARTILHA dados entre instâncias
  })

  const presenca = (type: string, who: string) => redis.xadd(KEY, "*", "type", type, "author_id", who)

  it("devolve o ÚLTIMO fato da instância", async () => {
    await presenca("participant_joined", "human-a")
    await presenca("participant_left", "human-a")
    expect(await lastPresenceEvent(redis as never, SID, "human-a")).toBe("participant_left")
    await presenca("participant_joined", "human-a")
    expect(await lastPresenceEvent(redis as never, SID, "human-a")).toBe("participant_joined")
  })

  it("controle: presença de OUTRA instância não conta", async () => {
    await presenca("participant_joined", "human-a")
    await presenca("participant_left", "human-b")
    expect(await lastPresenceEvent(redis as never, SID, "human-a")).toBe("participant_joined")
    expect(await lastPresenceEvent(redis as never, SID, "human-c")).toBe("")
  })

  it("stream sem presença, ou vazio → vazio", async () => {
    expect(await lastPresenceEvent(redis as never, SID, "human-a")).toBe("")
    await redis.xadd(KEY, "*", "type", "message", "author_id", "human-a")
    expect(await lastPresenceEvent(redis as never, SID, "human-a")).toBe("")
  })

  it("atravessa páginas: a presença está a mais de 200 entradas do fim", async () => {
    // Leitor FALSO com a semântica do XREVRANGE (fim inclusivo, COUNT, mais novo primeiro): o
    // ioredis-mock devolve VAZIO para um id como limite superior — medido —, e com ele este teste
    // reprovaria o código certo. A mesma paginação foi medida contra o Redis real do demo.
    const entradas: Array<[string, string[]]> = [["1-0", ["type", "participant_joined", "author_id", "human-a"]]]
    for (let i = 2; i <= 452; i++) entradas.push([`${i}-0`, ["type", "message", "author_id", "cliente"]])
    const seq = (id: string) => Number(id.split("-")[0])
    const chamadas: string[] = []
    const fake = {
      xrevrange: async (_k: string, end: string, _s: string, _c: string, n: number) => {
        chamadas.push(end)
        const teto = end === "+" ? Infinity : seq(end)
        return entradas.filter(([id]) => seq(id) <= teto).reverse().slice(0, n)
      },
    }
    expect(await lastPresenceEvent(fake as never, SID, "human-a")).toBe("participant_joined")
    expect(chamadas.length).toBe(3)            // 452 entradas em páginas de 201 (uma repetida por virada)
  })
})
