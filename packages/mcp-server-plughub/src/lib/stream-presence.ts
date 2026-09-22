/**
 * stream-presence.ts — AGH-05 (2026-09-22): presença no stream só muda quando a mudança é REAL.
 *
 * O WebSocket do agente gravava `participant_left` no `close` — no ATO, antes de a carência de
 * 2,5 s decidir se era queda ou F5. E a reconexão gravava `participant_joined` de novo. Com o
 * Console voltando sozinho aos pools (medido 2026-09-22: 174 ms entre o `close` e o novo
 * `agent/ws`), o contato deixou de ir para a fila, mas o stream seguia dizendo "saiu / entrou"
 * a cada F5 — e o estado de mídia do gateway TIRA o atendente no `participant_left` e só o
 * devolve num `routing.assigned`, que sem re-roteamento não vem: um F5 no meio da chamada
 * deixaria o agente fora da própria chamada (`room_not_ready` sem fim).
 *
 * Esta função responde "qual foi o último fato de presença DESTA instância nesta sessão?", para
 * que quem grava não repita o mesmo: dois `joined` seguidos não são dois fatos.
 */

type StreamReader = {
  xrevrange: (key: string, end: string, start: string, ...args: (string | number)[]) =>
    Promise<Array<[string, string[]]>>
}

export type PresenceEvent = "participant_joined" | "participant_left" | ""

const PRESENCE = new Set(["participant_joined", "participant_left"])

/** Último `participant_joined`/`participant_left` da instância, lendo o stream de trás para
 *  frente em páginas (teto de 2 000 entradas). `""` se nunca houve — ou se não deu para ler:
 *  quem pergunta GRAVA nesse caso, porque presença duplicada é ruído e presença ausente é fato
 *  perdido. */
export async function lastPresenceEvent(
  redis: StreamReader,
  sessionId: string,
  instanceId: string,
): Promise<PresenceEvent> {
  if (!sessionId || !instanceId) return ""
  const key = `session:${sessionId}:stream`
  // Paginação por limite INCLUSIVO, descartando a entrada repetida na virada: o exclusivo
  // `(id` é do Redis 6.2+ e nem todo cliente/mock o entende — medido no teste.
  let end = "+", skip = ""
  for (let page = 0; page < 10; page++) {
    const raw = await redis.xrevrange(key, end, "-", "COUNT", 201)
    const entries = (raw ?? []).filter(([id]) => id !== skip)
    if (entries.length === 0) return ""
    for (const [, flat] of entries) {
      let type = "", author = ""
      for (let i = 0; i + 1 < flat.length; i += 2) {
        if (flat[i] === "type") type = flat[i + 1]!
        else if (flat[i] === "author_id") author = flat[i + 1]!
      }
      if (PRESENCE.has(type) && author === instanceId) return type as PresenceEvent
    }
    skip = entries[entries.length - 1]![0]
    end = skip
  }
  return ""
}
