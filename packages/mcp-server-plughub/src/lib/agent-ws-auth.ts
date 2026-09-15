/**
 * agent-ws-auth — quem pode abrir o WebSocket do agente humano (`/agent/ws`).
 *
 * CAP-19 (2026-09-15). O upgrade não olhava credencial nenhuma, e a borda do platform-ui
 * (`/agent-ws`, porta 5174, publicada em todas as interfaces) o repassava. Medido ao vivo,
 * sem token: `?pool=&user_id=` inventados registravam instância pronta que RECEBIA contato;
 * `?session_id=` alheio lia os eventos de agente da sessão e ESCREVIA no stream canônico dela
 * como atendente. O `127.0.0.1:3100` da CAP-13 não cobria isto.
 *
 * ── Onde viaja a credencial ────────────────────────────────────────────────
 * No subprotocolo: `Sec-WebSocket-Protocol: plughub.bearer, <jwt>`. O browser não deixa pôr
 * `Authorization` num WebSocket, e JWT na URL é proibido na casa (URL vai para access log de
 * proxy). O servidor responde só `plughub.bearer` — nunca devolve o token.
 *
 * ── O que se decide aqui ───────────────────────────────────────────────────
 *  · a identidade é o `sub` ASSINADO; `user_id` da query só é aceito se for igual (divergência
 *    recusa, nunca é "corrigida" em silêncio — seria o chamador escolhendo quem é);
 *  · abrir o socket de um pool exige `agent_assist.atender` (read_write) com escopo que cubra o
 *    pool — escopo vazio é grant global, a mesma semântica do `abac_can` do py-authz;
 *  · a recusa NOMEIA o motivo, e o código de fechamento separa credencial (4401) de permissão
 *    (4403).
 *
 * O pertencimento a uma SESSÃO (`?session_id=` de reconexão) não é decidido aqui: depende do
 * Redis e é conferido no handler, contra `session:{id}:human_agents`.
 */

export const AGENT_WS_PROTOCOL = "plughub.bearer"

export type AgentWsAuth =
  | { ok: true;  sub: string; tenantId: string }
  | { ok: false; code: 4401 | 4403 | 1011; reason: string }

const ACCESS_RANK: Record<string, number> = { none: 0, read_only: 1, read_write: 2 }

/** Extrai o JWT do `Sec-WebSocket-Protocol` (`plughub.bearer, <jwt>`). */
export function bearerFromProtocols(header: string | string[] | undefined): string {
  const raw = Array.isArray(header) ? header.join(",") : (header ?? "")
  const parts = raw.split(",").map(s => s.trim()).filter(Boolean)
  const i = parts.indexOf(AGENT_WS_PROTOCOL)
  return i >= 0 && parts[i + 1] ? parts[i + 1]! : ""
}

export function poolInGrantScope(scope: unknown, poolId: string): boolean {
  if (!Array.isArray(scope) || scope.length === 0) return true
  return scope.map(String).some(s => s === poolId || s === `pool:${poolId}`)
}

export function authorizeAgentWs(args: {
  protocolHeader: string | string[] | undefined
  poolId:         string
  queryUserId:    string
  /** Confere assinatura e expiração; lança em credencial inválida. */
  verify:         (token: string) => Record<string, unknown>
  /** Serviço sem segredo configurado — não há como conferir, e isso não é culpa do chamador. */
  isUnavailable?: (err: unknown) => boolean
}): AgentWsAuth {
  const token = bearerFromProtocols(args.protocolHeader)
  if (!token) return { ok: false, code: 4401, reason: "credencial ausente (subprotocolo plughub.bearer)" }

  let payload: Record<string, unknown>
  try {
    payload = args.verify(token)
  } catch (err) {
    if (args.isUnavailable?.(err)) return { ok: false, code: 1011, reason: "verificador de credencial indisponivel" }
    return { ok: false, code: 4401, reason: "credencial invalida" }
  }

  const sub = typeof payload["sub"] === "string" ? payload["sub"] : ""
  if (!sub) return { ok: false, code: 4401, reason: "credencial sem sub" }
  if (args.queryUserId && args.queryUserId !== sub) {
    return { ok: false, code: 4403, reason: "user_id da query difere do sub da credencial" }
  }

  const mc = (payload["module_config"] ?? {}) as Record<string, Record<string, { access?: string; scope?: unknown }>>
  const grant = mc["agent_assist"]?.["atender"]
  if ((ACCESS_RANK[grant?.access ?? "none"] ?? 0) < ACCESS_RANK["read_write"]!) {
    return { ok: false, code: 4403, reason: "sem agent_assist.atender" }
  }
  if (args.poolId && !poolInGrantScope(grant?.scope, args.poolId)) {
    return { ok: false, code: 4403, reason: `agent_assist.atender nao cobre o pool ${args.poolId}` }
  }
  return { ok: true, sub, tenantId: typeof payload["tenant_id"] === "string" ? payload["tenant_id"] : "" }
}
