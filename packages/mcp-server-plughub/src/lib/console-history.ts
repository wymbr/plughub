/**
 * console-history.ts — WCH-02, fatia "Console recarregado" (2026-09-22).
 *
 * O Console recarregado refaz o contato pelo `conversation.assigned` e lê o histórico por
 * `GET /api/conversation_history`, que servia só a lista `session:{sid}:messages` (escrita pelo
 * gateway: cliente e agentes). Dois fatos do contato vivem SÓ no stream canônico e se perdiam no F5:
 *   - a chamada presa ao contato de chat (`media.call`, WCH-01) — o Console só a conhecia pelo
 *     evento ao vivo, então recarregar no meio da chamada escondia a sobreposição de mídia;
 *   - a nota do supervisor (`/supervisor/message`, analytics-api) — entregue ao vivo por
 *     `agent:events` desde a WCH-05, mas nunca escrita na lista.
 *
 * Esta função é a PROJEÇÃO do stream para o Console: pura sobre as entradas do XRANGE, sem decidir
 * nada além de ler. A transcrição da supervisão deriva o mesmo `callActive` do mesmo evento
 * (`SessionTranscript.tsx::lastCallEntry`) — duas leituras do MESMO fato, nunca dois fatos.
 */

export type RawStreamEntry = [string, string[]]

export interface ConsoleNote {
  id:         string
  author:     "supervisor"
  text:       string
  timestamp:  string
  visibility: "agents_only" | "all"
}

export interface StreamProjection {
  /** Último `media.call` diz `started` e o contato não fechou. */
  callActive:      boolean
  supervisorNotes: ConsoleNote[]
}

function fieldsOf(flat: string[]): Record<string, string> {
  const o: Record<string, string> = {}
  for (let i = 0; i + 1 < flat.length; i += 2) o[flat[i]!] = flat[i + 1]!
  return o
}

function jsonObj(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {}
  try {
    const v = JSON.parse(raw) as unknown
    return v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {}
  } catch { return {} }
}

/** `visibility` chega cru (`agents_only`, escrito pelo analytics-api) ou em JSON (`"all"`,
 *  escrito pelo `writeStreamEntry`). As duas formas são o mesmo valor. */
function scalar(raw: string | undefined): string {
  if (!raw) return ""
  try {
    const v = JSON.parse(raw) as unknown
    return typeof v === "string" ? v : raw
  } catch { return raw }
}

export function projectStreamForConsole(entries: RawStreamEntry[]): StreamProjection {
  let callState = ""
  let closed = false
  const supervisorNotes: ConsoleNote[] = []

  for (const [streamId, flat] of entries) {
    const f = fieldsOf(flat)
    const type = f["type"] ?? ""
    if (type === "session_closed") { closed = true; continue }
    if (type === "media.call") {
      callState = f["state"] || String(jsonObj(f["payload"])["state"] ?? "")
      continue
    }
    if (type !== "message") continue
    const role = f["author_role"] || String(jsonObj(f["author"])["role"] ?? "")
    if (role !== "supervisor") continue
    // Só as duas visibilidades que a rota do supervisor aceita. Lista de participantes é nota
    // DIRIGIDA, e este histórico não sabe quem está lendo — ficar de fora é o lado seguro.
    const visibility = scalar(f["visibility"])
    if (visibility !== "agents_only" && visibility !== "all") continue
    const payload = jsonObj(f["payload"])
    const content = payload["content"] as Record<string, unknown> | undefined
    const text = typeof content?.["text"] === "string" ? content["text"] as string : ""
    if (!text) continue
    supervisorNotes.push({
      id:        String(payload["message_id"] || f["event_id"] || streamId),
      author:    "supervisor",
      text,
      timestamp: f["timestamp"] ?? "",
      visibility,
    })
  }
  return { callActive: !closed && callState === "started", supervisorNotes }
}

/** Intercala as notas na lista pela hora. Hora ilegível em qualquer lado: acrescenta ao fim, na
 *  ordem do stream — ordenar por um relógio que não se lê inventaria a posição. */
export function mergeByTimestamp<T extends { timestamp?: string }>(base: T[], extra: T[]): T[] {
  if (extra.length === 0) return base
  const all = [...base, ...extra]
  if (all.some(m => Number.isNaN(Date.parse(m.timestamp ?? "")))) return all
  return all
    .map((m, i) => ({ m, i, t: Date.parse(m.timestamp!) }))
    .sort((a, b) => a.t - b.t || a.i - b.i)
    .map(x => x.m)
}
