/**
 * console-history.ts — o histórico do Console é PROJEÇÃO do stream canônico (ALW-18, 2026-09-28).
 *
 * `GET /api/conversation_history` servia a lista `session:{sid}:messages`, uma SEGUNDA casa da
 * conversa, com seis escritores em três adapters do gateway. Medido em 2026-09-28 sobre 326
 * sessões vivas: ela divergia do stream nos DOIS sentidos — duplicava entradas, não tinha a fala
 * do agente em canal nenhum além de webchat/webrtc (o único escritor de saída era o
 * `webchat_channel`: WhatsApp, SMS, e-mail e voz nunca mostraram o que o agente disse), e a
 * chamada SIP ficava só com as respostas do cliente. Nenhum desses casos fica vermelho — a tela
 * mostra MENOS conversa, e menos parece plausível. A lista saiu; esta função é a única leitura.
 *
 * Desde a WCH-02 ela já derivava daqui a chamada presa ao contato (`media.call`) e as notas do
 * supervisor; agora deriva a conversa inteira, na ORDEM do stream (não há mais intercalação por
 * relógio entre duas fontes).
 *
 * O que entra, e por quê:
 *   - `message` de cliente, agente (IA ou humano) e supervisor, pelo `content` — NUNCA o
 *     `original_content`, que é do papel autorizado da auditoria. O `content` já é a versão
 *     mascarada: tokens `[cpf:tk_…:***00]` (o Console os desenha) e a resposta de formulário
 *     redigida pelo bridge (`masked_field_echo`), que é quem grava a resposta de menu no stream;
 *   - `system_notice` — o aviso da plataforma (espera em fila muda), com autor `system`.
 * O que NÃO entra:
 *   - fala transcrita do cliente (`content.type = audio_transcript`): quem atende OUVIU, e vê-la
 *     como texto digitado no chat foi recusado na VOZ-05 fatia 4 — o registro dela é da sessão
 *     (transcrição do supervisor), não do chat;
 *   - `interaction_request` (o prompt de menu), que a lista também nunca teve;
 *   - mensagem DIRIGIDA (visibilidade = lista de participantes) que não inclui o cliente: nota
 *     privada a um agente, e este histórico não sabe quem está lendo — ficar de fora é o lado
 *     seguro. Dirigida AO cliente (ex.: NPS) entra, porque o cliente a viu.
 *
 * Humano × IA: pela identidade da instância — `human-{userId}` / `human_agent_*` é a convenção da
 * plataforma para instância humana (`lib/human-liveness.ts`). Não se pergunta ao Redis pela
 * instância, como o caminho AO VIVO faz: histórico é lido depois, e a instância pode não existir
 * mais. ⚠️ O `server.ts` grava `author_id = poolId` quando o agente humano não tem instância
 * (fallback antigo do WS); essa linha sai como `agent_ai`. Não se adivinha a partir da falta.
 */

export type RawStreamEntry = [string, string[]]

export type ConsoleAuthor = "customer" | "agent_human" | "agent_ai" | "supervisor" | "system"

export interface ConsoleMessage {
  id:         string
  author:     ConsoleAuthor
  text:       string
  timestamp:  string
  visibility: string | string[]
  /** VOZ-28 — anexo do cliente (`payload.content.attachment`, gravado pelo bridge) */
  attachment?: Record<string, unknown>
}

export interface StreamProjection {
  /** Último `media.call` diz `started` e o contato não fechou. */
  callActive: boolean
  messages:   ConsoleMessage[]
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

/** `visibility` chega cru (`agents_only`, escrito pelo analytics-api), em JSON (`"all"`, escrito
 *  pelo `writeStreamEntry`) ou como lista JSON de participantes. As formas de string são o mesmo
 *  valor; a lista volta como lista. */
function visibilityOf(raw: string | undefined): string | string[] {
  if (!raw) return "all"
  try {
    const v = JSON.parse(raw) as unknown
    if (typeof v === "string") return v
    if (Array.isArray(v)) return v.map(String)
    return raw
  } catch { return raw }
}

function roleOf(f: Record<string, string>): string {
  return f["author_role"] || String(jsonObj(f["author"])["role"] ?? "")
}

function instanceOf(f: Record<string, string>): string {
  const a = jsonObj(f["author"])
  return String(a["instance_id"] || a["participant_id"] || f["author_id"] || "")
}

function authorOf(f: Record<string, string>, type: string): ConsoleAuthor {
  if (type === "system_notice") return "system"
  const role = roleOf(f)
  if (role === "customer")   return "customer"
  if (role === "supervisor") return "supervisor"
  return instanceOf(f).startsWith("human") ? "agent_human" : "agent_ai"
}

export function projectStreamForConsole(entries: RawStreamEntry[]): StreamProjection {
  let callState = ""
  let closed = false

  // 1ª passada: quem é o cliente — é o que decide se uma mensagem dirigida entra.
  const clientes = new Set<string>()
  for (const [, flat] of entries) {
    const f = fieldsOf(flat)
    if (f["type"] === "message" && roleOf(f) === "customer") {
      const a = jsonObj(f["author"])
      for (const id of [a["participant_id"], a["instance_id"], f["author_id"]]) {
        if (typeof id === "string" && id) clientes.add(id)
      }
    }
  }

  const messages: ConsoleMessage[] = []
  for (const [streamId, flat] of entries) {
    const f = fieldsOf(flat)
    const type = f["type"] ?? ""
    if (type === "session_closed") { closed = true; continue }
    if (type === "media.call") {
      callState = f["state"] || String(jsonObj(f["payload"])["state"] ?? "")
      continue
    }
    if (type !== "message" && type !== "system_notice") continue

    const visibility = visibilityOf(f["visibility"])
    if (Array.isArray(visibility) && !visibility.some(p => clientes.has(p))) continue

    const payload = jsonObj(f["payload"])
    const content = (payload["content"] ?? {}) as Record<string, unknown>
    if (content["type"] === "audio_transcript") continue
    const text = typeof content["text"] === "string" ? content["text"] as string : ""
    if (!text) continue

    messages.push({
      id:        String(payload["message_id"] || f["event_id"] || streamId),
      author:    authorOf(f, type),
      text,
      timestamp: f["timestamp"] ?? "",
      visibility,
      ...(content["attachment"] && typeof content["attachment"] === "object"
        ? { attachment: content["attachment"] as Record<string, unknown> } : {}),
    })
  }
  return { callActive: !closed && callState === "started", messages }
}
