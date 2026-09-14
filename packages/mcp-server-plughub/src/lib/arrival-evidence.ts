/**
 * arrival-evidence.ts — PID-09 (2026-09-14)
 *
 * A CHEGADA autenticada por um canal é evidência de posse (ADR D9): a Meta assina o webhook,
 * e o `from` de uma mensagem do WhatsApp é o telefone que a enviou. Quando esse telefone é a
 * âncora AUTORITATIVA de um cliente, a sessão aberta por ele provou a mesma posse que o OTP
 * provaria — sem pedir código.
 *
 * Quem sabe que a assinatura bateu e de quem é o número é o channel-gateway; quem GRAVA é o
 * mcp-server, pelo único escritor da evidência (`writeIdentityEvidence`). Este módulo é o juiz
 * do pedido que atravessa essa fronteira: o que o gateway pode afirmar, e o que o servidor
 * carimba sozinho.
 *
 *   - mecanismo só `whatsapp`: OTP é provado pela tool que confere o código, nunca por pedido
 *   - `verified` exige `customer_id` e `source = authoritative` — número informado pelo
 *     próprio cliente não prova nada (a mesma regra do desafio de OTP, PID-10)
 *   - `verified_at` e `proven_in_session` são do SERVIDOR: um relógio e uma sessão vindos do
 *     corpo seriam o chamador declarando a própria prova
 */
import {
  IDENTITY_EVIDENCE_STATUSES,
  type IdentityEvidenceRecord, type IdentityEvidenceStatus, type IdentityMechanism,
} from "@plughub/schemas"

/** Mecanismos que uma CHEGADA pode registrar. `otp` fica fora de propósito. */
export const ARRIVAL_MECHANISMS: readonly IdentityMechanism[] = ["whatsapp"]

export type ArrivalJudgement =
  | { kind: "ok"; tenantId: string; sessionId: string; mechanism: IdentityMechanism; record: IdentityEvidenceRecord }
  | { kind: "refuse"; status: 400 | 422; error: string; message: string }

export function judgeArrivalEvidence(body: unknown, nowIso: string): ArrivalJudgement {
  const b   = (body ?? {}) as Record<string, unknown>
  const str = (k: string) => (typeof b[k] === "string" ? (b[k] as string).trim() : "")
  const tenantId  = str("tenant_id")
  const sessionId = str("session_id")
  const mechanism = str("mechanism")
  const status    = str("status")
  if (!tenantId || !sessionId) {
    return { kind: "refuse", status: 400, error: "tenant_id_session_id_required", message: "tenant_id + session_id obrigatórios" }
  }
  if (!(ARRIVAL_MECHANISMS as readonly string[]).includes(mechanism)) {
    return { kind: "refuse", status: 422, error: "mechanism_not_arrival",
      message: `mecanismo '${mechanism}' não é registrável por chegada (aceitos: ${ARRIVAL_MECHANISMS.join(", ")})` }
  }
  if (!(IDENTITY_EVIDENCE_STATUSES as readonly string[]).includes(status)) {
    return { kind: "refuse", status: 422, error: "status_invalido", message: `status '${status}' desconhecido` }
  }
  const anchorKind = str("anchor_kind")
  const record: IdentityEvidenceRecord = {
    status: status as IdentityEvidenceStatus,
    ...(anchorKind ? { anchor_kind: anchorKind } : {}),
  }
  if (status === "verified") {
    const customerId = str("customer_id")
    const source     = str("source")
    if (!customerId) {
      return { kind: "refuse", status: 422, error: "verified_sem_cliente",
        message: "chegada verificada sem customer_id — a prova seria de ninguém" }
    }
    if (source !== "authoritative") {
      return { kind: "refuse", status: 422, error: "verified_sem_fonte_autoritativa",
        message: `chegada verificada com source='${source}' — só o telefone do cadastro autoritativo prova posse` }
    }
    Object.assign(record, { customer_id: customerId, source, verified_at: nowIso, proven_in_session: sessionId })
  }
  return { kind: "ok", tenantId, sessionId, mechanism: mechanism as IdentityMechanism, record }
}
